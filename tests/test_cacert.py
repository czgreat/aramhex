"""1.1.1：根证书去掉 eMudhra（CVE-2026-111943），不动第三方库（老用户照常增量更新）。"""
import os

import requests

import lolhex
from lolhex.patch import is_patchable


def test_bundle_without_emudhra():
    lines = lolhex._CA.read_text(encoding="utf-8").splitlines()
    text = "\n".join(lines[3:])  # 前 3 行是说明
    assert "eMudhra" not in text and "emSign" not in text
    assert text.count("BEGIN CERTIFICATE") >= 100
    assert "ISRG Root X1" in text and "DigiCert Global Root G2" in text  # aramhex.com（Let's Encrypt）、GitHub


def test_requests_uses_bundle():
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(lolhex._CA)
    s = requests.Session()
    assert s.merge_environment_settings("https://aramhex.com/x", {}, None, None, None)["verify"] == str(lolhex._CA)


def test_bundle_is_incrementally_updatable():
    assert is_patchable("_internal/lolhex/data/cacert.pem")
    assert not is_patchable("_internal/certifi/cacert.pem")  # 所以不改 certifi
