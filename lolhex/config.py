"""运行配置与本地目录。

配置文件：%APPDATA%\\AramHex\\config.json（可用环境变量 ARAMHEX_HOME 改目录）。
便携版：程序目录里有 portable.txt 时，配置、日志和数据都放在程序目录的 userdata 下，不写 %APPDATA%。
首次运行会写出默认值，之后手改即可；未知键保留不动。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR_NAME = "AramHex"
# 去掉的设置项：老设置文件里有也不再写回（1.1.1 删了 2999 接口；浮窗不再对截图隐身，录屏开关也删了；
# 自动截图只是调试用，设置页去掉，只在调试模式（debug.txt）下截）
REMOVED = {"use_liveclient", "allow_capture", "auto_screenshot"}
# 识别频率：各种扫描间隔乘以这个倍数。电脑配置低时放慢，省 CPU
SPEEDS = {
    "standard": ("标准（推荐）", 1.0),
    "saver": ("省电：识别稍慢", 2.0),
    "low": ("低配电脑：识别更慢，最省 CPU", 4.0),
}


def program_dir() -> Path:
    """程序所在目录（打包后是 exe 旁边，源码运行时是仓库根目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_portable() -> bool:
    return (program_dir() / "portable.txt").is_file()


def app_home() -> Path:
    env = os.environ.get("ARAMHEX_HOME")
    if env:
        home = Path(env)
    elif is_portable():
        home = program_dir() / "userdata"
    else:
        base = os.environ.get("APPDATA") or str(Path.home())
        home = Path(base) / APP_DIR_NAME
    home.mkdir(parents=True, exist_ok=True)
    return home


def _parse_config(raw: bytes) -> dict:
    """读设置文件。手改后常见的几种写法也认：带 BOM 的 UTF-8、GBK（老记事本存成 ANSI）、末尾多一个逗号。"""
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        for candidate in (text, re.sub(r",\s*([}\]])", r"\1", text)):
            try:
                data = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
            raise ValueError("不是 JSON 对象")
    raise ValueError("不是合法的 JSON")


@dataclass
class Settings:
    # 在悬浮层与主界面显示胜率数值。关掉后只显示评级与排名。
    show_winrate: bool = True
    # 腾讯官方资料（英雄、海克斯字典、出装、加点）的刷新间隔（小时）。
    refresh_hours: float = 6.0
    # 启动时检查数据仓库有没有新的统计数据，有就下载（按 SHA-256 校验）。
    check_data_updates: bool = True
    # 已校准时，检查三张卡标题区域的间隔（毫秒）。
    scan_interval_ms: int = 300
    # 未校准时，整块中部区域检测+识别的间隔（秒）。单次约 1 秒（单线程）。
    bootstrap_interval_s: float = 3.0
    # 海克斯名称模糊匹配的最低分（0–100）。
    match_threshold: float = 72.0
    # 模式未知时是否也扫描。
    scan_when_mode_unknown: bool = True
    # 小样本平滑强度：先验场次 k。
    smoothing_games: int = 300
    # 悬浮层开关（--headless 也会关闭）。
    overlay: bool = True
    # 悬浮按钮的位置：top-right / top-left。
    overlay_corner: str = "top-right"
    # 三选一标签放在卡片哪里：top=标题上方（压在图标下半，默认）/ bottom=描述下面的空白处。
    badge_position: str = "top"
    # 参考数据的分数段：""=全体（默认），casual/mid/high=娱乐/中分/前 5%（1.1.0 以前的 top 读到当 high）。
    data_band: str = ""
    # 识别频率（见 SPEEDS）：standard / saver / low。
    speed: str = "standard"
    # 查分：自己的「名字#编号」和上次查到的结果（{"as_of", "results", "queried_at"}）
    riot_id: str = ""
    my_rank: dict = field(default_factory=dict)
    # 云控公告：已经弹过的公告 id
    seen_notice: str = ""
    # 上次运行的版本：变了说明刚更新过，提示一次「已更新到 x」
    last_version: str = ""
    # 第一次打开时的「请把游戏设成无边框」提示，看过就不再弹
    seen_welcome: bool = False
    # 游戏客户端的语言（外服）：zh_CN 中文（国服，繁中台服也认）/ en_US / ja_JP / ko_KR，见 i18n.py。韩文要另外下载识别数据
    game_lang: str = "zh_CN"
    # 匿名使用统计：第一次打开时随机生成的编号（和硬件、账号无关，重装就是新的），发往 aramhex.com 的请求带上（见 cloud.headers）。
    # 界面上不显示；要关就把 config.json 里的 send_usage 改成 false
    install_id: str = ""
    send_usage: bool = True
    # 玩家自己在设置里改过的项（1.1.1）：云控改默认值时不碰这些（见 cloudsettings.py）
    user_set: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.forced: dict = {}  # 云控正在强制的项 → 玩家原来的值；存文件时写原来的值
        self.debug_mode = False  # 调试模式（debug.txt / --debug / 测试版）：详细日志 + 自动截图；不存进设置文件
        self._lock = threading.RLock()  # 界面线程和后台线程（上传日志等）都会存设置

    def set_by_user(self, key: str, value) -> None:
        """玩家自己改了某一项（设置页、托盘、分段下拉框）：记成玩家改的；云控正在强制这一项时，
        记下玩家要的值（云控撤掉后用它），当前值仍按强制的。"""
        with self._lock:
            self.mark_user(key)
            if key in self.forced:
                self.forced[key] = value
            else:
                setattr(self, key, value)

    def mark_user(self, *keys: str) -> None:
        for k in keys:
            if k not in self.user_set:
                self.user_set.append(k)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or app_home() / "config.json"
        log = logging.getLogger(__name__)
        data: dict = {}
        broken = False
        raw = b""
        if path.exists():
            try:
                raw = path.read_bytes()
                data = _parse_config(raw)
            except (OSError, ValueError) as e:
                broken = True
                log.warning("设置文件读不了：%s", e)
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known and k != "extra"})
        s.extra = {k: v for k, v in data.items() if k not in known and k not in REMOVED}
        if not isinstance(s.user_set, list):
            s.user_set = []
        if data and "user_set" not in data:
            # 1.1.0 以前的设置文件没记谁改过：和默认值不一样的就当玩家自己改的，云控改默认值时不碰
            dflt = cls()
            s.user_set = [f.name for f in fields(cls) if f.name in data and f.name not in ("extra", "user_set")
                          and getattr(s, f.name) != getattr(dflt, f.name)]
        s.fresh_install = not data and not broken  # 没有设置文件：第一次装（用来区分「刚安装」和「刚更新」）
        if not isinstance(s.install_id, str) or not re.fullmatch(r"[0-9a-f]{16}", s.install_id):
            s.install_id = ""  # 手改坏了：重新生成（不是字符串时请求头发不出去）
        if not isinstance(s.send_usage, bool):
            s.send_usage = str(s.send_usage).strip().lower() in ("true", "1", "yes", "on")  # 认不出就当关了
        from .i18n import normalize_lang
        s.game_lang = normalize_lang(s.game_lang)  # 以前的繁中选项 zh_TW、手改的怪值都当中文
        # 1.1.1：设置页去掉「显示胜率数字」（用这个软件就是要看胜率），以前关掉的也打开
        s.show_winrate = True
        if s.data_band == "top":
            s.data_band = "high"  # 1.1.1 去掉「顶尖」，和「高分」合成「前 5%」
        if broken:
            # 实在读不懂：原文件留个备份，后面照常写新的；用户在里面关没关匿名统计看不出来，这次就当关了，
            # 能从原文里找到 "send_usage": true/false 就照它的
            m = re.search(rb'"send_usage"\s*:\s*(true|false)', raw)
            s.send_usage = bool(m) and m.group(1) == b"true"
            s.seen_welcome = True  # 不是新装，别再弹欢迎框
            backup = path.with_name(path.name + ".broken")
            if backup.exists():  # 以前也坏过：不覆盖上一次的备份
                backup = path.with_name(f"{path.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}")
            try:
                path.replace(backup)
                log.warning("坏的设置文件改名为 %s，这次用默认设置", backup.name)
            except OSError as e:
                log.warning("坏的设置文件没法改名：%s", e)
        if not s.install_id or not path.exists():
            s.install_id = s.install_id or secrets.token_hex(8)
            s.save(path)
        return s

    @property
    def speed_factor(self) -> float:
        return SPEEDS.get(self.speed, SPEEDS["standard"])[1]

    def save(self, path: Path | None = None) -> bool:
        """写设置文件；写不进去（只读、权限、磁盘满）只记日志，返回 False，程序照常用。"""
        path = path or app_home() / "config.json"
        with self._lock:
            data = asdict(self)
            data.update(self.forced)  # 云控强制的项存玩家原来的值：云控撤掉就恢复
            extra = data.pop("extra", {}) or {}
            data.update(extra)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            try:
                # 先写临时文件再换过去：两个线程同时存、或者写到一半断电，都不会留下写坏的设置文件
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, path)
                return True
            except OSError as e:
                logging.getLogger(__name__).warning("设置文件写不进去：%s", e)
                return False
