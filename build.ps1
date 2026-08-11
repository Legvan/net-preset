<#
.SYNOPSIS
    Checks the source, then builds the standalone application.

.DESCRIPTION
    Produces one artifact in dist\:

      net-preset.exe    the whole application in a single file, which asks for
                        administrator rights once, at launch

    Two things about this script are deliberate and should not be "simplified"
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
    without it.

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
    any machine that refuses the binary.

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
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host '  Done.' -ForegroundColor Green
Write-Host '  Test the build before shipping it: launch dist\net-preset.exe, accept the' -ForegroundColor DarkGray
Write-Host '  UAC prompt, and confirm a window titled net-preset appears.' -ForegroundColor DarkGray
Write-Host '  If it is refused instead -- "Zasady kontroli aplikacji zablokowaly ten plik" --' -ForegroundColor DarkGray
Write-Host '  that is Smart App Control declining an unsigned binary, not a broken build.' -ForegroundColor DarkGray
Write-Host ''

exit 0
