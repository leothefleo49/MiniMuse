; Inno Setup script for the MiniMuse Windows installer.
;
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=1.1.0 scripts\desktop-app\minimuse-installer.iss
;
; Builds dist/MiniMuse-<AppVersion>-win64-setup.exe from dist/MiniMuse.exe (the PyInstaller
; one-file build, scripts/desktop-app/minimuse_onefile.spec).
;
; Why a per-user install under {localappdata}\Programs\MiniMuse:
;   * that is the canonical exe path the app's self-update swaps in place
;     (nanomuse/server/selfupdate.py canonical_frozen_exe), so the updater keeps working
;     and the Windows Firewall rule / taskbar pin / shortcuts stay pinned to one path;
;   * no admin rights needed to install or to self-update.

#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

; The .iss lives in scripts/desktop-app/; everything it reads or writes is relative to the repo root.
#define RepoRoot "..\..\\"

[Setup]
AppId={{7A3C9F2E-5B41-4D6A-9E08-2F5C1D4B8A76}
AppName=MiniMuse
AppVersion={#AppVersion}
AppVerName=MiniMuse {#AppVersion}
AppPublisher=leothefleo49
AppPublisherURL=https://github.com/leothefleo49/MiniMuse
AppSupportURL=https://github.com/leothefleo49/MiniMuse/issues
AppUpdatesURL=https://github.com/leothefleo49/MiniMuse/releases/latest
DefaultDirName={localappdata}\Programs\MiniMuse
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#RepoRoot}dist
OutputBaseFilename=MiniMuse-{#AppVersion}-win64-setup
SetupIconFile={#RepoRoot}assets\brand\mm.ico
UninstallDisplayIcon={app}\MiniMuse.exe
UninstallDisplayName=MiniMuse
; The one-file runtime unpacks itself on start; keep the size honest.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
CloseApplications=no

[Files]
Source: "{#RepoRoot}dist\MiniMuse.exe"; DestDir: "{app}"; DestName: "MiniMuse.exe"; Flags: ignoreversion
Source: "{#RepoRoot}CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\MiniMuse"; Filename: "{app}\MiniMuse.exe"
Name: "{autodesktop}\MiniMuse"; Filename: "{app}\MiniMuse.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Run]
Filename: "{app}\MiniMuse.exe"; Description: "{cm:LaunchProgram,MiniMuse}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; The app runs a local server inside its own process; make sure it is not holding the exe.
Filename: "{cmd}"; Parameters: "/C taskkill /IM MiniMuse.exe /F"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
; Updates swap the exe in place, so the installed exe may differ from this installer's; remove by name.
Type: files; Name: "{app}\MiniMuse.exe"
Type: files; Name: "{app}\MiniMuse-*.exe"
Type: files; Name: "{app}\swap_error.txt"
