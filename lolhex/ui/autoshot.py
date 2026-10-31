"""自动截图：海克斯三选一和选英雄时，自动保存一张整屏截图（含助手画的浮窗和标注）。

1.1.1 起浮窗本来就能被截到；识别用的截图会把浮窗涂掉，这里存的是不涂的原样（看浮窗摆得对不对）。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from threading import Thread

from PySide6.QtCore import QTimer

from ..config import app_home

log = logging.getLogger(__name__)
KEEP = 300                # 最多留这么多张，多了删最早的
SETTLE_MS = {"三选一": 900, "选英雄": 300}  # 推荐和标注画出来以后再截
CS_STABLE_S = 4.0         # 选英雄：标出来的英雄数这么久没再增加才截（队友陆续选好、识别完）


def shots_dir() -> Path:
    d = app_home() / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


class AutoShot:
    def __init__(self, engine):
        self.engine = engine
        self._offer_key = None   # 已经截过的三选一（三张海克斯）
        self._cs_shot = 0        # 这次选英雄截图时标了几个英雄
        self._cs_seen = (0, 0.0)  # (标出来的英雄数, 这个数第一次出现的时间)
        self._busy = False

    def tick(self, snap: dict, game_rect, client_rect) -> None:
        """浮窗每次刷新时调用：出现新的三选一推荐、或进入选英雄界面，就安排截一张。"""
        if not getattr(self.engine.settings, "debug_mode", False):  # 只在调试模式（debug.txt）下截
            return
        ev, offer = snap.get("evaluation"), snap.get("offer")
        if game_rect and ev and offer:
            key = tuple(offer.get("slots") or [])
            if key != self._offer_key and any(key) and not any(offer.get("pending_columns") or ()):
                if self._schedule(game_rect, "三选一"):
                    self._offer_key = key
        elif not offer:
            self._offer_key = None
        cs = snap.get("champ_select")
        if client_rect and cs:
            n = sum(1 for k in ("team", "bench", "cards") for s in cs.get(k) or [] if s.get("grade"))
            now = time.monotonic()
            if n != self._cs_seen[0]:
                self._cs_seen = (n, now)
            elif n > self._cs_shot and now - self._cs_seen[1] >= CS_STABLE_S:
                if self._schedule(client_rect, "选英雄"):
                    self._cs_shot = n  # 后面又多标出英雄时再截一张
        elif not cs:
            self._cs_shot, self._cs_seen = 0, (0, 0.0)

    def _schedule(self, rect, kind: str) -> bool:
        if self._busy:
            return False
        self._busy = True
        QTimer.singleShot(SETTLE_MS[kind], lambda: self._open(rect, kind))
        return True

    def _open(self, rect, kind: str) -> None:
        # mss 使用线程本地实例；截屏、PNG 编码和写盘都不占 Qt 主线程。
        try:
            Thread(target=self._grab, args=(rect, kind), name="debug-screenshot", daemon=True).start()
        except Exception:
            self._busy = False
            log.exception("自动截图启动失败")

    def _grab(self, rect, kind: str) -> None:
        try:
            import cv2

            from ..vision import capture
            img = capture.grab(rect, mask=False)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]  # 采集完成时刻，PNG 编码之前
            if not capture.is_blank(img):
                ok, buf = cv2.imencode(".png", img)
                if ok:
                    path = shots_dir() / f"{stamp}-{kind}.png"
                    n = 1
                    while path.exists():  # 同一毫秒里截了两张（测试里会遇到）：加序号，不覆盖
                        n += 1
                        path = shots_dir() / f"{stamp}-{n}-{kind}.png"
                    path.write_bytes(buf.tobytes())
                    log.info("自动截图：%s", path.name)
                    self._prune()
        except Exception:
            log.exception("自动截图失败")
        finally:
            try:
                from ..vision import capture
                capture.release()  # 每张截图都是新线程：用完就关，不留 GDI 位图
            except Exception:
                log.exception("释放截屏资源失败")
            self._busy = False

    @staticmethod
    def _prune() -> None:
        files = sorted(shots_dir().glob("*.png"))
        for old in files[:-KEEP]:
            old.unlink(missing_ok=True)
