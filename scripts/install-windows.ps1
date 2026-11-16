<#
.SYNOPSIS
    Instala (o actualiza a mano) Oraqlo en este PC Windows como servicio de usuario.

.DESCRIPTION
    - Descarga el wheel de la última GitHub Release (o usa -Wheel), verifica su SHA256
      e instala la versión en %LOCALAPPDATA%\Oraqlo\app\versions\<versión> (un venv).
    - Copia el lanzador estable (app\launcher.py) que arranca el servidor, lo vigila
      y revierte actualizaciones que no arrancan.
    - Registra la tarea programada "Oraqlo" (al iniciar sesión, sin límite de tiempo,
      también con batería) y la arranca.
    - Publica la app en tu tailnet con `tailscale serve` (HTTPS) para abrirla en el celular.
    Los datos (base SQLite, informes, backups) viven en %LOCALAPPDATA%\Oraqlo y no se tocan
    al reinstalar ni al desinstalar.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -ImportFrom C:\Github\Oraqlo
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$Version = "latest",
    [string]$Wheel,
    [int]$Port = 8765,
    [string]$OraqloHome = (Join-Path $env:LOCALAPPDATA "Oraqlo"),
    [string]$ImportFrom,
    [string]$Repo = "ahiram1701/Oraqlo",
    [switch]$NoTask,
    [switch]$NoTailscale,
    [switch]$NoStart,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "Oraqlo"
$App = Join-Path $OraqloHome "app"
$Versions = Join-Path $App "versions"

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

function Find-Tailscale {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path $default) { return $default }
    return $null
}

# ── Desinstalar ──────────────────────────────────────────────────────────
if ($Uninstall) {
    Step "Quitando la tarea programada"
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like "*$App*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $ts = Find-Tailscale
    if ($ts) {
        Step "Retirando la publicación de tailscale serve"
        & $ts serve --https=443 off 2>$null
    }
    if (Test-Path $App) { Remove-Item -Recurse -Force $App }
    Write-Host "Oraqlo desinstalado. Tus datos siguen en $OraqloHome (bórralos a mano si quieres)."
    exit 0
}

function Test-OraqloHealth([int]$P) {
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$P/api/health" -TimeoutSec 2
        if ($h.app -eq "oraqlo" -and $h.status -eq "ok") { return $h }
    } catch { }
    return $null
}

# ── Comprobaciones previas ───────────────────────────────────────────────
# Las apps empaquetadas (MSIX) redirigen en silencio lo que se escribe en
# %LOCALAPPDATA% a su almacén privado: la tarea programada no lo encontraría.
$Probe = "oraqlo-probe-" + [guid]::NewGuid()
[IO.File]::WriteAllText((Join-Path $env:LOCALAPPDATA $Probe), "")
$Virtual = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Packages\*\LocalCache\Local\$Probe") -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:LOCALAPPDATA $Probe) -Force -ErrorAction SilentlyContinue
if ($Virtual -and $OraqloHome.StartsWith($env:LOCALAPPDATA, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Esta PowerShell corre dentro de una app empaquetada y Windows redirigiría la instalación a $(Split-Path $Virtual[0].FullName). Ejecuta el instalador desde una PowerShell normal (Win+X > Terminal)."
}

# ── Puerto ───────────────────────────────────────────────────────────────
# Sin -Port explícito se conserva el de una instalación anterior. Si otra
# aplicación ocupa el puerto, se usa el siguiente libre: no hace falta elegir.
# (Luego se cambia desde Ajustes en la app; el lanzador también salta solo.)
function Test-PortBusy([int]$P) {
    $tcp = New-Object System.Net.Sockets.TcpClient
    try { return $tcp.ConnectAsync("127.0.0.1", $P).Wait(300) } catch { return $false } finally { $tcp.Dispose() }
}
$PortFile = Join-Path $App "port.txt"
if (-not $PSBoundParameters.ContainsKey("Port") -and (Test-Path $PortFile)) {
    $saved = (Get-Content $PortFile -Raw).Trim().TrimStart([char]0xFEFF)
    if ($saved -match '^\d+$') { $Port = [int]$saved }
}
if (-not (Test-OraqloHealth $Port)) {
    $Wanted = $Port
    while (Test-PortBusy $Port) {
        $Port++
        if ($Port -gt $Wanted + 100) { throw "No encontré un puerto libre entre $Wanted y $Port." }
    }
    if ($Port -ne $Wanted) { Warn "El puerto $Wanted lo usa otra aplicación: Oraqlo usará el $Port (puedes cambiarlo en Ajustes)." }
}

# ── Python 3.12+ ─────────────────────────────────────────────────────────
Step "Buscando Python 3.12 o superior"
$Python = $null
foreach ($v in @("3.13", "3.12")) {
    try {
        $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $Python = $exe.Trim(); break }
    } catch { }
}
if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $ok = & $cmd.Source -c "import sys; print(sys.version_info >= (3, 12))"
        if ($ok -eq "True") { $Python = (& $cmd.Source -c "import sys; print(sys.executable)").Trim() }
    }
}
if (-not $Python) { throw "Necesitas Python 3.12+ (https://www.python.org/downloads/ o 'winget install Python.Python.3.12')." }
$PythonW = Join-Path (Split-Path $Python) "pythonw.exe"
if (-not (Test-Path $PythonW)) { $PythonW = $Python }
Write-Host "    $Python"

# ── Obtener el wheel ─────────────────────────────────────────────────────
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("oraqlo-install-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
try {
    if ($Wheel) {
        Step "Usando el wheel local $Wheel"
        $WheelPath = (Resolve-Path $Wheel).Path
    } else {
        Step "Consultando la release '$Version' de $Repo"
        $headers = @{ "User-Agent" = "oraqlo-installer"; "Accept" = "application/vnd.github+json" }
        $url = if ($Version -eq "latest") { "https://api.github.com/repos/$Repo/releases/latest" }
               else { "https://api.github.com/repos/$Repo/releases/tags/v$($Version.TrimStart('v'))" }
        $release = Invoke-RestMethod -Uri $url -Headers $headers
        $asset = $release.assets | Where-Object { $_.name -like "*.whl" } | Select-Object -First 1
        $sums = $release.assets | Where-Object { $_.name -eq "SHA256SUMS" } | Select-Object -First 1
        if (-not $asset -or -not $sums) { throw "La release $($release.tag_name) no trae wheel y SHA256SUMS." }
        $WheelPath = Join-Path $Tmp $asset.name
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $WheelPath -Headers $headers -UseBasicParsing
        $sumsPath = Join-Path $Tmp "SHA256SUMS"
        Invoke-WebRequest -Uri $sums.browser_download_url -OutFile $sumsPath -Headers $headers -UseBasicParsing
        $expected = (Get-Content $sumsPath | Where-Object { $_ -match [regex]::Escape($asset.name) } |
                     ForEach-Object { ($_ -split "\s+")[0] }) | Select-Object -First 1
        $actual = (Get-FileHash -Algorithm SHA256 $WheelPath).Hash.ToLower()
        if (-not $expected -or $actual -ne $expected.ToLower()) { throw "El SHA256 del wheel no coincide: instalación abortada." }
        Write-Host "    SHA256 verificado"
    }
    $WheelName = Split-Path $WheelPath -Leaf
    if ($WheelName -notmatch "^oraqlo-(?<ver>[^-]+)-") { throw "Nombre de wheel inesperado: $WheelName" }
    $NewVersion = $Matches["ver"]

    # ── Instalar la versión ──────────────────────────────────────────────
    $Venv = Join-Path $Versions $NewVersion
    Step "Instalando Oraqlo $NewVersion en $Venv"
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno virtual." }
    $VenvPython = Join-Path $Venv "Scripts\python.exe"
    & $VenvPython -m pip install --disable-pip-version-check -q $WheelPath
    if ($LASTEXITCODE -ne 0) { throw "Falló la instalación del wheel." }

    Step "Copiando el lanzador estable"
    New-Item -ItemType Directory -Force -Path $App | Out-Null
    & $VenvPython -c "import shutil, oraqlo.launcher as l; shutil.copy(l.__file__, r'$App\launcher.py')"

    $CurrentFile = Join-Path $App "current.txt"
    $Current = if (Test-Path $CurrentFile) { (Get-Content $CurrentFile -Raw).Trim().TrimStart([char]0xFEFF) } else { $null }
    if ($Current -and $Current -ne $NewVersion) {
        [IO.File]::WriteAllText((Join-Path $App "previous.txt"), $Current)
        [IO.File]::WriteAllText((Join-Path $App "pending.txt"), $NewVersion)
    }
    # Sin BOM: Set-Content -Encoding UTF8 de PowerShell 5.1 lo añadiría.
    [IO.File]::WriteAllText($CurrentFile, $NewVersion)
    [IO.File]::WriteAllText($PortFile, "$Port")
    Add-Content -Path (Join-Path $App "update.log") -Value "$(Get-Date -Format s) [instalador] instalada $NewVersion"

    $env:ORAQLO_HOME = $OraqloHome
    $Oraqlo = Join-Path $Venv "Scripts\oraqlo.exe"
    if ($ImportFrom) {
        Step "Importando datos de la versión TUI desde $ImportFrom"
        & $Oraqlo import-legacy $ImportFrom
    }
    $ConfigFile = Join-Path $OraqloHome "oraqlo.toml"
    if (-not (Test-Path $ConfigFile)) {
        Warn "No hay ${ConfigFile}: Oraqlo usará valores por defecto (elige proveedor según tus claves). Mira config/oraqlo.example.toml."
    }
    & $Oraqlo doctor --preflight
}
finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}

# ── Tarea programada ─────────────────────────────────────────────────────
if (-not $NoTask) {
    Step "Registrando la tarea programada '$TaskName' (al iniciar sesión)"
    $Launcher = Join-Path $App "launcher.py"
    $action = New-ScheduledTaskAction -Execute $PythonW `
        -Argument "`"$Launcher`" --home `"$OraqloHome`" --port $Port" -WorkingDirectory $App
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description "Oraqlo: servidor web local (lanzador con auto-actualización)" -Force | Out-Null

    if (-not $NoStart) {
        Step "Arrancando Oraqlo"
        Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
            Where-Object { $_.CommandLine -like "*$App*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-ScheduledTask -TaskName $TaskName
        $healthy = $false
        foreach ($i in 1..60) {
            # El lanzador puede haber saltado de puerto si otra app lo ocupó entretanto.
            if (Test-Path $PortFile) { $Port = [int](Get-Content $PortFile -Raw).Trim().TrimStart([char]0xFEFF) }
            $h = Test-OraqloHealth $Port
            if ($h) { $healthy = $true; break }
            Start-Sleep -Seconds 1
        }
        if ($healthy) { Write-Host "    Oraqlo $($h.version) responde en http://127.0.0.1:$Port" -ForegroundColor Green }
        else { Warn "Oraqlo no respondió en 60 s. Revisa $App\server.log y $App\update.log." }
    }
}

# ── Tailscale ────────────────────────────────────────────────────────────
if (-not $NoTailscale) {
    $ts = Find-Tailscale
    if (-not $ts) {
        Warn "Tailscale no está instalado: https://tailscale.com/download (luego vuelve a ejecutar este script)."
    } else {
        Step "Publicando Oraqlo en tu tailnet (tailscale serve, HTTPS)"
        & $ts serve --bg $Port
        # Marca para que Oraqlo mantenga la publicación si alguna vez cambia de puerto.
        [IO.File]::WriteAllText((Join-Path $App "tailscale.txt"), "on")
        try {
            $status = & $ts status --json | ConvertFrom-Json
            $dns = $status.Self.DNSName.TrimEnd(".")
            Write-Host ""
            Write-Host "    Desde tu celular (con Tailscale activo): https://$dns" -ForegroundColor Green
            Write-Host "    Ábrela en Safari/Chrome y elige 'Añadir a pantalla de inicio' para instalarla."
        } catch {
            Warn "No pude leer tu nombre en el tailnet: ejecuta 'tailscale serve status'."
        }
    }
}

Write-Host ""
Write-Host "Listo. En este PC: http://127.0.0.1:$Port  ·  Datos: $OraqloHome" -ForegroundColor Green
Write-Host "El puerto se cambia en Ajustes > Conexión. Registro: $App\update.log"
Write-Host "Las actualizaciones se instalan solas (updates.mode en oraqlo.toml: auto | notify | off)."
