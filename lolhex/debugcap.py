"""测试版调试采集：给海斗 4.0 测试服用，多存截图和原始数据，以后做小地图认图、核对新的三选一界面。

开关：config.json 的 debug_capture（测试版默认开，界面上不显示）。
每局一个文件夹 <程序数据目录>/debug/<开局时间>/：
- env.json：显示器、游戏窗口（客户区 / 整窗）、DPI、游戏设置里的分辨率、窗口模式、HUD 和小地图缩放、小地图是否翻转；
- game-config/：游戏 Config 下的 PersistedSettings.json、game.cfg 原样复制；
- shots/：整窗 JPG。开局（2999 第一次读到等级起）90 秒内每 3 秒一张（开局选海克斯），之后每 15 秒一张；升级后 0.5、2、4 秒各一张；
  认出三选一时存一张无损 PNG 和识别结果（offer-*.json），选完后 0.3、1.5 秒各一张（看选完的特效）；
- minimap/：和每张整窗图同时存的左下角、右下角无损 PNG。小地图默认在右下角，设置里「翻转小地图」后在左下角，
  对局中也能改，所以两边都存；范围按最大的 HUD 和小地图缩放留够。env.json 里记着开局时的翻转设置；
- liveclient/：2999 接口的 allgamedata，读到的第一份和之后每 30 秒一份；
- lcu/：客户端 /lol-gameflow/v1/session（地图、模式、变体），选英雄时和开局各一份。
  只读本机客户端，只在调试采集开着时读，凭据只在内存里用、不写日志；
- 手动截图：Ctrl+Alt+S，整窗无损 PNG 存到 shots/（文件名带 manual）；不在对局里时截整个桌面，存到 debug/manual/。
debug 文件夹超过 5GB 时，从最早的对局删起。
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from .vision import window as win

log = logging.getLogger(__name__)

PERIOD_S = 15.0           # 对局中整窗截图间隔
OPENING_S = 90.0          # 开局这么久内截得勤一些
OPENING_PERIOD_S = 3.0
LEVEL_BURST = (0.5, 2.0, 4.0)
AFTER_OFFER = (0.3, 1.5)
LIVE_PERIOD_S = 30.0
MAX_BYTES = 5 * 1024 ** 3
JPEG_QUALITY = 90
MINIMAP_W, MINIMAP_H = 0.40, 0.55   # 小地图区域占窗口宽、高的比例（最大缩放也装得下）
HOTKEY_ID = 0xA11
SETTING_KEYS = ("Width", "Height", "WindowMode", "GlobalScale", "MinimapScale", "FlipMiniMap")


def game_settings(persisted: dict) -> dict:
    """PersistedSettings.json → 调试关心的几项 {名字: 值}。"""
    out = {}
    for f in persisted.get("files") or []:
        for sec in f.get("sections") or []:
            for item in sec.get("settings") or []:
                if item.get("name") in SETTING_KEYS:
                    out[item["name"]] = item.get("value")
    return out


def minimap_boxes(width: int, height: int, flipped) -> dict[str, tuple[int, int, int, int]]:
    """{名字: (x, y, w, h)}，窗口内坐标。flipped=None（不知道）时左右都给。"""
    w, h = int(width * MINIMAP_W), int(height * MINIMAP_H)
    right, left = (width - w, height - h, w, h), (0, height - h, w, h)
    if flipped is None:
        return {"right": right, "left": left}
    return {"left": left} if flipped else {"right": right}


def _flipped(settings: dict):
    v = str(settings.get("FlipMiniMap", "")).strip().lower()
    return {"1": True, "true": True, "0": False, "false": False}.get(v)


def prune(root: Path, max_bytes: int = MAX_BYTES, keep: Path | None = None) -> list[Path]:
    """root 下的对局文件夹总大小超过 max_bytes 时，从最早的删起（keep 不删）。返回删掉的。"""
    if not root.is_dir():
        return []
    dirs = sorted((d for d in root.iterdir() if d.is_dir() and d.name[:1].isdigit()), key=lambda d: d.name)
    sizes = {d: sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) for d in dirs}
    total = sum(sizes.values())
    removed = []
    for d in dirs:
        if total <= max_bytes:
            break
        if d == keep:
            continue
        shutil.rmtree(d, ignore_errors=True)
        total -= sizes[d]
        removed.append(d)
    return removed


def _stamp() -> str:
    return datetime.now().strftime("%H%M%S-%f")[:-3]


class Lcu:
    """本机客户端接口：lockfile 在 LeagueClientUx.exe 同目录。只读。"""

    def __init__(self):
        self._auth = None  # (端口, 口令)，只在内存

    def _lockfile(self) -> tuple[int, str] | None:
        exe = win.process_path(win.find_client_window())
        if not exe:
            return None
        try:
            parts = (Path(exe).parent / "lockfile").read_text(encoding="utf-8").split(":")
            return int(parts[2]), parts[3]
        except (OSError, IndexError, ValueError):
            return None

    def get(self, path: str):
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        for fresh in (False, True):
            if fresh or not self._auth:
                self._auth = self._lockfile()
            if not self._auth:
                return None
            port, pw = self._auth
            try:
                r = requests.get(f"https://127.0.0.1:{port}{path}", auth=("riot", pw), verify=False, timeout=3)
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in (401, 403):
                    return None
            except (requests.RequestException, ValueError):
                pass
        return None


class DebugRecorder:
    def __init__(self, engine, root: Path | None = None):
        self.engine = engine
        self.root = root or engine.home / "debug"
        self.session: Path | None = None
        self.lcu = Lcu()
        self._lock = threading.Lock()
        self._due: list[tuple[float, str]] = []   # (time.monotonic(), 文件名标签)
        self._started = 0.0
        self._next_shot = self._next_live = 0.0
        self._level = None
        self._flipped = None
        self._cs_lcu = False
        self._pending_lcu = None
        self._opening_until = 0.0
        self._offer_open = False

    # ---------- 引擎调用（任意线程） ----------

    def offer(self, rect, offer) -> None:
        """认出三选一（浮窗画上去之前）。"""
        self._offer_open = True
        if not self.session:
            return
        stamp = _stamp()
        self._save_frame(rect, f"offer-{stamp}", png=True)
        info = {"slots": list(offer.slots), "texts": list(offer.texts), "scores": list(offer.scores),
                "boxes": [list(b) if b else None for b in offer.boxes], "rect": [rect.left, rect.top, rect.width, rect.height]}
        self._write_json(self.session / "shots" / f"offer-{stamp}.json", info)

    def offer_closed(self) -> None:
        if self._offer_open:
            self._offer_open = False
            self._schedule(AFTER_OFFER, "picked")

    # ---------- 线程 ----------

    def start(self) -> None:
        threading.Thread(target=self._loop, name="debug", daemon=True).start()

    def _loop(self) -> None:
        hotkey = self._register_hotkey()
        while not self.engine._stop.is_set():
            try:
                if hotkey:
                    self._poll_hotkey()
                self._tick()
            except Exception:
                log.exception("调试采集出错")
            self.engine._stop.wait(0.1)

    def _tick(self) -> None:
        st = self.engine.state
        phase = st.get("phase")
        if phase == "client" and st.get("champ_select") and not self._cs_lcu:
            self._cs_lcu = True
            self._pending_lcu = self.lcu.get("/lol-gameflow/v1/session")
        if phase != "ingame":
            if self.session:
                log.info("调试采集：这局结束，存在 %s", self.session)
                self.session = None
            if phase != "client" or not st.get("champ_select"):
                self._cs_lcu = False
            return
        now = time.monotonic()
        if not self.session:
            self._open_session(now)
        level = st.get("level")
        if level and self._level and level > self._level:
            self._schedule(LEVEL_BURST, f"lv{level}")
        if level and not self._level:
            self._opening_until = now + OPENING_S  # 2999 读到等级：真正开局（加载界面不算）
        self._level = level or self._level
        with self._lock:
            due = [d for d in self._due if d[0] <= now]
            self._due = [d for d in self._due if d[0] > now]
        hwnd = win.find_game_window()
        rect = win.client_rect(hwnd)
        visible = rect is not None and win.is_foreground(hwnd)
        if visible:
            for _, tag in due:
                self._save_frame(rect, f"{tag}-{_stamp()}")
            if now >= self._next_shot:
                every = OPENING_PERIOD_S if now < self._opening_until else PERIOD_S
                self._next_shot = now + every
                self._save_frame(rect, f"t{int(now - self._started):04d}-{_stamp()}")
        if self.engine.live and now >= self._next_live:
            data = self.engine.live.all_game_data()
            if data:
                self._next_live = now + LIVE_PERIOD_S
                self._write_json(self.session / "liveclient" / f"{_stamp()}.json", data)
            else:
                self._next_live = now + 5.0

    def _open_session(self, now: float) -> None:
        self.session = self.root / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session.mkdir(parents=True, exist_ok=True)
        self._started = now
        self._next_shot = self._next_live = now
        self._level = None
        self._opening_until = now + 150  # 没开 2999 时按加载界面加开局算
        with self._lock:
            self._due = []
        cs, self._pending_lcu = self._pending_lcu, None
        if cs:
            self._write_json(self.session / "lcu" / "champ-select-session.json", cs)
        self._write_json(self.session / "lcu" / "ingame-session.json", self.lcu.get("/lol-gameflow/v1/session"))
        env = self._environment()
        self._write_json(self.session / "env.json", env)
        try:
            removed = prune(self.root, keep=self.session)
            if removed:
                log.info("调试采集：超过 5GB，删了最早的 %d 局", len(removed))
        except OSError:
            log.exception("调试采集：清理旧文件失败")
        g = env.get("game") or {}
        log.info("调试采集：新的一局 %s；游戏客户区 %s，整窗 %s，DPI %s；游戏设置 %s；显示器 %s",
                 self.session.name, g.get("client_rect"), g.get("window_rect"), g.get("dpi"),
                 env.get("game_settings"), env.get("monitors"))

    def _environment(self) -> dict:
        from . import __version__
        from .vision import capture
        hwnd = win.find_game_window()
        rect, wrect = win.client_rect(hwnd), win.window_rect(hwnd)
        env = {"app": __version__, "started": datetime.now().isoformat(timespec="seconds"),
               "data": {k: v for k, v in (self.engine.store.status() or {}).items()
                        if k in ("dataset_revision", "dataset_patch", "dataset_label", "patch")},
               "game": {"client_rect": rect and [rect.left, rect.top, rect.width, rect.height],
                        "window_rect": wrect and [wrect.left, wrect.top, wrect.width, wrect.height],
                        "dpi": win.window_dpi(hwnd), "exe": win.process_path(hwnd)}}
        try:
            env["monitors"] = capture._mss().monitors
        except Exception:
            env["monitors"] = None
        exe = env["game"]["exe"]
        settings = {}
        if exe:
            cfg = Path(exe).parent / "Config"
            out = self.session / "game-config"
            for name in ("PersistedSettings.json", "game.cfg"):
                try:
                    if (cfg / name).is_file():
                        out.mkdir(exist_ok=True)
                        shutil.copy2(cfg / name, out / name)
                except OSError:
                    log.exception("调试采集：复制游戏设置失败")
            try:
                settings = game_settings(json.loads((cfg / "PersistedSettings.json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        env["game_settings"] = settings
        self._flipped = _flipped(settings)
        return env

    # ---------- 存文件 ----------

    def _schedule(self, delays, tag: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._due += [(now + d, tag) for d in delays]

    def _save_frame(self, rect, name: str, png: bool = False) -> None:
        import cv2

        from .vision import capture
        img = capture.grab(rect)
        if capture.is_blank(img):
            return
        shots, mini = self.session / "shots", self.session / "minimap"
        shots.mkdir(exist_ok=True)
        mini.mkdir(exist_ok=True)
        ext, params = (".png", []) if png else (".jpg", [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        ok, buf = cv2.imencode(ext, img, params)
        if ok:
            (shots / f"{name}{ext}").write_bytes(buf.tobytes())
        for side, (x, y, w, h) in minimap_boxes(img.shape[1], img.shape[0], None).items():  # 对局中可能改翻转：两边都存
            ok, buf = cv2.imencode(".png", img[y:y + h, x:x + w])
            if ok:
                (mini / f"{name}-{side}.png").write_bytes(buf.tobytes())

    @staticmethod
    def _write_json(path: Path, data) -> None:
        if data is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, TypeError, ValueError):
            log.exception("调试采集：写 %s 失败", path.name)

    # ---------- 手动截图热键 ----------

    def _register_hotkey(self) -> bool:
        if not win.IS_WINDOWS:
            return False
        import ctypes
        ok = ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, 0x0001 | 0x0002 | 0x4000, ord("S"))  # Alt|Ctrl|NoRepeat
        log.info("调试采集已开：手动截图 Ctrl+Alt+S%s；文件在 %s", "" if ok else "（热键被别的程序占用，用不了）", self.root)
        return bool(ok)

    def _poll_hotkey(self) -> None:
        import ctypes
        from ctypes import wintypes
        msg = wintypes.MSG()
        while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0x0312, 0x0312, 1):  # WM_HOTKEY, PM_REMOVE
            if msg.wParam == HOTKEY_ID:
                self._manual_shot()

    def _manual_shot(self) -> None:
        import cv2

        from .vision import capture
        rect = win.client_rect(win.find_game_window())
        if rect and self.session:
            self._save_frame(rect, f"manual-{_stamp()}", png=True)
            log.info("调试采集：手动截图（对局中）")
            return
        mon = capture._mss().monitors[0]  # 整个桌面（所有显示器）
        img = capture.grab(win.Rect(mon["left"], mon["top"], mon["width"], mon["height"]))
        folder = self.root / "manual"
        folder.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".png", img)
        if ok:
            path = folder / f"{datetime.now():%Y%m%d}-{_stamp()}.png"
            path.write_bytes(buf.tobytes())
            log.info("调试采集：手动截图 %s", path.name)
