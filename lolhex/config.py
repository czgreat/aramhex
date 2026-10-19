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
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR_NAME = "AramHex"
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
    # 读取游戏内 2999 接口（Riot 官方只读、非 LCU）识别英雄与等级。默认关闭。
    use_liveclient: bool = False
    # 在悬浮层与主界面显示胜率数值。关掉后只显示评级与排名。
    show_winrate: bool = True
    # 腾讯官方资料（英雄、海克斯字典、出装、加点）的刷新间隔（小时）。
    refresh_hours: float = 6.0
    # 启动时检查数据仓库有没有新的统计数据，有就下载（按 SHA-256 校验）。
    check_data_updates: bool = True
    # 已校准时，检查三张卡标题区域的间隔（毫秒）。
    scan_interval_ms: int = 300
    # 未校准时，整块中部区域检测+识别的间隔（秒）。单次约 0.3–0.5s CPU。
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
    # 参考数据的分数段：""=全体（默认），casual/mid/high/top=娱乐/中分/高分/顶尖。
    data_band: str = ""
    # 识别频率（见 SPEEDS）：standard / saver / low。
    speed: str = "standard"
    # 录屏、截图时显示浮窗（直播、做视频用）。默认不显示：画面干净，也不会识别到自己画的字。
    allow_capture: bool = False
    # 自动截图：海克斯三选一和选英雄时自动保存整屏截图（含浮窗），存在 <程序数据目录>/screenshots。
    auto_screenshot: bool = False
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
    # 匿名使用统计：第一次打开时随机生成的编号（和硬件、账号无关，重装就是新的），只在查云控时带上。
    # 界面上不显示；要关就把 config.json 里的 send_usage 改成 false
    install_id: str = ""
    send_usage: bool = True
    extra: dict = field(default_factory=dict)

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
        s.extra = {k: v for k, v in data.items() if k not in known}
        s.fresh_install = not data and not broken  # 没有设置文件：第一次装（用来区分「刚安装」和「刚更新」）
        if not isinstance(s.install_id, str) or not re.fullmatch(r"[0-9a-f]{16}", s.install_id):
            s.install_id = ""  # 手改坏了：重新生成（不是字符串时请求头发不出去）
        if not isinstance(s.send_usage, bool):
            s.send_usage = str(s.send_usage).strip().lower() in ("true", "1", "yes", "on")  # 认不出就当关了
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
        data = asdict(self)
        extra = data.pop("extra", {}) or {}
        data.update(extra)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except OSError as e:
            logging.getLogger(__name__).warning("设置文件写不进去：%s", e)
            return False
