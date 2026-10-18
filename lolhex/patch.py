"""增量更新：只下载新版本里变了的几个文件（通常是程序本体和内置数据，约 10–20MB），不用重下完整安装包。

发版时 GitHub Actions 把新版本的文件清单和"会变的文件"推到本仓库的 updates 分支：
    manifest.json        {"schema_version": 1, "version", "encoding": "gzip", "files": {相对路径: {sha256, size}},
                          "patchable": [相对路径]}
    files/<相对路径>.gz   patchable 里的文件（AramHex.exe、_internal/base_library.zip、_internal/lolhex/**）的 gzip 压缩版。
                          jsDelivr 不给下 .exe（403），压缩后就可以；SHA-256 校验的是解压后的原文件。
    files/<相对路径>      原文件，给 1.0.3（只认原文件）从 GitHub 下载用
GitHub 和 jsDelivr 都能读这个分支，国内连不上 GitHub 时走 jsDelivr（单个文件 ≤20MB，整个分支 ≤150MB）。

程序这边：
1. 读清单，确认版本号就是要更新到的版本（jsDelivr 有缓存，旧清单就换下一个地址）；
2. 和本机文件逐个比 SHA-256。变了的文件都在 patchable 里就能增量更新；
   第三方库也变了（例如升级 Qt）就不行，退回下载完整安装包；
3. 下载变了的文件到程序目录下的 _update_new，逐个校验；
4. 助手退出后，由一个 PowerShell 小脚本替换文件：先把旧文件挪到 _update_old，任何一步失败就全部挪回去，
   最后重新打开助手。安装版和便携版都能用（程序目录都在用户目录下，不需要管理员权限）。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from . import GITHUB_REPO, MIRROR
from .config import program_dir

log = logging.getLogger(__name__)
BRANCH = "updates"
JSD_HOSTS = ("fastly.jsdelivr.net", "gcore.jsdelivr.net", "cdn.jsdelivr.net")


def tag_for(version: str) -> str:
    """发版时给这一版的增量更新文件打的 tag。jsDelivr 对分支名的解析会缓存好几个小时（推送后还拿到旧清单），
    按 tag 取则立即是新的，所以下载一律按 tag。"""
    return f"updates-v{version}"


def sources_for(version: str) -> list[str]:
    from . import cloud
    tag = tag_for(version)
    extra = [u.replace("{version}", version).rstrip("/") for u in cloud.mirrors("update")]
    return (extra + [f"{MIRROR}/{version}", f"https://raw.githubusercontent.com/{GITHUB_REPO}/{tag}"]
            + [f"https://{host}/gh/{GITHUB_REPO}@{tag}" for host in JSD_HOSTS]
            # 分支地址兜底（1.0.3 发布时还没有 tag）；清单版本对不上会被跳过
            + [f"https://raw.githubusercontent.com/{GITHUB_REPO}/{BRANCH}"]
            + [f"https://{host}/gh/{GITHUB_REPO}@{BRANCH}" for host in JSD_HOSTS])


SOURCES = sources_for("0")  # 只用作默认值的形状；实际按要更新到的版本取 sources_for(version)
# 会随我们发版变化的文件；其余都是第三方库（Qt、OpenCV、识别模型……），变了就要完整安装包
PATCHABLE_PREFIXES = ("AramHex.exe", "_internal/base_library.zip", "_internal/lolhex/")
STAGING, BACKUP = "_update_new", "_update_old"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{1107079E-6C48-46FD-938D-04B1C3EA9A72}_is1"
HEADERS = {"User-Agent": "aramhex"}


class NeedFullInstall(Exception):
    """这次更新不能增量完成（第三方库变了、清单拿不到等），要下载完整安装包。"""


# _internal 下的 VC++ 运行库是打包时从打包机的系统目录收进来的（1.0.0 的 msvcp140.dll 是 14.51），
# GitHub 的 Windows 构建机更新系统就会变；每个不到 1MB，也放进增量更新
VC_RUNTIME = {f"_internal/{n}" for n in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
                                         "vcruntime140.dll", "vcruntime140_1.dll")}


def is_patchable(rel: str) -> bool:
    # 第三方库的 *.dist-info/ 是安装记录（RECORD 等），每次重新 pip install 内容都会变，但不影响运行；
    # 一共只有约 0.3MB，也放进增量更新，免得因为它们就要重下完整安装包
    return (rel == PATCHABLE_PREFIXES[0] or rel == PATCHABLE_PREFIXES[1] or rel.startswith(PATCHABLE_PREFIXES[2])
            or ".dist-info/" in rel or rel.lower() in VC_RUNTIME)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: Path, version: str) -> dict:
    """发版用：扫描打包好的程序目录，生成清单。"""
    root = Path(root)
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            files[rel] = {"sha256": sha256_file(p), "size": p.stat().st_size}
    return {"schema_version": 1, "version": version, "files": files,
            "patchable": [rel for rel in files if is_patchable(rel)]}


def check_manifest(m: dict, version: str) -> dict:
    if not isinstance(m, dict) or m.get("schema_version") != 1 or m.get("version") != version:
        raise NeedFullInstall(f"更新清单不是 {version} 的")
    for rel in m.get("files") or {}:
        if rel.startswith(("/", "\\")) or ".." in rel.split("/") or rel.startswith((STAGING, BACKUP)):
            raise NeedFullInstall(f"更新清单里有不合法的路径：{rel}")
    if not set(m.get("patchable") or []) <= set(m.get("files") or {}):
        raise NeedFullInstall("更新清单不完整")
    return m


def fetch_manifest(version: str, sources=SOURCES, timeout: float = 15) -> tuple[dict, str]:
    """依次试每个地址，返回 (清单, 地址)。版本对不上（如 jsDelivr 缓存还是旧的）就换下一个。"""
    errors = []
    for base in sources:
        try:
            r = requests.get(f"{base}/manifest.json", headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return check_manifest(r.json(), version), base
        except (requests.RequestException, ValueError, NeedFullInstall) as e:
            errors.append(f"{base.split('/')[2]}：{e}")
    raise NeedFullInstall("拿不到更新清单：" + "；".join(errors))


def plan(manifest: dict, root: Path) -> tuple[list[str], list[str]]:
    """和本机文件比较，返回 (要下载的文件, 要删掉的文件)。有第三方库变了就抛 NeedFullInstall。"""
    root = Path(root)
    files, patchable = manifest["files"], set(manifest["patchable"])
    changed = []
    for rel, f in files.items():
        p = root / rel
        if p.is_file() and p.stat().st_size == f["size"] and sha256_file(p) == f["sha256"]:
            continue
        if rel not in patchable:
            raise NeedFullInstall(f"{rel} 也变了，要用完整安装包")
        changed.append(rel)
    stale = [p.relative_to(root).as_posix() for p in (root / "_internal" / "lolhex").rglob("*")
             if p.is_file() and p.relative_to(root).as_posix() not in files] if (root / "_internal" / "lolhex").is_dir() else []
    return changed, stale


def download(manifest: dict, changed: list[str], root: Path, sources=SOURCES, progress=None,
             timeout: float = 30) -> Path:
    """下载变了的文件到 <root>/_update_new，逐个校验 SHA-256。返回暂存目录。"""
    from .data import http
    staging = Path(root) / STAGING
    shutil.rmtree(staging, ignore_errors=True)
    gz = manifest.get("encoding") == "gzip"
    packed = manifest.get("packed") or {}
    # 进度按实际要下载的字节算：有压缩版就按压缩后的大小（清单里有记录时）
    share = {rel: (packed.get(rel) if gz and packed.get(rel) else manifest["files"][rel]["size"]) for rel in changed}
    total = sum(share.values())
    finished = 0  # 已经下完的文件的字节数

    def report(n_in_file):
        if progress:
            progress(min(finished + n_in_file, total), total)
    try:
        for rel in changed:
            want = manifest["files"][rel]
            body, errors = None, []
            # 优先下压缩版（.gz）：jsDelivr 不给下 .exe，压缩后也更小；没有压缩版的旧清单下原文件
            suffixes = [".gz", ""] if gz else [""]
            for base in sources:
                for suffix in suffixes:
                    got = [0]

                    def on_bytes(n):
                        got[0] += n
                        report(got[0])
                    try:
                        raw = http.fetch(f"{base}/files/{rel}{suffix}", headers=HEADERS, timeout=timeout,
                                         on_bytes=on_bytes)
                        data = gzip.decompress(raw) if suffix else raw
                        if len(data) != want["size"] or hashlib.sha256(data).hexdigest() != want["sha256"]:
                            raise ValueError("校验不对")
                        body = data
                        finished += share[rel]
                        break
                    except (requests.RequestException, ValueError, OSError, EOFError) as e:
                        errors.append(f"{base.split('/')[2]}{suffix}：{e}")
                if body is not None:
                    break
            if body is None:
                raise NeedFullInstall(f"{rel} 下载失败：" + "；".join(errors))
            out = staging / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(body)
            report(0)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def apply_script(root: Path, changed: list[str], stale: list[str], pid: int, version: str,
                 relaunch: str | None) -> str:
    """生成替换文件的 PowerShell 脚本：等助手退出 → 旧文件挪到 _update_old → 新文件挪进来 →
    删掉新版本里没有的旧文件；任何一步失败就全部挪回去。最后重新打开助手。"""
    def q(s: str) -> str:
        return "'" + str(s).replace("'", "''") + "'"
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$root = {q(Path(root))}",
        f"$new = Join-Path $root {q(STAGING)}",
        f"$old = Join-Path $root {q(BACKUP)}",
        f"$changed = @({', '.join(q(r) for r in changed)})",
        f"$stale = @({', '.join(q(r) for r in stale)})",
        # 等助手退出；30 秒还没退（例如卡在某个网络请求上）就结束它——只结束我们自己的这个进程
        f"$pid0 = {int(pid)}",
        "try { Wait-Process -Id $pid0 -Timeout 30 -ErrorAction Stop } catch { }",
        "if (Get-Process -Id $pid0 -ErrorAction SilentlyContinue) {"
        " Stop-Process -Id $pid0 -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }",
        "Start-Sleep -Milliseconds 500",
        # 文件刚释放时偶尔还被占着（杀毒软件扫描等）：每次挪文件最多重试 10 次
        "function Move-Retry($from, $to) { for ($i = 0; $i -lt 10; $i++) {"
        " try { Move-Item -LiteralPath $from $to -Force -ErrorAction Stop; return } catch { if ($i -eq 9) { throw }"
        " Start-Sleep -Seconds 1 } } }",
        "if (Test-Path $old) { Remove-Item -Recurse -Force $old }",
        "$moved = @()",
        "try {",
        "  foreach ($rel in $changed + $stale) {",
        "    $dst = Join-Path $root $rel",
        "    if (Test-Path -LiteralPath $dst) {",
        "      $bak = Join-Path $old $rel",
        "      New-Item -ItemType Directory -Force (Split-Path $bak) | Out-Null",
        "      Move-Retry $dst $bak",
        "      $moved += $rel",
        "    }",
        "  }",
        "  foreach ($rel in $changed) {",
        "    $dst = Join-Path $root $rel",
        "    New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null",
        "    Move-Retry (Join-Path $new $rel) $dst",
        "  }",
        # 「应用」列表里的版本号：现在的安装包只装给当前用户（HKCU）；早期可能选过"所有用户"（HKLM，没管理员权限改不了，忽略）
        f"  foreach ($hive in 'HKCU:\\', 'HKLM:\\') {{ try {{ $k = $hive + {q(UNINSTALL_KEY)}; "
        f"if (Test-Path $k) {{ Set-ItemProperty -Path $k -Name DisplayVersion -Value {q(version)} -ErrorAction Stop }} }} "
        "catch { } }",
        "  Remove-Item -Recurse -Force $old -ErrorAction SilentlyContinue",
        "} catch {",
        "  foreach ($rel in $changed) { $dst = Join-Path $root $rel; if (Test-Path -LiteralPath $dst) {"
        " Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue } }",
        "  foreach ($rel in $moved) { $dst = Join-Path $root $rel;"
        " New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null;"
        " Move-Item -LiteralPath (Join-Path $old $rel) $dst -Force -ErrorAction SilentlyContinue }",
        "  Set-Content -LiteralPath (Join-Path $root 'update-failed.txt') -Value $_.ToString() -Encoding UTF8",
        "}",
        "Remove-Item -Recurse -Force $new -ErrorAction SilentlyContinue",
    ]
    if relaunch:
        lines.append(f"Start-Process -FilePath {q(relaunch)} -WorkingDirectory $root")
    lines.append("Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue")
    return "\n".join(lines) + "\n"


def launch_apply(root: Path, changed: list[str], stale: list[str], version: str,
                 relaunch: str | None = None, pid: int | None = None, wait: bool = False) -> Path:
    """写出并启动替换脚本。调用后助手应当马上退出，让出文件。"""
    script = Path(tempfile.gettempdir()) / f"aramhex-apply-{version}-{os.getpid()}.ps1"
    body = apply_script(root, changed, stale, pid or os.getpid(), version, relaunch)
    script.write_text(body, encoding="utf-8-sig")  # Windows PowerShell 5.1 要 BOM 才按 UTF-8 读中文路径
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script)]
    if wait:
        subprocess.run(args, check=False, timeout=180)
    else:
        # 不能用 DETACHED_PROCESS：PowerShell 没有控制台时会直接退出。用 CREATE_NO_WINDOW 隐藏窗口，
        # 新进程组让它在助手退出后继续运行
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(args, close_fds=True, creationflags=flags, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return script


def take_failure(root: Path | None = None) -> str | None:
    """上次增量更新没装上（已还原成旧版本）时返回原因，并清理留下的临时目录；没有返回 None。"""
    root = root or install_root()
    if root is None:
        return None
    for d in (STAGING, BACKUP):
        shutil.rmtree(Path(root) / d, ignore_errors=True)
    f = Path(root) / "update-failed.txt"
    if not f.exists():
        return None
    try:
        reason = f.read_text(encoding="utf-8-sig", errors="replace").strip()
    finally:
        f.unlink(missing_ok=True)
    return reason or "未知原因"


def install_root() -> Path | None:
    """打包后的程序目录；源码运行时没有，不做增量更新。"""
    return program_dir() if getattr(sys, "frozen", False) else None


RANK_MIN_BYTES = 3 * 1024 * 1024  # 要下载的超过这么多才测速挑线路


def rank_by_speed(sources: list[str], rel: str, sample: int = 256 * 1024, timeout: float = 6) -> list[str]:
    """几条线路同时下载同一个文件的前 256KB，按速度从快到慢排。连不上的排最后。
    实测 172 上：GitHub 32KB/s，jsDelivr 的 gcore 节点 207KB/s——不能"GitHub 能连上就一直用它"。"""
    import time
    from concurrent.futures import ThreadPoolExecutor

    def probe(base):
        start = time.monotonic()
        got = 0
        try:
            with requests.get(f"{base}/files/{rel}", headers={**HEADERS, "Range": f"bytes=0-{sample - 1}"},
                              timeout=timeout, stream=True) as r:
                if r.status_code not in (200, 206):
                    return 0.0
                for chunk in r.iter_content(32 * 1024):
                    got += len(chunk)
                    if got >= sample or time.monotonic() - start > timeout:
                        break
        except requests.RequestException:
            return 0.0
        return got / max(time.monotonic() - start, 1e-3)

    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        speeds = list(pool.map(probe, sources))
    order = sorted(range(len(sources)), key=lambda i: -speeds[i])
    log.info("线路测速（KB/s）：%s", "，".join(f"{sources[i].split('/')[2]} {speeds[i] / 1024:.0f}" for i in order))
    return [sources[i] for i in order]


def prepare(version: str, progress=None, root: Path | None = None, sources=None,
            phase=None) -> tuple[list[str], list[str]]:
    """查清单、比较、下载。成功返回 (变了的文件, 要删的文件)，下一步调 launch_apply；不能增量就抛 NeedFullInstall。"""
    sources = sources or sources_for(version)
    root = root or install_root()
    if root is None:
        raise NeedFullInstall("源码运行，不做增量更新")
    say = phase or (lambda text: None)  # 界面显示当前在做什么
    say("检查更新文件…")
    manifest, base = fetch_manifest(version, sources)
    say("比对本机文件…")
    changed, stale = plan(manifest, root)
    if not changed and not stale:
        raise NeedFullInstall("本机文件已经和新版本一样")
    log.info("增量更新到 %s：%d 个文件要换（%.1f MB），清单来自 %s", version, len(changed),
             sum(manifest["files"][r]["size"] for r in changed) / 1e6, base)
    say("下载更新…")
    order = [base] + [s for s in sources if s != base]
    size = sum(manifest["files"][r]["size"] for r in changed)
    if len(order) > 1 and changed and size >= RANK_MIN_BYTES:  # 小更新直接用拿到清单的线路，测速反而更慢
        say("挑选最快的下载线路…")
        order = rank_by_speed(order, max(changed, key=lambda r: manifest["files"][r]["size"]))
    download(manifest, changed, root, order, progress)
    return changed, stale


def main(argv=None) -> int:
    """发版用：python -m lolhex.patch <程序目录> <版本> <输出目录>，生成 updates 分支的内容。"""
    argv = sys.argv[1:] if argv is None else argv
    root, version, out = Path(argv[0]), argv[1], Path(argv[2])
    m = {**build_manifest(root, version), "encoding": "gzip"}
    shutil.rmtree(out, ignore_errors=True)
    packed = 0
    for rel in m["patchable"]:
        dst = out / "files" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dst)
        gz = gzip.compress((root / rel).read_bytes(), 9, mtime=0)
        if len(gz) > 20 * 1024 * 1024:
            raise SystemExit(f"{rel} 压缩后仍超过 jsDelivr 的 20MB 上限")
        dst.with_name(dst.name + ".gz").write_bytes(gz)
        m.setdefault("packed", {})[rel] = len(gz)  # 压缩后大小：程序据此算下载进度
        packed += len(gz)
    (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                       encoding="utf-8")
    size = sum(m["files"][r]["size"] for r in m["patchable"])
    print(f"updates 分支内容：{len(m['patchable'])} 个文件，原始 {size / 1e6:.1f} MB，压缩后 {packed / 1e6:.1f} MB → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
