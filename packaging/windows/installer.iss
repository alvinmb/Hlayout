; Inno Setup script for HLayout. Built in CI (see
; .github/workflows/build-installers.yml) against the PyInstaller onedir
; build at dist/hlayout/, after that step has run:
;   iscc packaging\windows\installer.iss /DMyAppVersion=1.2.3
;
; MyAppVersion is passed in by the workflow from the git tag; it
; defaults below so the script can still be opened/compiled standalone.

#ifndef MyAppVersion
#define MyAppVersion "0.0.0"
#endif

#define MyAppName "HLayout"
#define MyAppPublisher "Alvin Brown"
; Numeric part of the version for the Windows version resource, which
; only takes digits ("0.0.0-dev12" -> "0.0.0").
#define MyAppNumVersion Copy(MyAppVersion, 1, Pos("-", MyAppVersion + "-") - 1)
#define MyAppExeName "hlayout.exe"
#define RepoRoot ".."  ; this script lives in packaging\windows

[Setup]
AppId={{9F1B9C2E-6C2C-4A9E-9C1E-2B6E1E7C6C8A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/alvinmb/Hlayout
LicenseFile={#RepoRoot}\..\LICENSE
; Product name and version on the installer's Properties > Details tab;
; code signing checks these match the signed hlayout.exe.
VersionInfoVersion={#MyAppNumVersion}
VersionInfoTextVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppNumVersion}
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoCopyright=Copyright (C) Alvin Brown. GPL-3.0.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#RepoRoot}\..\dist-installers
OutputBaseFilename=HLayout-{#MyAppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
SetupIconFile={#RepoRoot}\icons\hlayout.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The full PyInstaller onedir payload (dist/hlayout/**), built by the
; workflow immediately before this script runs.
Source: "{#RepoRoot}\..\dist\hlayout\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The licence next to hlayout.exe, where users look for it.
Source: "{#RepoRoot}\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
