; Inno Setup — Sleng Uniquifier (Electron)

#define MyAppName      "Sleng Uniquifier"
#define MyAppVersion   "1.0.58"
#define MyAppPublisher "Sleng"
#define MyAppExeName   "Sleng Uniquifier.exe"
#define PackedDir      "..\..\dist\electron-packed\Sleng Uniquifier-win32-x64"

[Setup]
AppId={{B2C3D4E5-F6A7-8901-BCDE-F12345678901}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\..\dist\installer
OutputBaseFilename=SlengUniquifier_Setup_v{#MyAppVersion}
SetupIconFile=icon.ico
; lzma2/max (16 MB словник) — 90-95% від ultra64 за стисненням
; але ВТРИЧИ-ЧЕТВЕРИЧИ швидше на CI. На 463 МБ це -5..-7 хв.
; ultra64 економило ~20 МБ але робило installer 12+ хв.
; Якщо потрібен максимально малий exe для production-public release —
; можна тимчасово повернути ultra64 і запушити окремий тег.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

; --- Auto-update support ---
; CloseApplications=force — installer тихо закриє запущену стару версію.
; RestartApplications=yes — після install автоматично перезапустить її.
; Saves a manual "close app before update" prompt.
CloseApplications=force
RestartApplications=yes
CloseApplicationsFilter=*.exe,*.dll

[Languages]
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "english";   MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#PackedDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; nowait — installer не блокується запуском
; postinstall — запустити після завершення установки
; (НЕ використовуємо skipifsilent — щоб при /SILENT-update новий .exe запускався автоматом)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall
