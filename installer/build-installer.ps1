<#
.SYNOPSIS
    Compila dist\Oraqlo-Setup-<versión>.exe a partir del wheel que haya en dist\.
    Lo usan ci.yml (para detectar roturas) y release.yml (para publicarlo).
#>
[CmdletBinding()]
param([string]$Dist = "dist")

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path $PSScriptRoot
Set-Location $Root

# uv fijado por versión y hash: es lo que trae Python al PC del usuario.
$UvVersion = "0.13.8"
$UvSha256 = "529e78a9cec6cd7e3e014f7ecd8bda0c113969d4ed0f2efeedb85c30943325b8"

$wheel = Get-ChildItem (Join-Path $Dist "oraqlo-*.whl") | Select-Object -First 1
if (-not $wheel) { throw "No hay wheel en $Dist." }
if ($wheel.Name -notmatch "^oraqlo-(?<ver>[^-]+)-") { throw "Nombre de wheel inesperado: $($wheel.Name)" }
$version = $Matches["ver"]

$build = Join-Path $Root "build\installer"
New-Item -ItemType Directory -Force -Path $build | Out-Null
$zip = Join-Path $build "uv.zip"
Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip -UseBasicParsing
if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $UvSha256) { throw "El SHA256 de uv no coincide." }
Expand-Archive $zip $build -Force

$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    choco install innosetup -y --no-progress | Out-Host
    if (-not (Test-Path $iscc)) { throw "No se encontró ISCC.exe tras instalar Inno Setup." }
}

& $iscc "/DVersion=$version" "/DWheel=$($wheel.FullName)" "/DUvExe=$(Join-Path $build 'uv.exe')" "/O$((Resolve-Path $Dist).Path)" installer\oraqlo.iss
if ($LASTEXITCODE -ne 0) { throw "ISCC falló ($LASTEXITCODE)." }
Get-Item (Join-Path $Dist "Oraqlo-Setup-$version.exe")
