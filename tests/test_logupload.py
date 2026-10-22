"""1.1.1（A10）：帮助改善软件开着、云控点名本机才上传日志；上传前去掉账号名和电脑用户名。"""
import gzip

from lolhex import cloud, logupload
from lolhex.config import Settings

ID = "0123456789abcdef"


class Resp:
    def __init__(self, code):
        self.status_code = code


def setup(tmp_path, monkeypatch, text):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setenv("USERNAME", "zhangsan")
    monkeypatch.setenv("COMPUTERNAME", "DESKTOP-ABC123")
    (tmp_path / "aramhex.log").write_text(text, encoding="utf-8")
    s = Settings.load(tmp_path / "config.json")
    s.install_id, s.riot_id = ID, "提莫队长#54321"
    sent = []
    monkeypatch.setattr(logupload.requests, "post",
                        lambda url, data=None, headers=None, **k: sent.append((url, gzip.decompress(data).decode(), headers))
                        or Resp(200))
    return s, sent


def cfg(*req):
    return cloud._merge({"logs": {"request": list(req)}})


def test_redact():
    t = logupload.redact(r"目录：C:\Users\zhangsan\AppData\Roaming\AramHex 查分 提莫队长#54321 对面 亚索#ABC12 GitHub #4 版本 #1234567",
                         "提莫队长#54321")
    assert "zhangsan" not in t and "提莫队长" not in t and "亚索" not in t
    assert r"C:\Users\<用户>\AppData" in t and "GitHub #4" in t and "#1234567" in t


def test_upload_only_when_named_and_once(tmp_path, monkeypatch):
    s, sent = setup(tmp_path, monkeypatch, "启动；目录：C:\\Users\\zhangsan\\x DESKTOP-ABC123 提莫队长#54321\n")
    assert logupload.maybe_upload(s, cfg("ffffffffffffffff")) == [] and not sent   # 没点名本机
    assert logupload.maybe_upload(s, cfg(ID)) == [ID]
    url, body, headers = sent[0]
    assert url == "https://aramhex.com/api/logs" and headers["X-AramHex-Id"] == ID
    assert "zhangsan" not in body and "DESKTOP-ABC123" not in body and "提莫队长" not in body and "启动" in body
    assert logupload.maybe_upload(s, cfg(ID)) == [] and len(sent) == 1               # 同一条只传一次
    assert logupload.maybe_upload(s, cfg(ID, ID + ":t2")) == [ID + ":t2"]             # 换个单号再传
    assert sent[1][2]["X-AramHex-Ticket"] == "t2"
    assert Settings.load(tmp_path / "config.json").extra["logs_sent"] == [ID, ID + ":t2"]


def test_no_upload_when_turned_off_or_foreign_url(tmp_path, monkeypatch):
    s, sent = setup(tmp_path, monkeypatch, "x\n")
    s.send_usage = False
    assert logupload.maybe_upload(s, cfg(ID)) == [] and not sent
    s.send_usage = True
    bad = cloud._merge({"logs": {"url": "https://evil.example/api", "request": [ID]}})
    assert bad["logs"]["url"] == "https://aramhex.com/api/logs"  # 别的网址不收
    assert cloud._merge({"logs": {"request": "坏的"}})["logs"]["request"] == []


def test_only_tail_of_big_log(tmp_path, monkeypatch):
    s, sent = setup(tmp_path, monkeypatch, "旧的一行\n" * 200000 + "最后一行\n")
    logupload.maybe_upload(s, cfg(ID))
    body = sent[0][1]
    assert len(body.encode()) <= logupload.MAX_BYTES and body.replace("\r\n", "\n").endswith("最后一行\n") and body.startswith("旧的一行")


def test_retry_on_server_errors_but_not_on_rejects(tmp_path, monkeypatch):
    # 复核 10-22：5xx、429 下次再试（最多 5 次）；400/403/404/413 不再试
    s, _ = setup(tmp_path, monkeypatch, "x\n")
    codes = [502]
    monkeypatch.setattr(logupload.requests, "post", lambda *a, **k: Resp(codes[0]))
    for _ in range(3):
        assert logupload.maybe_upload(s, cfg(ID)) == []
    assert s.extra["logs_tries"][ID] == 3 and ID not in s.extra["logs_sent"]
    codes[0] = 201
    assert logupload.maybe_upload(s, cfg(ID)) == [ID] and ID not in s.extra["logs_tries"]
    codes[0] = 403
    assert logupload.maybe_upload(s, cfg(ID + ":t9")) == [ID + ":t9"]   # 被拒：算处理完，不再试
    codes[0] = 500
    for _ in range(7):
        logupload.maybe_upload(s, cfg(ID + ":t8"))
    assert s.extra["logs_tries"][ID + ":t8"] == logupload.MAX_TRIES     # 最多试 5 次
    assert logupload._tickets(cfg(ID + ":中文", ID + ":", ID + ":ok-1"), ID) == [ID + ":ok-1"]  # 单号和服务端同一规则


def test_redact_case_and_spaces(monkeypatch):
    monkeypatch.setenv("USERNAME", "John Smith")
    monkeypatch.setenv("COMPUTERNAME", "DESKTOP-AB12CD")
    t = logupload.redact(r"C:\Users\john smith\x c:\users\Zhang San\y desktop-ab12cd hide on bush#kr1",
                         "Hide on bush#KR1")
    assert "john" not in t.lower() and "zhang" not in t.lower() and "desktop" not in t.lower() and "hide on" not in t
