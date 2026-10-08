#define AppName "Arabica AI Pro"
#define AppVersion "0.2.0-dev"
[Setup]
AppId={{15ACDE63-998B-4865-95B8-648D73F7D52A}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\ArabicaAIPro
DefaultGroupName=Arabica AI Pro
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=ArabicaAIPro_Setup
Compression=lzma2
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize=1800000000
WizardStyle=modern
ArchitecturesAllowed=x64
UninstallDisplayIcon={app}\ArabicaAIPro.exe

[Files]
Source: "..\dist\ArabicaAIPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Arabica AI Pro"; Filename: "{app}\ArabicaAIPro.exe"
Name: "{userdesktop}\Arabica AI Pro"; Filename: "{app}\ArabicaAIPro.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Значок на рабочем столе"

[Run]
Filename: "{app}\ArabicaAIPro.exe"; Description: "Запустить Arabica AI Pro"; Flags: nowait postinstall skipifsilent
