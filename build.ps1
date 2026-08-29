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

    THE CHECKS COME FIRST. uv sync, ruff -- formatting and then lint -- and
    pytest run before PyInstaller, and any one of them failing stops the script
    with that tool's own exit code. A build script that packages a red test
    suite is worse than no build script, because the executable it hands over
    looks finished.

    THE ICON IS REDRAWN RATHER THAN TRUSTED. assets\net-preset.ico is generated
    by packaging\make_icon.py, so it can drift from the script that draws it in
    either direction: an edit to the generator that is never run, or an icon
    edited by hand that the next run would undo. Neither looks like a failure.
    So the icon is drawn again into build\ and compared against the committed
    file, and the build stops if they differ -- drawn to one side rather than
    over the top, because a build that dirtied the working tree every time it
    ran would cost the one signal that says whether anything really changed.

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

    TWO MORE THINGS ARE READ BACK OFF THE EXECUTABLE, and for the same reason:
    they go missing quietly. An executable with no icon and no version resource
    builds, runs, and does everything it did before -- it just looks like a
    script in Explorer and says nothing about itself in Properties -> Details.
    So the largest entry of assets\net-preset.ico is looked for in the binary's
    bytes, which says this artwork is there rather than that some icon is, and
    the six fields the Details tab shows are read off the file and required to
    say something, with the version among them compared against pyproject.toml.

    The installer is read back afterwards too, though not for its
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

function Get-DeclaredValue {
    <#
        The first capture of $Pattern inside pyproject.toml's [project] table,
        which is where every value the artifacts of this build have to agree
        with is written down. Stops the script if there is none.

        Anchored to the [project] table rather than matching anywhere in the
        file. [project] comes first today and is the only table carrying either
        of the keys read below, so the simpler read would work -- right up until
        a [tool.something] table with a version of its own is added above it, at
        which point a check would compare against the wrong value and pass.

        Line by line, so each pattern has to fit on one line. That is how both
        of them are written today, and a value spread over several lines stops
        the build here rather than being read wrongly.
    #>
    param([string]$Path, [string]$Pattern, [string]$What)

    $inProject = $false
    foreach ($line in Get-Content $Path) {
        if ($line -match '^\s*\[') { $inProject = $line -match '^\s*\[project\]\s*$'; continue }
        if ($inProject -and $line -match $Pattern) { return $Matches[1] }
    }
    Write-Fail "pyproject.toml has no $What in its [project] table."
    exit 1
}

function Get-DeclaredVersion {
    param([string]$Path)
    return Get-DeclaredValue $Path '^\s*version\s*=\s*"([^"]+)"' 'version'
}

function Get-DeclaredAuthor {
    <#
        The name packaging\net-preset.spec writes into the executable as
        CompanyName, so that the installer can be held to the same one.
    #>
    param([string]$Path)
    return Get-DeclaredValue $Path '^\s*authors\s*=.*?name\s*=\s*"([^"]+)"' 'authors entry'
}

function Get-LargestIcon {
    <#
        The image data of the biggest entry in an .ico, as a Latin-1 string.

        An icon file is a six byte header, then sixteen bytes per entry: byte
        zero of an entry is its width, with zero standing for 256 because the
        field is a single byte, and the last two fields are the length and the
        offset of the image itself. The biggest entry is picked because it is
        the one no other file on this machine is likely to hold by accident.

        Latin-1 maps every byte to the character of the same number, so a search
        over the text is a search over the bytes. UTF-8 would not: it is lossy
        for anything that is not valid UTF-8, which most of an icon is not.
    #>
    param([string]$Path)

    $bytes = [IO.File]::ReadAllBytes($Path)
    $entries = [BitConverter]::ToUInt16($bytes, 4)
    if ($entries -lt 1) { Write-Fail "$Path holds no icons at all."; exit 1 }

    $widest = 0
    $offset = 0
    $length = 0
    for ($index = 0; $index -lt $entries; $index++) {
        $at = 6 + 16 * $index
        $width = [int]$bytes[$at]
        if ($width -eq 0) { $width = 256 }
        if ($width -gt $widest) {
            $widest = $width
            $length = [int][BitConverter]::ToUInt32($bytes, $at + 8)
            $offset = [int][BitConverter]::ToUInt32($bytes, $at + 12)
        }
    }
    return [PSCustomObject]@{
        Size = $widest
        Data = [Text.Encoding]::GetEncoding(28591).GetString($bytes, $offset, $length)
    }
}

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

    Write-Step 'Checking formatting (ruff format --check)'

    # Ahead of the lint step, which is where .github\workflows\build.yml runs it
    # too: a contributor who fails this fails at the same step, with the same
    # text, that the workflow will show them. Nothing here watched formatting
    # until that workflow was written, and what it found had been sitting in two
    # files for weeks -- ruff check has nothing to say about it, and the README
    # lists ruff format as a thing to run rather than a thing that is checked.
    # A formatting difference is the one kind of red build that says nothing at
    # all about the program, so it is worth being told before pytest has run.
    $code = Invoke-Native { uv run ruff format --check . }
    if ($code -ne 0) {
        Write-Fail "ruff format --check exited with $code."
        Write-Host '    Format the tree and commit the result:' -ForegroundColor Red
        Write-Host '        uv run ruff format .' -ForegroundColor White
        exit $code
    }
    Write-Ok 'every file is formatted'

    Write-Step 'Linting (ruff)'
    $code = Invoke-Native { uv run ruff check . }
    if ($code -ne 0) { Write-Fail "ruff exited with $code."; exit $code }
    Write-Ok 'no lint findings'

    Write-Step 'Testing (pytest)'
    $code = Invoke-Native { uv run pytest -q }
    if ($code -ne 0) { Write-Fail "pytest exited with $code."; exit $code }
    Write-Ok 'the suite is green'

    Write-Step 'Redrawing the icon and comparing it with the committed one'

    # assets\net-preset.ico is generated, so it can drift from the script that
    # draws it. An edit to packaging\make_icon.py that is never run leaves a
    # repository whose icon nobody can reproduce, and an icon edited by hand
    # leaves a generator that would undo it; neither looks like a failure and
    # nothing else here would notice either.
    #
    # Redrawn into build\ rather than over the committed file. Regenerating in
    # place would leave the working tree dirty after every build, which costs
    # the one signal that says whether anything really changed.
    $committedIcon = Join-Path $Repo 'assets\net-preset.ico'
    $drawnIcon = Join-Path $Repo 'build\icon-check\net-preset.ico'
    $code = Invoke-Native { uv run python packaging\make_icon.py $drawnIcon }
    if ($code -ne 0) { Write-Fail "packaging\make_icon.py exited with $code."; exit $code }
    if (-not (Test-Path $drawnIcon)) { Write-Fail "expected $drawnIcon, but it is not there."; exit 1 }

    $drawnHash = (Get-FileHash $drawnIcon -Algorithm SHA256).Hash
    $committedHash = (Get-FileHash $committedIcon -Algorithm SHA256).Hash
    if ($drawnHash -ne $committedHash) {
        Write-Fail 'assets\net-preset.ico is not what packaging\make_icon.py draws.'
        Write-Host '    Redraw it and commit the result:' -ForegroundColor Red
        Write-Host '        uv run python packaging\make_icon.py' -ForegroundColor White
        exit 1
    }
    Write-Ok 'the committed icon is the one make_icon.py draws'

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

    Write-Step 'Reading the icon and the version resource back out of the binary'

    # The same idea as the manifest above, and for the same reason: the artifact
    # is asked what it carries rather than the spec being trusted to have put it
    # there. Both of these are quiet when they go missing -- an executable with
    # no icon and no version resource builds, runs, and does everything it did
    # before; it just looks like a script in Explorer and says nothing about
    # itself in Properties -> Details, which is the whole of what this task was.
    #
    # PyInstaller writes every entry of the .ico into the resource section
    # unchanged, so the largest one can simply be looked for in the bytes. That
    # is stronger than counting icons: it says this artwork is there, not that
    # some icon is.
    $icon = Get-LargestIcon $committedIcon
    $binary = [Text.Encoding]::GetEncoding(28591).GetString([IO.File]::ReadAllBytes($exe))
    if ($binary.IndexOf($icon.Data, [StringComparison]::Ordinal) -lt 0) {
        Write-Fail "the executable does not carry the $($icon.Size) px entry of assets\net-preset.ico."
        Write-Host '    Check icon= in packaging\net-preset.spec.' -ForegroundColor Red
        exit 1
    }
    Write-Ok "the executable carries the icon (found its $($icon.Size) px entry)"

    # Every one of these is read out of pyproject.toml and LICENSE by the spec,
    # so none of them can drift -- but a version= dropped from EXE() would empty
    # all six at once, and that is what this looks for. The version itself is
    # compared as well, because "not empty" would be satisfied by a resource
    # that had somehow been built from something else.
    $declared = Get-DeclaredVersion (Join-Path $Repo 'pyproject.toml')
    $details = (Get-Item $exe).VersionInfo
    $said = [ordered]@{
        'ProductName'     = $details.ProductName
        'FileDescription' = $details.FileDescription
        'CompanyName'     = $details.CompanyName
        'LegalCopyright'  = $details.LegalCopyright
        'FileVersion'     = $details.FileVersion
        'ProductVersion'  = $details.ProductVersion
    }
    $blank = @($said.Keys | Where-Object { -not "$($said[$_])".Trim() })
    if ($blank.Count -gt 0) {
        Write-Fail "the executable's version resource says nothing for: $($blank -join ', ')."
        Write-Host '    Check version=VERSION_INFO in packaging\net-preset.spec.' -ForegroundColor Red
        exit 1
    }
    $carried = "$($details.ProductVersion)".Trim()
    if ($carried -ne $declared) {
        Write-Fail "the executable says $carried, pyproject.toml says $declared."
        exit 1
    }
    Write-Ok "it calls itself $($said.ProductName) $carried, by $($said.CompanyName)"

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

    Write-Step 'Reading the icon and the version back out of the installer'

    # The installer's own icon goes missing exactly as quietly as the
    # executable's: drop SetupIconFile and Inno substitutes its built-in icon,
    # the compile still succeeds, and nothing says so. This is the artifact the
    # operator double-clicks first, so it is checked the same way -- the same
    # 256 px entry, looked for in the same bytes.
    #
    # Inno stores the payload compressed, so a match here is the icon Inno put
    # in the setup program's own resources and not the copy of net-preset.exe
    # inside it.
    $setupBinary = [Text.Encoding]::GetEncoding(28591).GetString([IO.File]::ReadAllBytes($setup))
    if ($setupBinary.IndexOf($icon.Data, [StringComparison]::Ordinal) -lt 0) {
        Write-Fail "the installer does not carry the $($icon.Size) px entry of assets\net-preset.ico."
        Write-Host '    Check SetupIconFile in packaging\net-preset.iss.' -ForegroundColor Red
        exit 1
    }
    Write-Ok "the installer carries the icon (found its $($icon.Size) px entry)"

    # packaging\net-preset.iss carries the version a second time, because ISCC
    # cannot read pyproject.toml. Nothing makes the two agree, and a stale one is
    # invisible: the installer builds, installs, and then misreports itself in
    # Apps & features and in its own uninstall entry for the rest of its life.
    # Inno stamps AppVersion into the compiled file's version resource, so the
    # finished artifact can be asked what it thinks it is. Unlike the executable
    # checked above, whose version the spec reads straight out of pyproject.toml,
    # this one really is a second hand-maintained copy, and this is the only
    # thing that watches it.
    $declared = Get-DeclaredVersion (Join-Path $Repo 'pyproject.toml')
    $stampedInfo = (Get-Item $setup).VersionInfo
    $stamped = "$($stampedInfo.ProductVersion)".Trim()
    if ($stamped -ne $declared) {
        Write-Fail "the installer says $stamped, pyproject.toml says $declared."
        Write-Host '    Bring AppVersion in packaging\net-preset.iss back in step.' -ForegroundColor Red
        exit 1
    }
    Write-Ok "the installer carries version $stamped"

    # AppPublisher is the other hand-maintained copy in that file, and the same
    # argument applies to it: ISCC cannot read pyproject.toml, so nothing makes
    # the two agree. Inno documents AppPublisher as the default for
    # VersionInfoCompany, which puts it in the compiled file's version resource
    # beside the version already read above -- so the finished artifact can be
    # asked this too, and the file is open either way.
    $publisher = "$($stampedInfo.CompanyName)".Trim()
    $author = Get-DeclaredAuthor (Join-Path $Repo 'pyproject.toml')
    if ($publisher -ne $author) {
        Write-Fail "the installer says $publisher, pyproject.toml says $author."
        Write-Host '    Bring AppPublisher in packaging\net-preset.iss back in step.' -ForegroundColor Red
        exit 1
    }
    Write-Ok "the installer names $publisher as the publisher"
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host '  Done.' -ForegroundColor Green
Write-Host '  Test both artifacts before shipping them: launch dist\net-preset.exe, accept' -ForegroundColor DarkGray
Write-Host '  the UAC prompt, and confirm a window titled net-preset appears, with the plug' -ForegroundColor DarkGray
Write-Host '  in its title bar rather than Tk''s feather. Then run dist\net-preset-setup.exe,' -ForegroundColor DarkGray
Write-Host '  tick the desktop shortcut, and confirm it lands in Program Files and starts' -ForegroundColor DarkGray
Write-Host '  from the Start Menu with the same icon on the shortcut.' -ForegroundColor DarkGray
Write-Host '  If either is refused instead -- "Zasady kontroli aplikacji zablokowaly ten plik" --' -ForegroundColor DarkGray
Write-Host '  that is Smart App Control declining an unsigned binary, not a broken build.' -ForegroundColor DarkGray
Write-Host ''

exit 0
