# PyInstaller 配置：海克斯大乱斗助手（onedir，无控制台窗口）。
# 用法：pyinstaller packaging/lolhex.spec --noconfirm --distpath build/dist --workpath build/work
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPEC).parent))
from binary_provenance import check_binary_sources

datas = collect_data_files("rapidocr")  # OCR 模型（.onnx）与配置（.yaml）
datas += [("../lolhex/ui/icon.png", "lolhex/ui")]
datas += [("../lolhex/data/hero_aliases.json", "lolhex/data")]  # 英雄别名（来源 Hexdata）
datas += [("../lolhex/data/bundled", "lolhex/data/bundled")]  # 内置统计数据（scripts/update_data.py）
datas += [("../lolhex/data/hero_nicknames.json", "lolhex/data")]  # 英雄外号（网上常见叫法）
datas += [("../lolhex/data/i18n.json", "lolhex/data")]  # 外服名字表（scripts/update_i18n.py）
datas += [("../lolhex/data/client_icons.json", "lolhex/data")]  # 拳头客户端英雄头像（scripts/update_client_icons.py）
datas += [("../lolhex/data/hero_icons.json", "lolhex/data")]  # 腾讯英雄头像（scripts/update_hero_icons.py）
datas += [("../lolhex/data/static_bundled.json", "lolhex/data")]  # 腾讯官方资料兜底（scripts/update_static.py）
datas += [("../lolhex/data/hero_bundled.json", "lolhex/data")]  # 单英雄官方出装、加点兜底（同上）
datas += [("../lolhex/data/item_icons.json", "lolhex/data")]  # 装备、召唤师技能图标兜底（同上）
datas += [("../lolhex/data/cacert.pem", "lolhex/data")]  # 根证书（去掉 eMudhra，scripts/make_cacert.py）
binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    ["launcher.py"],
    pathex=[".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=["lolhex.ui.overlay", "lolhex.vision.detector", "lolhex.vision.capture", "lolhex.vision.ocr",
                   "lolhex.ui.mainwindow", "lolhex.ui.autostart",
                   "PySide6.QtNetwork"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
check_binary_sources(a.binaries, [sys.prefix, sys.base_prefix, os.environ["SystemRoot"], Path(SPEC).parent.parent])

# 精简：去掉用不到的大文件（压缩后共约 20MB）。打包后的自检会跑一遍 OpenCV 和文字识别，确认没删到要用的。
# - opencv_videoio_ffmpeg：视频解码，我们只处理截图
# - opengl32sw.dll：Qt 的软件 OpenGL，界面只用 Widgets
# - PIL 的 _avif：AVIF 图片格式
# - qdirect2d.dll：Qt 的 Direct2D 平台插件，用的是 qwindows
# - PySide6/translations：Qt 自带界面的翻译，程序没有加载
DROP_BINARIES = ("opencv_videoio_ffmpeg", "opengl32sw.dll", "PIL/_avif.", "platforms/qdirect2d.dll")


def _keep(entry):
    name = entry[0].replace("\\", "/")
    return not any(k in name for k in DROP_BINARIES) and not name.startswith("PySide6/translations/")


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AramHex",
    console=False,
    icon="lolhex.ico",
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AramHex")
