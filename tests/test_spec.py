r"""What packaging\net-preset.spec asks PyInstaller for.

The spec is Python, but it is Python only PyInstaller can run: it arrives with
SPECPATH, Analysis, PYZ and EXE already in its namespace. Supplying those four
is the whole of what it takes to run the real file here and read back what EXE
was handed, which is the only way to check the version resource and the icon
without building a twelve megabyte executable in the middle of the unit suite.

The values are compared against pyproject.toml and LICENSE read here, not
against constants written down again -- the point of the spec reading them at
build time is that there is one copy of each, and a test with a second copy
would be the same mistake in a different file.
"""

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "packaging" / "net-preset.spec"
ICON = REPO / "assets" / "net-preset.ico"


class Stub:
    """Stands in for a PyInstaller object the spec passes on to another one."""

    def __getattr__(self, name: str) -> str:
        return f"<{name}>"


class Call:
    """Records how the spec called one of PyInstaller's four names."""

    def __init__(self) -> None:
        self.args: tuple = ()
        self.kwargs: dict = {}
        self.count = 0

    def __call__(self, *args, **kwargs) -> Stub:
        self.args, self.kwargs = args, kwargs
        self.count += 1
        return Stub()


@pytest.fixture(scope="module")
def spec() -> dict:
    """The spec, run with PyInstaller's four names replaced by recorders."""
    namespace = {
        "SPECPATH": str(SPEC.parent),
        "Analysis": Call(),
        "PYZ": Call(),
        "EXE": Call(),
        "__file__": str(SPEC),
    }
    # Compiled from bytes, not text: the coding cookie PyInstaller puts on line
    # one of every spec is a syntax error in a str passed to compile().
    exec(compile(SPEC.read_bytes(), str(SPEC), "exec"), namespace)
    return namespace


@pytest.fixture(scope="module")
def project() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def fields(spec: dict) -> dict[str, str]:
    """The string table of the version resource EXE was given, as a plain dict."""
    tables = spec["EXE"].kwargs["version"].kids[0].kids
    assert len(tables) == 1, "one string table, in one language"
    return {entry.name: entry.val for entry in tables[0].kids}


def numbers(spec: dict) -> tuple[int, int, int, int]:
    """The four-part file version, unpacked out of the two DWORDs it is stored in.

    FixedFileInfo does not keep the tuple it was given: it packs each pair into
    a DWORD there and then, and those DWORDs are what end up in the binary. So
    this reads what will actually be written rather than what was passed in.
    """
    info = spec["EXE"].kwargs["version"].ffi
    return (
        info.fileVersionMS >> 16,
        info.fileVersionMS & 0xFFFF,
        info.fileVersionLS >> 16,
        info.fileVersionLS & 0xFFFF,
    )


# -- the four-part conversion --------------------------------------------------


def test_a_three_part_version_is_padded_to_four(spec):
    assert spec["_numeric_version"]("0.2.0") == (0, 2, 0, 0)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1", (1, 0, 0, 0)),
        ("1.2", (1, 2, 0, 0)),
        ("1.2.3", (1, 2, 3, 0)),
        ("1.2.3.4", (1, 2, 3, 4)),
        ("10.20.30", (10, 20, 30, 0)),
        # A pre-release keeps its digits and loses the rest; the string fields
        # carry the whole thing, so nothing is lost from the executable.
        ("0.2.0rc1", (0, 2, 0, 0)),
    ],
)
def test_the_version_tuple_is_the_numbers_pyproject_wrote(spec, version, expected):
    assert spec["_numeric_version"](version) == expected


@pytest.mark.parametrize("version", ["", "x.y.z", "1.beta.0", "1.2.3.4.5", "70000.1.0"])
def test_a_version_that_cannot_be_four_numbers_stops_the_build(spec, version):
    """Loudly, rather than as a quiet 0.0.0.0 that would look like an answer."""
    with pytest.raises(ValueError):
        spec["_numeric_version"](version)


# -- what reaches the executable -----------------------------------------------


def test_the_version_resource_carries_the_version_pyproject_declares(spec, project):
    declared = project["version"]
    assert numbers(spec) == spec["_numeric_version"](declared)
    assert fields(spec)["FileVersion"] == declared
    assert fields(spec)["ProductVersion"] == declared


def test_the_product_version_is_stored_as_well_as_the_file_version(spec):
    """Explorer shows one of each, and a build that filled in half would look done."""
    info = spec["EXE"].kwargs["version"].ffi
    assert (info.productVersionMS, info.productVersionLS) == (
        info.fileVersionMS,
        info.fileVersionLS,
    )


def test_the_details_tab_is_filled_in_from_the_project(spec, project):
    said = fields(spec)
    assert said["ProductName"] == project["name"]
    assert said["InternalName"] == project["name"]
    assert said["FileDescription"] == project["description"]
    assert said["CompanyName"] == project["authors"][0]["name"]
    assert said["OriginalFilename"] == "net-preset.exe"


def test_the_copyright_is_the_one_in_the_licence(spec):
    licence = (REPO / "LICENSE").read_text(encoding="utf-8").splitlines()
    stated = [line.strip() for line in licence if line.startswith("Copyright")]
    assert stated, "LICENSE has no copyright line for the executable to carry"
    assert fields(spec)["LegalCopyright"] == stated[0]


def test_every_field_the_details_tab_shows_has_something_in_it(spec):
    assert all(value.strip() for value in fields(spec).values()), fields(spec)


def test_the_executable_is_given_the_icon(spec):
    assert spec["EXE"].kwargs["icon"] == str(ICON)


def test_the_icon_is_bundled_where_the_window_looks_for_it(spec):
    """The resource is for Windows; this copy is the one Tk can open."""
    assert spec["Analysis"].kwargs["datas"] == [(str(ICON), ".")]
