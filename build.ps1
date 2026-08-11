<#
.SYNOPSIS
    Checks the source, then builds the standalone application and its installer.

.DESCRIPTION
    Produces two artifacts in dist\:

      net-preset.exe          the whole application in a single file, which asks
                              for administrator rights once, at launch
      net-preset-setup.exe    an installer that deploys that same file into
                              Program Files, with a Start Menu entry, an
                              uninstall entry and an optional desktop shortcut

    One PyInstaller build, two ways of handing it over. The installer packages
    the executable this script has just built and just verified; it does not
    build a second one, and there is no separate portable archive, because the
    executable already is the portable form.

    Three things about this script are deliberate and should not be "simplified"
    away:

    THE CHECKS COME FIRST. uv sync, ruff and pytest run before PyInstaller, and
    any one of them failing stops the script with that tool's own exit code. A
    build script that packages a red test suite is worse than no build script,
    because the executable it hands over looks finished.

    THE MANIFEST IS VERIFIED AFTERWARDS. uac_admin=True in
    packaging\net-preset.spec is the line that makes this build worth making,
    and losing it would not look like a failure. The executable would still
    build and still work, because src\net_preset\elevation.py catches the
    unelevated case and relaunches through ShellExecute -- but that is the
    fallback meant for running from source, and it costs a second process and a
    prompt raised after the program has started rather than one prompt before it
    does. Nothing about that is visible from the outside, so the build reads the
    request back out of the finished binary and refuses to report success
    without it. The installer is read back afterwards too, though not for its
    manifest. An Inno Setup installer's manifest says asInvoker and goes on saying
    it whatever PrivilegesRequired is set to, because Setup asks for elevation at
    run time rather than declaring it: it relaunches itself through ShellExecuteEx
    with the runas verb, which is the same route src\net_preset\elevation.py takes
    when the program is run from source. Compiling the script twice with nothing
    changed but that directive gives two installers whose manifests match tag for
    tag, so there is nothing there to check. What the installer can be asked is
    its version, which Inno stamps into the file's resources from AppVersion, and
    that closes the one coupling nothing else here watches:
    packaging\net-preset.iss names the version a second time, and nothing makes it
    agree with pyproject.toml. So the version is read back out of the finished
    installer and compared against the one the project declares.

    THERE IS NO -SkipInstaller. The sibling build script has one; this one does
    not need it. The installer step is last, and the executable it packages is
    finished and on disk before that step starts, so a machine without Inno
    Setup does not lose the executable -- it loses the final step and is told
    which winget command fixes it. A switch would buy a quieter failure at the
    price of a supported way to reach the end of this script with half the
    deliverables built, which is the kind of thing this script exists to refuse.

    The executable is not code-signed, and on this hardware that makes every
    build a coin flip. Smart App Control is enforced here and judges each
    unsigned binary on its own: one build of this script was refused at process
    creation -- "Zasady kontroli aplikacji zablokowaly ten plik", before any
    window, any UAC prompt, or any code of ours ran -- and the next build, from
    the same unchanged script, was admitted and stopped instead at the elevation
    gate, which is the manifest doing its job. Rebuilding is not a workaround,
    it is a re-roll, and this project's own README records it. So a green build
    here does not promise a runnable executable, and a refused executable does
    not mean a broken build. Signing is the fix. Until then, run from source on
    any machine that refuses the binary. Neither artifact is signed and the
    installer is not the way around this: an unsigned setup executable is the
    shape Smart App Control scrutinises hardest, and the sibling project watched
    it refuse one installer while admitting the application beside it.

.EXAMPLE
    .\build.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Repo = $PSScriptRoot

function Write-Step { param([string]$T) Write-Host "`n==> $T" -ForegroundColor Cyan }
function Write-Ok { param([string]$T) Write-Host "    OK  $T" -ForegroundColor Green }
function Write-Fail { param([string]$T) Write-Host "    FAILED  $T" -ForegroundColor Red }

function Invoke-Native {
    <#
        Runs a native executable and returns its exit code.

        PowerShell 5.1 turns anything a native command writes to stderr into an
        ErrorRecord, and under $ErrorActionPreference = 'Stop' that aborts the
        script. uv, ruff, pytest and PyInstaller all write ordinary progress to
        stderr, so a perfectly good build would die on its own log output. Exit
        codes are the honest signal here.

        $LASTEXITCODE is what a native executable leaves behind; it says nothing
        about cmdlets, which report through $? instead. Every step below runs uv,
        so every step below is checked on $LASTEXITCODE.
    #>
    param([scriptblock]$Command)

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Out-Host, not the pipeline: whatever the command prints must not end up
        # concatenated with the exit code this function returns.
        & $Command | Out-Host
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

Push-Location $Repo
try {
    Write-Step 'Locating the toolchain'

    # Get-Command is a cmdlet, so there is no exit code to read; -ErrorAction
    # SilentlyContinue turns "not found" into $null instead of a terminating error.
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Write-Fail 'uv is not on PATH.'
        Write-Host '    Install it and re-run:' -ForegroundColor Red
        Write-Host '        winget install --id astral-sh.uv' -ForegroundColor White
        exit 1
    }
    Write-Ok "uv ($($uv.Source))"

    Write-Step 'Syncing the environment (uv sync)'
    $code = Invoke-Native { uv sync }
    if ($code -ne 0) { Write-Fail "uv sync exited with $code."; exit $code }
    Write-Ok 'environment is up to date'

    Write-Step 'Linting (ruff)'
    $code = Invoke-Native { uv run ruff check . }
    if ($code -ne 0) { Write-Fail "ruff exited with $code."; exit $code }
    Write-Ok 'no lint findings'

    Write-Step 'Testing (pytest)'
    $code = Invoke-Native { uv run pytest -q }
    if ($code -ne 0) { Write-Fail "pytest exited with $code."; exit $code }
    Write-Ok 'the suite is green'

    Write-Step 'Building the executable (PyInstaller, onefile)'

    # "python -m PyInstaller", not the pyinstaller command. Installing PyInstaller
    # puts an unsigned generated launcher at .venv\Scripts\pyinstaller.exe, and on
    # a machine with Smart App Control enforced that launcher can itself be
    # blocked: this build hit exactly that, uv reporting it as a failure to spawn
    # pyinstaller, "Zasady kontroli aplikacji zablokowaly ten plik". The verdict
    # is not stable -- the same launcher ran again an hour later, untouched -- so
    # it cannot be waited out or rebuilt around. Running the module through the
    # interpreter, which is signed, is the same build by a route Smart App Control
    # has no opinion about, and it is the same reason install.ps1 exists in the
    # sibling project.
    # --clean, as the sibling passes it. PyInstaller's own detection of changed
    # options is good -- it notices an edited spec and rebuilds what the edit
    # affected -- but a script whose output is meant to be shipped should not
    # depend on cache invalidation being right every time. Discarding build\ and
    # the cache first costs a few seconds and makes the artifact measured at the
    # end of this run the same one a fresh clone would produce.
    $code = Invoke-Native {
        uv run python -m PyInstaller packaging\net-preset.spec `
            --clean --noconfirm --distpath dist --workpath build
    }
    if ($code -ne 0) { Write-Fail "PyInstaller exited with $code."; exit $code }

    $exe = Join-Path $Repo 'dist\net-preset.exe'
    if (-not (Test-Path $exe)) { Write-Fail "expected $exe, but it is not there."; exit 1 }
    Write-Ok "dist\net-preset.exe ($([math]::Round((Get-Item $exe).Length / 1MB, 1)) MB)"

    Write-Step 'Reading the elevation request back out of the binary'

    # The manifest is UTF-8 XML in the executable's resource section, so it is
    # legible in a straight decode of the file. Crude, but it needs no tooling
    # beyond what every Windows machine already has.
    $text = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($exe))
    if ($text -notmatch 'requireAdministrator') {
        Write-Fail 'the embedded manifest does not ask for requireAdministrator.'
        Write-Host '    Check uac_admin=True in packaging\net-preset.spec.' -ForegroundColor Red
        exit 1
    }
    Write-Ok 'the manifest asks for requireAdministrator'

    Write-Step 'Compiling the installer (Inno Setup)'

    # Inno Setup can be installed per-user or per-machine, winget will do either,
    # and version 7 will one day sit where 6 does now. Six candidates cover both
    # scopes for both versions, newest last so an existing 6 keeps being used
    # until it is gone. Get-Command is no help: ISCC.exe is not put on PATH by
    # any of those installers.
    $iscc = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 7\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $iscc) {
        Write-Fail 'Inno Setup is not installed.'
        Write-Host '    dist\net-preset.exe is built and usable; only the installer is missing.' -ForegroundColor Red
        Write-Host '    Install Inno Setup and re-run:' -ForegroundColor Red
        Write-Host '        winget install --id JRSoftware.InnoSetup' -ForegroundColor White
        exit 1
    }
    Write-Ok "ISCC ($iscc)"

    $code = Invoke-Native { & $iscc 'packaging\net-preset.iss' | Select-Object -Last 3 }
    if ($code -ne 0) { Write-Fail "Inno Setup exited with $code."; exit $code }

    $setup = Join-Path $Repo 'dist\net-preset-setup.exe'
    if (-not (Test-Path $setup)) { Write-Fail "expected $setup, but it is not there."; exit 1 }
    Write-Ok "dist\net-preset-setup.exe ($([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB)"

    Write-Step 'Reading the version back out of the installer'

    # packaging\net-preset.iss carries the version a second time, because ISCC
    # cannot read pyproject.toml. Nothing makes the two agree, and a stale one is
    # invisible: the installer builds, installs, and then misreports itself in
    # Apps & features and in its own uninstall entry for the rest of its life.
    # Inno stamps AppVersion into the compiled file's version resource, so the
    # finished artifact can be asked what it thinks it is.
    # Anchored to the [project] table rather than taking the first version = "..."
    # in the file. [project] comes first today and is the only table carrying that
    # key, so the simpler read would work -- right up until a [tool.something]
    # table with a version of its own is added above it, at which point the check
    # would compare the installer against the wrong number and pass.
    $declared = $null
    $inProject = $false
    foreach ($line in Get-Content (Join-Path $Repo 'pyproject.toml')) {
        if ($line -match '^\s*\[') { $inProject = $line -match '^\s*\[project\]\s*$'; continue }
        if ($inProject -and $line -match '^\s*version\s*=\s*"([^"]+)"') { $declared = $Matches[1]; break }
    }
    if (-not $declared) {
        Write-Fail 'pyproject.toml has no version in its [project] table.'
        exit 1
    }
    $stamped = (Get-Item $setup).VersionInfo.ProductVersion
    if ($stamped) { $stamped = $stamped.Trim() }
    if ($stamped -ne $declared) {
        Write-Fail "the installer says $stamped, pyproject.toml says $declared."
        Write-Host '    Bring AppVersion in packaging\net-preset.iss back in step.' -ForegroundColor Red
        exit 1
    }
    Write-Ok "the installer carries version $stamped"
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host '  Done.' -ForegroundColor Green
Write-Host '  Test both artifacts before shipping them: launch dist\net-preset.exe, accept' -ForegroundColor DarkGray
Write-Host '  the UAC prompt, and confirm a window titled net-preset appears. Then run' -ForegroundColor DarkGray
Write-Host '  dist\net-preset-setup.exe, tick the desktop shortcut, and confirm it lands in' -ForegroundColor DarkGray
Write-Host '  Program Files and starts from the Start Menu.' -ForegroundColor DarkGray
Write-Host '  If either is refused instead -- "Zasady kontroli aplikacji zablokowaly ten plik" --' -ForegroundColor DarkGray
Write-Host '  that is Smart App Control declining an unsigned binary, not a broken build.' -ForegroundColor DarkGray
Write-Host ''

exit 0
