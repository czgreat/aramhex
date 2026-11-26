# 打包：PyInstaller 生成程序目录 → GUI 自检 → package.ps1 出 Inno Setup 安装版 + 便携版 ZIP + SHA256SUMS.txt。
# 用法（PowerShell 7）：pwsh -NoProfile -File packaging/build.ps1 [-PythonExe <python>] [-InnoCompiler <ISCC.exe>] [-PortableOnly] [-TestTag test.1] [-Official]
# -Official：打官方包，lolhex 里必须先放好官方模块（不公开，见 lolhex/official.py）；缺了、坏了都不出包
# -PortableOnly：只出便携版（测试版自己用，不需要 Inno Setup）
# -TestTag：测试包一定要带，版本号变成「1.1.3-test.1」，比正式版 1.1.3 旧，正式版一发就会提示更新。
#           测试包自动附带空的 debug.txt，解压运行即开启详细日志和自动截图。
#           以前测试包和正式版同号，测过的人收不到正式版的更新提示（1.1.2 正式发布时有 32 台）
# 输出：build/release/AramHex-Setup-<版本>.exe、AramHex-Portable-<版本>.zip、SHA256SUMS.txt
param([string]$PythonExe, [string]$InnoCompiler, [switch]$PortableOnly, [string]$TestTag, [switch]$Official, [string]$Terms)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'  # 控制台不是 UTF-8 时（如 GitHub 的 Windows 构建机）输出中文也不能崩
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = if ($PythonExe) { (Get-Command $PythonExe).Source } else { Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path $py)) { throw "缺少 Python：先运行 python -m venv .venv 并安装 requirements-build.txt" }
& $py -m PyInstaller --version
if ($LASTEXITCODE) { throw '缺少 PyInstaller：pip install -r requirements-build.txt' }

# 测试标记、官方标记写进 lolhex/_build.py（不进仓库）；都不带时删掉上次留下的，免得正式包带上测试号
$buildInfo = Join-Path $root 'lolhex\_build.py'
$module = Get-ChildItem -LiteralPath (Join-Path $root 'lolhex') -Filter '_official*.pyd'
if ($Official -and @($module).Count -ne 1) { throw '-Official 需要 lolhex 里正好有一个官方模块（_official*.pyd）' }
if (-not $Official -and $module) { throw 'lolhex 里有官方模块，却没带 -Official：要么带上，要么先拿走' }
if ($Terms -and -not (Test-Path -LiteralPath $Terms)) { throw "找不到使用条款 $Terms" }
$marks = @()
if ($TestTag) {
    if ($TestTag -notmatch '^test\.\d+$') { throw '-TestTag 要写成 test.<数字>，例如 test.1' }
    $marks += "TEST_TAG = `"$TestTag`"  # packaging/build.ps1 -TestTag 生成，测试包专用"
}
if ($Official) { $marks += 'OFFICIAL = True' }
if ($marks) {
    Set-Content -LiteralPath $buildInfo -Encoding utf8 -Value $marks
} elseif (Test-Path -LiteralPath $buildInfo) {
    Remove-Item -LiteralPath $buildInfo
}
$version = (& $py -c "import pathlib,lolhex; assert pathlib.Path(lolhex.__file__).resolve() == pathlib.Path('lolhex/__init__.py').resolve(), 'Python imported lolhex outside the current checkout'; print(lolhex.__version__)").Trim()
if ($LASTEXITCODE) { throw 'Python 没有使用当前源码目录，不能打包' }
Write-Host "版本 $version"
if (-not (Test-Path (Join-Path $root 'lolhex\data\bundled\latest.json'))) {
    throw '缺少内置统计数据：先运行 python scripts/update_data.py'
}
& $py -c "from lolhex.data.public import Dataset, BUNDLED_DIR; d = Dataset(BUNDLED_DIR, 'bundled'); print('内置数据', d.patch, d.snapshot_at)"
if ($LASTEXITCODE) { throw '内置统计数据校验失败' }
if (-not $TestTag) {
    # 正式包在哪里打都要过：待审数据、外服名字表（测试包可以带待审数据，不查）
    & $py scripts\check_release_data.py
    if ($LASTEXITCODE) { throw '发版检查没通过，不能打正式包（原因见上面几行）' }
}

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
if ([bool]$gui.official -ne [bool]$Official) { throw "官方模块不对：要 $([bool]$Official)，实际 $([bool]$gui.official)" }
Write-Host "自检通过：Qt $($gui.qt)，文字识别读到「$($gui.ocr_text)」"

if ($TestTag) {
    # 放进同一份程序目录，测试安装版和便携版都默认开启调试；运行时已经支持这个标记。
    [IO.File]::WriteAllBytes((Join-Path $dist 'debug.txt'), [byte[]]@())
}

# 安装版、便携版、校验和：和正式包共用一个脚本
$package = @{ Dist = $dist; Version = $version; Out = (Join-Path $root 'build\release'); PythonExe = $py }
if ($InnoCompiler) { $package.InnoCompiler = $InnoCompiler }
if ($PortableOnly) { $package.PortableOnly = $true }
if ($Terms) { $package.Terms = $Terms }
& (Join-Path $PSScriptRoot 'package.ps1') @package
# 测试标记只给这一次打包用，打完删掉；中途失败留下的，下次不带 -TestTag 打包时开头也会删
if (Test-Path -LiteralPath $buildInfo) { Remove-Item -LiteralPath $buildInfo }
