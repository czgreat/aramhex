"""云控配置（改了不用发版）。只有官方安装包会取；没有官方模块时一律用内置默认值，见 official.py。

    downloads   网盘链接 {setup, portable}（每次发版会换）
    community   社区入口列表 [{name, text, url, qr, primary}]：QQ 群、B 站、赞助……
                软件「关于」里全部列出；primary 的那条在主界面顶部显示成按钮（网站同理）
    notice      公告 {id, text, url}：同一个 id 只弹一次
    min_version 低于这个版本必须更新
    rank        查分 {enabled, message, as_of}：可以远程关掉查询
    event       当前活动 {mode, label, start, end}：例如 first-prismatic「首次必棱彩」，活动期间改用这个规则的统计数据
    mirrors     额外线路 {update: [".../{version}"], data: [...]}：换服务器、加线路不用发版，排在内置线路前面
    features    功能开关 {名字: true/false}（1.1.2）：新做法出问题时云控关掉、退回 1.1.1 的老做法，不用发版。
                没写 / 连不上 = 开。title_priority 整块扫描先读标题；same_name_once 同一行同名海克斯只算一张；
                overlay_fit 分数缩放时浮层收回多出的 1 像素、不反复重摆；cards_drop_early 选完英雄卡马上撤卡下胜率；
                companion_last_fg 客户端被别的程序盖着时点浮窗，不再把标注画到那个程序上
                four_choice（1.1.8）认四选一、第一次认出没认全时整块核对布局；关掉按三选一认（1.1.5 的认法）
                panel_no_activate（1.1.9）点浮窗图标、详情不抢前台，只有点进搜英雄的输入框才抢；关掉退回 1.1.8（点了就抢、点完还回去）
                count_switch（1.2.0）三张、四张之间切换：读到半截名字先按另一种张数读标题条；整块扫描没认全时
                张数和位置一起换；这局第一次认出没认全时等整块核对完再出推荐。关掉退回 1.1.9
                layout_memory（1.2.0）本机三张、四张的标题位置各存一份；这局还没认出时两种位置轮流读标题条，
                不用等整块扫描（没见过四选一按三张的卡距推算）。关掉只用最近那份（1.2.0-test.5 的做法）；
                count_switch、four_choice 关了也不轮流
                overlay_bg（1.2.0）详情里的「背景」按钮（透明 / 半透明 / 不透明）；关掉不显示，按透明画（1.1.9 的样子），玩家选的留着
                panel_typing_in_game（1.2.0）对局里从游戏点进详情搜英雄（前台是详情或补全列表）照常算游戏在前台，浮窗不藏、
                三选一标签照常画（从别的程序点进来的不算）；在详情里打字时快捷键不生效。关掉退回 1.1.9
                overlay_hotkey（1.2.0，没写 = 关）设置页「详情快捷键」和快捷键本身。默认不显示、不注册，只用鼠标移到浮窗位置唤出，
                设置页「浮窗位置」下面写明；打开退回 1.1.9（玩家设过的键还在设置文件里，打开后照常生效）
    update      检查频率和分批推送（1.1.0 起）：
                  check_minutes   多久查一次云控和新版本（默认 180，30–1440）；启动时、手动「检查更新」照常查
                  data_minutes    多久查一次统计数据（默认 360，60–1440）
                  official_minutes 官方资料检查（0 关闭，60 一小时，43200 三十天）；只从 mirrors.official 更新
                  rollout_version / rollout_percent  这个版本先只提示一部分人（0–100）。按本机随机编号分组，
                                  同一台电脑分组固定，把比例调大，之前提示过的人仍在里面
                  paused          true 时暂停提示新版本（发现问题时用）；「最低版本」和手动检查不受影响

    site        换官网地址（1.1.3）{urls: ["https://新域名"]}：检查更新、下载更新、软件里的官网链接先用这里的地址，
                官方模块内置的永远排在最后兜底。只收 https://域名[:端口]，最多 3 个。
                1.3.0 起配置、统计数据、官方资料、查分、日志只连官方模块内置的地址；写内置的地址可以调先后。

连不上就用上次存下的（app_home/cloud.json），再没有就用内置默认值。
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import unicodedata

import requests

from . import HOMEPAGE, __version__, official
from .config import app_home, atomic_write_text

log = logging.getLogger(__name__)
DEFAULT = {
    "downloads": {},
    "community": [],
    "notice": {},
    "min_version": "",
    # as_of：查分库最新数据的日期（1.1.1）；比本机上次查到的新，就自动重查一次
    "rank": {"enabled": True, "message": "", "as_of": ""},
    "event": {"mode": "", "label": "", "start": "", "end": ""},
    "mirrors": {"update": [], "data": [], "official": []},
    "features": {},
    "augments": {"aliases": {}, "rarity_fix": {}},
    "update": {"check_minutes": 180, "data_minutes": 360, "official_minutes": 0, "rollout_version": "", "rollout_percent": 100,
               "paused": False},
    # 大厅里助手图标的位置（客户端 1280×720 下的像素，按窗口高度等比例）：图标中心离右边、离上边，直径。
    # 默认在头像右边「在线」那一行、铃铛左边；客户端改版挪了位置时云控改这三个数就行
    "ui": {"client_badge_right": 60, "client_badge_top": 53, "client_badge_size": 26},
    "settings": [],  # 云控设置规则，见 cloudsettings.py
    "settings_v2": [],  # 1.1.9 起：可以按电脑挑的规则，1.1.8 及以前不读这一段，见 cloudsettings.py
    # 开发者点名上传日志（见 logupload.py）：request 里是匿名编号（可带「:单号」）
    # max_kb：上传最近多少 KB 日志（1.1.2，64–4096，默认 512）；当前日志不够长时接着读上一个日志文件
    # verbose：临时记详细日志的电脑 ["<匿名编号>:<单号>"]（1.1.2，每个单号最多 24 小时，不截图，见 logupload.py）
    "logs": {"request": [], "max_kb": 512, "verbose": []},
    "site": {"urls": []},
}
RESOURCE_TIMEOUT = 30  # 官方模块一次调用的总时间：领凭证、换线路都算在内，机械盘上第一次还要核对程序文件
_SITE_RE = re.compile(r"https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:[0-9]{1,5})?")
_lock = threading.Lock()
_cache: dict | None = None
# 写缓存文件单独一把锁：界面线程常调 get()，不能等写盘（fsync、杀毒软件扫描都可能慢）。
# _saved 记最新一次 refresh 的编号，同时有两次 refresh 时旧的不会盖掉新的。
_write_lock = threading.Lock()
_saved = 0
_install_id: str | None = None  # 匿名使用统计的编号；关掉时为 None，不带
_INSTALL_ID_HEADER = "X-AramHex-Id"


def set_install_id(value: str | None) -> None:
    global _install_id
    _install_id = value or None


def headers(**extra) -> dict:
    """软件自己发的请求统一带上版本号，不带匿名编号。"""
    return {"User-Agent": f"aramhex/{__version__}", **extra}


def update_headers(url: str, **extra) -> dict:
    """检查、下载更新用：发往官网的再带匿名编号（关了使用统计就不带），GitHub 等别人的服务器只带版本号。"""
    h = headers(**extra)
    if _install_id and url.startswith("https://") and own(url):
        h[_INSTALL_ID_HEADER] = _install_id  # 只用来数「同一台电脑是不是一直在用」
    return h


class _UpdateSession(requests.Session):
    def rebuild_auth(self, prepared_request, response) -> None:
        super().rebuild_auth(prepared_request, response)
        if self.should_strip_auth(response.request.url, prepared_request.url):
            prepared_request.headers.pop(_INSTALL_ID_HEADER, None)


def get_update(url: str, *, headers: dict | None = None, **kwargs) -> requests.Response:
    """发更新请求，跨源跳转时移除匿名编号。"""
    request_headers = update_headers(url, **(headers or {}))
    if _INSTALL_ID_HEADER in request_headers:
        with _UpdateSession() as session:
            return session.get(url, headers=request_headers, **kwargs)
    return requests.get(url, headers=request_headers, **kwargs)


def fetch(url: str, timeout: float = 15) -> requests.Response:
    """取一个小文件：official:// 的经官方模块（timeout 放宽成整次调用的总时间），其余直接请求。"""
    if official.is_resource(url):
        kind, name = official.split(url)
        return official.resource(kind, name, sites(), timeout=max(timeout, RESOURCE_TIMEOUT))
    return requests.get(url, headers=headers(), timeout=timeout)


def _path():
    return app_home() / "cloud.json"


def _num(v, default: float) -> float:
    """云控里的数：认不出、无穷大、NaN 都按默认值（写错一个数不能让程序起不来）。"""
    if isinstance(v, bool):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if math.isfinite(f) else default


def _coerce(default, v):
    """字典里的一项按默认值的类型收下：bool 只认 true/false，数按 _num，其余转成字符串。"""
    if isinstance(default, bool):
        return v if isinstance(v, bool) else default
    if isinstance(default, (int, float)):
        return _num(v, default)
    return str(v)


# 这些字典里的值都是文字（公告、网盘链接）；features 是开关
_STR_DICTS = ("notice", "downloads")


def _augment_rules(raw) -> dict:
    """可选热修：每类最多 128 个 ID；每 ID 最多 8 个别名，总共最多 512 个。"""
    out = {"aliases": {}, "rarity_fix": {}}
    if not isinstance(raw, dict):
        log.info("云控海克斯：augments 必须是对象，忽略")
        return out
    for field in out:
        entries = raw.get(field, {})
        if not isinstance(entries, dict):
            log.info("云控海克斯：%s 必须是对象，忽略", field)
            continue
        count = 0
        for index, (aid, value) in enumerate(entries.items()):
            if index >= 128:
                log.info("云控海克斯：%s 超过 128 项，忽略剩余项", field)
                break
            valid_id = isinstance(aid, str) and re.fullmatch(r"[1-9][0-9]{0,8}", aid)
            if field == "aliases":
                valid = (isinstance(value, list) and len(value) <= 8
                         and all(isinstance(n, str) and 1 <= len(n.strip()) <= 64
                                 and not any(unicodedata.category(c).startswith("C") for c in n)
                                 for n in value))
                if valid:
                    value = list(dict.fromkeys(n.strip() for n in value))
                    if count + len(value) > 512:
                        log.info("云控海克斯：别名超过 512 个，忽略剩余项")
                        break
            else:
                valid = isinstance(value, str) and value in ("silver", "gold", "prismatic")
            if not valid_id or not valid:
                log.info("云控海克斯：%s 第 %d 项格式不对，忽略", field, index + 1)
                continue
            out[field][aid] = value
            if field == "aliases":
                count += len(value)
    return out


def _site_urls(raw) -> list[str]:
    """云控 site.urls：只收 https://域名[:端口]（不带路径），去掉结尾的 /，最多 3 个。"""
    urls = (raw.get("urls") if isinstance(raw, dict) else None) or []
    if not isinstance(urls, list):
        return []
    out = []
    for u in urls:
        u = u.strip().rstrip("/").lower() if isinstance(u, str) else ""
        if _SITE_RE.fullmatch(u) and u not in out and u not in official.sites()[:1]:
            out.append(u)
    return out[:3]


def _with_builtin(urls: list[str]) -> list[str]:
    """云控地址在前，再接官方模块内置的，去重。没有官方模块就一个都没有。"""
    return list(dict.fromkeys(urls + official.sites())) if official.available() else []


def _owns(url: str, sites: list[str]) -> bool:
    return any(url.startswith(s + "/") for s in sites)


def _merge(raw: dict) -> dict:
    out = json.loads(json.dumps(DEFAULT))
    if not isinstance(raw, dict):
        return out
    out["site"]["urls"] = _site_urls(raw.get("site"))
    for k, v in raw.items():
        if k == "site":
            continue
        if k == "augments":
            out[k] = _augment_rules(v)
        elif k == "community" and isinstance(v, list):
            items = []
            for it in v[:8]:
                if not isinstance(it, dict) or not str(it.get("name") or "").strip():
                    continue
                url = str(it.get("url") or "")
                qr = str(it.get("qr") or "")
                items.append({"name": str(it["name"]).strip()[:20], "text": str(it.get("text") or "").strip()[:40],
                              "url": url if url.startswith(("https://", "http://")) else "",
                              "qr": qr if qr.startswith("https://") else "", "primary": bool(it.get("primary"))})
            out[k] = items
        elif k in ("settings", "settings_v2") and isinstance(v, (dict, list)):
            # 云控设置规则（见 cloudsettings.py）：原样留着，用的时候逐项检查
            out[k] = [r for r in (v if isinstance(v, list) else [v]) if isinstance(r, dict)][:20]
        elif k == "logs" and isinstance(v, dict):
            if isinstance(v.get("request"), list):
                out[k]["request"] = [str(x)[:64] for x in v["request"] if isinstance(x, str)][:200]
            if isinstance(v.get("verbose"), list):
                out[k]["verbose"] = [str(x)[:64] for x in v["verbose"] if isinstance(x, str)][:50]
            kb = v.get("max_kb")
            if isinstance(kb, (int, float)) and not isinstance(kb, bool) and math.isfinite(kb):
                out[k]["max_kb"] = int(max(64, min(4096, kb)))
        elif k == "mirrors" and isinstance(v, dict):
            for kk in ("update", "data", "official"):
                urls = v.get(kk)
                if isinstance(urls, list):
                    out[k][kk] = [u for u in urls if isinstance(u, str) and u.startswith("https://")][:5]
        elif k in out and isinstance(out[k], dict) and isinstance(v, dict):
            for kk, vv in v.items():
                if not isinstance(vv, (str, bool, int, float)):
                    continue
                if kk in out[k]:
                    out[k][kk] = _coerce(out[k][kk], vv)
                elif k in _STR_DICTS:
                    out[k][kk] = str(vv)
                elif k == "features":
                    if isinstance(vv, bool):
                        out[k][kk] = vv
                else:
                    out[k][kk] = vv
        elif k in out and not isinstance(out[k], (dict, list)) and isinstance(v, (str, int, float)):
            out[k] = str(v)
    return out


def get() -> dict:
    """当前配置（不联网）。"""
    global _cache
    with _lock:
        if _cache is None and not official.available():
            _cache = _merge({})  # 以前装过官方版、留下的云控也不用
        if _cache is None:
            try:
                _cache = _merge(json.loads(_path().read_text(encoding="utf-8")))
            except Exception:  # 存下的云控坏了：按内置默认值，下次联网再取
                _cache = _merge({})
        return _cache


def sites() -> list[str]:
    """官网地址：云控 site.urls 在前，官方模块内置的永远在最后；没有官方模块时是空的。"""
    return _with_builtin((get().get("site") or {}).get("urls", []))


def site() -> str:
    """现在用的官网地址（软件里的官网、下载链接）；没有官方模块时用 GitHub 主页。"""
    return next(iter(sites()), HOMEPAGE)


def help_page() -> str:
    return f"{site()}/#faq" if official.available() else HOMEPAGE


def own(url: str) -> bool:
    """是不是发往我们自己官网的请求（检查、下载更新时带匿名编号）。"""
    return _owns(url, sites())


def mirrors(kind: str) -> list[str]:
    """云控里的额外线路（update 里的 {version} 由调用方替换）。"""
    return list((get().get("mirrors") or {}).get(kind) or [])


def community() -> list[dict]:
    return list(get().get("community") or [])


def primary_link() -> dict | None:
    """主界面顶部按钮：第一条标了 primary 且有链接的。"""
    return next((c for c in community() if c.get("primary") and c.get("url")), None)


def event() -> dict | None:
    """正在进行的活动 {mode, label}；没有返回 None。start / end 是带时区的 ISO 时间，空着表示不限。"""
    from datetime import datetime, timezone
    ev = get().get("event") or {}
    mode = str(ev.get("mode") or "").strip()
    if not mode or mode == "normal":
        return None
    now = datetime.now(timezone.utc)
    try:
        if ev.get("start") and now < datetime.fromisoformat(ev["start"]):
            return None
        if ev.get("end") and now >= datetime.fromisoformat(ev["end"]):
            return None
    except (TypeError, ValueError):
        return None
    return {"mode": mode, "label": str(ev.get("label") or mode)}


def _minutes(key: str, default: int, lo: int, hi: int) -> int:
    v = int(_num((get().get("update") or {}).get(key, default), default))
    return max(lo, min(hi, v))


def check_minutes() -> int:
    """多久查一次云控和新版本（分钟）。"""
    return _minutes("check_minutes", 180, 30, 1440)


def data_minutes() -> int:
    """多久查一次统计数据（分钟）。"""
    return _minutes("data_minutes", 360, 60, 1440)


def official_minutes() -> int:
    """官方资料检查间隔：0 关闭，60 一小时，43200 三十天。"""
    v = _num((get().get("update") or {}).get("official_minutes", 0), 0)
    return max(0, min(43200, int(v)))


def bucket(install_id: str | None) -> int:
    """本机分组 0–99：按安装时生成的随机编号算，只在本机用。没有编号的算最后一组。"""
    import hashlib
    if not install_id:
        return 99
    return int(hashlib.sha256(install_id.encode()).hexdigest()[:8], 16) % 100


def offer_update(version: str, install_id: str | None) -> bool:
    """定时检查查到新版本后，要不要提示这台电脑（分批推送、暂停）。"""
    u = get().get("update") or {}
    if u.get("paused") is True:  # 只认 true；写成 "false"、"no" 这类文字不算暂停
        return False
    if str(u.get("rollout_version") or "").lstrip("vV") != str(version).lstrip("vV"):
        return True
    pct = int(_num(u.get("rollout_percent", 100), 100))
    return bucket(install_id) < max(0, min(100, pct))


def feature(name: str, default: bool = True) -> bool:
    v = (get().get("features") or {}).get(name)
    return default if v is None else bool(v)


def refresh(timeout: float = 6, url: str | None = None) -> dict:
    """联网取最新配置并存下来；失败就返回上次的。没指定地址时由官方模块按 sites() 的顺序取。"""
    global _cache, _saved
    raw = None
    for u in [url] if url else official.sources("config"):
        try:
            r = fetch(u, timeout)
            r.raise_for_status()
            raw = r.json()
            if not isinstance(raw, dict):
                raise ValueError("格式不对")
            break
        except (requests.RequestException, ValueError) as e:
            log.info("取云控配置失败：%s %s", u, e)
            raw = None
    if raw is None:
        return get()
    cfg = _merge(raw)
    text = json.dumps(raw, ensure_ascii=False)
    with _lock:
        _cache = cfg
        _saved += 1
        mine = _saved
    with _write_lock:
        if mine == _saved:  # 等锁期间有更新的 refresh：由它来写
            try:
                atomic_write_text(_path(), text)
            except OSError:
                pass
    return cfg
