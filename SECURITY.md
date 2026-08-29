# Security

## Reporting a vulnerability

Open a [security advisory](https://github.com/Legvan/net-preset/security/advisories/new) on
this repository, or an ordinary issue if the problem is not sensitive.

## What this program does, stated plainly

net-preset runs with an administrator token by design, asks for one at every launch, and
uses it to change the IPv4 configuration of one physical Ethernet adapter by running
`netsh`. That is the entire point of the program rather than an implementation detail — a
tool that changes IPv4 configuration cannot do it with anything less — but it is also the
kind of behaviour worth declaring rather than leaving a reader to infer.

**It asks for administrator rights at every launch.** Changing IPv4 configuration requires
them and there is nothing else the program does, so it asks once at startup rather than
once per click. The frozen executable declares `requireAdministrator` in its embedded
manifest — `uac_admin=True` in [`packaging/net-preset.spec`](packaging/net-preset.spec),
which `build.ps1` reads back out of the finished binary and will not call a build finished
without — and Windows shows the prompt before any of this code runs. Run from source there
is no manifest, so [`src/net_preset/elevation.py`](src/net_preset/elevation.py) checks the
process token with `IsUserAnAdmin` and relaunches itself through `ShellExecuteW` with the
`runas` verb. Declining the prompt does not end the program: the window opens with `USTAW`
disabled and a status line saying that applying will not work.

**It changes one adapter, and only its IPv4 configuration.** The operator picks a physical
Ethernet card once; nothing else on the machine is reconfigured. Every command it can build
is in [`src/net_preset/commands.py`](src/net_preset/commands.py) and there are five of
them: `interface ipv4 set address`, `set dnsservers` and `add dnsservers` to apply a
profile, and `set address` and `set dnsservers` with `source=dhcp` to hand the adapter
back. No IPv6, no WINS, no Wi-Fi profiles, no firewall rules, no routes beyond the default
gateway the profile names.

**It reads the adapters without starting anything.** `GetAdaptersAddresses` in `iphlpapi`,
bound with `ctypes` in [`src/net_preset/adapters.py`](src/net_preset/adapters.py), answers
in one call with the names, GUIDs, addresses, gateways, name servers and DHCP flags the
window shows and the apply verifies itself against.

**It touches the registry read-only, in two places.**
`HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}` for
each adapter's `NetCfgInstanceId` and `*PhysicalMediaType`, which is the only thing that
separates a real Ethernet card from a Bluetooth PAN adapter claiming to be one, and
`HKCU\SOFTWARE\Microsoft\Windows\DWM` for `AccentColor`, so the buttons match the rest of
the desktop. Both keys are opened for reading. Nothing in `src\` calls `SetValueEx`,
`CreateKey`, `DeleteKey` or `DeleteValue`.

**The complete list of Windows APIs it calls** is seven, all of them `ctypes.windll` calls
and all of them greppable in one line: `GetAdaptersAddresses` to read the adapters,
`GetSystemDirectoryW` to find `netsh`, `GetOEMCP` to decode what `netsh` said,
`IsUserAnAdmin` and `ShellExecuteW` to elevate a run from source, and `GetParent` with
`DwmSetWindowAttribute` to make the title bar dark.

**It writes two files, both under the operator's own profile.**

```
%LOCALAPPDATA%\net-preset\profiles.json
%LOCALAPPDATA%\net-preset\settings.json
```

`profiles.json` holds the addresses that were typed in; `settings.json` holds the GUID of
the adapter that was picked, and nothing else. Both are written to a temporary file beside
the target and moved into place, so an interrupted write cannot leave a half-file behind.
Nothing is written to Program Files, and the installer's per-machine directory never needs
to be writable at run time.

## What it deliberately does not do

**No network access.** The program opens no sockets and contacts no server, at any point.
There is no update check and no telemetry. Nothing under `src\` imports `socket`, `ssl`,
`http`, `urllib` or their relatives; the only thing it ever starts is `netsh`.

**No shell, ever.** Every command is built as a list of separate arguments and handed to
`subprocess.run` with `shell=False` — the one `subprocess.run` in the program, in
[`src/net_preset/apply.py`](src/net_preset/apply.py). Nothing is concatenated into a
command line, there is no `os.system` and no `shell=True`, and an adapter name containing
spaces or Polish characters survives intact because of it rather than in spite of it.

**No reaching for rights it was not given.** It asks once, through the two documented
Windows mechanisms — a manifest when it is frozen, `ShellExecuteW` with `runas` when it is
not — and takes no for an answer. A declined prompt leaves it running unelevated with
`USTAW` disabled and a status line saying why. There is no second attempt, no scheduled
task, no service, no startup entry and no COM elevation moniker.

**No trust in what `netsh` says about itself.** `netsh` exits 0 on a command it did not
understand, so no exit code is believed on its own: after the commands run, the adapter is
read back through `GetAdaptersAddresses` and compared field by field against what was
asked for, and the status line reports the comparison rather than the commands. That is a
correctness property first, but it is also what stops a substituted or broken `netsh` from
being reported to the operator as a successful apply.

## How a profile reaches the command line

Every field of a profile is validated twice, in two different places, by the same function.

Once in the dialog, by [`profile.validate`](src/net_preset/profile.py), before a profile
can be saved: the mask must be a contiguous one wide enough to hold a host, the address
must not be the network or the broadcast address, the gateway must be inside the subnet,
and every address field — the address, the gateway and both name servers — must be a
strict dotted quad. `parse_ipv4` is strict on purpose: no surrounding whitespace, no
leading zeros, which some parsers read as octal, and no digit outside ASCII, because
`str.isdigit()` and `int()` both accept Arabic-Indic ones.

Once again in [`store.load_profiles`](src/net_preset/store.py), for every entry read back
off disk, so a hand-edited or corrupted `profiles.json` cannot push unvalidated text into a
command. An entry that no longer validates is dropped and counted, and the window says how
many were lost.

The adapter name in the commands does not come from a profile at all. It is the
`FriendlyName` the IP helper API reported for the adapter the operator chose, and
`settings.json` stores that adapter as a GUID which is only ever used to look one up among
the adapters Windows has just listed. A GUID edited by hand can fail to match; it cannot
introduce a name.

## Where `netsh` comes from, and why that is not obvious

`netsh` is named by its full path, resolved from `GetSystemDirectoryW` on every call in
[`src/net_preset/system.py`](src/net_preset/system.py). That is a security property, not
tidiness, and the reason is worth writing down because it was a real local privilege
escalation in this program before the first release.

`netsh` was originally invoked by bare name. `subprocess` hands a list to `CreateProcess`
with `lpApplicationName` as `NULL`, and `CreateProcess` then resolves a bare name by
searching: **the directory the calling executable was loaded from first, then the current
directory, and only then `System32`** — unless `SafeProcessSearchMode` is set, which it is
not by default, and which moves the current directory down that list without ever touching
the first entry. The README sends the portable `net-preset.exe` out on a USB stick, into a
Downloads folder, onto a desktop: directories anyone can write to without a token. A
`netsh.exe` dropped beside it would have been started in preference to Windows' own copy,
with the administrator token the operator had granted at the UAC prompt seconds earlier.
Anyone able to write a file next to the program would have become an administrator.

The elevated relaunch had the same shape and got the same fix. `ShellExecuteW` with a null
`lpDirectory` hands the elevated copy the unelevated parent's current directory, which
keeps a writable directory on the search path Windows uses for DLLs and for programs named
without one. It is now started in the system directory.

**From `GetSystemDirectoryW`, deliberately not from `%SystemRoot%`.** An environment block
is inherited from whatever started the process, and a standard user can set `%SystemRoot%`
or `%windir%` for their own session through `HKCU\Environment` — the trick behind several
published UAC bypasses. The API reads no environment at all; it answers from the system
root the kernel recorded at boot, and cannot be redirected by anyone who is not already an
administrator.

There is deliberately no check that the file exists and no falling back to the bare name
when it does not, because the bare name is the hole. A `netsh` that cannot be started
raises an `OSError` that the window already turns into a line naming the path it could not
start.

**No released version carries this.** It was found during a pre-publication audit and
fixed before the first tag — the commit is *Resolve netsh from the system directory rather
than by name*, and it is an ancestor of `v0.1.0` — so there is nothing here to upgrade away
from. The reasoning is recorded in `src/net_preset/system.py` and in the commit history,
and each half of the fix is pinned by tests that fail if it is undone:
[`tests/test_system.py`](tests/test_system.py) for the environment being unable to move the
system directory, [`tests/test_commands.py`](tests/test_commands.py) for the path being
named in full even when nothing is there to run, and
[`tests/test_elevation.py`](tests/test_elevation.py) for the elevated copy never inheriting
this process's directory.

## The published binaries are not signed

Neither `net-preset.exe` nor `net-preset-setup.exe` carries a code signature. For anyone
downloading them that means Windows cannot tell you who built the file or whether it has
been altered since, SmartScreen and Smart App Control will treat each build on its own
merits, and the README records Smart App Control refusing one build and admitting the next
from an unchanged script.

What stands in for a signature, and it is not the same thing: every artifact is built by
[`.github/workflows/build.yml`](.github/workflows/build.yml) on a GitHub-hosted Windows
runner from a commit in this repository, and the run's log records the SHA-256 of both
files. A download can be checked against that log. It proves where a file came from; it
does not prove the file reaching you is that one, which is what a signature would do.

If you would rather not run an unsigned binary, run from source — `uv run net-preset` goes
through a signed Python interpreter — or build it yourself with `.\build.ps1` and compare
your hashes with the run's.

## Scope

net-preset grants no access the signed-in operator does not already have. Every change it
makes is one an administrator can make with `netsh` by hand, and it can make none of them
without an administrator token that Windows asked for and somebody granted. It adds no
trust of its own, installs no driver and no service, and inherits whatever the IP helper
API and `netsh` enforce.

Two consequences of running elevated are worth stating rather than leaving to be
discovered:

`%LOCALAPPDATA%` is resolved **while the program is elevated**, so when a standard user's
UAC prompt is approved with a different administrator account the profiles are read and
written under that administrator's profile directory. The machine then appears to hold two
separate lists. This is a property of the program and not of how it was installed.

`profiles.json` and `settings.json` sit in a directory that only that account and
administrators can write to, so the re-validation on load described above is
defence in depth rather than a trust boundary. Anyone who can edit those files can already
do more than edit those files.
