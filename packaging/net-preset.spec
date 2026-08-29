# -*- mode: python ; coding: utf-8 -*-
#
# Builds net-preset as one elevated executable: dist\net-preset.exe.
#
# Three decisions here are deliberate and should not be "simplified" away:
#
#   UAC_ADMIN. uac_admin=True writes requireAdministrator into the executable's
#   manifest, so Windows raises the UAC prompt before the process starts and the
#   program arrives already holding an administrator token. Changing IPv4
#   configuration is the whole point of this application, so it asks once, at
#   launch. src\net_preset\elevation.py stays for running from source, where
#   there is no manifest to carry the request; in this build it never fires.
#
#   NO CONSOLE. console=False, because this is a Tkinter application. A console
#   build would put a black window behind every launch and keep it there.
#
#   ONEFILE, UNLIKE THE SIBLING BUILD. card-wedge deliberately ships onedir; this
#   one is a single self-extracting executable, because one file that can be
#   copied onto a machine and double-clicked is the deliverable. The cost is not
#   theoretical and was measured here: a self-extracting executable is a common
#   malware shape, and with Smart App Control enforced Windows refused one build
#   of this spec at process creation -- "Zasady kontroli aplikacji zablokowaly ten
#   plik", before any window, any UAC prompt, or any code of ours ran. The next
#   build, from this same unchanged file, was admitted. Smart App Control weighs
#   each unsigned binary on its own and its verdict does not hold from one build
#   to the next, which this project's own README records. Signing is the fix;
#   until then, expect either outcome and run from source when refused.
#
# net-preset has no runtime dependencies, so none of the hidden-import or
# binary-collection machinery the sibling build needs for pyscard belongs here:
# everything the program imports is the standard library, and PyInstaller's own
# hook for tkinter finds the Tcl/Tk payload without being told.
#
# Paths are built from SPECPATH rather than written relative, because PyInstaller
# resolves the two kinds against different bases: a relative script path is taken
# against this file's directory, a relative pathex entry against whatever the
# working directory happened to be. Writing both as absolute paths off SPECPATH
# makes them agree and makes the build independent of where it was invoked from.
#
# A spec is Python, but it is Python that only PyInstaller can execute: it runs
# the file with SPECPATH, Analysis, PYZ and EXE already in the namespace, so to a
# linter every one of those is an undefined name, and the coding cookie on line 1
# is PyInstaller's own convention rather than something to modernise away. The
# two suppressions below are for exactly those, and are deliberately narrow:
# anything else this file gets wrong, ruff will still say so.
# ruff: noqa: F821, UP009

import os
import tomllib

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

REPO = os.path.abspath(os.path.join(SPECPATH, '..'))
SRC = os.path.join(REPO, 'src')

# Drawn by packaging\make_icon.py, which build.ps1 re-runs into a scratch
# directory and compares against this file before it packages anything: the
# icon is generated, so it can drift from its generator, and a build that
# shipped an icon nobody could reproduce would be the same kind of quiet
# mistake as a stale AppVersion in the installer script.
ICON = os.path.join(REPO, 'assets', 'net-preset.ico')

# The name of the artifact, which build.ps1 and packaging\net-preset.iss both
# spell out. Not read from pyproject.toml, deliberately: renaming the project
# should not silently rename the deliverable out from under the two files that
# name it.
EXE_NAME = 'net-preset'


# -- the version resource ------------------------------------------------------
#
# Without one, Properties -> Details on net-preset.exe is empty: no product
# name, no version, no description, nobody's name on it.
#
# Every value below is read out of pyproject.toml and LICENSE rather than typed
# here. That is not tidiness. packaging\net-preset.iss already has to name the
# version a second time, and build.ps1 exists in part to read it back out of the
# compiled installer and refuse a build where the two disagree; a third
# hand-maintained copy would put back exactly the drift that check was written
# to catch. A spec is Python and runs at build time, and tomllib is in the
# standard library, so there is no reason to have one.


def _project():
    """The [project] table of pyproject.toml."""
    with open(os.path.join(REPO, 'pyproject.toml'), 'rb') as handle:
        return tomllib.load(handle)['project']


def _numeric_version(version):
    """pyproject's "0.2.0" as the four numbers a Windows version resource holds.

    The resource has no room for anything but four 16-bit numbers, so a
    pre-release suffix -- "0.2.0rc1" -- contributes its digits and loses the
    rest. Nothing is lost overall: FileVersion and ProductVersion below are
    strings and carry the version exactly as pyproject.toml writes it, and
    those are what Explorer shows.

    Anything that cannot be read as a number is raised rather than guessed at.
    A version resource that quietly said 0.0.0.0 would be worse than no
    version resource at all, because it would look like an answer.
    """
    parts = version.split('.')
    if len(parts) > 4:
        raise ValueError(f'version {version!r} has more parts than a version resource holds')
    numbers = []
    for part in parts:
        digits = ''
        for character in part:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            raise ValueError(f'version {version!r} has a part that starts with no digit: {part!r}')
        number = int(digits)
        if number > 0xFFFF:
            raise ValueError(f'version {version!r} has a part too large for the resource: {part!r}')
        numbers.append(number)
    return tuple(numbers + [0] * (4 - len(numbers)))


def _copyright():
    """The copyright line LICENSE carries, so the executable cannot contradict it."""
    with open(os.path.join(REPO, 'LICENSE'), encoding='utf-8') as handle:
        for line in handle:
            if line.startswith('Copyright'):
                return line.strip()
    raise ValueError('LICENSE has no line beginning "Copyright"')


PROJECT = _project()
VERSION = PROJECT['version']

# 0409 is US English and 04B0 is 1200, the Unicode code page. The strings below
# are English, matching the rest of the repository; the window's own text is
# Polish and is not affected by anything here.
#
# One of these is more visible than the rest. FileDescription is what Windows
# puts on the UAC prompt as the program name, and this executable raises one at
# every launch by manifest, so it is the line the operator reads most often.
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_numeric_version(VERSION),
        prodvers=_numeric_version(VERSION),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    '040904B0',
                    [
                        StringStruct('CompanyName', PROJECT['authors'][0]['name']),
                        StringStruct('FileDescription', PROJECT['description']),
                        StringStruct('FileVersion', VERSION),
                        StringStruct('InternalName', PROJECT['name']),
                        StringStruct('LegalCopyright', _copyright()),
                        StringStruct('OriginalFilename', EXE_NAME + '.exe'),
                        StringStruct('ProductName', PROJECT['name']),
                        StringStruct('ProductVersion', VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)

a = Analysis(
    [os.path.join(SRC, 'net_preset', '__main__.py')],
    pathex=[SRC],
    binaries=[],
    # The same icon again, this time as a file inside the bundle rather than a
    # resource on the executable, because the two are read by different things.
    # Windows reads the resource; Tk cannot -- `wm iconbitmap` wants a path to
    # an .ico on disk -- so the window would keep its feather with only the
    # resource present. Unpacked to the root of sys._MEIPASS, which is where
    # src\net_preset\resources.py looks for it in a frozen run.
    datas=[(ICON, '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing below is imported by net-preset, and none of it is in the bundle.
    # They are named anyway because PyInstaller follows optional and conditional
    # imports through the standard library and through its own hooks, and each of
    # these is heavy enough that picking one up by accident would cost tens of
    # megabytes. pytest is the live one: it is a dev dependency, so it really is
    # installed in the environment PyInstaller analyses.
    excludes=['numpy', 'pandas', 'pytest', 'PIL'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # upx=False, deliberately, where the sibling says True. UPX is not installed
    # here, so True would be a no-op today and a silent change of artifact the
    # day anyone installs it for an unrelated reason. There is nothing to buy
    # with it: 11.6 MB against a 20 MB ceiling is not a size problem. And there
    # is something to lose, because packing is a well-known heuristic-AV trigger
    # and this binary is already the shape that gets scrutinised hardest --
    # onefile, self-extracting and unsigned -- on a machine where Smart App
    # Control is demonstrably flipping its verdict on unsigned binaries. Stacking
    # a second trigger onto a live problem buys nothing.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    # The icon in Explorer, on the taskbar, in Alt-Tab and behind the
    # installer's shortcuts. PyInstaller writes every entry of the .ico into
    # the executable's resources, so Windows picks the size it wants rather
    # than scaling one; that is what the nine sizes in make_icon.py are for.
    icon=ICON,
    # Built above, out of pyproject.toml and LICENSE. Without it Properties ->
    # Details on the executable has nothing in it at all.
    version=VERSION_INFO,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)
