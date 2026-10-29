"""程序更新：查 GitHub Releases 有没有新版本；用户点了才下载安装，从不自己装。

    GET https://api.github.com/repos/<仓库>/releases/latest
      tag_name  版本（v1.2.3）
      body      更新说明
      assets    AramHex-Setup-<版本>.exe（安装版）、AramHex-Portable-<版本>.zip（便携版）、SHA256SUMS.txt

安装包的 SHA-256 优先用 GitHub 给每个 asset 算的 digest（"sha256:..."），没有时读 SHA256SUMS.txt。
下载后核对 SHA-256，不一致就删掉并报错。匿名接口每个 IP 每小时 60 次，启动、再次打开、每 30 分钟各查一次足够。
便携版不能替换自己：有新版本时只打开下载页。

国内常常连不上 GitHub：查不到时改从 jsDelivr 读最新版本号（取最新 tag 里的 lolhex/__init__.py）。
这条路拿不到安装包和 SHA-256，所以只提示有新版本、打开下载页，不在程序里直接安装。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests

from . import GITHUB_REPO, MIRROR, RELEASES_PAGE, WEBSITE, cloud

log = logging.getLogger(__name__)
API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
FILE_RE = re.compile(r"^AramHex-Setup-[0-9][0-9A-Za-z.\-]*\.exe$")
SUMS = "SHA256SUMS.txt"
VERSION_MIRRORS = [f"https://{host}/gh/{GITHUB_REPO}@latest/lolhex/__init__.py"
                   for host in ("fastly.jsdelivr.net", "gcore.jsdelivr.net", "cdn.jsdelivr.net")]
_VERSION = re.compile(r'^__version__\s*=\s*"([0-9][0-9A-Za-z.\-]*)"', re.M)


PROBE_HOSTS = ("fastly.jsdelivr.net", "gcore.jsdelivr.net")
SITE_LATEST = f"{MIRROR}/latest.json"
DOWNLOAD_PAGE = f"{WEBSITE}/#download"


def download_page() -> str:
    """官网下载页（云控换了官网地址就用新的）。"""
    return f"{cloud.site()}/#download"
SLOW_BPS = 150 * 1024    # 整包下载：前 SLOW_AFTER_S 秒平均低于这个速度就放弃，改让用户去网盘
SLOW_AFTER_S = 12


class SlowDownload(Exception):
    """从 GitHub 下载安装包太慢。"""


def pan_links(timeout: float = 6) -> dict:
    """当前的网盘链接 {"setup": ..., "portable": ...}（云控配置，每次发版会换）；取不到返回空字典。"""
    from . import cloud
    d = cloud.refresh(timeout).get("downloads") or {}
    return {k: str(d[k]) for k in ("setup", "portable") if d.get(k)}


def check_site(current: str, timeout: float = 6, url: str = SITE_LATEST) -> dict | None:
    """官网镜像上的最新版本（DMIT 从 GitHub Release 同步：版本、说明、安装包名、SHA-256、GitHub 下载地址）。"""
    r = requests.get(url, headers=cloud.headers(url), timeout=timeout)
    r.raise_for_status()
    j = r.json()
    version = str(j["version"]).lstrip("vV")
    if not is_newer(version, current):
        return None
    s = j.get("setup") or {}
    sha = str(s.get("sha256") or "").lower()
    ok = bool(FILE_RE.match(str(s.get("name") or "")) and re.fullmatch(r"[0-9a-f]{64}", sha) and s.get("url"))
    return {"version": version, "file": s.get("name") if ok else None, "sha256": sha if ok else None,
            "size": s.get("size") if ok else None, "url": s.get("url") if ok else None,
            "notes": str(j.get("notes") or "").strip()[:600], "page": RELEASES_PAGE, "mirror": not ok}


def _next_versions(v: str) -> list[str]:
    k = list(version_key(v)) + [0, 0, 0]
    major, minor, patch_ = k[:3]
    base = [f"{major}.{minor}.{patch_}"] if is_test(v) else []  # 测试版 1.1.3-test.2 先试正式版 1.1.3
    return base + [f"{major}.{minor}.{patch_ + 1}", f"{major}.{minor + 1}.0", f"{major + 1}.0.0"]


def probe_newer(current: str, timeout: float = 8, hosts=PROBE_HOSTS, steps: int = 6) -> tuple[str | None, bool]:
    """按 tag 试探下一个版本（1.0.4 → 试 1.0.5、1.1.0、2.0.0），找到就从那个版本接着试。
    jsDelivr 按 tag 取是即时的，不像"最新版"别名那样会缓存好几个小时。返回 (最高版本或 None, 是否连上过)。"""
    from .patch import tag_for
    found, reached, v = None, False, current
    for _ in range(steps):
        hit = None
        for nxt in _next_versions(v):
            for host in hosts:
                try:
                    r = requests.get(f"https://{host}/gh/{GITHUB_REPO}@{tag_for(nxt)}/manifest.json",
                                     headers=cloud.headers(), timeout=timeout)
                except requests.RequestException:
                    continue
                reached = True
                if r.ok:
                    try:
                        if r.json().get("version") == nxt:
                            hit = nxt
                    except ValueError:
                        pass
                break  # 这个节点答复了（有或没有），不用再问别的节点
            if hit:
                break
        if not hit:
            break
        found = v = hit
    return found, reached


def check_mirror(current: str, timeout: float = 8, mirrors=VERSION_MIRRORS, probe_hosts=PROBE_HOSTS) -> dict | None:
    """GitHub 连不上时从镜像查最新版本：按 tag 试探下一个版本，再读各节点的"最新版"，取最高的。
    有新版本返回只带下载页的信息（安装包地址和 SHA-256 拿不到，增量更新另有清单校验），没有返回 None；
    镜像都连不上时抛异常。"""
    from concurrent.futures import ThreadPoolExecutor

    def alias(url):
        r = requests.get(url, headers=cloud.headers(url), timeout=timeout)
        r.raise_for_status()
        m = _VERSION.search(r.text)
        if not m:
            raise ValueError("镜像上的版本号读不出来")
        return m.group(1)

    versions, last, reached = [], None, False
    # 试探 tag 和问各节点的"最新版"同时进行（各节点对"最新版"的缓存不一样，都问，取最高的）
    with ThreadPoolExecutor(max_workers=1 + len(mirrors)) as pool:
        probe = pool.submit(probe_newer, current, timeout, probe_hosts) if probe_hosts else None
        asks = [pool.submit(alias, url) for url in mirrors]
        for fut in asks:
            try:
                versions.append(fut.result())
                reached = True
            except (requests.RequestException, ValueError) as e:
                last = e
        if probe:
            probed, ok = probe.result()
            reached |= ok
            if probed:
                versions.append(probed)
    if not reached:
        raise last or requests.ConnectionError("没有可用的镜像")
    best = max(versions, key=version_key, default=None)
    if not best or not is_newer(best, current):
        return None
    return {"version": best, "file": None, "sha256": None, "size": None, "url": None,
            "notes": "", "page": RELEASES_PAGE, "mirror": True}


_PRE = re.compile(r"^v?(\d+(?:\.\d+){0,3})(?:-([A-Za-z]+)\.?(\d*))?$")


def version_key(v: str) -> tuple:
    """「1.1.3-test.2」这种测试版排在 1.1.2 之后、正式版 1.1.3 之前：正式版一发，测试版就会提示更新（1.1.3）。
    以前只取数字，1.1.3-test.2 会被当成 1.1.3.2，比正式版还新。"""
    s = str(v).strip()
    m = _PRE.match(s)
    if not m:
        return tuple(int(x) for x in re.findall(r"\d+", s)[:4])
    nums = [int(x) for x in m.group(1).split(".")]
    nums += [0] * (4 - len(nums))
    pre = (0, int(m.group(3) or 0)) if m.group(2) else (1, 0)
    return (*nums, *pre)


def is_test(v: str) -> bool:
    """带测试标记的版本号（1.1.3-test.2）。"""
    m = _PRE.match(str(v).strip())
    return bool(m and m.group(2))


def is_newer(latest: str, current: str) -> bool:
    return bool(version_key(latest)) and version_key(latest) > version_key(current)


def _sha_from_sums(assets: list[dict], name: str, timeout: float) -> str | None:
    sums = next((a for a in assets if a.get("name") == SUMS), None)
    if not sums:
        return None
    r = requests.get(sums["browser_download_url"], headers=cloud.headers(sums["browser_download_url"]), timeout=timeout)
    r.raise_for_status()
    for line in r.text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def check(current: str, timeout: float = 8, strict: bool = False, api: str = API,
          mirrors=VERSION_MIRRORS, probe_hosts=PROBE_HOSTS, site: str | None = SITE_LATEST) -> dict | None:
    """有新版本返回它的信息，没有或查不到返回 None。先问官网镜像，不通再问 GitHub 和 jsDelivr。
    strict=True（用户手动点"检查更新"）时查不到就抛异常，好告诉用户是没新版本还是没连上。
    site 用默认值时按云控的官网地址顺序问（换了新官网先问新的，内置 aramhex.com 最后）。"""
    for url in ([f"{s}/u/latest.json" for s in cloud.sites()] if site == SITE_LATEST else [site] if site else []):
        try:
            return check_site(current, min(timeout, 6), url)
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            log.info("官网查更新失败（%s），换下一个：%s", url, e)
    try:
        r = requests.get(api, headers=cloud.headers(api, Accept="application/vnd.github+json"), timeout=timeout)
        if r.status_code == 404:
            return None  # 还没有发布过
        r.raise_for_status()
        rel = r.json()
        version = str(rel.get("tag_name") or "").lstrip("vV")
        if rel.get("draft") or rel.get("prerelease") or not is_newer(version, current):
            return None
        assets = rel.get("assets") or []
        setup = next((a for a in assets if FILE_RE.match(a.get("name") or "")), None)
        if not setup:
            raise ValueError("这个版本没有安装包")
        digest = str(setup.get("digest") or "")
        sha = digest[7:].lower() if digest.startswith("sha256:") else _sha_from_sums(assets, setup["name"], timeout)
        if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("这个版本的安装包没有 SHA-256，不能安全下载")
    except requests.RequestException as e:  # 连不上 GitHub（或被限流）：换镜像查版本号
        log.info("GitHub 查更新失败，改查镜像：%s", e)
        try:
            return check_mirror(current, timeout, mirrors, probe_hosts)
        except (requests.RequestException, ValueError) as e2:
            log.info("镜像也查不到：%s", e2)
            if strict:
                raise e from e2
            return None
    except (ValueError, KeyError, TypeError) as e:
        log.info("检查更新失败：%s", e)
        if strict:
            raise
        return None
    return {"version": version, "file": setup["name"], "sha256": sha, "size": setup.get("size"),
            "url": setup["browser_download_url"], "notes": (rel.get("body") or "").strip()[:600],
            "page": rel.get("html_url") or RELEASES_PAGE}


def download(info: dict, progress=None, folder: Path | None = None, slow_bps: float = SLOW_BPS,
             slow_after_s: float = SLOW_AFTER_S) -> Path:
    """下载安装包并核对 SHA-256；不一致就删掉并报错。progress(已下载字节, 总字节)。
    下了 slow_after_s 秒平均速度还低于 slow_bps 就停下，抛 SlowDownload（国内连 GitHub 常见）。"""
    import time
    folder = Path(folder or Path(tempfile.gettempdir()) / "aramhex-update")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / info["file"]
    h = hashlib.sha256()
    done = 0
    start = time.monotonic()
    with requests.get(info["url"], stream=True, timeout=(15, 30), headers=cloud.headers(info["url"])) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
                spent = time.monotonic() - start
                if slow_bps and spent >= slow_after_s and done / spent < slow_bps and done < total:
                    f.close()
                    dest.unlink(missing_ok=True)
                    raise SlowDownload(f"下载太慢（{done / spent / 1024:.0f}KB/s）")
    if h.hexdigest() != info["sha256"]:
        dest.unlink(missing_ok=True)
        raise ValueError("安装包校验失败（下载不完整或文件被改过）")
    return dest


def launch_installer(path: Path) -> None:
    """启动安装程序（显示安装进度，装完自动重新打开助手）。调用后助手应当马上退出，让出文件。"""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(path), "/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART"], close_fds=True,
                     creationflags=flags, cwd=os.path.dirname(str(path)))
