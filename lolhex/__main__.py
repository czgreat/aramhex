"""入口：python -m lolhex [选项]

  （无参数）          启动助手（游戏浮窗）
  --headless          不显示浮窗，只跑识别（调试用）
  --refresh           只刷新数据并打印状态后退出
  --scan-image 文件   用一张游戏截图测试三选一识别（排查识别问题用）
  --hero 名称         配合 --scan-image，指定英雄以输出推荐
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import Settings, app_home


def _fix_stdio() -> None:
    """控制台不是 UTF-8、或用 pythonw 启动没有控制台时，输出中文不能崩。"""
    import io
    import os
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8"))
        elif hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _setup_logging(debug: bool) -> None:
    home = app_home()
    handlers = [logging.StreamHandler(sys.stderr),
                RotatingFileHandler(home / "aramhex.log", maxBytes=20 * 1024 * 1024, backupCount=3, encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    for noisy in ("urllib3", "RapidOCR"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _scan_image(engine, path: str, hero: str | None) -> int:
    import cv2
    import numpy as np

    from . import recommend
    from .vision.detector import AugmentDetector

    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        print("读不到图片：", path)
        return 2
    engine.store.refresh()
    names = {aid: a["name"] for aid, a in engine.store.mayhem_augments().items()}
    from .vision.matcher import NameMatcher
    det = AugmentDetector(NameMatcher(names, engine.settings.match_threshold))
    offer = det.scan_full(img)
    if not offer:
        print("没有识别到三选一画面。")
        return 1
    hid = engine.store.resolve_hero(hero) if hero else None
    ev = recommend.evaluate(engine.store, hid, offer.slots, engine.settings.smoothing_games)
    out = {"size": [img.shape[1], img.shape[0]], "texts": offer.texts, "slots": offer.slots,
           "calibration": det.calibration.boxes if det.calibration else None,
           "advice": ev["advice"],
           "cards": [{k: r.get(k) for k in ("name", "rarity", "tier", "rank", "win", "games", "score", "verdict", "reasons")}
                     for r in ev["slots"]]}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lolhex", description="海克斯大乱斗助手")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--scan-image")
    ap.add_argument("--hero")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--minimized", action="store_true", help="启动后只显示托盘（开机自启用）")
    ap.add_argument("--self-test-gui", metavar="REPORT", help="离线检查打包后的 Qt 和窗口插件，写入 JSON 后退出")
    args = ap.parse_args(argv)

    if args.self_test_gui:
        from .gui_selftest import run
        return run(args.self_test_gui)

    _fix_stdio()
    from . import BETA, __version__, cloud
    _setup_logging(args.debug or BETA)
    settings = Settings.load()
    if BETA and not settings.extra.get("beta_defaults"):  # 测试版第一次打开：默认开 2999 和自动截图（之后可以在设置里关）
        settings.use_liveclient = settings.auto_screenshot = True
        settings.extra["beta_defaults"] = 1
        settings.save()
    if BETA and not settings.extra.get("beta_debug"):  # 测试版：调试采集默认开（要关改 config.json 的 debug_capture）
        settings.debug_capture = True
        settings.extra["beta_debug"] = 1
        settings.save()
    cloud.set_install_id(settings.install_id if settings.send_usage else None)
    logging.getLogger("lolhex").info("海克斯大乱斗助手 %s 启动；目录：%s；参数：%s", __version__,
                                     app_home(), " ".join(sys.argv[1:]) or "（无）")

    from .engine import Engine

    if args.refresh:
        engine = Engine(settings)
        engine.store.refresh(force=True)
        print(json.dumps(engine.store.status(), ensure_ascii=False, indent=2))
        return 0 if not engine.store.last_error else 1
    if args.scan_image:
        return _scan_image(Engine(settings), args.scan_image, args.hero)

    if args.headless:
        from .vision.window import enable_dpi_awareness
        enable_dpi_awareness()
        engine = Engine(settings)
        engine.start()
        print("海克斯大乱斗助手已启动（无界面）。Ctrl+C 退出")
        try:
            engine._stop.wait()
        except KeyboardInterrupt:
            pass
        return 0

    from .ui.overlay import run
    try:
        code = run(lambda: Engine(settings), minimized=args.minimized)
    except KeyboardInterrupt:
        code = 0
    # 界面已关：直接结束进程，不等后台线程收尾（卡住时会占着程序文件，导致更新替换失败）
    logging.shutdown()
    os._exit(code or 0)


if __name__ == "__main__":
    sys.exit(main())
