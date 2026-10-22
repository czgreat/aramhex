"""生成 lolhex/data/cacert.pem：certifi 自带的根证书去掉 eMudhra（emSign）的。

2026-10 eMudhra 中间证书被攻破（CVE-2026-111943；Go 1.27.3/1.26.10、Mozilla 都已拉黑）。
certifi 在打包后的 _internal/certifi/ 里，改它老用户就不能增量更新；所以另放一份到 lolhex/data/（增量更新会换），
启动时用 REQUESTS_CA_BUNDLE 让 requests 用这份（lolhex/__init__.py）。certifi 升级后重跑一次：

    python scripts/make_cacert.py
"""
import re
from pathlib import Path

import certifi

OUT = Path(__file__).resolve().parent.parent / "lolhex" / "data" / "cacert.pem"
DROP = "eMudhra"


def main() -> None:
    src = Path(certifi.where()).read_text(encoding="utf-8")
    keep, drop = [], []
    for b in re.split(r"(?=\n# Issuer: )", src):
        (drop if DROP in b.split("-----BEGIN")[0] else keep).append(b)
    head = (f"# AramHex 用的根证书：certifi {certifi.__version__} 的 cacert.pem 去掉 eMudhra（emSign）的根证书。\n"
            "# 2026-10 eMudhra 中间证书被攻破（CVE-2026-111943，Go 1.27.3/1.26.10、Mozilla 都已拉黑），\n"
            "# 助手只连 aramhex.com、GitHub、jsDelivr、腾讯，都不用 eMudhra 的证书。由 scripts/make_cacert.py 生成\n")
    OUT.write_text(head + "".join(keep).strip() + "\n", encoding="utf-8", newline="\n")
    print("去掉：", [re.search(r'Label: "([^"]+)"', b).group(1) for b in drop])


if __name__ == "__main__":
    main()
