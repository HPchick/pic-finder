; PIC FINDER installer script for Inno Setup 6.
;
; Build it with installer\make-installer.ps1, not by hand. That script finds
; ISCC.exe, decides whether a Chinese language file is available, and passes
; AppVersion so the version lives in exactly one place.
;
; This file is ASCII-only on purpose. Inno Setup reads a BOM-less .iss using
; the system ANSI code page, so Chinese text placed here would be mangled on
; some machines. All Chinese text shown by the wizard comes from the language
; file, which make-installer.ps1 points at with /DChineseIsl=...
;
; Install shape (see DD-07):
;   - self contained folder, no registry keys of our own;
;   - user can change the install path;
;   - desktop shortcut and start menu entry, desktop one off by default;
;   - installed per user only, so the program directory is writable and the
;     program can keep its data next to itself. DD-07 warns about exactly that
;     case; installing per user avoids it without special handling.
;   - no privilege mode dialog and no elevation request at all. See DD-25.

#define AppName "PIC FINDER"
#define AppExe "PIC FINDER.exe"
#define AppPublisher "PIC FINDER Project"
#ifndef AppSourceDir
  #error AppSourceDir must be supplied by make-installer.ps1
#endif
#ifndef AppOutputDir
  #error AppOutputDir must be supplied by make-installer.ps1
#endif

; AppVersion is passed in by make-installer.ps1. The fallback keeps the script
; compilable on its own, but a hand build would produce a mislabelled package.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; AppId must never change: the uninstaller and upgrade detection key off it.
AppId={{8E31A4B6-6C51-4C0E-9E1E-2E0F6A1D7C42}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=
OutputDir={#AppOutputDir}
OutputBaseFilename=PIC-FINDER-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\assets\picfinder.ico
UninstallDisplayIcon={app}\{#AppExe}
; Per user only, and deliberately no PrivilegesRequiredOverridesAllowed.
;
; Two reasons, the second one learned the hard way.
;
; First, the program keeps its data next to itself. A machine wide install
; under Program Files is not writable, so the program would have to fall back
; to the user data directory. DD-07 flags that and paths.py handles it, but
; an install shape that cannot work as designed should not be offered.
;
; Second, "dialog" adds an install mode page whose "for all users" choice
; needs elevation. On 2026-10-07 the developer ran this setup on a machine
; without administrator rights, chose the administrative mode, and the setup
; failed without installing anything. The per user path needs no elevation at
; all, so the right fix is to remove the choice, not to document a workaround.
;
; With lowest, {autopf} resolves to {localappdata}\Programs, which is always
; writable by the current user.
PrivilegesRequired=lowest
DisableWelcomePage=no
AllowNoIcons=yes

[Languages]
#ifdef ChineseIsl
Name: "chinesesimplified"; MessagesFile: "{#ChineseIsl}"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Overwrite program files only. A reinstall or upgrade must never touch the
; data directory or the image library, per DD-07 and DD-20.
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; No [UninstallDelete] section on purpose.
;
; The program keeps data\ and library\ inside its own directory, so a full
; folder wipe would delete the user's downloaded images. The Inno uninstaller
; already removes every file it installed and any directory it created and
; left empty; user data stays behind. DD-07 says uninstalling deletes the
; folder, and this is a deliberate deviation: silently deleting a reference
; image library is worse than leaving a folder behind. Confirm with the
; developer before changing it.
