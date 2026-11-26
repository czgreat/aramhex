"""EXE 版本信息跟着 lolhex/__init__.py 走。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
from version_resource import app_version  # noqa: E402


def test_version_comes_from_package():
    import re
    source = (ROOT / "lolhex" / "__init__.py").read_text(encoding="utf-8")
    assert app_version(ROOT) == re.search(r'^__version__ = "(.+)"', source, re.M)[1]


@pytest.mark.parametrize("line", ["", '__version__ = "abc"', '__version__ = "1"', '__version__ = "1.2.3.4.5"'])
def test_bad_version_stops_the_build(tmp_path, line):
    (tmp_path / "lolhex").mkdir()
    (tmp_path / "lolhex" / "__init__.py").write_text(line, encoding="utf-8")
    with pytest.raises(ValueError):
        app_version(tmp_path)
