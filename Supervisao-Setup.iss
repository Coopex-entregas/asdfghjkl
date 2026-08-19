#define MyAppName "Supervisão"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "COOPEX"
#define MyAppExeName "Supervisao.exe"

[Setup]
AppId={{B79D936B-63F7-4A52-A3F8-5D4A51E3C026}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Supervisão
DefaultGroupName=Supervisão
DisableProgramGroupPage=yes
OutputDir=release
OutputBaseFilename=Supervisao-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=static\supervisao.ico
UninstallDisplayIcon={app}\Supervisao.exe
CloseApplications=yes
RestartApplications=no

[Files]
Source: "dist\Supervisao\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\Supervisão"; Filename: "{app}\Supervisao.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\Supervisão"; Filename: "{app}\Supervisao.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\Supervisao.exe"; Description: "Abrir Supervisão"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
