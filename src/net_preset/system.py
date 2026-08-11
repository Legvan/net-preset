r"""Where Windows keeps its own programs, asked of Windows rather than searched for.

Two places in this program hand a path to the operating system and would otherwise
leave the choice of directory to Windows: the netsh invocation in `commands`, and
the working directory of the elevated relaunch in `elevation`. Both are used by a
process holding the administrator token the operator granted at the UAC prompt, so
both have to name a directory that nothing short of an administrator can write to.

GetSystemDirectoryW is the trustworthy source for that name. %SystemRoot% and
%windir% are environment variables: the block they live in is inherited from
whatever started this process, and a standard user can set either of them for their
own session through HKCU\Environment, which is the trick behind several published
UAC bypasses. The API reads no environment at all -- it answers from the system root
the kernel recorded at boot -- so it cannot be redirected by anyone who is not
already an administrator.

Nothing here touches the filesystem, and the module imports cleanly on a platform
with no Windows API to call.
"""

from __future__ import annotations

import ctypes

__all__ = ["system_directory"]

# GetSystemDirectoryW writes into a caller's buffer and answers with the length it
# wrote; when the buffer is too small it answers instead with the length it would
# need, counting the terminating null. So a length at or past the end of the buffer
# is a refusal rather than a path, and only a shorter one is the directory. MAX_PATH
# is the documented ceiling for this particular directory.
_MAX_PATH = 260

# Used only where the API cannot be reached, which means this is not Windows -- and
# where there is neither a netsh to run nor a process to elevate. It is still an
# absolute path, because a bare name is the one thing this module must never hand
# back, whatever went wrong above it.
_FALLBACK = r"C:\Windows\System32"


def system_directory() -> str:
    r"""The directory Windows keeps its own programs in, normally C:\Windows\System32.

    A 32-bit process is answered with SysWOW64, which holds that process's own 32-bit
    copy of netsh. Both are Windows' copies, and neither can be written to without the
    token this program asks the operator for.
    """
    buffer = ctypes.create_unicode_buffer(_MAX_PATH)
    try:
        written = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    except AttributeError, OSError:
        # No windll means no Windows, and nothing that uses this path can run here.
        return _FALLBACK
    if not 0 < written < len(buffer):
        return _FALLBACK
    return buffer.value
