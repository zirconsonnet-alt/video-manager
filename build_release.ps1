param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistRoot = Join-Path $ProjectRoot "dist"
$BuildRoot = Join-Path $ProjectRoot "build"
$AppName = "VideoManager"
$PackageName = "video-manager-v$Version-windows"
$PackageDir = Join-Path $DistRoot $PackageName
$ZipPath = Join-Path $DistRoot "$PackageName.zip"

Set-Location $ProjectRoot

if (Test-Path $PackageDir) {
    Remove-Item -LiteralPath $PackageDir -Recurse -Force
}

if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name $AppName `
    --distpath $DistRoot `
    --workpath $BuildRoot `
    app.py

$GeneratedDir = Join-Path $DistRoot $AppName
if (-not (Test-Path $GeneratedDir)) {
    throw "PyInstaller did not create expected output: $GeneratedDir"
}

Move-Item -LiteralPath $GeneratedDir -Destination $PackageDir

Copy-Item -LiteralPath (Join-Path $ProjectRoot "README.md") -Destination $PackageDir
Copy-Item -LiteralPath (Join-Path $ProjectRoot "credential.example.json") -Destination $PackageDir

$ReleaseNotes = @"
# Video Manager v$Version

## How to start

1. Install ffmpeg and make sure it is available in PATH.
2. Run VideoManager.exe.
3. Optional: copy credential.example.json to credential.json and fill in Bilibili cookies for higher quality downloads.

## Notes

- app_config.json, credential.json, and video_manager_error.log are read and written next to VideoManager.exe.
- The download data directory is also created next to VideoManager.exe by default.
"@

Set-Content -LiteralPath (Join-Path $PackageDir "RELEASE_NOTES.md") -Value $ReleaseNotes -Encoding UTF8

Compress-Archive -LiteralPath $PackageDir -DestinationPath $ZipPath -Force

Write-Host "Built $ZipPath"
