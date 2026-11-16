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
; Restos de versiones anteriores del instalador.
Type: files; Name: "{app}\setup\*.whl"
Type: files; Name: "{app}\setup\uv.exe"

[Files]
; El wheel y uv.exe solo se usan durante la instalación (se extraen a {tmp}).
Source: "{#Wheel}"; Flags: dontcopy
Source: "{#UvExe}"; Flags: dontcopy
; El script se queda: lo usa la desinstalación.
Source: "..\scripts\install-windows.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "oraqlo.ico"; DestDir: "{app}\setup"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Oraqlo"; Filename: "{code:PythonW}"; \
  Parameters: """{app}\app\launcher.py"" --open --home ""{app}"""; WorkingDir: "{app}\app"; \
  IconFilename: "{app}\setup\oraqlo.ico"; Comment: "Abrir Oraqlo"; Check: not IsTest
Name: "{userdesktop}\Oraqlo"; Filename: "{code:PythonW}"; \
  Parameters: """{app}\app\launcher.py"" --open --home ""{app}"""; WorkingDir: "{app}\app"; \
  IconFilename: "{app}\setup\oraqlo.ico"; Comment: "Abrir Oraqlo"; Tasks: desktopicon; Check: not IsTest

[Run]
Filename: "{code:PythonW}"; Parameters: """{app}\app\launcher.py"" --open --home ""{app}"""; \
  Description: "Abrir Oraqlo"; Flags: postinstall nowait skipifsilent; Check: not IsTest

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\setup\install-windows.ps1"" -Uninstall -OraqloHome ""{app}"""; \
  Flags: runhidden waituntilterminated; RunOnceId: "OraqloUninstall"

[Code]
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

function LogTail(Path: String): String;
var
  Lines: TArrayOfString;
  I, First: Integer;
begin
  Result := '';
  if LoadStringsFromFile(Path, Lines) then
  begin
    First := GetArrayLength(Lines) - 10;
    if First < 0 then First := 0;
    for I := First to GetArrayLength(Lines) - 1 do
      Result := Result + Lines[I] + #13#10;
  end;
end;

// El trabajo de verdad se hace aquí, ANTES de copiar nada: si falla, Setup se
// detiene con el error y no queda registrada una instalación a medias.
// (Ninguna línea puede empezar por "#": el preprocesador la tomaría por directiva.)
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  App, Args, Log: String;
  Code: Integer;
begin
  Result := '';
  App := WizardDirValue;
  ExtractTemporaryFile('{#WheelName}');
  ExtractTemporaryFile('uv.exe');
  ExtractTemporaryFile('install-windows.ps1');
  Log := App + '\setup\install.log';
  DeleteFile(Log);
  Args := '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    ExpandConstant('{tmp}\install-windows.ps1') + '"' +
    ' -Wheel "' + ExpandConstant('{tmp}\{#WheelName}') + '"' +
    ' -Uv "' + ExpandConstant('{tmp}\uv.exe') + '"' +
    ' -OraqloHome "' + App + '"' +
    ' -LogFile "' + Log + '"';
  if IsTest then
    Args := Args + ' -NoTask -NoTailscale';
  WizardForm.PreparingLabel.Caption :=
    'Instalando Oraqlo. La primera vez descarga Python: puede tardar un par de minutos...';
  WizardForm.PreparingLabel.Visible := True;
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Args, '',
    SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    Result := 'No se pudo instalar Oraqlo.' + #13#10#13#10 + LogTail(Log) + #13#10 +
      'Registro completo: ' + Log;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox('¿Borrar también tus datos de Oraqlo (historial, objetivos, claves y ajustes)?' + #13#10#13#10 +
      'Si los conservas, Oraqlo los recuperará si lo vuelves a instalar.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{app}'), True, True, True);
end;
