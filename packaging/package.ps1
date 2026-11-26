# 用现成的程序目录出安装版、便携版 ZIP 和 SHA256SUMS.txt，不重新打包程序。
# build.ps1 打完程序目录后调用它；正式包（官方模块 + 签名清单）在别处打好后也用它出安装包，
# 这样装进用户电脑的就是签过名的那份目录，一个字节都不改。
# 用法（PowerShell 7）：pwsh -NoProfile -File packaging/package.ps1 -Dist <程序目录> -Version <版本> [-PythonExe <python>] [-Out <输出目录>] [-InnoCompiler <ISCC.exe>] [-PortableOnly] [-Terms <使用条款>]
# 正式版本号（不带 -test.N）会用 scripts/check_release_data.py 检查程序目录内置的资料
# 输出：<Out>/AramHex-Setup-<版本>.exe、AramHex-Portable-<版本>.zip，更新 SHA256SUMS.txt 中对应条目（Out 默认 build/release）。
param([Parameter(Mandatory)][string]$Dist, [Parameter(Mandatory)][string]$Version, [string]$Out,
      [string]$InnoCompiler, [switch]$PortableOnly, [string]$Terms, [string]$PythonExe)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$root = Split-Path -Parent $PSScriptRoot
if ($Version -notmatch '^\d+\.\d+\.\d+(-test\.\d+)?$') { throw "版本号不对：$Version" }
$Dist = (Resolve-Path -LiteralPath $Dist).ProviderPath
$exe = Join-Path $Dist 'AramHex.exe'
$internal = Join-Path $Dist '_internal'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf) -or -not (Test-Path -LiteralPath $internal -PathType Container)) {
    throw "不是程序目录：$Dist"
}
$testBuild = $Version -match '-test\.'
$baseVersion = $Version -replace '-test\.\d+$', ''
$stamped = (Get-Item -LiteralPath $exe).VersionInfo.ProductVersion
if (($stamped -and $stamped.Trim() -ne $baseVersion) -or (-not $stamped -and -not $testBuild)) {
    throw "EXE 的版本是 '$stamped'，应为 '$baseVersion'"
}
foreach ($local in 'portable.txt', 'userdata') {
    if (Test-Path -LiteralPath (Join-Path $Dist $local)) { throw "程序目录里有 $local：这是用过的目录，不能拿来出包" }
}
if ($Terms) { $Terms = (Resolve-Path -LiteralPath $Terms).ProviderPath }
if (-not $Out) { $Out = Join-Path $root 'build\release' }
$Out = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Out)
$separator = [IO.Path]::DirectorySeparatorChar
$distPath = [IO.Path]::GetFullPath($Dist).TrimEnd([char[]]'\/') + $separator
$outPath = [IO.Path]::GetFullPath($Out).TrimEnd([char[]]'\/') + $separator
if ($distPath.StartsWith($outPath, [StringComparison]::OrdinalIgnoreCase) -or
    $outPath.StartsWith($distPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw '输出目录和程序目录不能重叠'
}
if (-not $testBuild) {
    $data = Join-Path $internal 'lolhex\data'
    $py = if ($PythonExe) { (Get-Command $PythonExe).Source } else { Join-Path $root '.venv\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $py)) { throw "缺少 Python：用 -PythonExe 指定，发版检查要用" }
    & $py (Join-Path $root 'scripts\check_release_data.py') --data $data
    if ($LASTEXITCODE) { throw '发版检查没通过，不能出正式安装包（原因见上面几行）' }
}

[IO.Directory]::CreateDirectory($Out) | Out-Null
$work = Join-Path $Out ('.aramhex-package-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($work) | Out-Null
$keepWork = $false
try {
    $setup = $null
    if (-not $PortableOnly) {
        $iscc = @($InnoCompiler, (Join-Path $root '.tools\inno\ISCC.exe'), "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
            Where-Object { $_ } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $iscc) { throw '缺少 Inno Setup 6：winget install --id JRSoftware.InnoSetup -e' }
        $defines = @("/DAppVersion=$Version", "/DDist=$Dist", "/DOutDir=$work")
        if ($Terms) { $defines += "/DTerms=$Terms" }
        & $iscc @defines (Join-Path $PSScriptRoot 'LolHex.iss')
        if ($LASTEXITCODE) { throw 'Inno Setup 编译失败' }
        $setup = "AramHex-Setup-$Version.exe"
    }

    # 便携版：同一份程序目录加 portable.txt。
    $stage = Join-Path $work "AramHex-$Version"
    [IO.Directory]::CreateDirectory($stage) | Out-Null
    foreach ($item in Get-ChildItem -Force -LiteralPath $Dist) {
        Copy-Item -LiteralPath $item.FullName -Recurse -Destination $stage -Force
    }
    Set-Content -LiteralPath (Join-Path $stage 'portable.txt') -Encoding utf8 -Value @'
这是便携版：配置、日志和下载的数据都放在本目录的 userdata 文件夹里，不写系统目录。
删除整个文件夹即可卸载。有新版本时助手会提示，请到 GitHub 下载新的便携版替换。
https://github.com/czgreat/aramhex/releases
'@
    Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination $stage
    Copy-Item -LiteralPath (Join-Path $root 'NOTICE.md') -Destination $stage
    if ($Terms) { Copy-Item -LiteralPath $Terms -Destination (Join-Path $stage '使用条款.txt') }
    $zip = "AramHex-Portable-$Version.zip"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($stage, (Join-Path $work $zip), [IO.Compression.CompressionLevel]::Optimal, $true)

    $artifacts = @($setup, $zip) | Where-Object { $_ }
    $newSums = foreach ($name in $artifacts) {
        '{0}  {1}' -f (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $work $name)).Hash.ToLowerInvariant(), $name
    }
    $checksum = 'SHA256SUMS.txt'

    # 同一个输出目录一次只更新一套产物，直到新产物齐备才开始改旧文件。
    $lock = [IO.FileStream]::new((Join-Path $Out '.aramhex-package.lock'), [IO.FileMode]::CreateNew,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 4096, [IO.FileOptions]::DeleteOnClose)
    $published = [Collections.Generic.List[string]]::new()
    $replacing = $null
    try {
        $sumsFile = Join-Path $Out $checksum
        if ([IO.Directory]::Exists($sumsFile)) { throw "校验和目标必须是文件：$sumsFile" }
        $keptSums = if ([IO.File]::Exists($sumsFile)) {
            Get-Content -LiteralPath $sumsFile | Where-Object {
                $name = ($_ -split '\s+', 2)[1]
                $name -and $name -notin $artifacts -and (Test-Path -LiteralPath (Join-Path $Out $name) -PathType Leaf)
            }
        }
        $sums = @($keptSums) + @($newSums) | Where-Object { $_ }
        Set-Content -LiteralPath (Join-Path $work $checksum) -Encoding ascii -Value $sums

        foreach ($name in @($artifacts) + $checksum) {
            $source = Join-Path $work $name
            $target = Join-Path $Out $name
            if ([IO.File]::Exists($target)) {
                $replacing = $name
                [IO.File]::Replace($source, $target, (Join-Path $work "$name.previous"))
            } else {
                [IO.File]::Move($source, $target)
            }
            $published.Add($name)
            $replacing = $null
        }
    } catch {
        $publishError = $_
        try {
            # Windows 可能已把旧文件移到备份，随后替换报错（ERROR_UNABLE_TO_MOVE_REPLACEMENT_2）。
            if ($replacing -and [IO.File]::Exists((Join-Path $work "$replacing.previous"))) {
                $published.Add($replacing)
            }
            $published.Reverse()
            foreach ($name in $published) {
                $target = Join-Path $Out $name
                $previous = Join-Path $work "$name.previous"
                if ([IO.File]::Exists($previous)) {
                    if ([IO.File]::Exists($target)) {
                        [IO.File]::Replace($previous, $target, (Join-Path $work "$name.failed"))
                    } else {
                        [IO.File]::Move($previous, $target)
                    }
                } else {
                    [IO.File]::Delete($target)
                }
            }
        } catch {
            $keepWork = $true
            throw "写入产物失败，且旧产物未能全部恢复。备份在 $work。原错误：$publishError"
        }
        throw
    } finally {
        $lock.Dispose()
    }

    foreach ($name in @($artifacts) + $checksum) {
        $file = Get-Item -LiteralPath (Join-Path $Out $name)
        '{0}  {1:N1} MB' -f $file.Name, ($file.Length / 1MB)
    }
} finally {
    if (-not $keepWork) { Remove-Item -LiteralPath $work -Recurse -Force }
}
