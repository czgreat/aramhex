"""短用户名的脱敏不能损坏诊断数据，真实身份仍须隐藏。"""
import pytest

from lolhex.logupload import redact


@pytest.fixture(autouse=True)
def identities(monkeypatch):
    for key in ("USERNAME", "USER", "COMPUTERNAME", "USERDOMAIN"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("name", ["78", "34", "Win", "nos", "INFO"])
def test_short_identity_does_not_change_numeric_records(monkeypatch, name):
    monkeypatch.setenv("USERNAME", name)
    records = (
        "2026-11-06 20:35:56,789 INFO lolhex.diagnostics: "
        "运行环境：系统=Windows 11 CPU=Intel 逻辑核=16 可用/总内存=3478/16384MB "
        "识别语言=zh_CN 速度=standard 标题间隔=300毫秒 启动扫描间隔=3.0秒\n"
        "2026-11-06 20:35:56,790 INFO lolhex.engine: "
        "推荐状态发布：scan=3478 path=strip reason=titles source=strip observation=34 "
        "capture=4ms work=78ms after=1ms total=83ms frame_age=79ms\n"
        "这局认字：标题条 3478 次（平均 78 毫秒，CPU 34），"
        "整块扫描 34 次（平均 178 毫秒，CPU 78），标题OCR 78 次\n"
        "OCR整块耗时：输入=1600x887 总计=3478毫秒 等待锁=34毫秒 "
        "检测=3400毫秒 认字=78毫秒 文本行=34\n"
        "运行资源：已运行=3.4小时 本程序内存=478MB（峰值578MB，私有778MB） "
        "系统可用/总内存=3478/16384MB\n"
    )
    assert redact(records) == records


def test_short_numeric_identity_still_hidden_in_paths_and_plain_text(monkeypatch):
    monkeypatch.setenv("USERNAME", "78")
    monkeypatch.setenv("COMPUTERNAME", "34")
    text = (r"C:\Users\78\x D:\78\log 账号 78 电脑 34 旁人 亚索#ABC12" + "\n"
            + r"运行环境：系统=Windows 11 CPU=78 目录=C:\Users\78\x")
    result = redact(text)
    assert r"C:\Users\<用户>\x" in result
    assert "78" not in result and "34" not in result and "亚索" not in result


def test_riot_id_numeric_prefix_is_not_treated_as_diagnostic_number():
    result = redact("玩家 78#AB12 未识别英雄 78", "78#AB12")
    assert "78" not in result and "AB12" not in result


def test_unicode_names_and_case_are_still_removed(monkeypatch):
    monkeypatch.setenv("USERNAME", "王小明")
    monkeypatch.setenv("COMPUTERNAME", "DESKTOP-AB12")
    result = redact("王小明的日志 desktop-ab12 玩家 提莫队长", "提莫队长#54321")
    assert "王小明" not in result and "desktop" not in result.lower() and "提莫队长" not in result


def test_identity_in_diagnostic_message_outside_numeric_schema_is_hidden(monkeypatch):
    monkeypatch.setenv("USERNAME", "34")
    result = redact(r"扫描诊断：scan=34 capture=34ms note=34 文件=D:\34\x")
    assert "scan=34" in result and "capture=34ms" in result
    # note 不是诊断数字字段：不应因为出现在诊断事件中而漏掉身份。
    assert "note=<已去掉>" in result and r"D:\<已去掉>\x" in result
