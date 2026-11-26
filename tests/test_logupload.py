"""1.1.1（A10）：帮助改善软件开着、云控点名本机才上传日志；上传前去掉账号名和电脑用户名。"""
import base64
import gzip
import io
import json
from pathlib import Path

from lolhex import __version__, cloud, logupload
from lolhex.config import Settings

ID = "0123456789abcdef"


class Sent(list):
    """官方模块收到的日志请求，解开成 (日志文字, 请求里的字段)。"""

    def __init__(self, fake):
        self.fake = fake
        original = fake.resource

        def resource(kind, name, sites, **k):
            if kind == "logs":
                assert name == "" and k["method"] == "POST" and len(k["body"]) <= 1024 * 1024
                msg = json.loads(k["body"])
                self.append((gzip.decompress(base64.b64decode(msg.pop("gzip"))).decode(), msg))
            return original(kind, name, sites, **k)
        fake.resource = resource


def setup(tmp_path, monkeypatch, text, fake):
    monkeypatch.setenv("ARAMHEX_HOME", str(tmp_path))
    monkeypatch.setenv("USERNAME", "zhangsan")
    monkeypatch.setenv("COMPUTERNAME", "DESKTOP-ABC123")
    (tmp_path / "aramhex.log").write_text(text, encoding="utf-8")
    s = Settings.load(tmp_path / "config.json")
    s.install_id, s.riot_id = ID, "提莫队长#54321"
    return s, Sent(fake)


def cfg(*req):
    return cloud._merge({"logs": {"request": list(req)}})


def test_redact():
    t = logupload.redact(r"目录：C:\Users\zhangsan\AppData\Roaming\AramHex 查分 提莫队长#54321 对面 亚索#ABC12 GitHub #4 版本 #1234567",
                         "提莫队长#54321")
    assert "zhangsan" not in t and "提莫队长" not in t and "亚索" not in t
    assert r"C:\Users\<用户>\AppData" in t and "GitHub #4" in t and "#1234567" in t


def test_redact_fullwidth_hash_direction_marks_and_home_name(monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: Path("profile-owner"))
    text = "profile-owner 查分 Hide\u202e on bush ＃ KR1 对面 亚\u2067索＃ABC12"
    result = logupload.redact(text, "\u2066Hide on bush＃KR1\u2069")
    assert result == "<已去掉> 查分 <游戏账号> 对面 <游戏账号>"


def test_upload_only_when_named_and_once(tmp_path, monkeypatch, fake_official):
    s, sent = setup(tmp_path, monkeypatch, "启动；目录：C:\\Users\\zhangsan\\x DESKTOP-ABC123 提莫队长#54321\n", fake_official)
    assert logupload.maybe_upload(s, cfg("ffffffffffffffff")) == [] and not sent   # 没点名本机
    assert logupload.maybe_upload(s, cfg(ID)) == [ID]
    body, msg = sent[0]
    assert msg == {"id": ID, "ticket": "", "version": __version__}
    assert "zhangsan" not in body and "DESKTOP-ABC123" not in body and "提莫队长" not in body and "启动" in body
    assert logupload.maybe_upload(s, cfg(ID)) == [] and len(sent) == 1               # 同一条只传一次
    assert logupload.maybe_upload(s, cfg(ID, ID + ":t2")) == [ID + ":t2"]             # 换个单号再传
    assert sent[1][1]["ticket"] == "t2"
    assert Settings.load(tmp_path / "config.json").extra["logs_sent"] == [ID, ID + ":t2"]


def test_no_upload_when_turned_off_or_foreign_url(tmp_path, monkeypatch, fake_official):
    s, sent = setup(tmp_path, monkeypatch, "x\n", fake_official)
    s.send_usage = False
    assert logupload.maybe_upload(s, cfg(ID)) == [] and not sent
    s.send_usage = True
    bad = cloud._merge({"logs": {"url": "https://evil.example/api", "request": [ID]}})
    assert "url" not in bad["logs"]  # 地址只由官方模块给
    assert cloud._merge({"logs": {"request": "坏的"}})["logs"]["request"] == []


def test_revoking_consent_while_preparing_log_cancels_upload(tmp_path, monkeypatch, fake_official):
    s, sent = setup(tmp_path, monkeypatch, "准备上传\n", fake_official)
    pack = logupload._pack

    def prepare(raw):
        s.send_usage = False
        return pack(raw)

    monkeypatch.setattr(logupload, "_pack", prepare)
    assert logupload.maybe_upload(s, cfg(ID)) == []
    assert not sent and "logs_tries" not in s.extra


def test_revoking_consent_between_tickets_stops_remaining_uploads(tmp_path, monkeypatch, fake_official):
    s, sent = setup(tmp_path, monkeypatch, "准备上传\n", fake_official)
    send = fake_official.resource

    def finish_first(*args, **kwargs):
        response = send(*args, **kwargs)
        s.send_usage = False
        return response

    monkeypatch.setattr(fake_official, "resource", finish_first)
    assert logupload.maybe_upload(s, cfg(ID, ID + ":t2")) == [ID]
    assert len(sent) == 1


def test_duplicate_tickets_only_send_once(tmp_path, monkeypatch, fake_official):
    s, sent = setup(tmp_path, monkeypatch, "只发送一次\n", fake_official)
    assert logupload.maybe_upload(s, cfg(ID, ID)) == [ID]
    assert len(sent) == 1


def test_upload_merges_history_written_during_request(tmp_path, monkeypatch, fake_official):
    settings, _ = setup(tmp_path, monkeypatch, "运行日志\n", fake_official)
    send = fake_official.resource
    prior, retry = ID + ":prior", ID + ":retry"

    def update_history(*args, **kwargs):
        with settings._lock:
            settings.extra.update(logs_sent=[prior], logs_tries={retry: 2, ID: 1})
        return send(*args, **kwargs)

    monkeypatch.setattr(fake_official, "resource", update_history)
    assert logupload.maybe_upload(settings, cfg(ID)) == [ID]
    assert settings.extra["logs_sent"] == [prior, ID]
    assert settings.extra["logs_tries"] == {retry: 2}


def test_tail_keeps_only_complete_lines(tmp_path):
    path = tmp_path / "aramhex.log"
    path.write_bytes(b"older message\nkept\nunfinished")
    assert logupload._read_tail(path, 20) == b"kept\n"
    assert logupload._read_tail(path, 15) == b"kept\n"
    assert logupload._read_tail(path, 7) == b""


def test_tail_stops_at_snapshot_even_if_log_grows(monkeypatch):
    class GrowingLog(io.BytesIO):
        def tell(self):
            end = super().tell()
            self.write(b"appended after snapshot\n")
            return end

    monkeypatch.setattr(logupload, "open", lambda *args: GrowingLog(b"before snapshot\n"), raising=False)
    assert logupload._read_tail("aramhex.log", 64) == b"before snapshot\n"


def test_log_remains_within_limit_after_redaction(tmp_path, monkeypatch, fake_official):
    s, sent = setup(tmp_path, monkeypatch, "zhangsan\n" * 10000, fake_official)
    request = cloud._merge({"logs": {"request": [ID], "max_kb": 64}})
    assert logupload.maybe_upload(s, request) == [ID]
    body, _ = sent[0]
    assert body.startswith("<已去掉>\n") and len(body.encode()) <= 64 * 1024


def test_only_tail_of_big_log(tmp_path, monkeypatch, fake_official):
    s, sent = setup(tmp_path, monkeypatch, "旧的一行\n" * 200000 + "最后一行\n", fake_official)
    logupload.maybe_upload(s, cfg(ID))
    body = sent[0][0]
    assert len(body.encode()) <= logupload.MAX_BYTES and body.replace("\r\n", "\n").endswith("最后一行\n") and body.startswith("旧的一行")


def test_retry_on_server_errors_but_not_on_rejects(tmp_path, monkeypatch, fake_official):
    # 复核 10-22：5xx、429 下次再试（最多 5 次）；400/403/404/413 不再试
    s, _ = setup(tmp_path, monkeypatch, "x\n", fake_official)
    fake_official.log_status = 502
    for _ in range(3):
        assert logupload.maybe_upload(s, cfg(ID)) == []
    assert s.extra["logs_tries"][ID] == 3 and ID not in s.extra["logs_sent"]
    fake_official.log_status = 201
    assert logupload.maybe_upload(s, cfg(ID)) == [ID] and ID not in s.extra["logs_tries"]
    fake_official.log_status = 403
    assert logupload.maybe_upload(s, cfg(ID + ":t9")) == [ID + ":t9"]   # 被拒：算处理完，不再试
    fake_official.down = logupload.requests.ConnectionError("连不上")
    for _ in range(7):
        logupload.maybe_upload(s, cfg(ID + ":t8"))
    assert s.extra["logs_tries"][ID + ":t8"] == logupload.MAX_TRIES     # 最多试 5 次
    assert logupload._tickets(cfg(ID + ":中文", ID + ":", ID + ":ok-1"), ID) == [ID + ":ok-1"]  # 单号和服务端同一规则


def test_incompressible_log_still_fits_one_request():
    import random
    rnd = random.Random(7)
    raw = b"".join(b"%d %s\n" % (i, rnd.randbytes(60).hex().encode()) for i in range(30000))
    assert len(gzip.compress(raw)) > logupload.MAX_PACKED
    body = logupload._pack(raw)
    assert len(logupload._message(ID, "t" * 32, body)) <= 1024 * 1024
    kept = gzip.decompress(body)
    assert raw.endswith(kept) and kept.split(b"\n")[0].split()[0].isdigit() and len(kept) > 256 * 1024


def test_redact_case_and_spaces(monkeypatch):
    monkeypatch.setenv("USERNAME", "John Smith")
    monkeypatch.setenv("COMPUTERNAME", "DESKTOP-AB12CD")
    t = logupload.redact(r"C:\Users\john smith\x c:\users\Zhang San\y desktop-ab12cd hide on bush#kr1",
                         "Hide on bush#KR1")
    assert "john" not in t.lower() and "zhang" not in t.lower() and "desktop" not in t.lower() and "hide on" not in t


def test_cloud_max_kb_and_previous_file(tmp_path, monkeypatch, fake_official):
    # 1.1.2：云控 logs.max_kb 调上传大小；刚换过新日志文件时接着读 aramhex.log.1
    s, sent = setup(tmp_path, monkeypatch, "新文件\n" * 1000, fake_official)
    (tmp_path / "aramhex.log.1").write_text("上一个文件\n" * 300000, encoding="utf-8")
    big = cloud._merge({"logs": {"request": [ID], "max_kb": 2048}})
    assert big["logs"]["max_kb"] == 2048
    assert logupload.maybe_upload(s, big) == [ID]
    body = sent[0][0].replace("\r\n", "\n")
    assert body.endswith("新文件\n") and body.startswith("上一个文件\n")   # 旧文件在前、新文件在后
    assert 1900 * 1024 < len(body.encode()) <= 2048 * 1024
    assert cloud._merge({"logs": {"max_kb": 10 ** 9}})["logs"]["max_kb"] == 4096   # 有上限
    assert cloud._merge({"logs": {"max_kb": "大"}})["logs"]["max_kb"] == 512        # 不认就用默认
    assert cloud._merge({"logs": {"max_kb": True}})["logs"]["max_kb"] == 512


def test_default_does_not_read_previous_file_when_current_is_long(tmp_path, monkeypatch, fake_official):
    s, sent = setup(tmp_path, monkeypatch, "当前\n" * 200000, fake_official)
    (tmp_path / "aramhex.log.1").write_text("上一个文件\n" * 1000, encoding="utf-8")
    logupload.maybe_upload(s, cfg(ID))
    assert "上一个文件" not in sent[0][0] and len(sent[0][0].encode()) <= logupload.MAX_BYTES


def test_remote_verbose_needs_ticket_usage_and_expires(tmp_path, monkeypatch, fake_official):
    import logging
    s, _ = setup(tmp_path, monkeypatch, "x\n", fake_official)
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


def test_remote_verbose_expires_without_cloud_refresh(tmp_path, monkeypatch, fake_official):
    """到期靠本地计时，不等下次刷新云控；同一台电脑列了超过 20 个单号也不会因为裁剪记录而重新计时。"""
    import logging
    s, _ = setup(tmp_path, monkeypatch, "x\n", fake_official)
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


def test_remote_verbose_timer_restores_info(tmp_path, monkeypatch, fake_official):
    import logging
    import time
    s, _ = setup(tmp_path, monkeypatch, "x\n", fake_official)
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


def test_verbose_not_blocked_by_slow_upload(tmp_path, monkeypatch, fake_official):
    """日志上传卡在慢网（拿着上传锁）时，关「帮助改善软件」重算详细日志不能被挡住。"""
    import logging
    import threading
    s, _ = setup(tmp_path, monkeypatch, "x\n", fake_official)
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
