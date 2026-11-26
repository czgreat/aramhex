"""官方安装包的可选模块。

公开源码不带官网服务器地址，也不带请求官网服务的代码；这些在官方安装包的 lolhex/_official 里。
没有这个模块（从源码运行、自己打包）时：识别、浮窗、推荐照常，统计数据从 GitHub 数据仓库更新，
没有查分、云控、日志上传和自动更新。

模块要提供（API = 2）：
    sites() -> 官网地址列表
    urls(kind, sites, version) -> 公开更新的地址；kind：latest / update。这两种不要凭证，老版本、装坏的也能更新
    resource(kind, name, sites, *, method, body, byte_range, etag, timeout) -> (状态码, 内容, 响应头)
        kind：config / data / official / logs / rank。连接、凭证、换线路都在模块里，timeout 是整次调用的总秒数。
        本机没准备好（文件被改过、没装完整）抛 requests.RequestException
    rank(riot_id, sites, timeout) -> (状态码, 内容)
"""

from __future__ import annotations

import logging
import re
import sys

import requests

log = logging.getLogger(__name__)
API = 2
SCHEME = "official://"       # 只有官方模块能取的地址：official://data/latest.json
CHUNK = 1024 * 1024          # 大文件分段取：网速慢时一段一段来，不会整份超时
CHUNK_TIMEOUT = 60
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


def _load():
    # 没有官方打包标记就不加载私有模块，即使它被复制到源码或普通包里。
    # 标记只防止误加载，不是官网凭证。
    if not expected():
        return None
    try:
        from . import _official as module
    except ImportError as e:
        log.error("官方模块加载失败：%s", e)
        return None
    if getattr(module, "API", None) != API:
        log.error("官方模块版本不对：%s", getattr(module, "API", None))
        return None
    return module


def expected() -> bool:
    """是不是官方安装包（打包时写进 _build.py）：是的话模块必须在，不在就是没装完整。"""
    if not getattr(sys, "frozen", False):
        return False
    try:
        from ._build import OFFICIAL
    except ImportError:
        return False
    return bool(OFFICIAL)


_impl = _load()


def available() -> bool:
    return _impl is not None


def broken() -> bool:
    """官方安装包里模块却用不了（被杀毒软件隔离、更新没装完整）。"""
    return _impl is None and expected()


def updatable() -> bool:
    """官方安装包才自动更新；模块坏了也照常更新，好更新回来。"""
    return available() or expected()


def sites() -> list[str]:
    return list(_impl.sites()) if _impl else []


def urls(kind: str, site_list: list[str], version: str = "") -> list[str]:
    """公开更新的地址（latest / update）。"""
    return list(_impl.urls(kind, site_list, version)) if _impl else []


def sources(kind: str) -> list[str]:
    """要经官方模块取的资料（data / official）；没有模块就没有这条线路。"""
    return [SCHEME + kind] if _impl else []


def is_resource(url: str) -> bool:
    return url.startswith(SCHEME)


def split(url: str) -> tuple[str, str]:
    """official://data/a/b.json -> ("data", "a/b.json")"""
    kind, _, name = url[len(SCHEME):].partition("/")
    return kind, name


def resource(kind: str, name: str, site_list: list[str], *, method: str = "GET", body: bytes = b"",
             byte_range: str | None = None, timeout: float = 30) -> requests.Response:
    """请求官网的一项资源，包成 requests.Response，调用方和普通下载一样处理。
    失败只影响这一条线路：一律是 requests.RequestException 或非 200 状态。"""
    if _impl is None:
        raise requests.ConnectionError("没有官方模块")
    try:
        status, data, headers = _impl.resource(kind, name, list(site_list), method=method, body=body,
                                               byte_range=byte_range, timeout=timeout)
    except requests.RequestException:
        raise
    except (ValueError, TypeError) as e:  # 资源名不合规（云控、清单写错）：当这条线路没有
        raise requests.exceptions.InvalidURL(f"资源名不对：{kind}/{name}") from e
    r = requests.Response()
    r.status_code = status
    r._content, r._content_consumed = bytes(data), True
    r.headers.update(headers)
    r.url = f"{SCHEME}{kind}/{name}"
    r.encoding = "utf-8"
    return r


def download(kind: str, name: str, site_list: list[str], on_bytes=None, maximum: int | None = None) -> bytes:
    """分段取一个文件。文件中途换了或长度对不上就报错；内容对不对由调用方按清单核对。"""
    out, total = bytearray(), None
    while total is None or len(out) < total:
        first, last = len(out), len(out) + CHUNK - 1
        r = resource(kind, name, site_list, byte_range=f"bytes={first}-{last}", timeout=CHUNK_TIMEOUT)
        r.raise_for_status()
        m = _CONTENT_RANGE.fullmatch(r.headers.get("Content-Range", ""))
        start, end, size = map(int, m.groups()) if m else (0, 0, 0)
        if r.status_code != 206 or not first == start <= end <= last or end >= size \
                or end - start + 1 != len(r.content) or total not in (None, size):
            raise requests.exceptions.ContentDecodingError(f"分段对不上：{kind}/{name}")
        total = size
        if maximum is not None and total > maximum:
            raise requests.exceptions.ContentDecodingError(f"文件超过大小上限：{kind}/{name}")
        out += r.content
        if on_bytes:
            on_bytes(len(r.content))
    return bytes(out)


def rank(riot_id: str, site_list: list[str], timeout: float):
    if _impl is None:
        raise requests.ConnectionError("没有官方模块")
    return _impl.rank(riot_id, site_list, timeout)
