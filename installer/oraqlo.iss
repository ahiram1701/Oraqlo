; Oraqlo-Setup.exe: instalador clásico de Windows (Inno Setup 6).
;
; Lo compila CI (release.yml) con:
;   iscc /DVersion=X.Y.Z /DWheel=..\dist\oraqlo-X.Y.Z-py3-none-any.whl /DUvExe=..\build\uv.exe installer\oraqlo.iss
;
; El .exe solo empaqueta y pregunta; el trabajo lo hace scripts\install-windows.ps1
; (el mismo que la instalación avanzada), con uv para traer su propio Python. Así hay
; un único camino de instalación que probar.
;
; Parámetro oculto para pruebas: /ORAQLOTEST=1 instala sin tarea programada, sin
; Tailscale y sin accesos directos (con /DIR= apuntando a una carpeta desechable).

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef Wheel
  #error "Falta /DWheel=<ruta al wheel de Oraqlo>"
#endif
#ifndef UvExe
  #define UvExe "..\build\uv.exe"
#endif
#define WheelName ExtractFileName(Wheel)

[Setup]
AppId={{A7E7756A-8E57-4F2B-8432-228884B9B54D}
AppName=Oraqlo
AppVersion={#Version}
AppVerName=Oraqlo {#Version}
AppPublisher=Alberto H. Saucedo G.
AppPublisherURL=https://github.com/ahiram1701/Oraqlo
AppSupportURL=https://github.com/ahiram1701/Oraqlo/issues
; Por usuario: sin permisos de administrador. Los datos viven en la misma carpeta.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Oraqlo
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
UsePreviousAppDir=yes
OutputDir=..\dist
OutputBaseFilename=Oraqlo-Setup-{#Version}
SetupIconFile=oraqlo.ico
UninstallDisplayIcon={app}\setup\oraqlo.ico
UninstallDisplayName=Oraqlo
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
es.FinishedLabel=Oraqlo quedó instalado y arrancará solo cada vez que inicies sesión en Windows.%n%nLo abres desde el menú Inicio (busca "Oraqlo"). Todo se configura dentro de la app, en Ajustes.

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; Flags: unchecked

[InstallDelete]
; Wheels de instalaciones anteriores.
Type: files; Name: "{app}\setup\*.whl"

[Files]
Source: "{#Wheel}"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "..\scripts\install-windows.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "{#UvExe}"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "oraqlo.ico"; DestDir: "{app}\setup"; Flags: ignoreversion

[Run]
Filename: "{code:PythonW}"; Parameters: """{app}\app\launcher.py"" --open --home ""{app}"""; \
  Description: "Abrir Oraqlo"; Flags: postinstall nowait skipifsilent; Check: CanOpen

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\setup\install-windows.ps1"" -Uninstall -OraqloHome ""{app}"""; \
  Flags: runhidden waituntilterminated; RunOnceId: "OraqloUninstall"

[Code]
var
  InstallOk: Boolean;

function IsTest: Boolean;
begin
  Result := ExpandConstant('{param:ORAQLOTEST|0}') = '1';
end;

function PythonW(Param: String): String;
var
  S: AnsiString;
begin
  Result := 'pythonw.exe';
  if LoadStringFromFile(ExpandConstant('{app}\app\pythonw.txt'), S) then
    Result := Trim(String(S));
end;

function CanOpen: Boolean;
begin
  Result := InstallOk and not IsTest;
end;

function LogTail(Path: String): String;
var
  Lines: TArrayOfString;
  I, First: Integer;
begin
  Result := '';
  if LoadStringsFromFile(Path, Lines) then
  begin
    First := GetArrayLength(Lines) - 12;
    if First < 0 then First := 0;
    for I := First to GetArrayLength(Lines) - 1 do
      Result := Result + Lines[I] + #13#10;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Args, Log: String;
  Code: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Log := ExpandConstant('{app}\setup\install.log');
  DeleteFile(Log);
  Args := '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    ExpandConstant('{app}\setup\install-windows.ps1') + '"' +
    ' -Wheel "' + ExpandConstant('{app}\setup\{#WheelName}') + '"' +
    ' -Uv "' + ExpandConstant('{app}\setup\uv.exe') + '"' +
    ' -OraqloHome "' + ExpandConstant('{app}') + '"' +
    ' -LogFile "' + Log + '"';
  if IsTest then
    Args := Args + ' -NoTask -NoTailscale'
  else
  begin
    Args := Args + ' -Shortcuts';
    if WizardIsTaskSelected('desktopicon') then
      Args := Args + ' -DesktopShortcut';
  end;
  WizardForm.StatusLabel.Caption :=
    'Preparando Oraqlo. La primera vez descarga Python, puede tardar un par de minutos...';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    InstallOk := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Args, '',
      SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
  if not InstallOk then
    SuppressibleMsgBox('No se pudo terminar de instalar Oraqlo.' + #13#10#13#10 +
      LogTail(Log) + #13#10 + 'Registro completo: ' + Log, mbCriticalError, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    // (Ninguna línea puede empezar por "#": el preprocesador la tomaría por directiva.)
    if MsgBox('¿Borrar también tus datos de Oraqlo (historial, objetivos, claves y ajustes)?' + #13#10#13#10 +
      'Si los conservas, Oraqlo los recuperará si lo vuelves a instalar.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{app}'), True, True, True);
end;
