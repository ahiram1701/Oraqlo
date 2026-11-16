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
    [switch]$Uninstall,
    # Usados por Oraqlo-Setup.exe:
    [string]$Uv,               # uv.exe: trae su propio Python (no hace falta tenerlo instalado)
    [string]$LogFile,          # registra toda la salida (el .exe corre sin ventana)
    [switch]$Shortcuts,        # acceso directo "Oraqlo" en el menú Inicio
    [switch]$DesktopShortcut   # ... y en el escritorio
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$TaskName = "Oraqlo"
$App = Join-Path $OraqloHome "app"
$Versions = Join-Path $App "versions"
$StartMenuLink = Join-Path ([Environment]::GetFolderPath("Programs")) "Oraqlo.lnk"
$DesktopLink = Join-Path ([Environment]::GetFolderPath("Desktop")) "Oraqlo.lnk"
if ($LogFile) {
    New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
    Start-Transcript -Path $LogFile -Append | Out-Null
}

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

function Find-Tailscale {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path $default) { return $default }
    return $null
}

# Puerto HTTPS de la tailnet -> puerto local al que apunta su raíz "/".
function Get-ServeMap([string]$Ts) {
    $map = @{}
    try {
        $cfg = & $Ts serve status --json | ConvertFrom-Json
        foreach ($entry in $cfg.Web.PSObject.Properties) {
            $https = ($entry.Name -split ":")[-1]
            $proxy = $entry.Value.Handlers."/".Proxy
            if ($https -match '^\d+$' -and $proxy -match '127\.0\.0\.1:(\d+)') { $map[[int]$https] = [int]$Matches[1] }
        }
    } catch { }
    return $map
}

# Para los procesos de esta instalación y espera a que suelten sus archivos
# (Windows no deja borrar un python.exe en marcha).
function Stop-OraqloProcesses {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like "*$App*" })
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    foreach ($p in $procs) { Wait-Process -Id $p.ProcessId -Timeout 15 -ErrorAction SilentlyContinue }
}

function Remove-Tree([string]$Path) {
    foreach ($i in 1..10) {
        if (-not (Test-Path $Path)) { return }
        try { Remove-Item -Recurse -Force $Path -ErrorAction Stop; return }
        catch { if ($i -eq 10) { throw }; Start-Sleep -Seconds 1 }
    }
}

# ── Desinstalar ──────────────────────────────────────────────────────────
if ($Uninstall) {
    # Solo se toca lo que pertenece a ESTA carpeta: podría haber otra instalación
    # de Oraqlo (con la misma tarea y el mismo acceso directo) que no es nuestra.
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and ($task.Actions | Where-Object { "$($_.Arguments)" -like "*$OraqloHome*" })) {
        Step "Quitando la tarea programada"
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Stop-OraqloProcesses
    $ts = if ($NoTailscale) { $null } else { Find-Tailscale }
    $tsFile = Join-Path $App "tailscale.txt"
    $portTxt = Join-Path $App "port.txt"
    if ($ts -and (Test-Path $tsFile) -and (Test-Path $portTxt)) {
        # Solo se retira lo que apunta a ESTE Oraqlo: la raíz u otros puertos pueden
        # ser de otras apps. "on" = versiones antiguas, publicadas en la raíz (443).
        $saved = (Get-Content $tsFile -Raw).Trim().TrimStart([char]0xFEFF)
        $https = if ($saved -eq "on") { 443 } elseif ($saved -match '^\d+$') { [int]$saved } else { 0 }
        $ours = [int](Get-Content $portTxt -Raw).Trim().TrimStart([char]0xFEFF)
        if ($https -and (Get-ServeMap $ts)[$https] -eq $ours) {
            Step "Retirando la publicación de Oraqlo en la tailnet (:$https)"
            & $ts serve "--https=$https" off 2>$null
        }
    }
    foreach ($link in @($StartMenuLink, $DesktopLink)) {
        if (-not (Test-Path $link)) { continue }
        $target = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
        if ("$($target.Arguments) $($target.TargetPath)" -like "*$OraqloHome*") {
            Remove-Item $link -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Tree $App
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
$Python = $null
if ($Uv) {
    # Python propio dentro de app\python: no depende de lo que haya en el PC
    # y se borra al desinstalar.
    Step "Preparando Python (puede tardar un minuto la primera vez)"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $App "python"
    $env:UV_NO_PROGRESS = "1"
    $env:UV_CACHE_DIR = Join-Path ([IO.Path]::GetTempPath()) ("oraqlo-uv-" + [guid]::NewGuid())
    # --no-bin / --no-registry: nada fuera de la carpeta de Oraqlo (ni accesos en
    # ~/.local/bin ni registro de Windows); desinstalar lo borra todo.
    & $Uv python install 3.13 --managed-python --no-bin --no-registry
    Remove-Item -Recurse -Force $env:UV_CACHE_DIR -ErrorAction SilentlyContinue
    # Se usa el python.exe de la carpeta de la versión concreta, no el enlace
    # "cpython-3.13-..." que uv crea encima: Windows 11 puede bloquear ese enlace
    # dentro de un instalador (error 448, RedirectionGuard) y uv sale con error
    # aunque Python ya quedó completo.
    $Python = Get-ChildItem (Join-Path $env:UV_PYTHON_INSTALL_DIR "cpython-3.13.*-windows-*") -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName "python.exe" } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Python) { throw "No se pudo descargar Python (revisa tu conexión a internet)." }
} else {
    Step "Buscando Python 3.12 o superior"
}
foreach ($v in @("3.13", "3.12")) {
    if ($Python) { break }
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

    # Si el auto-updater ya instaló algo más nuevo, no se baja de versión.
    $CurrentFile = Join-Path $App "current.txt"
    $Current = if (Test-Path $CurrentFile) { (Get-Content $CurrentFile -Raw).Trim().TrimStart([char]0xFEFF) } else { $null }
    $SkipInstall = $false
    try {
        if ($Current -and (Test-Path (Join-Path $Versions $Current)) -and ([version]$Current -gt [version]$NewVersion)) {
            Write-Host "    Ya tienes Oraqlo $Current (más nuevo que $NewVersion): se conserva."
            $SkipInstall = $true
            $NewVersion = $Current
        }
    } catch { }

    # ── Instalar la versión ──────────────────────────────────────────────
    $Venv = Join-Path $Versions $NewVersion
    $VenvPython = Join-Path $Venv "Scripts\python.exe"
    if (-not $SkipInstall) {
        Step "Instalando Oraqlo $NewVersion en $Venv"
        Stop-OraqloProcesses
        Remove-Tree $Venv
        & $Python -m venv $Venv
        if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno virtual." }
        & $VenvPython -m pip install --disable-pip-version-check -q $WheelPath
        if ($LASTEXITCODE -ne 0) { throw "Falló la instalación del wheel." }
    }

    Step "Copiando el lanzador estable"
    New-Item -ItemType Directory -Force -Path $App | Out-Null
    & $VenvPython -c "import shutil, oraqlo.launcher as l; shutil.copy(l.__file__, r'$App\launcher.py')"

    if ($Current -and $Current -ne $NewVersion) {
        [IO.File]::WriteAllText((Join-Path $App "previous.txt"), $Current)
        [IO.File]::WriteAllText((Join-Path $App "pending.txt"), $NewVersion)
    }
    # Sin BOM: Set-Content -Encoding UTF8 de PowerShell 5.1 lo añadiría.
    [IO.File]::WriteAllText($CurrentFile, $NewVersion)
    [IO.File]::WriteAllText($PortFile, "$Port")
    [IO.File]::WriteAllText((Join-Path $App "pythonw.txt"), $PythonW)
    Add-Content -Path (Join-Path $App "update.log") -Value "$(Get-Date -Format s) [instalador] instalada $NewVersion"

    $env:ORAQLO_HOME = $OraqloHome
    $Oraqlo = Join-Path $Venv "Scripts\oraqlo.exe"
    if ($ImportFrom) {
        Step "Importando datos de la versión TUI desde $ImportFrom"
        & $Oraqlo import-legacy $ImportFrom
    }
    & $Oraqlo doctor --preflight
    if ($LASTEXITCODE -ne 0) { throw "La comprobación inicial de Oraqlo falló." }
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
        Stop-OraqloProcesses
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

# ── Accesos directos ─────────────────────────────────────────────────────
# Abren Oraqlo en el navegador (y lo arrancan si no estaba corriendo).
function New-OraqloShortcut([string]$Path) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($Path)
    $lnk.TargetPath = $PythonW
    $lnk.Arguments = "`"$(Join-Path $App 'launcher.py')`" --open --home `"$OraqloHome`""
    $lnk.WorkingDirectory = $App
    $lnk.Description = "Abrir Oraqlo"
    $icon = Join-Path $OraqloHome "setup\oraqlo.ico"
    if (Test-Path $icon) { $lnk.IconLocation = $icon }
    $lnk.Save()
}
if ($Shortcuts) {
    Step "Creando el acceso directo en el menú Inicio"
    New-OraqloShortcut $StartMenuLink
    if ($DesktopShortcut) { New-OraqloShortcut $DesktopLink }
}

# ── Tailscale ────────────────────────────────────────────────────────────
if (-not $NoTailscale) {
    $ts = Find-Tailscale
    if (-not $ts) {
        Warn "Tailscale no está instalado. Para usar Oraqlo en el celular, ve a Ajustes > Conexión."
    } else {
        # Puerto HTTPS propio (8443, o el siguiente libre): nunca la raíz, que puede
        # ser de otra app. Si una versión vieja ocupó la raíz con ESTE puerto, se libera.
        $map = Get-ServeMap $ts
        $tsFile = Join-Path $App "tailscale.txt"
        $saved = if (Test-Path $tsFile) { (Get-Content $tsFile -Raw).Trim().TrimStart([char]0xFEFF) } else { "" }
        if ($saved -eq "on" -and $map[443] -eq $Port) { & $ts serve --https=443 off 2>$null; $map.Remove(443) }
        $Https = 8443
        while ($map.ContainsKey($Https) -and $map[$Https] -ne $Port) { $Https++ }
        Step "Publicando Oraqlo en tu tailnet (HTTPS :$Https)"
        & $ts serve --bg "--https=$Https" "http://127.0.0.1:$Port" | Out-Host
        if ($LASTEXITCODE -eq 0) {
            # Oraqlo mantiene la publicación si alguna vez cambia de puerto local.
            [IO.File]::WriteAllText($tsFile, "$Https")
        } else {
            Warn "Tailscale no pudo publicar Oraqlo (¿sesión iniciada?). Hazlo luego desde Ajustes > Conexión."
        }
        try {
            $status = & $ts status --json | ConvertFrom-Json
            $dns = $status.Self.DNSName.TrimEnd(".")
            Write-Host ""
            Write-Host "    Desde tu celular (con Tailscale activo): https://${dns}:$Https" -ForegroundColor Green
            Write-Host "    Ábrela en Safari/Chrome y elige 'Añadir a pantalla de inicio' para instalarla."
        } catch {
            Warn "No pude leer tu nombre en el tailnet: míralo en Ajustes > Conexión."
        }
    }
}

Write-Host ""
Write-Host "Listo. En este PC: http://127.0.0.1:$Port  ·  Datos: $OraqloHome" -ForegroundColor Green
Write-Host "Todo lo demás (claves, modelos, puerto, celular, actualizaciones) se configura en Ajustes."
if ($LogFile) { Stop-Transcript | Out-Null }
