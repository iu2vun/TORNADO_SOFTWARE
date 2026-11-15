; Inno Setup 6 - installer di TornadoVNA (compilato da build.bat se Inno Setup e' installato)
#define AppName "TornadoVNA"
#define AppExe "TornadoVNA.exe"
#define AppVer "1.0.0"
[Setup]
AppId={{CC69B6CD-0BE4-4EC5-AACE-3AB1BC97F270}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Tornado Laboratori
DefaultDirName={autopf}\TornadoVNA
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=TornadoVNA-Setup-{#AppVer}
SetupIconFile=assets\TornadoVNA.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
[Tasks]
Name: "desktopicon"; Description: "Icona sul desktop"; GroupDescription: "Icone:"
[Files]
Source: "dist\TornadoVNA\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
