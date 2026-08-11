; Inno Setup script for net-preset.
;
; Installs per-machine, into Program Files. This is the one place where this script
; deliberately does not follow the sibling project. card-wedge installs per-user and
; its script says why: to keep that application at the same integrity level as a
; normally-launched Excel, because a mismatch makes Windows block its synthetic
; keystrokes and no row is ever typed. That reasoning does not carry over here, it
; inverts. net-preset carries requireAdministrator in its own manifest and cannot
; change an address without an administrator token, so the operator meets a UAC
; prompt at every launch whatever this installer does. A per-machine install
; therefore costs them nothing they were not already paying, and puts one copy in
; one place for every account on the machine. Do not "fix" this back to per-user by
; analogy with the sibling; the analogy is the trap.
;
; This file is UTF-8 with a byte order mark, which is what lets the Polish message
; at the bottom keep its diacritics. The sibling's message drops them. That was a
; precaution rather than a requirement: the compiler here reads UTF-8 with or
; without the mark, and the compiled setup was checked to carry the right code
; points. The mark is kept anyway because it is the form Inno Setup documents, and
; because a script read as this machine's ANSI code page instead would turn every
; Polish letter into mojibake without failing the build. The message stays inside
; what cp1250 can express, which is every Polish letter plus the dash and the
; quotation marks used below, so no encoding on the way has to invent anything.

#define AppName "net-preset"
; Kept in step with [project] version in pyproject.toml by hand: ISCC never reads
; the Python project metadata, so nothing here can make the two agree. Bumping one
; and forgetting the other would give an installer that misreports itself in Apps &
; features and in its own uninstall entry for the rest of its life. build.ps1
; catches that afterwards, by reading the version back out of the compiled file
; and comparing it against pyproject.toml -- but only afterwards. Bump both.
#define AppVersion "0.1.0"
#define AppPublisher "Legvan"
#define AppExeName "net-preset.exe"
#define AppURL "https://github.com/Legvan/net-preset"

[Setup]
AppId={{CC4DE2CE-6280-4CE7-AD55-3CF647BF6F61}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; This directive, with {autopf} above, is the whole of the per-machine decision.
; It cannot be read back out of the compiled installer, and that is worth writing
; down so nobody loses an afternoon proving otherwise. Setup's manifest says
; asInvoker and goes on saying it whatever this line is set to, because Inno asks
; for elevation at run time instead of declaring it: Setup relaunches itself
; through ShellExecuteEx with the runas verb, the same route
; src\net_preset\elevation.py takes when the program is run from source, and the
; only route that could support a runtime choice like the sibling's
; PrivilegesRequiredOverridesAllowed. Compile this script twice with nothing
; changed but this line and the two installers' manifests match tag for tag.
;
; One warning for whoever tries it anyway. Compile with Compression=none and the
; payload is stored verbatim, so net-preset.exe's own requireAdministrator turns
; up in the search and reads like a result. It is not one -- it sits at exactly
; the offset where the application begins plus the offset of its manifest within
; it, and it is absent from an installer carrying a payload that does not ask for
; elevation. The supervised install is what settles where the files land.
PrivilegesRequired=admin
; No PrivilegesRequiredOverridesAllowed, where the sibling has =dialog. Offering
; a per-user install would contradict the paragraph at the top of this file: the
; application needs an administrator token at every launch wherever it was
; installed from, so a per-user copy would buy the operator nothing and would
; cost the one shared copy. The choice is made here, once, and not offered again.
OutputDir=..\dist
OutputBaseFilename={#AppName}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}
LicenseFile=..\LICENSE
SetupLogging=yes

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; One file, because that is what build.ps1 produces: packaging\net-preset.spec is a
; --onefile build and the executable it makes is also shipped on its own, for a USB
; stick or a machine touched once. The installer is the second delivery of that one
; artifact, not a second build of it.
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; The README earns its place next to the executable: it is where the deferred DHCP
; switch, the settings path and the Smart App Control refusal are written down, and
; an installed copy is findable when a browser is not.
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; {group} and {autodesktop} both resolve to the all-users locations here, because
; PrivilegesRequired=admin makes the "auto" constants pick the machine-wide ones.
; That is the point of installing per-machine: one entry, visible to every account.
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Launching an application from an elevated installer is usually a mistake, because
; the application inherits the installer's token and runs elevated when it had no
; business being. Here it is what happens at every launch anyway, by manifest, so
; this checkbox hands the operator the same process they would have got from the
; shortcut -- minus one UAC prompt they have already answered for the installer.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; The uninstaller removes what it installed and nothing else. %LOCALAPPDATA%\net-preset
; keeps the profiles and the remembered card, and it is left where it is: uninstalling
; to move a machine to a newer build should not cost the addresses that were typed in
; once. Deleting that folder by hand is the way to be rid of them.

[Messages]
polish.FinishedLabel=Instalacja zakończona.%n%nProfil można ustawić bez wpiętego kabla: adres, maska, brama i DNS zapisują się od razu. Windows odkłada tylko wyjście z DHCP do chwili wpięcia kabla, więc do tego czasu ipconfig pokazuje „DHCP enabled: Yes”, a karta ma obok adres 169.254.x.x. To nie jest usterka — po wpięciu kabla Windows kończy sam.%n%nLinia stanu nie odświeża się sama. Po wpięciu kabla naciśnij USTAW jeszcze raz na tym samym profilu: powtórzenie jest bezpieczne i tylko ono sprawdza ponownie, czy karta przestała być klientem DHCP.
