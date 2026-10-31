"""游戏机端核心调度：英雄识别、海克斯识别与推荐。**不接入 LCU**。

线程：
- data：启动时及定期刷新数据（腾讯官方资料；自采统计数据有新版本时下载）；
- observer：只看窗口是否存在——客户端在前台且游戏没开时，截图识别选英雄界面：
  头像比对认出预选栏和我方的英雄（标胜率），OCR 认出自己的英雄名；
- vision：游戏窗口在前台时截屏识别海克斯三选一。
浮窗只读 snapshot()，并通过 set_hero / rescan / reset_calibration 操作。
"""

from __future__ import annotations

import copy
import json
from contextlib import nullcontext, contextmanager
import logging
import random
import threading
import time
from pathlib import Path

from . import cloud, i18n, recommend
from .config import Settings, app_home
from .data.public import PublicStore
from .data.store import DataStore
from .panel import hero_panel
from .vision import window as win
from .vision.matcher import NameMatcher, normalize

log = logging.getLogger(__name__)


_AUG_ALIASES: dict[str, list[str]] | None = None


def augment_aliases() -> dict[str, list[str]]:
    """{海克斯 ID: [OCR 常读错的写法]}，随安装包带（lolhex/data/augment_aliases.json）。"""
    global _AUG_ALIASES
    if _AUG_ALIASES is None:
        try:
            d = json.loads(Path(__file__).with_name("data").joinpath("augment_aliases.json").read_text(encoding="utf-8"))
            _AUG_ALIASES = {str(k): [str(x) for x in v] for k, v in d["aliases"].items() if isinstance(v, list)}
        except (OSError, ValueError, KeyError, AttributeError):
            _AUG_ALIASES = {}
    return {**_AUG_ALIASES, **cloud.get()["augments"]["aliases"]}


def augment_matcher(store, settings) -> NameMatcher:
    """三选一认字用的名字表：中文用腾讯的名字，再加繁中（台服等）和马来西亚服简中的名字；外服用那种语言的名字（ID 一样）。
    外服名字表里没有的（刚上线、名字表还没更新），退回中文名。"""
    names: dict[str, str | list[str]] = {aid: a["name"] for aid, a in store.mayhem_augments().items()}
    foreign = i18n.augment_names(settings.game_lang)
    if foreign:
        names = {aid: foreign.get(aid) or n for aid, n in names.items()}
        names.update({aid: n for aid, n in foreign.items() if aid not in names})
    else:
        extra = i18n.chinese_extra_augment_names()
        names = {aid: [n] + [x for x in extra.get(aid, []) if x != n] if extra.get(aid) else n
                 for aid, n in names.items()}
    # OCR 常读错的写法（augment_aliases.json）：同一个 ID 多一个名字，和繁中名一样
    for aid, extra in augment_aliases().items():
        if aid in names:
            n = names[aid]
            names[aid] = (n if isinstance(n, list) else [n]) + extra
    return NameMatcher(names, settings.match_threshold, min_margin=6.0)


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


def _unread(offer: dict, i: int) -> str:
    """日志里没认出的卡：带上 OCR 读到的原文（截短），方便查是哪个海克斯名对不上。"""
    texts = offer.get("texts") or []
    text = " ".join(str(texts[i]).split())[:16] if i < len(texts) and texts[i] else ""
    return f"未识别「{text}」" if text else "未识别"


class Engine:
    def __init__(self, settings: Settings, home: Path | None = None, store: DataStore | None = None):
        self.settings = settings
        self.home = home or app_home()
        if store is not None:
            self.store = store
        else:
            self.store = PublicStore(self.home / "data", settings.refresh_hours,
                                     check_updates=settings.check_data_updates, managed_official=True)
        if hasattr(self.store, "set_band"):
            self.store.set_band(settings.data_band or None)
        self._lock = threading.RLock()
        if getattr(self.store, "official", None) is not None:
            @contextmanager
            def activation_guard():
                # 与阶段切换共用锁；不能在检查 hold 后、写入完成前进入游戏。
                with self._lock, self.store._lock:
                    yield
            self.store.official.activation_guard = activation_guard
            self.store.activation_guard = activation_guard
        self._stop = threading.Event()
        self._data_wake = threading.Event()  # 对局结束时叫醒数据线程：对局中推迟的数据更新马上补上
        if hasattr(self.store, "hold"):
            self.store.hold = lambda: (self.state["phase"] == "ingame"
                                       or bool(self.state.get("champ_select")))
        self._rescan = threading.Event()
        self._detector = None  # 延迟创建，避免没进游戏就加载 OCR 模型
        self._icon_bank = None  # 英雄头像库，头像下载好后创建
        from .data.icons import IconCache
        self.icons = IconCache(self.home / "data" / "img", allow_network=False)
        self._cs_memo = None    # 选人界面各位置的识别结果缓存（champselect.Memo）
        self._manual_hero = False
        self._cs_last_seen = 0.0  # 上次在选英雄界面里的时刻（time.monotonic）
        self._cs_gone_since = None  # 扫描确认离开选英雄界面的时刻；客户端不在前台（没扫描）不算离开
        self._last_eval_log = None  # 同一个三选一只记一次评估结果
        self._evaluation_revision = 0
        self._panel_cache: tuple[str | None, dict | None] = (None, None)
        self.state: dict = {
            "phase": None,  # None / client / ingame
            "hero_id": None, "hero_source": None, "hero_candidates": [],
            "game_window": False, "client_window": False,
            "vision": {"status": "idle", "calibrated": False, "last_scan": None, "blank": False},
            "offer": None, "evaluation": None,
            "champ_select": None,  # {"bench": [...], "team": [...]}，每项带胜率和排名
        }

    # ---------- 生命周期 ----------

    def start(self) -> None:
        for name, fn in (("data", self._data_loop), ("observer", self._observer_loop),
                         ("vision", self._vision_loop)):
            threading.Thread(target=fn, name=name, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._data_wake.set()

    def cloud_changed(self) -> None:
        """收到云控后让后台重算资料到期时间，不能继续等待旧的长间隔。"""
        self._data_wake.set()

    def snapshot(self) -> dict:
        with self._lock:
            s = copy.deepcopy(self.state)
        s["data"] = self.store.status()
        s["settings"] = {"show_winrate": self.settings.show_winrate, "badge_position": self.settings.badge_position}
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

    def set_band(self, band: str | None, by_user: bool = True) -> None:
        """切换参考分数段（None/"" = 全体），记住选择，并按新数据重算面板和三选一推荐。
        by_user=False：云控改的，不算玩家自己选的。"""
        if by_user:
            self.settings.set_by_user("data_band", band or "")  # 云控正在强制时只记下玩家要的值
        else:
            self.settings.data_band = band or ""
        band = self.settings.data_band or None
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
        with self._lock:
            self._scan_epoch = getattr(self, "_scan_epoch", 0) + 1
        if self._detector:
            self._detector._last_fp = None
        self._rescan.set()

    def set_game_lang(self, lang: str) -> None:
        """设置里换了游戏语言：换认字用的名字表，选人界面的缓存作废。"""
        self.settings.game_lang = i18n.normalize_lang(lang)
        self._hm = None
        self._cs_memo = None
        if self._detector:
            self._detector.set_matcher(self._augment_matcher())
        self.rescan()
        log.info("游戏语言：%s", self.settings.game_lang)

    def reset_calibration(self) -> None:
        with self._lock:
            self._scan_epoch = getattr(self, "_scan_epoch", 0) + 1
        if self._detector:
            self._detector.reset_calibration()
        self.state["vision"]["calibrated"] = False
        self._rescan.set()

    def hero_panel(self, hero_key) -> dict | None:
        # Qt 定时刷新调用这里：资料缺失/过期也只读缓存和内置数据。
        # 玩家模式只用已验证快照；资料联网更新统一由数据线程完成。
        with getattr(self.store, "_lock", nullcontext()):
            hid = self.store.resolve_hero(hero_key)
            official = getattr(self.store, "official", None)
            snapshot = official.snapshot if official is not None else None
            rarity_rules = cloud.get()["augments"]["rarity_fix"]
            if (self._panel_cache[0] == hid and self._panel_cache[1]
                    and getattr(self, "_panel_snapshot", None) is snapshot
                    and getattr(self, "_panel_rarity_rules", None) == rarity_rules):
                return self._panel_cache[1]
            p = hero_panel(self.store, hid, self.settings.smoothing_games,
                           allow_network=False) if hid else None
            self._panel_cache = (hid, p)
            self._panel_snapshot = snapshot
            self._panel_rarity_rules = rarity_rules
        return p

    # ---------- 数据 ----------

    def _data_loop(self) -> None:
        self._warm_up()
        while not self._stop.is_set():
            self._data_wake.clear()
            try:
                self.store.refresh()
                self._load_icons()
                self._panel_cache = (None, None)
                self._reevaluate()
            except Exception:
                log.exception("数据刷新异常")
            try:
                every = cloud.data_minutes() * 60 * random.uniform(0.9, 1.1)  # 每台电脑错开，免得新数据一发大家同时下载
            except Exception:  # 云控出什么怪值都不能让数据线程退出
                log.exception("读云控的数据检查间隔失败，按 6 小时")
                every = 6 * 3600.0
            if hasattr(self.store, "sync_every_s"):
                self.store.sync_every_s = every
            wait_s = max(600.0, min(self.settings.refresh_hours * 3600 / 4, every / 2))
            official = getattr(self.store, "official", None)
            if official is not None:
                due = official.seconds_until_check()
                if due is not None:
                    wait_s = max(1.0, min(wait_s, due))
            self._data_wake.wait(wait_s)

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
        return augment_matcher(self.store, self.settings)

    def _sync_augment_tables(self, det) -> None:
        """云控与官方资料变化在识别线程下一轮生效，避免在一次 OCR 中途混用名字表。"""
        augments = self.store.augments
        revision = (augments, cloud.get()["augments"], self.settings.game_lang, self.settings.match_threshold)
        previous = getattr(self, "_augment_tables_revision", None)
        if previous is None or previous[0] is not augments or previous[1:] != revision[1:]:
            det.set_matcher(self._augment_matcher())
            det.rarities = {aid: row.get("rarity") for aid, row in augments.items()}
            self._augment_tables_revision = revision
            self._panel_cache = (None, None)
            self._reevaluate()

    def _hero_matcher(self) -> NameMatcher:
        heroes = self.store.heroes
        cached = getattr(self, "_hm", None)
        if cached and cached[0] is heroes:
            return cached[1]
        m = self._build_hero_matcher()
        self._hm = (heroes, m)
        return m

    def _build_hero_matcher(self) -> NameMatcher:
        names: dict[str, str | list[str]] = {}
        foreign = i18n.hero_names(self.settings.game_lang)
        tw = {} if foreign else i18n.chinese_extra_hero_names()  # 繁中（台服等）+ 马来西亚服简中
        for hid, h in self.store.heroes.items():
            if foreign:  # 外服：只认那种语言的名字和称号（选人界面写的是名字，开局卡片可能是称号）
                names[hid] = foreign.get(hid) or []
                continue
            names[hid] = h.get("name") or ""
            names[hid + "#nick"] = h.get("nick") or ""
            names[hid + "#full"] = f"{h.get('nick', '')} {h.get('name', '')}"
            names[hid + "#tw"] = tw.get(hid) or []  # 繁中（台服等）、马来西亚服简中
        return NameMatcher(names, 86.0)

    def _load_icons(self, download: bool = True) -> None:
        from .vision.champselect import Memo
        from .vision.portraits import IconBank, client_icons, ensure_icons
        folder = self.home / "data" / "icons"
        heroes = self.store.heroes
        if not heroes:
            return
        managed = getattr(self.store, "official", None) is not None
        new = ensure_icons(folder, heroes) if download and not managed else 0
        if new or self._icon_bank is None:
            bank = IconBank.from_dir(folder, heroes, client_icons())
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
        self._reevaluate()

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
        self._apply_champ_select(cs, img)

    CS_NEW_AFTER = 8.0   # 扫描确认离开选英雄界面超过这么多秒再进来，算新的一把（中途偶尔一次没认出界面不算）
    CS_NEW_GAP = 90.0    # 这么久完全没看到选英雄界面（切到别的窗口、排队）再看到，也算新的一把：大乱斗选英雄不会这么长

    def _apply_champ_select(self, cs, img=None) -> None:
        now = time.monotonic()
        if cs is False:
            if self._cs_gone_since is None and self._cs_last_seen:
                self._cs_gone_since = now
            # 同一画面也可能是选人结束、游戏窗口还没出现；仅凭离开选人不能判定秒退。
            # 先保留身份，下一次进入选人时再判新局；实际对局结束仍由 _on_game_end 清理。
        else:
            left = self._cs_gone_since is not None and now - self._cs_gone_since > self.CS_NEW_AFTER
            gap = self._cs_last_seen and now - self._cs_last_seen > self.CS_NEW_GAP
            if (left or gap) and (self.state["hero_id"] or self._manual_hero):
                # 新的一把：清掉上一把的英雄。只在对局结束时清的话，选英雄阶段有人秒退（没进游戏），
                # 下一把在认出新英雄之前一直挂着上一把的（GitHub 玩家反馈，10-21）。
                # 选英雄时切到浏览器、主窗口去手动选英雄，客户端不在前台不扫描，不能当成离开（复核 10-22）
                log.info("选英雄：新的一把，清掉上一把的英雄")
                self._clear_selection()
            self._cs_last_seen, self._cs_gone_since = now, None
        self._set(champ_select=cs or None)
        if cs is False:  # 不在选英雄界面
            return
        if self._manual_hero:
            return
        if cs and cs.get("mine"):
            self._set_hero_auto(cs["mine"], "ocr")
            self._set(hero_candidates=[])
        elif not cs and self._icon_bank is None and img is not None:
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
        lang = self.settings.game_lang
        self._cs_tick = getattr(self, "_cs_tick", 0) + 1
        in_cs = self._try_texts(i18n.ui_texts("bench", lang),
                                lambda t: champselect.is_champ_select(img, ocr.recognize, memo, t))
        # 开局选卡时顶部预选栏被盖住、没有"可用"两个字，所以卡片按"选择你的英雄"单独判断，
        # 否则要等点完卡、回到正常选人界面才有标注
        cards = self._try_texts(i18n.ui_texts("pick", lang), lambda t: champselect.read_cards(
            img, ocr.recognize, ocr.detect, self._hero_by_name, origin, memo, t))
        self._drop_gone_cards(cards)
        got = None
        if not in_cs and not cards and lang != "zh_CN" and self._icon_bank is not None:
            # 外服的字放在哪还没有实机截图核对过：读不到时看预选栏，认出 3 个以上英雄头像也算在选英雄界面
            got = champselect.read(img, self._icon_bank, origin, self._read_hero_name, memo)
            in_cs = len(got["bench"]) >= 3
        if not in_cs and not cards:
            return False  # 大厅、房间、商城等界面：不标注、不识别
        cs = None
        if in_cs and self._icon_bank is not None:
            got = got or champselect.read(img, self._icon_bank, origin, self._read_hero_name, memo)
            if got["bench"] or got["team"]:
                cs = {k: [{**s, **self.hero_brief(s["hero_id"])} for s in got[k]] for k in ("bench", "team")}
                cs["mine"] = got["mine"]
        if cards:
            cs = cs or {"bench": [], "team": [], "mine": None}
            cs["cards"] = [{**c, **self.hero_brief(c["hero_id"])} for c in cards]
        return cs

    def _drop_gone_cards(self, cards) -> None:
        """选完英雄卡、卡片没了：先把卡下面的胜率撤掉。后面还要认预选栏和我方 5 个英雄，
        画面刚变缓存全失效，要 1–2 秒；以前等这一轮认完才更新，剩下那张卡的胜率就多挂这么久。
        选英雄状态本身留着（只去掉 cards），主浮窗不会因此收起。"""
        if cards or not cloud.feature("cards_drop_early"):
            return
        with self._lock:
            cs = self.state.get("champ_select")
            if cs and cs.get("cards"):
                self.state["champ_select"] = {k: v for k, v in cs.items() if k != "cards"}

    def _try_texts(self, texts: list[str], fn):
        """界面上的字有几种写法时（中文：简体、繁中）挨个试。上次认到的那种先试，简体每次都试；
        别的写法每 4 次才试一次，国服玩家在大厅里几乎不多花时间。"""
        pref = getattr(self, "_cs_pref", {})
        self._cs_pref = pref
        first = pref.get(texts[0], texts[0])
        got = None
        for i, t in enumerate([first] + [t for t in texts if t != first]):
            if i and t != texts[0] and self._cs_tick % 4:
                continue
            if got := fn(t):
                pref[texts[0]] = t
                return got
        return got

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
            if len(normalize(ln.text)) < 2:  # 整屏什么字都有：单独一个字（聊天、名字里的）不当英雄名，「慎」「劫」靠头像认
                continue
            key, sc = m.match(ln.text)
            if key:
                found.append((key.split("#")[0], sc, ln.h))
        mine, cands = pick_my_hero(found)
        if mine:
            self._set_hero_auto(mine, "ocr")
            self._set(hero_candidates=[c for c in cands if c != mine])
        elif cands:
            self._set(hero_candidates=cands)

    def _clear_selection(self) -> None:
        with self._lock:
            self._scan_epoch = getattr(self, "_scan_epoch", 0) + 1
            self._manual_hero = False
            self._last_eval_log = None
            self._evaluation_revision += 1
            self.state.update(hero_id=None, hero_source=None, hero_candidates=[],
                              offer=None, evaluation=None)

    def _on_game_end(self) -> None:
        self._clear_selection()
        if self._detector is not None:
            self._detector.clear_tracking()
        self._cs_memo = None  # 选人缓存按截图尺寸分开存；改过窗口大小的旧尺寸不留到下一局
        log.info("对局结束")
        from .diagnostics import resource_summary
        log.info("运行资源：%s", resource_summary())
        self._data_wake.set()

    # ---------- 海克斯识别 ----------

    def _strip_step(self, visible: bool, since_hit: float) -> float:
        """已校准时多久查一次标题条（秒）：没在选海克斯时按 idle_scan_ms（默认和快扫一样，云控可调大），
        三选一开着、关掉后几秒内按 scan_interval_ms 快扫。对局里画面一直在动，标题条几乎每次都要认字
        （10-24 日志一局约 2500 次、每次约 35 毫秒 CPU）。"""
        ms = self.settings.scan_interval_ms
        if not visible and since_hit > getattr(self.settings, "active_hold_s", Settings.active_hold_s):
            ms = max(ms, self.settings.idle_scan_ms)
        return ms / 1000 * self.settings.speed_factor

    def _fallback_every(self, confirmed: bool, proven: bool = False) -> float:
        """已校准时整块兜底扫描的间隔（秒）：还在用默认位置照常按 bootstrap 间隔扫；
        本机校准过按 fallback_scan_s（默认 30）；这局标题条已认出过三选一按 fallback_proven_s（默认 3600）。"""
        if not confirmed:
            return self.settings.bootstrap_interval_s * self.settings.speed_factor
        if proven:
            return float(max(self.settings.fallback_scan_s, self.settings.fallback_proven_s))
        return float(self.settings.fallback_scan_s)

    def _vision_loop(self) -> None:
        from .vision import capture
        from .vision.detector import AugmentDetector

        win.lower_thread_priority()  # 认字和游戏抢 CPU 时让游戏先
        try:
            import cv2
            cv2.setNumThreads(1)  # OpenCV 缩图也默认开满核的线程池
        except Exception:
            pass
        last_full = last_strip = 0.0
        last_hit = float("-inf")  # 上次认出三选一的时间：之后 active_hold_s 秒内照常快扫
        visible = False
        misses = 0  # 刷新动画会短暂缺卡，连续 3 次识别不到才算关闭
        proven = None  # 这局标题条认出过三选一时的窗口尺寸：位置对了，兜底整块放慢
        while not self._stop.is_set():
            if self.state["phase"] != "ingame":
                proven = None
                if visible:
                    self._on_offer(None)
                    visible = False
                if self._detector is not None:
                    self._log_vision_cost(self._detector)
                capture.release()  # 不在局内就关掉截屏实例：它占着上次截图大小的位图（4K 约 33MB），挂托盘时不该一直留着
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
                self._detector = AugmentDetector(self._augment_matcher(), self.home / "calibration.json",
                    rarities={aid: a.get("rarity") for aid, a in self.store.augments.items()})
            det = self._detector
            det.color_check = getattr(self.settings, "color_check", "log")
            det.recheck_seconds = getattr(self.settings, "recheck_seconds", Settings.recheck_seconds)
            det.pending_seconds = getattr(self.settings, "pending_seconds", Settings.pending_seconds)
            det.low_match_reads = getattr(self.settings, "low_match_reads", Settings.low_match_reads)
            det.group_confirm = getattr(self.settings, "group_confirm", Settings.group_confirm)
            self._sync_augment_tables(det)
            det.ensure_calibration(rect.width, rect.height)
            if proven is not None and proven != (rect.width, rect.height):
                proven = None  # 换过窗口尺寸：换回来也要等标题条重新认出才放慢
            now = time.monotonic()
            forced = self._rescan.is_set()
            self._rescan.clear()
            offer, ran = None, False
            self._scan_discarded = False
            try:
                if det.calibrated_for(rect.width, rect.height) and not forced:
                    if now - last_strip >= self._strip_step(visible, now - last_hit):
                        last_strip = now
                        offer = self._observed_scan(det, rect, full=False, reason="titles", hwnd=hwnd)
                        ran = True
                        if offer is not None and not self._scan_discarded:
                            proven = (rect.width, rect.height)
                        # 一直识别不到：整块扫描一次，防止卡片位置变了
                        every = self._fallback_every(det.confirmed_for(rect.width, rect.height),
                                                     proven is not None)
                        if offer is None and not self._scan_discarded and not visible and now - last_full > every:
                            last_full = now
                            offer = self._observed_scan(det, rect, full=True, reason="fallback", hwnd=hwnd)
                elif forced or now - last_full >= self.settings.bootstrap_interval_s * self.settings.speed_factor:
                    last_full = now
                    offer = self._observed_scan(det, rect, full=True,
                        reason="manual" if forced else "bootstrap", check_blank=True, hwnd=hwnd)
                    ran = True
            except Exception:
                log.exception("识别异常")
            self.state["vision"].update(status="scanning", calibrated=det.confirmed_for(rect.width, rect.height))
            if ran and getattr(self, "_scan_discarded", False):
                # 不把“旧画面被丢弃”累计成三次读空，也不再用旧客户区触发整块兜底。
                visible, misses, proven = False, 0, None
                last_full = last_strip = 0.0
                self._stop.wait(0.1)
                continue
            if ran:
                self.state["vision"]["last_scan"] = time.time()
                try:  # 评估出错（数据有怪值等）只记日志，识别线程照常跑，不能整局停掉
                    if offer is not None:
                        misses = 0
                        last_hit = now
                        if not visible:
                            self._save_offer_shot(rect)  # 趁浮窗还没画上去
                        visible = True
                        self._on_offer(offer)
                    elif visible:
                        misses += 1
                        if misses >= 3:
                            visible = False
                            self._on_offer(None)
                except Exception:
                    log.exception("三选一评估异常")
            self._stop.wait(0.1)

    def _scan_invalid_reason(self, context) -> str | None:
        """Win32 几何读取很轻；不用新截图/OCR 判断一张在途画面是否仍属于当前窗口。"""
        epoch, hwnd, rect = context
        if self.state.get("phase") != "ingame" or epoch != getattr(self, "_scan_epoch", 0):
            return "session"
        if win.find_game_window() != hwnd:
            return "window"
        current = win.client_rect(hwnd)
        # 同一窗口仅切后台/最小化时保留有效校准；浮层已有独立的前后台隐藏逻辑。
        if current is None and not win.is_foreground(hwnd):
            return None
        return "geometry" if current != rect else None

    def _discard_scan(self, det, context, reason: str) -> None:
        self._scan_discarded = True
        # 在途全扫描可能已写入定位；只失效内存，不删磁盘上的。磁盘上那份只在画面仍有效时才写，
        # 是本机确认过的位置：换回它，和刚启动时一样。以前置空后下一局先用默认位置，默认位置对不上的
        # 电脑（改过游戏界面缩放等）会整局每 3 秒整块扫描一次，直到再认出一组三选一。云控 calib_restore 可关
        det.clear_tracking()
        det.calibration = None
        restore = getattr(det, "restore_calibration", None)
        restored = bool(restore and getattr(getattr(self, "settings", None), "calib_restore", True) and restore())
        with self._lock:
            current = self.state.get("offer")
            if context[0] == getattr(self, "_scan_epoch", 0) or (current or {}).get("_scan_context") == context:
                self.state.update(offer=None, evaluation=None)
        now = time.monotonic()
        if now - getattr(self, "_scan_discard_logged_at", float("-inf")) >= 60:
            self._scan_discard_logged_at = now
            log.info("丢弃过时识别画面：reason=%s，%s", reason, "换回本机保存的位置" if restored else "下一轮重新定位")

    def _observed_scan(self, det, rect, *, full: bool, reason: str, check_blank: bool = False, hwnd: int = 0):
        """记录现有扫描耗时，并拒绝期间已换会话/窗口/客户区的结果。"""
        from dataclasses import replace
        from .vision import capture
        self._scan_sequence = getattr(self, "_scan_sequence", 0) + 1
        sequence = self._scan_sequence
        started = time.perf_counter()
        captured = None
        finished = None
        offer = None
        outcome = "error"
        self._scan_discarded = False
        context = (getattr(self, "_scan_epoch", 0), hwnd, rect) if hwnd else None
        try:
            if context and (invalid := self._scan_invalid_reason(context)):
                self._discard_scan(det, context, invalid)
                outcome = "stale"
                return None
            frame = capture.grab(rect if full else det.strip_rect(rect))
            captured = time.perf_counter()
            blank = check_blank and capture.is_blank(frame)
            if check_blank:
                self.state["vision"]["blank"] = blank
            if blank:
                outcome = "blank"
            else:
                defer = getattr(det, "defer_calibration_writes", None)
                with defer() if defer and context else nullcontext(None) as commit:
                    offer = det.scan_full(frame, (rect.left, rect.top)) if full else det.check_strip(frame, rect)
                    if context and (invalid := self._scan_invalid_reason(context)):
                        self._discard_scan(det, context, invalid)
                        outcome = "stale"
                        return None
                    if commit is not None:
                        commit()
                outcome = "matched" if offer is not None else "unmatched"
            finished = time.perf_counter()
            if blank and context and (invalid := self._scan_invalid_reason(context)):
                self._discard_scan(det, context, invalid)
                outcome = "stale"
                return None
            if offer is not None:
                offer = replace(offer, trace={"scan": sequence, "path": "full" if full else "strip",
                    "reason": reason, "started": started, "captured": captured, "finished": finished,
                    "capture_ms": (captured-started)*1000, "work_ms": (finished-captured)*1000,
                    "context": context})
            elif not blank:
                # 低命中批次仍可能已经读到一列新名字；关闭宽限不能继续画该列旧分数。
                pending = getattr(det, "pending_columns", (False, False, False))
                if any(pending):
                    self._mask_offer_columns(pending, context)
            return offer
        finally:
            if finished is None:
                finished = time.perf_counter()
            capture_ms = ((captured if captured is not None else finished)-started)*1000
            work_ms = (finished-captured)*1000 if captured is not None else 0.0
            path = "full" if full else "strip"
            logged = getattr(self, "_slow_scan_logged", {})
            if finished-started >= 1 and finished-logged.get(path, float("-inf")) >= 60:
                logged[path] = finished
                self._slow_scan_logged = logged
                log.info("扫描诊断：scan=%d path=%s reason=%s window=%dx%d confirmed=%s capture=%.0fms work=%.0fms total=%.0fms outcome=%s stage=%s matches=%d",
                         sequence, path, reason, rect.width, rect.height, det.confirmed_for(rect.width, rect.height),
                         capture_ms, work_ms, (finished-started)*1000,
                         outcome, "work" if captured is not None else "capture", offer.matched() if offer is not None else 0)

    @staticmethod
    def _log_vision_cost(det) -> None:
        c = det.cost
        if c["strip"] or c["full"]:
            def avg(k, n):
                return c[k] * 1000 / max(1, c[n])
            log.info("这局认字：标题条 %d 次（平均 %.0f 毫秒，CPU %.0f），整块扫描 %d 次（平均 %.0f 毫秒，CPU %.0f），标题OCR %d 次",
                     c["strip"], avg("strip_s", "strip"), avg("strip_cpu", "strip"),
                     c["full"], avg("full_s", "full"), avg("full_cpu", "full"), c.get("strip_ocr", 0))
            log.info("这局识别诊断：全域文本行=%d 全域名字匹配=%d 条带缓存=%d 标题名字拒绝=%d 颜色冲突拒绝=%d 颜色冲突按名字认=%d 待确认观察=%d",
                     c.get("full_lines", 0), c.get("full_matches", 0), c.get("strip_cache", 0),
                     c.get("name_reject", 0), c.get("color_reject", 0), c.get("color_trust", 0), c.get("pending", 0))
            log.info("这局标题优先：检测框=%d 实际认字=%d 提前完成=%d 重复名字拒绝=%d 同名多处=%d",
                     c.get("full_detected", 0), c.get("full_recognized", 0), c.get("full_early", 0),
                     c.get("full_duplicate_reject", 0), c.get("full_repeat_name", 0))
        det.cost = dict.fromkeys(c, 0)

    SHOTS_KEEP = 3

    def _save_offer_shot(self, rect) -> None:
        """前几次出现三选一时存一张全屏截图（缩到 1920 宽），用来核对浮窗位置；存够就不再截。"""
        folder = self.home / "shots"
        try:
            if folder.is_dir() and len(list(folder.glob("offer_*.jpg"))) >= self.SHOTS_KEEP:
                return
            from .vision import capture
            frame = capture.grab(rect, mask=False)  # 核对浮窗位置用：要原样
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

    def _mask_offer_columns(self, pending, context=None) -> None:
        with self._lock:
            current = self.state.get("offer")
            if (not current or self.state.get("phase") != "ingame"
                    or (context and context[0] != getattr(self, "_scan_epoch", 0))):
                return
            before = current.get("pending_columns", (False, False, False))
            masked = tuple(a or b for a, b in zip(before, pending))
            if tuple(before) != masked:
                self._evaluation_revision += 1  # 在途重算不能把旧可见状态带回来。
                current["pending_columns"] = masked

    def _on_offer(self, offer) -> None:
        context = (offer.trace or {}).get("context") if offer is not None else None
        if context and (invalid := self._scan_invalid_reason(context)):
            self._discard_scan(self._detector, context, invalid)
            return
        with self._lock:
            prev = self.state.get("offer")
            if offer is None:
                self._evaluation_revision += 1
                self.state.update(offer=None, evaluation=None)
                return
            if self.state["phase"] != "ingame" or (context and context[0] != getattr(self, "_scan_epoch", 0)):
                return  # 这帧识别完成时已经退出对局，不再发布旧卡片。
            if getattr(offer, "group_changed", False):
                self._evaluation_revision += 1
                self.state.update(offer=None, evaluation=None)
                return
            slots = list(offer.slots)
            if prev and prev.get("corrected"):
                slots = [s or p for s, p in zip(slots, prev["slots"])]
            if prev and prev["slots"] == slots:
                if tuple(prev.get("pending_columns", (False, False, False))) != offer.pending_columns:
                    self._evaluation_revision += 1
                # 卡片内容不变也可能移动或切换分辨率：保留评分，更新绘制坐标。
                prev.update(texts=offer.texts, scores=offer.scores, boxes=offer.boxes, at=offer.at,
                            padded=offer.padded, source=offer.source, observation=offer.observation,
                            pending_columns=offer.pending_columns, _scan_context=context)
                return
            if prev:
                # 新分数尚未算好也先隐藏已换卡列，另外两列继续画原来的完整结果。
                self._mask_offer_columns([p or old != new for p, old, new in
                                          zip(offer.pending_columns, prev["slots"], slots)], context)
            # 每列从什么时候开始没认出（浮窗前几秒写「识别中」，卡刚翻出来字没读全时不闪「未知」）
            now = time.monotonic()
            before = (prev or {}).get("unread_since") or [None] * len(slots)
            unread_since = [None if s else (before[i] if i < len(before) and before[i] is not None
                                            and prev and not prev["slots"][i] else now)
                            for i, s in enumerate(slots)]
            next_offer = {"slots": slots, "texts": offer.texts, "scores": offer.scores,
                          "boxes": offer.boxes, "at": offer.at, "padded": offer.padded,
                          "source": offer.source, "observation": offer.observation,
                          "unread_since": unread_since, "pending_columns": offer.pending_columns,
                          "_scan_context": context}
        # 计算期间保留上一对完整结果；新卡片和新评分一起发布，避免定时刷新的界面读到空评分而整组隐藏。
        evaluation = self._reevaluate(next_offer)
        trace = offer.trace
        if evaluation is not None and trace is not None:
            published = time.perf_counter()
            log.info("推荐状态发布：scan=%d path=%s reason=%s source=%s observation=%d capture=%.0fms work=%.0fms after=%.0fms total=%.0fms frame_age=%.0fms",
                     trace["scan"], trace["path"], trace["reason"], offer.source, offer.observation,
                     trace["capture_ms"], trace["work_ms"], (published-trace["finished"])*1000,
                     (published-trace["started"])*1000, (published-trace["captured"])*1000)

    def _reevaluate(self, next_offer: dict | None = None) -> dict | None:
        with self._lock:
            self._evaluation_revision += 1
            revision = self._evaluation_revision
            previous = self.state.get("offer")
            offer = next_offer if next_offer is not None else previous
            hid = self.state.get("hero_id")
            slots = list(offer["slots"]) if offer else None
        if not offer:
            return None
        with getattr(self.store, "_lock", nullcontext()):
            ev = recommend.evaluate(self.store, hid, slots, self.settings.smoothing_games)
        context = offer.get("_scan_context")
        if context and (invalid := self._scan_invalid_reason(context)):
            self._discard_scan(self._detector, context, invalid)
            return None
        with self._lock:
            # 后台重算期间可能已结束对局、换英雄或刷新卡片，不能覆盖新状态。
            if (revision != self._evaluation_revision
                    or self.state.get("offer") is not previous or self.state.get("hero_id") != hid
                    or (next_offer is not None and self.state["phase"] != "ingame")
                    or (context and context[0] != getattr(self, "_scan_epoch", 0))
                    or offer["slots"] != slots):
                return None
            self.state.update(offer=offer, evaluation=ev)
        key = (hid, tuple(slots))
        if key != self._last_eval_log:
            self._last_eval_log = key
            log.info("三选一（%s）：%s｜%s", (self.store.heroes.get(str(hid)) or {}).get("name") or "未识别英雄",
                     "；".join(f"{s.get('name') or _unread(offer, i)} {s['verdict']}"
                              + (f" {s['score']:+.1f}" if s.get("score") is not None else "")
                              + (f"（{s['basis']}）" if s.get("basis") else "") for i, s in enumerate(ev["slots"])),
                     ev["advice"])
        return ev
