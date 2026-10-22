"""帮助改善软件（1.1.1）：开发者在云控里点名某台电脑，才上传这台电脑的运行日志，用来查问题。

- 只在设置里「帮助改善软件」开着时才会上传（就是 send_usage，默认开）；关掉就不发匿名编号，也不上传。
- 云控 config.json：
      "logs": {"url": "https://aramhex.com/api/logs", "request": ["<匿名编号>", "<匿名编号>:<单号>"]}
  列表里有本机的匿名编号才上传；同一条（编号或编号:单号）只传一次，要再传一次就换个单号。
- 只传 aramhex.log 最后 MAX_BYTES 字节，上传前去掉游戏账号名（名字#编号）、电脑用户名、计算机名。
"""

from __future__ import annotations

import gzip
import logging
import os
import re
import threading

import requests

from . import __version__, cloud
from .config import app_home

log = logging.getLogger(__name__)
MAX_BYTES = 512 * 1024
_lock = threading.Lock()


TICKET = re.compile(r"[0-9A-Za-z_-]{1,32}")  # 单号：和服务端同一个规则（字母、数字、_、-，最多 32 位）
GIVE_UP = {400, 403, 404, 413}  # 这几种重试也没用；其他失败（5xx、429、断网）下次刷新云控再试
MAX_TRIES = 5


def _tickets(cfg: dict, install_id: str) -> list[str]:
    req = ((cfg or {}).get("logs") or {}).get("request") or []
    out = []
    for e in req:
        if not isinstance(e, str):
            continue
        if e == install_id:
            out.append(e)
        elif e.startswith(install_id + ":") and TICKET.fullmatch(e.partition(":")[2]):
            out.append(e)
    return out


def redact(text: str, riot_id: str = "") -> str:
    """去掉日志里能认出人的东西：游戏账号名、Windows 用户名（含路径里的）、计算机名。"""
    names = []
    rid = (riot_id or "").strip()
    if rid:
        names += [rid, rid.rpartition("#")[0]]
    for var in ("USERNAME", "USER", "COMPUTERNAME", "USERDOMAIN"):
        v = os.environ.get(var) or ""
        if len(v) >= 2:
            names.append(v)
    for n in sorted({n for n in names if n and len(n) >= 2}, key=len, reverse=True):
        text = re.sub(re.escape(n), "<已去掉>", text, flags=re.I)  # 日志里的大小写可能和设置里的不一样
    # 用户文件夹名可以带空格：一直到下一个 \ / 引号 或行尾
    text = re.sub(r"(?i)([A-Z]:[\\/]+(?:Users|Documents and Settings)[\\/]+)[^\\/\"'\r\n：]+", r"\1<用户>", text)
    text = re.sub(r"(/(?:home|Users)/)[^/\"'\r\n]+", r"\1<用户>", text)
    # 名字#编号 这种写法（别人的游戏账号也可能出现在日志里）
    text = re.sub(r"[^\s#：:，,（()）\[\]\"'<>]{1,16}#[0-9A-Za-z]{3,5}(?![0-9A-Za-z])", "<游戏账号>", text)
    return text


def _read_tail(path, limit: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - limit))
        data = f.read()
    if size > limit:  # 从中间截的：丢掉半行
        data = data.split(b"\n", 1)[-1]
    return data


def maybe_upload(settings, cfg: dict, timeout: float = 30) -> list[str]:
    """云控点名了本机就上传日志；返回这次传成功的单号。不在主线程调用。"""
    if not settings.send_usage or not settings.install_id:
        return []
    sent = settings.extra.get("logs_sent") if isinstance(settings.extra.get("logs_sent"), list) else []
    tries = settings.extra.get("logs_tries") if isinstance(settings.extra.get("logs_tries"), dict) else {}
    todo = [t for t in _tickets(cfg, settings.install_id) if t not in sent and int(tries.get(t) or 0) < MAX_TRIES]
    url = str(((cfg or {}).get("logs") or {}).get("url") or "")
    if not todo or not url.startswith(cloud.WEBSITE + "/"):
        return []
    if not _lock.acquire(blocking=False):
        return []
    done = []
    try:
        path = app_home() / "aramhex.log"
        try:
            text = _read_tail(path, MAX_BYTES).decode("utf-8", "replace")
        except OSError as e:
            log.info("读不到日志，不上传：%s", e)
            return []
        body = gzip.compress(redact(text, settings.riot_id).encode("utf-8"))
        failed = {}
        for t in todo:
            try:
                r = requests.post(url, data=body, timeout=timeout,
                                  headers=cloud.headers(url, **{"Content-Type": "application/gzip",
                                                                "X-AramHex-Id": settings.install_id,
                                                                "X-AramHex-Ticket": t.partition(":")[2],
                                                                "X-AramHex-Version": __version__}))
            except requests.RequestException as e:
                log.info("上传日志失败（下次再试）：%s", e)
                failed[t] = int(tries.get(t) or 0) + 1
                break
            if r.status_code in (200, 201, 204, 409):  # 409：服务器已经收过这一份
                done.append(t)
                log.info("已按开发者请求上传运行日志（%d KB，已去掉账号名和电脑用户名）", len(body) // 1024)
            elif r.status_code in GIVE_UP:
                log.info("上传日志被拒（%s），不再重试这一份", r.status_code)
                done.append(t)
            else:
                log.info("上传日志失败（%s），下次再试", r.status_code)
                failed[t] = int(tries.get(t) or 0) + 1
        if done or failed:
            with settings._lock:  # 界面线程也会改设置、存设置
                settings.extra["logs_sent"] = (sent + done)[-20:]
                settings.extra["logs_tries"] = {**{k: v for k, v in tries.items() if k not in done}, **failed}
            settings.save()
    finally:
        _lock.release()
    return done
