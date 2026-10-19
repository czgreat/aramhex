"""韩服识别数据（选装）：韩文的文字识别模型，安装包里不带，设置里选了韩文再下载。

内置的识别模型认得简繁中文、日文、英文，唯独不认韩文；韩文模型 13.5MB，只有韩服玩家用得上，
所以不放进安装包，用到时从下面的地址下载，按 SHA-256 校验后存到程序数据目录的 models 下。
模型来自 RapidOCR（PaddleOCR 的 ONNX 版，Apache-2.0），和内置模型同一来源。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import requests

from . import __version__
from .config import app_home

log = logging.getLogger(__name__)

KOREAN = {
    "file": "korean_PP-OCRv5_rec_mobile.onnx",
    "size": 13488748,
    "sha256": "cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b",
    "urls": ["https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/rec/"
             "korean_PP-OCRv5_rec_mobile.onnx"],
}


def model_path(pack: dict = KOREAN) -> Path:
    return app_home() / "models" / pack["file"]


def installed(pack: dict = KOREAN) -> Path | None:
    """下好了返回路径。只比大小（每次启动都算 SHA-256 太慢）；下载时已经校验过。"""
    p = model_path(pack)
    return p if p.is_file() and p.stat().st_size == pack["size"] else None


def download(pack: dict = KOREAN, progress=None, timeout: float = 30) -> Path:
    """下载并校验，成功返回路径；都失败抛最后一个错误。progress(已下载字节, 总字节)。"""
    dest = model_path(pack)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for url in pack["urls"]:
        try:
            h, done = hashlib.sha256(), 0
            with requests.get(url, stream=True, timeout=timeout,
                              headers={"User-Agent": f"aramhex/{__version__}"}) as r:
                r.raise_for_status()
                with part.open("wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, pack["size"])
            if done != pack["size"] or h.hexdigest() != pack["sha256"]:
                raise ValueError(f"下载的文件校验不对（{done} 字节）")
            part.replace(dest)
            log.info("韩服识别数据下载完成：%s", dest)
            return dest
        except (requests.RequestException, OSError, ValueError) as e:
            log.warning("从 %s 下载韩服识别数据失败：%s", url, e)
            last = e
            part.unlink(missing_ok=True)
    raise last or RuntimeError("没有下载地址")
