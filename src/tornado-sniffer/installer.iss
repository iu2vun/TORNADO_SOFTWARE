; Inno Setup 6 - installer di Tornado Serial Sniffer (compilato da build.bat se Inno Setup e' installato)
#define AppName "Tornado Serial Sniffer"
#define AppExe "TornadoSniffer.exe"
#define AppVer "0.1.0"
[Setup]
AppId={{C47E1B90-6D2A-4A53-9F18-2B8E05D7A3C1}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Tornado Laboratori
DefaultDirName={autopf}\TornadoSniffer
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=TornadoSniffer-Setup-{#AppVer}
SetupIconFile=assets\TornadoSniffer.ico
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
Source: "dist\TornadoSniffer\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "tools\install_deps.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "installa_dipendenze.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\Manuale_TornadoSniffer.pdf"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Manuale"; Filename: "{app}\Manuale_TornadoSniffer.pdf"; Check: FileExists(ExpandConstant('{app}\Manuale_TornadoSniffer.pdf'))
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
