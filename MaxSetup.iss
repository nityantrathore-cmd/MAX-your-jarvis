; Inno Setup script - builds the installer  installer\MaxSetup.exe
; Easiest way to use it: run build_installer.bat (it builds the two programs first).
; Change the version here:
#define AppName "Max"
#define AppVersion "2.3"

[Setup]
AppId={{705F9610-ED97-4EBD-820A-16B243E65AAA}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Max
DefaultDirName={autopf}\Max
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer
OutputBaseFilename=MaxSetup
SetupIconFile=maxAA.ico
UninstallDisplayIcon={app}\Max.exe
UninstallDisplayName=Max voice assistant
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardImageFile=installer_assets\wizard_big.bmp
WizardSmallImageFile=installer_assets\wizard_small.bmp
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcuts for Max and Max Settings"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start Max automatically when Windows starts (he will keep listening for his name)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "dist\Max.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\MaxSettings.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "maxAA.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "maxSettingsAA.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Max\Max"; Filename: "{app}\Max.exe"; IconFilename: "{app}\maxAA.ico"
Name: "{autoprograms}\Max\Max Settings"; Filename: "{app}\MaxSettings.exe"; IconFilename: "{app}\maxSettingsAA.ico"
Name: "{autoprograms}\Max\Uninstall Max"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Max"; Filename: "{app}\Max.exe"; IconFilename: "{app}\maxAA.ico"; Tasks: desktopicon
Name: "{autodesktop}\Max Settings"; Filename: "{app}\MaxSettings.exe"; IconFilename: "{app}\maxSettingsAA.ico"; Tasks: desktopicon
Name: "{userstartup}\Max"; Filename: "{app}\Max.exe"; IconFilename: "{app}\maxAA.ico"; Tasks: startup

[Run]
Filename: "{app}\Max.exe"; Description: "Start Max now"; Flags: nowait postinstall skipifsilent
Filename: "{app}\MaxSettings.exe"; Description: "Open Max Settings"; Flags: nowait postinstall skipifsilent unchecked

[Code]
procedure StopMax();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM Max.exe /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM MaxSettings.exe /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopMax();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopMax();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if not UninstallSilent then
    begin
      if MsgBox('Also delete Max''s saved settings, API key, memory, skills and log?' + #13#10 +
                '(Choose No to keep them in case you install Max again.)',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(ExpandConstant('{userappdata}\Max'), True, True, True);
    end;
  end;
end;
