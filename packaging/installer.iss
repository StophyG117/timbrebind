; Inno Setup script for Timbrebind. Built by build.ps1, which passes
; /DAppVersion=x.y.z from timbrebind/config.py.
;
; Timbrebind - Copyright (C) 2026 Christoph Glennon
; Licensed under the GNU GPL v3 or later.

#define AppName "Timbrebind"
#define AppExe "Timbrebind.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{358A2E5A-36C4-486C-81F9-74E891307256}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Christoph Glennon
AppCopyright=Copyright (C) 2026 Christoph Glennon
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
; Per-user install by default (no admin prompt); the dialog offers all users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\LICENSE
OutputDir=..\release
OutputBaseFilename=Timbrebind-{#AppVersion}-setup
SetupIconFile=build\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; A running tray app is closed by StopRunningApp below (AppMutex would make
; silent installs/uninstalls abort instead).
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start {#AppName} when I sign in to Windows"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Timbrebind\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Same value the app's own "Start with Windows" toggle writes.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Timbrebind"; ValueData: """{app}\{#AppExe}"""; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  AppMutexName = 'Local\Timbrebind-tray';  // held by the running tray app

// Close a running Timbrebind. Interactive runs ask first; silent runs
// just close it. Returns False if the user declined.
function StopRunningApp(Silent: Boolean): Boolean;
var
  Code, I: Integer;
begin
  Result := True;
  if not CheckForMutexes(AppMutexName) then
    exit;
  if not Silent and (MsgBox('{#AppName} is running. Close it and continue?',
                            mbConfirmation, MB_OKCANCEL) <> IDOK) then
  begin
    Result := False;
    exit;
  end;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/im {#AppExe} /f', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  for I := 1 to 20 do
  begin
    if not CheckForMutexes(AppMutexName) then
      exit;
    Sleep(100);
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := StopRunningApp(WizardSilent);
end;

function InitializeUninstall(): Boolean;
begin
  Result := StopRunningApp(UninstallSilent);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Remove the start-up entry whether the installer or the app created it.
  // Settings in %APPDATA%\Timbrebind are kept for a later reinstall.
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Timbrebind');
end;
