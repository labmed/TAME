param(
    [ValidateSet('All', 'Exe', 'Msi', 'Python')][string]$Target = 'All',
    [string]$Python = 'python',
    [string]$WixDir = '',
    [string]$BuildEnvironment = '',
    [switch]$SkipInstall,
    [switch]$SkipFrontend
)
$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$Project = Join-Path $Root 'tametools'
$VersionMatch = [regex]::Match((Get-Content (Join-Path $Project 'pyproject.toml') -Raw), '(?m)^version\s*=\s*"(\d+\.\d+\.\d+)"')
if (-not $VersionMatch.Success) { throw 'Missing project version.' }
$Version = $VersionMatch.Groups[1].Value
$Dist = Join-Path $Root "dist\$Version"
$AppDir = Join-Path $Dist 'windows-exe\tametools'
if (-not $BuildEnvironment) { $BuildEnvironment = Join-Path $Root '.build-venv' }
$BuildPython = Join-Path $BuildEnvironment 'Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = ''
$env:PYTHONNOUSERSITE = '1'

function Run([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program $Arguments" }
}
New-Item -ItemType Directory -Force $Dist | Out-Null
if ($Target -ne 'Msi') {
    if (-not (Test-Path $BuildPython)) { Run $Python @('-m', 'venv', $BuildEnvironment) }
    # Clear stale package files even when reusing dependency installations.
    $OldBuild = Join-Path $Project 'build'
    if (Test-Path $OldBuild) { Remove-Item $OldBuild -Recurse -Force }
    if (-not $SkipInstall) {
        Run $BuildPython @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel', 'build')
        Run $BuildPython @('-m', 'pip', 'install', "$Project[analysis,nhanes,integration,parquet,web,windows]", '--no-build-isolation')
    }
    if ($SkipInstall) { Run $BuildPython @('-m', 'pip', 'install', '--no-deps', '--no-build-isolation', $Project) }
    Run $BuildPython @('-m', 'pip', 'check')
    Run $BuildPython @('-c', "from tametools import __version__; assert __version__ == '$Version', __version__")
    & $BuildPython -m pip list --format=freeze | Set-Content (Join-Path $Dist 'windows-build-requirements.txt') -Encoding UTF8
}
if ($Target -in @('All', 'Python')) {
    Run $BuildPython @('-m', 'build', '--no-isolation', '--outdir', $Dist, $Project)
}
if ($Target -in @('All', 'Exe')) {
    if (-not $SkipFrontend) {
    Push-Location (Join-Path $Root 'web\frontend')
    try {
        Run 'npm.cmd' @('ci', '--no-audit', '--no-fund')
        $env:VITE_TAMETOOLS_API_BASE = ''
        Run 'npm.cmd' @('run', 'check')
        Run 'npm.cmd' @('run', 'build')
    } finally { Pop-Location }
    }
    Run $BuildPython @('-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', (Join-Path $Dist 'windows-exe'), '--workpath', (Join-Path $Root "build\pyinstaller-$Version"), (Join-Path $Root 'packaging\windows\tametools.spec'))
    $Plugins = Join-Path $AppDir 'plugins'
    New-Item -ItemType Directory -Force $Plugins | Out-Null
    Copy-Item (Join-Path $Project 'src\tametools\plugins\*.py') $Plugins -Force
    Copy-Item (Join-Path $Root 'packaging\windows\plugins\*') $Plugins -Recurse -Force
    Run $BuildPython @((Join-Path $Root 'scripts\stage_windows_audit_files.py'), $AppDir)
    Run (Join-Path $AppDir 'tametools.exe') @('--version')
    Run (Join-Path $AppDir 'tametools.exe') @('plugins')
    Run (Join-Path $AppDir 'tametools-web.exe') @('--version')
}
if ($Target -in @('All', 'Msi')) {
    $Cli = Join-Path $AppDir 'tametools.exe'
    if (-not (Test-Path $Cli) -or -not (Test-Path (Join-Path $AppDir 'tametools-web.exe'))) { throw "Build EXE first: $AppDir" }
    $ExeVersion = & $Cli --version
    if ($LASTEXITCODE -ne 0 -or "$ExeVersion".Trim() -ne "tametools $Version") { throw "EXE version does not match MSI $Version" }
    if (-not $WixDir -and (Test-Path (Join-Path $Root '.wix311\heat.exe'))) { $WixDir = Join-Path $Root '.wix311' }
    function WixTool([string]$Name) {
        if ($WixDir) { return (Join-Path $WixDir $Name) }
        return (Get-Command $Name -ErrorAction Stop).Source
    }
    $MsiWork = Join-Path $Root "build\msi-$Version"
    New-Item -ItemType Directory -Force $MsiWork | Out-Null
    Run (WixTool 'heat.exe') @('dir', $AppDir, '-cg', 'HarvestedComponents', '-dr', 'INSTALLFOLDER', '-ag', '-srd', '-sfrag', '-var', 'var.SourceDir', '-out', (Join-Path $MsiWork 'harvest.wxs'))
    Run (WixTool 'candle.exe') @('-ext', 'WixUIExtension', '-arch', 'x64', "-dSourceDir=$AppDir", "-dVersion=$Version", '-out', "$MsiWork\", (Join-Path $Root 'packaging\windows\tametools.wxs'), (Join-Path $MsiWork 'harvest.wxs'))
    Run (WixTool 'light.exe') @('-ext', 'WixUIExtension', '-cultures:ko-kr', '-out', (Join-Path $Dist "tametools-$Version-x64.msi"), (Join-Path $MsiWork 'tametools.wixobj'), (Join-Path $MsiWork 'harvest.wixobj'))
}
Write-Host "tametools $Version build complete: $Dist"
