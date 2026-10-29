# 打包：PyInstaller 生成程序目录 → GUI 自检 → Inno Setup 安装版 + 便携版 ZIP + SHA256SUMS.txt。
# 用法（PowerShell 7）：pwsh -NoProfile -File packaging/build.ps1 [-PythonExe <python>] [-InnoCompiler <ISCC.exe>] [-PortableOnly] [-TestTag test.1]
# -PortableOnly：只出便携版（测试版自己用，不需要 Inno Setup）
# -TestTag：发到群里、局域网测试的包一定要带，版本号变成「1.1.3-test.1」，比正式版 1.1.3 旧，正式版一发就会提示更新。
#           以前测试包和正式版同号，测过的人收不到正式版的更新提示（1.1.2 正式发布时有 32 台）
# 输出：build/release/AramHex-Setup-<版本>.exe、AramHex-Portable-<版本>.zip、SHA256SUMS.txt
param([string]$PythonExe, [string]$InnoCompiler, [switch]$PortableOnly, [string]$TestTag)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'  # 控制台不是 UTF-8 时（如 GitHub 的 Windows 构建机）输出中文也不能崩
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = if ($PythonExe) { (Get-Command $PythonExe).Source } else { Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path $py)) { throw "缺少 Python：先运行 python -m venv .venv 并安装 requirements-build.txt" }
& $py -m PyInstaller --version
if ($LASTEXITCODE) { throw '缺少 PyInstaller：pip install -r requirements-build.txt' }

# 测试标记写进 lolhex/_build.py（不进仓库）；不带 -TestTag 时删掉上次留下的，免得正式包带上测试号
$buildInfo = Join-Path $root 'lolhex\_build.py'
if ($TestTag) {
    if ($TestTag -notmatch '^test\.\d+$') { throw '-TestTag 要写成 test.<数字>，例如 test.1' }
    Set-Content -LiteralPath $buildInfo -Encoding utf8 -Value "TEST_TAG = `"$TestTag`"  # packaging/build.ps1 -TestTag 生成，测试包专用"
} elseif (Test-Path -LiteralPath $buildInfo) {
    Remove-Item -LiteralPath $buildInfo
}
$version = (& $py -c "import lolhex; print(lolhex.__version__)").Trim()
Write-Host "版本 $version"
if (-not (Test-Path (Join-Path $root 'lolhex\data\bundled\latest.json'))) {
    throw '缺少内置统计数据：先运行 python scripts/update_data.py'
}
& $py -c "from lolhex.data.public import Dataset, BUNDLED_DIR; d = Dataset(BUNDLED_DIR, 'bundled'); print('内置数据', d.patch, d.snapshot_at)"
if ($LASTEXITCODE) { throw '内置统计数据校验失败' }

$originalPath = $env:PATH
$originalPythonPath = $env:PYTHONPATH
$originalPyinstallerConfig = $env:PYINSTALLER_CONFIG_DIR
try {
    $basePython = (& $py -c 'import sys; print(sys.base_prefix)').Trim()
    $env:PATH = @((Split-Path $py), $basePython, (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join ';'
    $env:PYTHONPATH = $null
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $root 'build\pyinstaller-cache'
    Push-Location packaging
    try {
        & $py -m PyInstaller lolhex.spec --clean --noconfirm --distpath ..\build\dist --workpath ..\build\work
        $code = $LASTEXITCODE
    } finally { Pop-Location }
} finally {
    $env:PATH = $originalPath
    $env:PYTHONPATH = $originalPythonPath
    $env:PYINSTALLER_CONFIG_DIR = $originalPyinstallerConfig
}
if ($code) { throw 'PyInstaller 打包失败' }
$dist = Join-Path $root 'build\dist\AramHex'

# 真实 Windows 窗口插件下跑一遍 Qt（QtCore/Gui/Widgets/Network），再跑一遍 OpenCV 和文字识别
$report = Join-Path $root 'build\gui-smoke.json'
if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report }
$smoke = Start-Process -FilePath (Join-Path $dist 'AramHex.exe') -ArgumentList @('--self-test-gui', ('"' + $report + '"')) -WindowStyle Hidden -PassThru
if (-not $smoke.WaitForExit(180000)) { $smoke.Kill(); throw '自检超时' }
if ($smoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) { throw 'Qt GUI 自检失败，不生成安装包' }
$gui = Get-Content -Raw -Encoding UTF8 -LiteralPath $report | ConvertFrom-Json
if (-not $gui.success -or $gui.version -ne $version -or $gui.platform -ne 'windows' -or -not $gui.ocr_ok) { throw "自检结果不对（界面或文字识别）：$($gui | ConvertTo-Json -Compress)" }
Write-Host "自检通过：Qt $($gui.qt)，文字识别读到「$($gui.ocr_text)」"

$setup = $null
if (-not $PortableOnly) {
    $iscc = @($InnoCompiler, (Join-Path $root '.tools\inno\ISCC.exe'), "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
              "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
        Where-Object { $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw '缺少 Inno Setup 6：winget install --id JRSoftware.InnoSetup -e' }
    & $iscc "/DAppVersion=$version" packaging\LolHex.iss
    if ($LASTEXITCODE) { throw 'Inno Setup 编译失败' }
}

$out = Join-Path $root 'build\release'
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Path $out | Out-Null
if (-not $PortableOnly) {
    $setup = "AramHex-Setup-$version.exe"
    Copy-Item (Join-Path $root "build\installer\$setup") (Join-Path $out $setup)
}

# 便携版：同一份程序目录 + portable.txt（有它时配置、日志、数据都放在程序目录的 userdata 下）
$stage = Join-Path $root "build\portable\AramHex-$version"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item -Recurse (Join-Path $dist '*') $stage
Set-Content -LiteralPath (Join-Path $stage 'portable.txt') -Encoding utf8 -Value @'
这是便携版：配置、日志和下载的数据都放在本目录的 userdata 文件夹里，不写系统目录。
删除整个文件夹即可卸载。有新版本时助手会提示，请到 GitHub 下载新的便携版替换。
https://github.com/czgreat/aramhex/releases
'@
Copy-Item (Join-Path $root 'LICENSE') $stage
$zip = "AramHex-Portable-$version.zip"
Compress-Archive -Path $stage -DestinationPath (Join-Path $out $zip) -CompressionLevel Optimal

$sums = foreach ($name in @($setup, $zip) | Where-Object { $_ }) {
    '{0}  {1}' -f (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $out $name)).Hash.ToLowerInvariant(), $name
}
Set-Content -LiteralPath (Join-Path $out 'SHA256SUMS.txt') -Encoding ascii -Value $sums
Get-ChildItem $out | ForEach-Object { '{0}  {1:N1} MB' -f $_.Name, ($_.Length / 1MB) }
# 测试标记只给这一次打包用，打完删掉；中途失败留下的，下次不带 -TestTag 打包时开头也会删
if (Test-Path -LiteralPath $buildInfo) { Remove-Item -LiteralPath $buildInfo }
