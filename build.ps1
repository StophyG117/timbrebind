# Builds Timbrebind release artifacts into .\release:
#   Timbrebind-<ver>-portable-win64.zip   unzip-and-run, settings kept beside the exe
#   Timbrebind-<ver>-setup.exe            Inno Setup installer (per-user, no admin)
#
# Needs: Python 3.12 with requirements.txt + pyinstaller, and Inno Setup 6.
# Usage: .\build.ps1            (both)    .\build.ps1 -SkipInstaller

param([switch]$SkipInstaller)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Run($exe, [string[]]$argv) {
    # Windows PowerShell 5.1 turns a native tool's stderr logging into errors
    # under 'Stop'; judge success by the exit code instead.
    $ErrorActionPreference = 'Continue'
    & $exe @argv 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "$exe failed with exit code $LASTEXITCODE" }
}

Run py @('packaging\make_assets.py')
$ver = (Get-Content packaging\build\version.txt -Raw).Trim()
$pkg = Join-Path $PSScriptRoot 'packaging\build'

Run py @('-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
    '--name', 'Timbrebind',
    '--icon', "$pkg\icon.ico",
    '--version-file', "$pkg\version_info.txt",
    '--hidden-import', 'pystray._win32',
    '--distpath', 'dist', '--workpath', 'build\pyinstaller', '--specpath', 'build',
    'Timbrebind.pyw')

$app = 'dist\Timbrebind'
# sounddevice ships PortAudio for every arch, plus ASIO builds. The x64
# non-ASIO DLL is the only one loaded (we use WASAPI), so drop the rest.
Get-ChildItem $app -Recurse -Filter 'libportaudio*.dll' |
    Where-Object { $_.Name -ne 'libportaudio64bit.dll' } | Remove-Item
Copy-Item LICENSE "$app\LICENSE.txt"
Copy-Item README.md "$app\README.md"

New-Item -ItemType Directory -Force release | Out-Null

# Portable zip: same files plus the "portable" marker.
$stage = "build\portable\Timbrebind-$ver"
if (Test-Path build\portable) { Remove-Item -Recurse -Force build\portable }
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item "$app\*" $stage -Recurse
Set-Content "$stage\portable" -Encoding ascii -Value @(
    'This file makes Timbrebind portable: settings and logs are kept in the',
    '"data" folder next to Timbrebind.exe. Delete it to use %APPDATA% instead.')
$zip = "release\Timbrebind-$ver-portable-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $stage -DestinationPath $zip
Write-Host "Portable: $zip"

if (-not $SkipInstaller) {
    $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
              "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw 'Inno Setup 6 not found (winget install JRSoftware.InnoSetup)' }
    Run $iscc @('/Q', "/DAppVersion=$ver", 'packaging\installer.iss')
    Write-Host "Installer: release\Timbrebind-$ver-setup.exe"
}

Get-ChildItem release | Format-Table Name, @{n='MB'; e={[math]::Round($_.Length / 1MB, 1)}}
