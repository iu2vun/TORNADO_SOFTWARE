; Inno Setup 6 - installer di Tornado USB Tree View (compilato da build.bat se Inno Setup e' installato)
#define AppName "Tornado USB Tree View"
#define AppExe "TornadoUSBTree.exe"
#define AppVer "0.1.0"
[Setup]
AppId={{8D05F3A6-7B1E-4C92-A4D8-16E9C2B5F073}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Tornado Laboratori
DefaultDirName={autopf}\TornadoUSBTree
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=TornadoUSBTree-Setup-{#AppVer}
SetupIconFile=assets\TornadoUSBTree.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
[Tasks]
Name: "desktopicon"; Description: "Icona sul desktop"; GroupDescription: "Icone:"
[Files]
Source: "dist\TornadoUSBTree\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "tools\install_deps.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "installa_dipendenze.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\Manuale_TornadoUSBTree.pdf"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Manuale"; Filename: "{app}\Manuale_TornadoUSBTree.pdf"; Check: FileExists(ExpandConstant('{app}\Manuale_TornadoUSBTree.pdf'))
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
