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


def test_cloud_max_kb_and_previous_file(tmp_path, monkeypatch):
    # 1.1.2：云控 logs.max_kb 调上传大小；刚换过新日志文件时接着读 aramhex.log.1
    s, sent = setup(tmp_path, monkeypatch, "新文件\n" * 1000)
    (tmp_path / "aramhex.log.1").write_text("上一个文件\n" * 300000, encoding="utf-8")
    big = cloud._merge({"logs": {"request": [ID], "max_kb": 2048}})
    assert big["logs"]["max_kb"] == 2048
    assert logupload.maybe_upload(s, big) == [ID]
    body = sent[0][1].replace("\r\n", "\n")
    assert body.endswith("新文件\n") and body.startswith("上一个文件\n")   # 旧文件在前、新文件在后
    assert 1900 * 1024 < len(body.encode()) <= 2048 * 1024
    assert cloud._merge({"logs": {"max_kb": 10 ** 9}})["logs"]["max_kb"] == 4096   # 有上限
    assert cloud._merge({"logs": {"max_kb": "大"}})["logs"]["max_kb"] == 512        # 不认就用默认
    assert cloud._merge({"logs": {"max_kb": True}})["logs"]["max_kb"] == 512


def test_default_does_not_read_previous_file_when_current_is_long(tmp_path, monkeypatch):
    s, sent = setup(tmp_path, monkeypatch, "当前\n" * 200000)
    (tmp_path / "aramhex.log.1").write_text("上一个文件\n" * 1000, encoding="utf-8")
    logupload.maybe_upload(s, cfg(ID))
    assert "上一个文件" not in sent[0][1] and len(sent[0][1].encode()) <= logupload.MAX_BYTES


def test_remote_verbose_needs_ticket_usage_and_expires(tmp_path, monkeypatch):
    import logging
    s, _ = setup(tmp_path, monkeypatch, "x\n")
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.INFO)
    on = cloud._merge({"logs": {"verbose": [ID + ":v1"]}})
    assert logupload.apply_verbose(s, on, now=1000.0) and root.level == logging.DEBUG
    assert Settings.load(tmp_path / "config.json").extra["verbose_seen"] == {ID + ":v1": 1000.0}  # 记下开始时间
    assert logupload.apply_verbose(s, on, now=1000.0 + 23 * 3600)            # 24 小时内一直开着
    assert not logupload.apply_verbose(s, on, now=1000.0 + 25 * 3600) and root.level == logging.INFO  # 到时自动关
    assert not logupload.apply_verbose(s, cloud._merge({"logs": {"verbose": [ID]}}), now=1.0)  # 必须带单号
    assert logupload.apply_verbose(s, cloud._merge({"logs": {"verbose": [ID + ":v2"]}}), now=1.0e6)  # 换单号再开
    assert not logupload.apply_verbose(s, cloud._merge({}), now=1.0e6) and root.level == logging.INFO  # 撤掉就恢复
    s.send_usage = False
    assert not logupload.apply_verbose(s, cloud._merge({"logs": {"verbose": [ID + ":v3"]}}), now=1.0e6)  # 关了「帮助改善软件」不生效
    s.send_usage, s.debug_mode = True, True
    assert logupload.apply_verbose(s, cloud._merge({}), now=1.0e6) and root.level == logging.DEBUG  # debug.txt 一直详细


def test_remote_verbose_expires_without_cloud_refresh(tmp_path, monkeypatch):
    """到期靠本地计时，不等下次刷新云控；同一台电脑列了超过 20 个单号也不会因为裁剪记录而重新计时。"""
    import logging
    s, _ = setup(tmp_path, monkeypatch, "x\n")
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.INFO)
    armed = []
    monkeypatch.setattr(logupload, "_arm", lambda _s, delay: armed.append(delay))
    on = cloud._merge({"logs": {"verbose": [ID + ":v1"]}})
    assert logupload.apply_verbose(s, on, now=1000.0)
    assert armed[-1] == 24 * 3600 + 1  # 到期那一刻重算
    assert logupload.apply_verbose(s, on, now=1000.0 + 20 * 3600) and armed[-1] == 4 * 3600 + 1
    assert not logupload.apply_verbose(s, cloud._merge({}), now=2000.0) and armed[-1] is None
    many = cloud._merge({"logs": {"verbose": [f"{ID}:t{i}" for i in range(50)]}})
    assert logupload.apply_verbose(s, many, now=5000.0)
    assert len(s.extra["verbose_seen"]) >= 50
    assert not logupload.apply_verbose(s, many, now=5000.0 + 25 * 3600)  # 50 个单号都到期，不会重新开


def test_remote_verbose_timer_restores_info(tmp_path, monkeypatch):
    import logging
    import time
    s, _ = setup(tmp_path, monkeypatch, "x\n")
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.INFO)
    on = cloud._merge({"logs": {"verbose": [ID + ":v1"]}})
    monkeypatch.setattr(cloud, "get", lambda: on)
    monkeypatch.setattr(logupload, "VERBOSE_HOURS", 0.5 / 3600)  # 半秒到期
    assert logupload.apply_verbose(s, on) and root.level == logging.DEBUG
    for _ in range(40):
        if root.level == logging.INFO:
            break
        time.sleep(0.1)
    assert root.level == logging.INFO
    logupload._arm(s, None)


def test_verbose_not_blocked_by_slow_upload(tmp_path, monkeypatch):
    """日志上传卡在慢网（拿着上传锁）时，关「帮助改善软件」重算详细日志不能被挡住。"""
    import logging
    import threading
    s, _ = setup(tmp_path, monkeypatch, "x\n")
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.INFO)
    on = cloud._merge({"logs": {"verbose": [ID + ":v1"]}})
    assert logupload.apply_verbose(s, on)
    assert logupload._lock.acquire(timeout=1)  # 模拟上传正在等网络
    try:
        s.send_usage = False
        done = []
        t = threading.Thread(target=lambda: done.append(logupload.apply_verbose(s, on)), daemon=True)
        t.start()
        t.join(2)
        assert done == [False] and root.level == logging.INFO
    finally:
        logupload._lock.release()
        logupload._arm(s, None)
