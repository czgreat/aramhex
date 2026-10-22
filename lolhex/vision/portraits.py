"""选英雄界面的英雄头像识别：把截图里的头像小块和官方方形头像库比对。

只读屏幕像素。头像库来自腾讯公开 CDN（game.gtimg.cn/.../champion/{alias}.png），
随程序内置一份（data/hero_icons.json，scripts/update_hero_icons.py 生成），首次运行不用等下载；
本地缓存里有的优先用缓存，内置里没有的新英雄照常下载到缓存。再加上随程序内置的拳头客户端头像（data/client_icons.json，
scripts/update_client_icons.py 生成）：一些英雄拳头换了新头像、腾讯 CDN 还是旧的（萨科、阿狸等），
预选栏显示的是新的，只比腾讯的认不出。每个英雄取两张里更像的那张。

比对方法：取头像中间的圆形区域（避开边框、圆角和选中高亮），缩到 20×20，
头像库按几种放大倍数各算一份（客户端头像裁得更紧），
各通道减均值后做归一化相关；对截图小块再试几种轻微的平移和缩放，容忍位置误差。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

SIDE = 20
# 截图小块的平移（占边长比例）与缩放，覆盖定位误差
SHIFTS = (-0.06, 0.0, 0.06)
SCALES = (0.9, 1.0, 1.1)
# 客户端头像比官方方形头像放得更大（约取中间 66%），头像库每个英雄准备几种放大倍数
ZOOMS = (0.84, 0.74, 0.66)
# 实机截图（取各倍数最好的一个）：真头像 0.79–0.98、领先第二名 0.15 以上；
# 空位、刷新动画中的暗头像最高约 0.68，几乎不领先
MIN_SCORE = 0.75   # 低于它就当作不是头像（空位、问号、锁定图标、别的界面图片）
MIN_MARGIN = 0.10  # 第一名要比第二名高出这么多才算确定
# 空位几乎是一块颜色：中间部分灰度标准差实测 1–7；真头像两套里最平的（乌迪尔）也有 19。
# 空位碰巧和某个头像有 0.76 的相关，加了第二套头像后机会更多，所以先看是不是空的
MIN_TEXTURE = 10.0


def _mask() -> np.ndarray:
    yy, xx = np.mgrid[0:SIDE, 0:SIDE]
    c = (SIDE - 1) / 2
    return ((xx - c) ** 2 + (yy - c) ** 2) <= (SIDE * 0.42) ** 2


MASK = _mask()


def _feature(img: np.ndarray, inner: float = 0.84) -> np.ndarray | None:
    """方形头像 → 归一化特征向量。inner：取中间多大比例（去掉外圈边框）。"""
    if img is None or img.size == 0 or min(img.shape[:2]) < 8:
        return None
    h, w = img.shape[:2]
    s = min(h, w) * inner
    x0, y0 = int(round((w - s) / 2)), int(round((h - s) / 2))
    crop = img[y0:y0 + int(s), x0:x0 + int(s)]
    small = cv2.resize(crop, (SIDE, SIDE), interpolation=cv2.INTER_AREA).astype(np.float32)
    v = small[MASK]  # (n, 3)
    v = v - v.mean(axis=0, keepdims=True)
    v = v.ravel()
    n = float(np.linalg.norm(v))
    if n < 1e-3:
        return None
    return v / n


def _variants(img: np.ndarray) -> list[np.ndarray]:
    h, w = img.shape[:2]
    out = []
    for sc in SCALES:
        s = min(h, w) * 0.84 / sc
        for dy in SHIFTS:
            for dx in SHIFTS:
                cx, cy = w / 2 + dx * w, h / 2 + dy * h
                x0, y0 = int(round(cx - s / 2)), int(round(cy - s / 2))
                x1, y1 = x0 + int(round(s)), y0 + int(round(s))
                if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
                    continue
                f = _feature(img[y0:y1, x0:x1], inner=1.0)
                if f is not None:
                    out.append(f)
    return out


def _flat(crop: np.ndarray) -> bool:
    if crop.size == 0:
        return True
    h, w = crop.shape[:2]
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)[int(h * 0.15):int(h * 0.85), int(w * 0.15):int(w * 0.85)]
    return g.size == 0 or float(g.std()) < MIN_TEXTURE


@dataclass
class Match:
    hero_id: str | None
    score: float
    second: float

    @property
    def sure(self) -> bool:
        return self.hero_id is not None


class IconBank:
    def __init__(self, icons: dict[str, np.ndarray | list[np.ndarray]]):
        """icons: {英雄 id: 头像} 或 {英雄 id: [几张头像]}（腾讯的、拳头客户端的）。"""
        ids, feats, owner = [], [], []
        for hid, imgs in icons.items():
            got = False
            for img in ([imgs] if isinstance(imgs, np.ndarray) else imgs):
                fs = [_feature(img, z) for z in ZOOMS]
                if all(f is not None for f in fs):
                    feats.extend(fs)
                    owner.extend([len(ids)] * len(fs))
                    got = True
            if got:
                ids.append(hid)
        self.ids = ids
        # 每张头像 len(ZOOMS) 行；owner 记每行属于第几个英雄
        self.owner = np.array(owner, np.intp)
        self.mat = np.stack(feats) if feats else np.zeros((0, int(MASK.sum()) * 3), np.float32)

    def __len__(self) -> int:
        return len(self.ids)

    def classify(self, crop: np.ndarray) -> Match:
        """一个头像小块（BGR，大致方形、头像居中）→ 英雄 id。"""
        if not self.ids or _flat(crop):
            return Match(None, 0.0, 0.0)
        vs = _variants(crop)
        if not vs:
            return Match(None, 0.0, 0.0)
        rows = (np.stack(vs) @ self.mat.T).max(axis=0)  # 每行取最好的截图变体
        sims = np.full(len(self.ids), -1.0)
        np.maximum.at(sims, self.owner, rows)  # 每个英雄取最像的那张头像、最好的放大倍数
        order = np.argsort(-sims)
        best, second = float(sims[order[0]]), float(sims[order[1]]) if len(order) > 1 else 0.0
        hid = self.ids[order[0]] if best >= MIN_SCORE and best - second >= MIN_MARGIN else None
        return Match(hid, best, second)

    @classmethod
    def from_dir(cls, folder: Path, heroes: dict[str, dict], extra: dict[str, np.ndarray] | None = None) -> "IconBank":
        """folder 里是腾讯头像缓存（{alias}.png），缺的用内置的；extra 是拳头客户端头像（client_icons()），只取 heroes 里有的英雄。"""
        icons: dict[str, list[np.ndarray]] = {}
        bundled = hero_icons()
        for hid, h in heroes.items():
            alias = h.get("alias")
            p = folder / f"{alias}.png"
            img = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR) if p.is_file() else None
            if img is None and alias in bundled:  # 缓存没有（第一次运行、下载失败）：用内置的
                img = cv2.imdecode(np.frombuffer(bundled[alias], np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                icons.setdefault(hid, []).append(img)
            if extra and hid in extra:
                icons.setdefault(hid, []).append(extra[hid])
        return cls(icons)


CLIENT_ICONS = Path(__file__).resolve().parent.parent / "data" / "client_icons.json"
HERO_ICONS = Path(__file__).resolve().parent.parent / "data" / "hero_icons.json"
_hero_icons: dict[str, bytes] | None = None


def hero_icons(path: Path = HERO_ICONS) -> dict[str, bytes]:
    """内置的腾讯头像 {alias: PNG 原始字节}；文件没有或坏了就返回空（全靠下载）。读一次就记住。"""
    global _hero_icons
    if _hero_icons is not None and path == HERO_ICONS:
        return _hero_icons
    import base64
    import json
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        out = {alias: base64.b64decode(b) for alias, b in d["icons"].items()}
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("内置英雄头像读不到：%s", e)
        out = {}
    if path == HERO_ICONS:
        _hero_icons = out
    return out


def client_icons(path: Path = CLIENT_ICONS) -> dict[str, np.ndarray]:
    """内置的拳头客户端头像 {英雄 id: BGR 图}；文件没有或坏了就返回空（只用腾讯的）。"""
    import base64
    import json
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        out = {}
        for hid, b in d["icons"].items():
            img = cv2.imdecode(np.frombuffer(base64.b64decode(b), np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                out[hid] = img
        return out
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("内置客户端头像读不到：%s", e)
        return {}


def ensure_icons(folder: Path, heroes: dict[str, dict]) -> int:
    """下载缺少的英雄头像（内置里已有的不下），返回新下载的数量。失败的下次再试。"""
    from ..data import http
    folder.mkdir(parents=True, exist_ok=True)
    bundled = hero_icons()
    n = 0
    for h in heroes.values():
        alias, url = h.get("alias"), h.get("icon")
        if not alias or not url or alias in bundled:
            continue
        p = folder / f"{alias}.png"
        if p.exists() and p.stat().st_size > 0:
            continue
        try:
            p.write_bytes(http.get(url, timeout=15, retries=1).content)
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("头像下载失败 %s：%s", alias, e)
    return n
