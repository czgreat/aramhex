import os
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest


def test_portable_packaging_handles_literal_paths_and_preserves_previous_output(tmp_path):
    powershell = shutil.which("pwsh")
    if not powershell:
        pytest.skip("需要 PowerShell 7")

    script = Path(__file__).resolve().parents[1] / "packaging" / "package.ps1"
    dist = tmp_path / "frozen [1]"
    internal = dist / "_internal"
    internal.mkdir(parents=True)
    (dist / "AramHex.exe").write_bytes(b"placeholder executables are allowed for test builds")
    data = internal / "payload.bin"
    data.write_bytes(b"contents of the frozen directory")
    out = tmp_path / "release"
    out.mkdir()
    unrelated = out / "keep.txt"
    unrelated.write_bytes(b"keep")
    command = [
        powershell, "-NoProfile", "-NonInteractive", "-File", str(script),
        "-Dist", str(dist), "-Out", str(out), "-Version", "1.3.0-test.1", "-PortableOnly",
    ]
    env = {**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1"}

    first = subprocess.run(command, env=env, capture_output=True, timeout=60)
    assert first.returncode == 0, first.stderr
    archive = out / "AramHex-Portable-1.3.0-test.1.zip"
    with ZipFile(archive) as bundle:
        assert bundle.read("AramHex-1.3.0-test.1/_internal/payload.bin") == data.read_bytes()
    previous = archive.read_bytes()

    checksum = out / "SHA256SUMS.txt"
    checksum.unlink()
    checksum.mkdir()
    data.write_bytes(b"replacement input")
    second = subprocess.run(command, env=env, capture_output=True, timeout=60)
    assert second.returncode != 0
    assert checksum.is_dir()
    assert archive.read_bytes() == previous
    assert unrelated.read_bytes() == b"keep"
    assert not list(out.glob(".aramhex-package*"))
