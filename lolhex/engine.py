"""游戏机端核心调度：英雄识别、海克斯识别与推荐。**不接入 LCU**。

线程：
- data：启动时及定期刷新数据（腾讯官方资料；自采统计数据有新版本时下载）；
- observer：只看窗口是否存在——客户端在前台且游戏没开时，截图识别选英雄界面：
  头像比对认出预选栏和我方的英雄（标胜率），OCR 认出自己的英雄名；
  可选读取游戏内 2999 接口（默认关闭）；
- vision：游戏窗口在前台时截屏识别海克斯三选一。
浮窗只读 snapshot()，并通过 set_hero / rescan / reset_calibration 操作。
例外：测试版调试采集（debugcap.py，config.json 的 debug_capture）会只读客户端的 gameflow 会话存档。
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from pathlib import Path

from . import recommend
from .config import Settings, app_home
from .data.public import PublicStore
from .data.store import DataStore
from .panel import hero_panel
from .vision import window as win
from .vision.matcher import NameMatcher

log = logging.getLogger(__name__)


def pick_my_hero(found: list[tuple[str, float, int]]) -> tuple[str | None, list[str]]:
    """选英雄界面 OCR 到的英雄名 → (本人英雄, 候选)。
    found: [(heroId, 匹配分, 文字高度)]。界面上自己的英雄名字号最大；
    最大字号明显大于其他（≥1.3 倍）才自动认定，否则只给候选让玩家点选。"""
    best: dict[str, tuple[float, int]] = {}
    for hid, sc, h in found:
        if hid not in best or h > best[hid][1]:
            best[hid] = (sc, h)
    order = sorted(best, key=lambda k: (-best[k][1], -best[k][0]))
    if not order:
        return None, []
    if len(order) == 1 or best[order[0]][1] >= 1.3 * best[order[1]][1]:
        return order[0], order[:6]
    return None, order[:6]


# 国服胜率排名 → 评级：前 10% S，30% A，60% B，85% C，其余 D
GRADES = ((0.10, "S"), (0.30, "A"), (0.60, "B"), (0.85, "C"), (1.01, "D"))


def rank_grade(rank, total) -> str | None:
    if not rank or not total:
        return None
    q = rank / total
    return next(g for lim, g in GRADES if q <= lim)


class Engine:
    def __init__(self, settings: Settings, home: Path | None = None, store: DataStore | None = None):
        self.settings = settings
        self.home = home or app_home()
        if store is not None:
            self.store = store
        else:
            self.store = PublicStore(self.home / "data", settings.refresh_hours,
                                     check_updates=settings.check_data_updates)
        if hasattr(self.store, "set_band"):
            self.store.set_band(settings.data_band or None)
        self.live = None
        if settings.use_liveclient:
            from .game.liveclient import LiveClient
            self.live = LiveClient()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._data_wake = threading.Event()  # 对局结束时叫醒数据线程：对局中推迟的数据更新马上补上
        if hasattr(self.store, "hold"):
            self.store.hold = lambda: self.state["phase"] == "ingame"
        self._rescan = threading.Event()
        self._detector = None  # 延迟创建，避免没进游戏就加载 OCR 模型
        self._icon_bank = None  # 英雄头像库，头像下载好后创建
        from .data.icons import IconCache
        self.icons = IconCache(self.home / "data" / "img")  # 装备、召唤师技能小图标
        self._cs_memo = None    # 选人界面各位置的识别结果缓存（champselect.Memo）
        self.vision_hold_until = 0.0  # time.monotonic() 在这之前暂停识别（自动截图用）
        self._manual_hero = False
        self._live_logged = False  # 这局「2999 读不到」只记一次
        self._last_eval_log = None  # 同一个三选一只记一次评估结果
        self._panel_cache: tuple[str | None, dict | None] = (None, None)
        self.debug = None
        if settings.debug_capture:
            from .debugcap import DebugRecorder
            self.debug = DebugRecorder(self)  # 测试版调试采集：多存截图和原始数据（见 debugcap.py）
        self.state: dict = {
            "phase": None,  # None / client / ingame
            "hero_id": None, "hero_source": None, "hero_candidates": [],
            "level": None, "game_window": False, "client_window": False,
            "vision": {"status": "idle", "calibrated": False, "last_scan": None, "blank": False},
            "offer": None, "evaluation": None,
            "enemies": [],  # 对面英雄 ID（2999 接口读到时才有）
            "champ_select": None,  # {"bench": [...], "team": [...]}，每项带胜率和排名
        }

    # ---------- 生命周期 ----------

    def start(self) -> None:
        for name, fn in (("data", self._data_loop), ("observer", self._observer_loop),
                         ("vision", self._vision_loop)):
            threading.Thread(target=fn, name=name, daemon=True).start()
        if self.debug:
            self.debug.start()

    def stop(self) -> None:
        self._stop.set()
        self._data_wake.set()

    def snapshot(self) -> dict:
        with self._lock:
            s = copy.deepcopy(self.state)
        s["data"] = self.store.status()
        s["settings"] = {"show_winrate": self.settings.show_winrate}
        s["hero"] = self.store.heroes.get(s["hero_id"] or "")
        s["candidates"] = [self.store.heroes.get(h, {"id": h}) for h in s["hero_candidates"]]
        return s

    def _set(self, **kw) -> None:
        with self._lock:
            self.state.update(kw)

    # ---------- 玩家操作 ----------

    def set_hero(self, hero_key) -> None:
        hid = self.store.resolve_hero(hero_key) if hero_key else None
        self._manual_hero = hid is not None
        self._set(hero_id=hid, hero_source="manual" if hid else None, hero_candidates=[])
        if hid:
            threading.Thread(target=self._prefetch_hero, args=(hid,), daemon=True).start()
        self._reevaluate()

    def set_band(self, band: str | None) -> None:
        """切换参考分数段（None/"" = 全体），记住选择，并按新数据重算面板和三选一推荐。"""
        self.settings.data_band = band or ""
        self.settings.save()
        if hasattr(self.store, "set_band"):
            self.store.set_band(band or None)
        self._panel_cache = (None, None)
        hid = self.state.get("hero_id")
        if hid:
            threading.Thread(target=self._prefetch_hero, args=(hid,), daemon=True).start()
        self._reevaluate()

    def correct_slot(self, index: int, augment_id: str | None) -> None:
        with self._lock:
            offer = self.state.get("offer")
            if not offer or not 0 <= index < 3:
                return
            offer["slots"][index] = augment_id
            offer["corrected"] = True
        self._reevaluate()

    def rescan(self) -> None:
        if self._detector:
            self._detector._last_fp = None
        self._rescan.set()

    def reset_calibration(self) -> None:
        if self._detector:
            self._detector.reset_calibration()
        self.state["vision"]["calibrated"] = False
        self._rescan.set()

    def hero_panel(self, hero_key) -> dict | None:
        hid = self.store.resolve_hero(hero_key)
        if self._panel_cache[0] == hid and self._panel_cache[1]:
            return self._panel_cache[1]
        p = hero_panel(self.store, hid, self.settings.smoothing_games) if hid else None
        self._panel_cache = (hid, p)
        return p

    # ---------- 数据 ----------

    def _data_loop(self) -> None:
        self._warm_up()
        while not self._stop.is_set():
            try:
                self.store.refresh()
                self._load_icons()
                if self._detector:
                    self._detector.set_matcher(self._augment_matcher())
                self._panel_cache = (None, None)
                self._reevaluate()
            except Exception:
                log.exception("数据刷新异常")
            self._data_wake.wait(max(600.0, self.settings.refresh_hours * 3600 / 4))
            self._data_wake.clear()

    def _warm_up(self) -> None:
        """启动时先用本地缓存加载头像库、预热 OCR，第一次进选人界面不用等网络和模型加载。"""
        try:
            self._load_icons(download=False)
            import numpy as np
            from .vision.ocr import OCR
            import cv2
            sample = np.full((64, 256, 3), 255, np.uint8)
            cv2.putText(sample, "LolHex 123", (8, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
            OCR.get().recognize(sample)  # 首次推理最慢，识别和检测都先各跑一次
            OCR.get().detect(sample)
        except Exception:
            log.exception("预热失败")

    def _augment_matcher(self) -> NameMatcher:
        names = {aid: a["name"] for aid, a in self.store.mayhem_augments().items()}
        return NameMatcher(names, self.settings.match_threshold)

    def _hero_matcher(self) -> NameMatcher:
        heroes = self.store.heroes
        cached = getattr(self, "_hm", None)
        if cached and cached[0] is heroes:
            return cached[1]
        m = self._build_hero_matcher()
        self._hm = (heroes, m)
        return m

    def _build_hero_matcher(self) -> NameMatcher:
        names = {}
        for hid, h in self.store.heroes.items():
            names[hid] = h.get("name") or ""
            names[hid + "#nick"] = h.get("nick") or ""
            names[hid + "#full"] = f"{h.get('nick', '')} {h.get('name', '')}"
        return NameMatcher(names, 86.0)

    def _load_icons(self, download: bool = True) -> None:
        from .vision.champselect import Memo
        from .vision.portraits import IconBank, ensure_icons
        folder = self.home / "data" / "icons"
        heroes = self.store.heroes
        if not heroes or (not download and not folder.is_dir()):
            return
        new = ensure_icons(folder, heroes) if download else 0
        if new or self._icon_bank is None:
            bank = IconBank.from_dir(folder, heroes)
            if len(bank):
                self._icon_bank = bank
                self._cs_memo = Memo()  # 头像库变了，缓存的识别结果作废
                log.info("英雄头像库：%d 个", len(bank))

    def hero_brief(self, hid: str) -> dict:
        """选人界面标注用：国服胜率、按胜率的排名，以及由排名换算的评级。
        腾讯英雄榜自带的排名掺了出场率，这里只按胜率排。"""
        ranks = self.store.ranks.get("heroes") or {}
        cached = getattr(self, "_win_rank", None)
        if not cached or cached[0] is not ranks:
            order = sorted((k for k, v in ranks.items() if v.get("win") is not None),
                           key=lambda k: -ranks[k]["win"])
            cached = (ranks, {k: i + 1 for i, k in enumerate(order)})
            self._win_rank = cached
        pos = cached[1]
        h = self.store.heroes.get(hid) or {}
        rank, total = pos.get(hid), len(pos) or None
        return {"hero_id": hid, "name": h.get("name"), "nick": h.get("nick"),
                "win": (ranks.get(hid) or {}).get("win"), "rank": rank, "total": total,
                "grade": rank_grade(rank, total)}

    def _prefetch_hero(self, hid: str) -> None:
        self.store.hero(hid)
        self._panel_cache = (None, None)

    # ---------- 窗口状态与英雄识别 ----------

    def _observer_loop(self) -> None:
        while not self._stop.is_set():
            interval = 3.0
            try:
                interval = self._observe_once()
            except Exception:
                log.exception("状态读取异常")
            self._stop.wait(interval)

    def _observe_once(self) -> float:
        game = win.find_game_window()
        client = win.find_client_window()
        prev = self.state["phase"]
        phase = "ingame" if game else ("client" if client else None)
        self._set(phase=phase, game_window=bool(game), client_window=bool(client))

        if prev == "ingame" and phase != "ingame":
            self._on_game_end()
        if phase != "client" and self.state["champ_select"]:
            self._set(champ_select=None)

        if phase == "ingame":
            snap = self.live.snapshot() if self.live else None
            if self.live and not snap and not self._live_logged:
                self._live_logged = True
                log.info("2999 接口暂时读不到（刚进游戏加载时正常）")
            if snap and snap.get("enemies") and not self.state["enemies"]:
                ids = [self.store.resolve_hero(e) for e in snap["enemies"]]
                self._set(enemies=[i for i in ids if i])
                log.info("2999 读到对面阵容：%s（认出 %d/%d）", "、".join(
                    (self.store.heroes.get(i) or {}).get("name") or str(i) for i in ids if i),
                    len([i for i in ids if i]), len(ids))
            if snap and not self._manual_hero:
                self._set(level=snap.get("level"))
                if not self.state["hero_id"]:
                    hid = self.store.resolve_hero(snap.get("champion_raw")) or \
                        self.store.resolve_hero(snap.get("champion_name"))
                    if hid:
                        self._set_hero_auto(hid, "liveclient")
            return 2.0
        if phase == "client" and win.is_foreground(client):
            self._champ_select_scan(client)
            # 画面没变的位置直接复用上次结果，所以可以扫得很勤
            return (0.5 if self.state["champ_select"] else 1.0) * self.settings.speed_factor
        if phase == "client":
            return 1.0  # 客户端切到后台：只查窗口不截图，切回来 1 秒内恢复
        return 3.0

    def _set_hero_auto(self, hid: str, source: str) -> None:
        if self.state["hero_id"] != hid:
            self._set(hero_id=hid, hero_source=source)
            threading.Thread(target=self._prefetch_hero, args=(hid,), daemon=True).start()
            self._reevaluate()

    def _champ_select_scan(self, hwnd: int) -> None:
        """截取客户端窗口：头像比对认出预选栏/我方英雄，OCR 认出自己的英雄名。
        只读屏幕像素，不读客户端进程。"""
        if time.monotonic() < self.vision_hold_until:  # 自动截图中：标注层临时可截，先不识别
            return
        rect = win.client_rect(hwnd)
        if not rect:
            return
        from .vision import capture
        img = capture.grab(rect)
        if capture.is_blank(img):
            return
        t0 = time.monotonic()
        cs = self.analyze_champ_select(img, (rect.left, rect.top))
        dt = time.monotonic() - t0
        if dt > 0.8:
            log.info("选英雄：这次识别用了 %.1f 秒", dt)
        self._set(champ_select=cs or None)
        if cs is False:  # 不在选英雄界面
            return
        if self._manual_hero:
            return
        if cs and cs.get("mine"):
            self._set_hero_auto(cs["mine"], "ocr")
            self._set(hero_candidates=[])
        elif not cs and self._icon_bank is None:
            self._champ_select_ocr(img)  # 头像库还没下载好时，退回整屏文字识别

    def analyze_champ_select(self, img, origin: tuple[int, int] = (0, 0)):
        """客户端截图 → 标注数据 {"bench", "team", "mine", "cards"?}；
        不在选英雄界面返回 False，在界面但什么都没认出返回 None。"""
        from .vision import champselect
        from .vision.ocr import OCR
        ocr = OCR.get()
        if self._cs_memo is None:
            self._cs_memo = champselect.Memo()
        memo = self._cs_memo
        in_cs = champselect.is_champ_select(img, ocr.recognize, memo)
        # 开局选卡时顶部预选栏被盖住、没有"可用"两个字，所以卡片按"选择你的英雄"单独判断，
        # 否则要等点完卡、回到正常选人界面才有标注
        cards = champselect.read_cards(img, ocr.recognize, ocr.detect, self._hero_by_name, origin, memo)
        if not in_cs and not cards:
            return False  # 大厅、房间、商城等界面：不标注、不识别
        cs = None
        if in_cs and self._icon_bank is not None:
            got = champselect.read(img, self._icon_bank, origin, self._read_hero_name, memo)
            if got["bench"] or got["team"]:
                cs = {k: [{**s, **self.hero_brief(s["hero_id"])} for s in got[k]] for k in ("bench", "team")}
                cs["mine"] = got["mine"]
        if cards:
            cs = cs or {"bench": [], "team": [], "mine": None}
            cs["cards"] = [{**c, **self.hero_brief(c["hero_id"])} for c in cards]
        return cs

    def _read_hero_name(self, crop) -> str | None:
        from .vision.ocr import OCR
        txt, sc = OCR.get().recognize(crop)
        return self._hero_by_name(txt) if sc >= 0.6 else None

    def _hero_by_name(self, txt: str) -> str | None:
        if not txt:
            return None
        key, _ = self._hero_matcher().match(txt)
        return key.split("#")[0] if key else None

    def _champ_select_ocr(self, img) -> None:
        from .vision.detector import _resize
        from .vision.ocr import OCR
        scale = min(1.0, 1600 / max(1, img.shape[1]))
        m = self._hero_matcher()
        found = []
        for ln in OCR.get().detect(_resize(img, scale)):
            key, sc = m.match(ln.text)
            if key:
                found.append((key.split("#")[0], sc, ln.h))
        mine, cands = pick_my_hero(found)
        if mine:
            self._set_hero_auto(mine, "ocr")
            self._set(hero_candidates=[c for c in cands if c != mine])
        elif cands:
            self._set(hero_candidates=cands)

    def _on_game_end(self) -> None:
        self._manual_hero = False
        self._set(hero_id=None, hero_source=None, hero_candidates=[], level=None,
                  offer=None, evaluation=None, enemies=[])
        self._live_logged = False
        log.info("对局结束")
        self._data_wake.set()

    # ---------- 海克斯识别 ----------

    def _vision_loop(self) -> None:
        from .vision import capture
        from .vision.detector import AugmentDetector

        last_full = last_strip = 0.0
        visible = False
        misses = 0  # 刷新动画会短暂缺卡，连续 3 次识别不到才算关闭
        while not self._stop.is_set():
            if time.monotonic() < self.vision_hold_until:  # 自动截图时浮窗临时可截：这段时间不识别，免得读到自己画的字
                self._stop.wait(0.1)
                continue
            if self.state["phase"] != "ingame":
                if visible:
                    self._on_offer(None)
                    visible = False
                self.state["vision"]["status"] = "idle"
                self._rescan.wait(1.0)
                self._rescan.clear()
                continue
            hwnd = win.find_game_window()
            rect = win.client_rect(hwnd)
            if not rect or not win.is_foreground(hwnd):
                self.state["vision"]["status"] = "paused"
                self._stop.wait(0.5)
                continue
            if self._detector is None:
                self._detector = AugmentDetector(self._augment_matcher(), self.home / "calibration.json")
            det = self._detector
            det.ensure_calibration(rect.width, rect.height)
            now = time.monotonic()
            forced = self._rescan.is_set()
            self._rescan.clear()
            offer, ran = None, False
            try:
                if det.calibrated_for(rect.width, rect.height) and not forced:
                    if now - last_strip >= self.settings.scan_interval_ms / 1000 * self.settings.speed_factor:
                        last_strip = now
                        offer = det.check_strip(capture.grab(det.strip_rect(rect)), rect)
                        ran = True
                        # 一直识别不到：整块扫描一次，防止卡片位置变了；
                        # 还在用默认位置时照常按 bootstrap 间隔扫，本机校准过就 30 秒一次
                        confirmed = det.confirmed_for(rect.width, rect.height)
                        every = 30 if confirmed else self.settings.bootstrap_interval_s * self.settings.speed_factor
                        if offer is None and not visible and now - last_full > every:
                            last_full = now
                            offer = det.scan_full(capture.grab(rect), (rect.left, rect.top))
                elif forced or now - last_full >= self.settings.bootstrap_interval_s * self.settings.speed_factor:
                    last_full = now
                    frame = capture.grab(rect)
                    blank = capture.is_blank(frame)
                    self.state["vision"]["blank"] = blank
                    offer = None if blank else det.scan_full(frame, (rect.left, rect.top))
                    ran = True
            except Exception:
                log.exception("识别异常")
            self.state["vision"].update(status="scanning", calibrated=det.confirmed_for(rect.width, rect.height))
            if ran:
                self.state["vision"]["last_scan"] = time.time()
                if offer is not None:
                    misses = 0
                    if not visible:
                        self._save_offer_shot(rect)  # 趁浮窗还没画上去
                        if self.debug:
                            self.debug.offer(rect, offer)
                    self._on_offer(offer)
                    visible = True
                elif visible:
                    misses += 1
                    if misses >= 3:
                        self._on_offer(None)
                        visible = False
                        if self.debug:
                            self.debug.offer_closed()
            self._stop.wait(0.1)

    SHOTS_KEEP = 3

    def _save_offer_shot(self, rect) -> None:
        """前几次出现三选一时存一张全屏截图（缩到 1920 宽），用来核对浮窗位置；存够就不再截。"""
        folder = self.home / "shots"
        try:
            if folder.is_dir() and len(list(folder.glob("offer_*.jpg"))) >= self.SHOTS_KEEP:
                return
            from .vision import capture
            frame = capture.grab(rect)
        except Exception:
            log.exception("截图失败")
            return

        def save():
            try:
                import cv2
                from .vision.detector import _resize
                img = _resize(frame, min(1.0, 1920 / max(1, frame.shape[1])))
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
                if ok:
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / time.strftime("offer_%Y%m%d_%H%M%S.jpg")).write_bytes(buf.tobytes())
            except Exception:
                log.exception("截图保存失败")
        threading.Thread(target=save, daemon=True).start()

    def _on_offer(self, offer) -> None:
        with self._lock:
            prev = self.state.get("offer")
        if offer is None:
            self._set(offer=None, evaluation=None)
            return
        slots = list(offer.slots)
        if prev and prev.get("corrected"):
            slots = [s or p for s, p in zip(slots, prev["slots"])]
        if prev and prev["slots"] == slots:
            return
        self._set(offer={"slots": slots, "texts": offer.texts, "scores": offer.scores,
                         "boxes": offer.boxes, "at": offer.at})
        self._reevaluate()

    def _reevaluate(self) -> dict | None:
        with self._lock:
            offer = self.state.get("offer")
            hid = self.state.get("hero_id")
        if not offer:
            return None
        ev = recommend.evaluate(self.store, hid, offer["slots"], self.settings.smoothing_games)
        self._set(evaluation=ev)
        key = (hid, tuple(offer["slots"]))
        if key != self._last_eval_log:
            self._last_eval_log = key
            log.info("三选一（%s）：%s｜%s", (self.store.heroes.get(str(hid)) or {}).get("name") or "未识别英雄",
                     "；".join(f"{s.get('name') or '未识别'} {s['verdict']}"
                              + (f" {s['score']:+.1f}" if s.get("score") is not None else "")
                              + (f"（{s['basis']}）" if s.get("basis") else "") for s in ev["slots"]),
                     ev["advice"])
        return ev
