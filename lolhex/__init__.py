"""AramHex 海克斯大乱斗助手：屏幕识别 + 自采统计数据 + 腾讯官方资料。"""

import os as _os
from pathlib import Path as _Path

__version__ = "1.1.2"

# 根证书：用 lolhex/data/cacert.pem（certifi 的去掉 eMudhra，见 scripts/make_cacert.py）。
# 放在这里，所有入口（界面、增量更新、命令行）联网前都已生效；用户自己设了 REQUESTS_CA_BUNDLE（公司代理等）就不动
_CA = _Path(__file__).resolve().parent / "data" / "cacert.pem"
if _CA.is_file():
    _os.environ.setdefault("REQUESTS_CA_BUNDLE", str(_CA))
# 测试版：调试模式（详细日志、三选一和选英雄时自动截图），方便事后查问题。正式版为 False
BETA = False

GITHUB_REPO = "czgreat/aramhex"
DATA_REPO = "czgreat/aramhex-data"
WEBSITE = "https://aramhex.com"
HELP_PAGE = f"{WEBSITE}/#faq"
MIRROR = f"{WEBSITE}/u"
HOMEPAGE = f"https://github.com/{GITHUB_REPO}"
RELEASES_PAGE = f"{HOMEPAGE}/releases"
DATA_HOMEPAGE = f"https://github.com/{DATA_REPO}"
