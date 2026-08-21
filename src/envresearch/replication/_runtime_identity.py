"""Private non-reusable runtime identity capture and validation."""

from __future__ import annotations

import ctypes
import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from envresearch.replication._container_lifecycle import ContainerCleanupError

BirthProbe = Callable[[int, int], str]


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Kernel-bound identity for one newly spawned session leader."""

    pid: int
    pgid: int
    birth_sha256: str


def capture_process_identity(
    pid: int, birth_probe: BirthProbe | None = None
) -> ProcessIdentity:
    """Capture a non-reusable token immediately after a new session starts."""
    if type(pid) is not int or pid < 1:
        raise ContainerCleanupError("runtime process identity is invalid")
    probe = process_birth_sha256 if birth_probe is None else birth_probe
    token = probe(pid, pid)
    if not _canonical_sha256(token):
        raise ContainerCleanupError("runtime process birth identity is invalid")
    return ProcessIdentity(pid=pid, pgid=pid, birth_sha256=token)


def process_birth_sha256(pid: int, pgid: int) -> str:
    """Read a kernel birth token without relying on a mutable process name."""
    if sys.platform == "darwin":
        payload = _darwin_birth_payload(pid, pgid)
    elif sys.platform.startswith("linux"):
        payload = _linux_birth_payload(pid, pgid)
    else:
        raise ContainerCleanupError("process birth identity is unavailable")
    return hashlib.sha256(payload).hexdigest()


def mount_sha256(path: Path) -> str:
    """Bind one canonical reviewed host mount without persisting its raw path."""
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def _linux_birth_payload(pid: int, pgid: int) -> bytes:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        boot = (
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        )
    except FileNotFoundError as error:
        raise ProcessLookupError(pid) from error
    except OSError as error:
        raise ContainerCleanupError("process birth identity is unavailable") from error
    close = stat.rfind(")")
    fields = stat[close + 2 :].split()
    if close < 1 or len(fields) < 20 or int(fields[2]) != pgid:
        raise ContainerCleanupError("runtime process-group identity is invalid")
    return f"linux\0{boot}\0{pid}\0{pgid}\0{fields[19]}".encode()


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _darwin_birth_payload(pid: int, pgid: int) -> bytes:
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError as error:
        raise ContainerCleanupError("process birth identity is unavailable") from error
    info = _ProcBsdInfo()
    size = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size == 0:
        if ctypes.get_errno() in {3, 22}:
            raise ProcessLookupError(pid)
        raise ContainerCleanupError("process birth identity is unavailable")
    if size != ctypes.sizeof(info) or info.pbi_pid != pid or info.pbi_pgid != pgid:
        raise ContainerCleanupError("runtime process-group identity is invalid")
    return (
        f"darwin\0{pid}\0{pgid}\0{info.pbi_start_tvsec}\0{info.pbi_start_tvusec}"
    ).encode()


def _canonical_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
