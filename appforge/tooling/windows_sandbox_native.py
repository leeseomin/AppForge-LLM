"""Native Windows AppContainer + Job Object process launcher.

This module is imported only by the trusted helper process.  The calling AppForge
process supplies a sanitized environment and captures only stdout/stderr.  The
helper grants one per-workspace AppContainer SID access to the workspace and the
selected toolchains, launches the target with no capabilities by default, and
places the complete process tree in a kill-on-close Job Object.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

from .sandbox import ExecutionSandboxUnavailable, approved_windows_path_entries


def _resolved_inside(path: Path, root: Path) -> bool:
    """Return whether the resolved target remains under ``root``."""

    try:
        candidate = os.path.normcase(os.path.abspath(os.fspath(path.resolve())))
        parent = os.path.normcase(os.path.abspath(os.fspath(root.resolve())))
        return os.path.commonpath([candidate, parent]) == parent
    except (OSError, ValueError):
        return False


def _lexically_inside(path: Path, root: Path) -> bool:
    """Return whether the written path is under ``root`` without following links."""

    try:
        candidate = os.path.normcase(os.path.abspath(os.fspath(path)))
        parent = os.path.normcase(os.path.abspath(os.fspath(root)))
        return os.path.commonpath([candidate, parent]) == parent
    except (OSError, ValueError):
        return False


def _safe_external_path_entries(workspace: Path, entries: list[str]) -> list[Path]:
    """Filter toolchain roots without trusting workspace junctions or symlinks.

    Project-local ``.venv`` and ``node_modules/.bin`` entries are already covered by
    the workspace ACL.  A generated project must not turn either entry into a
    junction to an arbitrary host directory and thereby expand its own allow-list.
    """

    external: list[Path] = []
    for raw in entries:
        candidate = Path(raw)
        if not candidate.exists():
            continue
        if _lexically_inside(candidate, workspace):
            # Genuine project entries need no separate ACL.  Reparse-point escapes
            # are deliberately omitted rather than promoted to trusted roots.
            if not _resolved_inside(candidate, workspace):
                continue
            continue
        resolved = candidate.resolve()
        if not any(
            os.path.normcase(os.fspath(existing)) == os.path.normcase(os.fspath(resolved))
            for existing in external
        ):
            external.append(resolved)
    return external


if os.name != "nt":

    def run_appcontainer_command(
        workspace: Path,
        sandbox_home: Path,
        command: list[str],
        *,
        allow_network: bool,
        memory_mb: int = 4096,
        max_processes: int = 64,
        cpu_rate: int = 8000,
    ) -> int:
        del workspace, sandbox_home, command, allow_network, memory_mb, max_processes, cpu_rate
        raise ExecutionSandboxUnavailable("the native AppContainer launcher requires Windows")

else:
    import ctypes
    from ctypes import wintypes

    # Process creation and AppContainer attributes.
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    CREATE_SUSPENDED = 0x00000004
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    CREATE_NO_WINDOW = 0x08000000
    STARTF_USESTDHANDLES = 0x00000100
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
    SE_GROUP_ENABLED = 0x00000004
    WIN_CAPABILITY_INTERNET_CLIENT_SID = 85

    # Job Object limits and information classes.
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x00000001
    JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x00000004
    JOB_OBJECT_UILIMIT_ALL = 0x000000FF
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    JOB_OBJECT_BASIC_UI_RESTRICTIONS_CLASS = 4
    JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION_CLASS = 15

    # Handle and wait constants.
    DUPLICATE_SAME_ACCESS = 0x00000002
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    INFINITE = 0xFFFFFFFF
    WAIT_OBJECT_0 = 0
    ERROR_INSUFFICIENT_BUFFER = 122
    HRESULT_ALREADY_EXISTS = 0x800700B7

    ULONG_PTR = ctypes.c_size_t
    SIZE_T = ctypes.c_size_t
    LPVOID = ctypes.c_void_p
    PSID = ctypes.c_void_p
    HRESULT = ctypes.c_long

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", PSID), ("Attributes", wintypes.DWORD)]

    class SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [
            ("AppContainerSid", PSID),
            ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
            ("CapabilityCount", wintypes.DWORD),
            ("Reserved", wintypes.DWORD),
        ]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", LPVOID)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", SIZE_T),
            ("MaximumWorkingSetSize", SIZE_T),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", SIZE_T),
            ("JobMemoryLimit", SIZE_T),
            ("PeakProcessMemoryUsed", SIZE_T),
            ("PeakJobMemoryUsed", SIZE_T),
        ]

    class JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):
        _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]

    class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)

    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(SID_AND_ATTRIBUTES),
        wintypes.DWORD,
        ctypes.POINTER(PSID),
    ]
    userenv.CreateAppContainerProfile.restype = HRESULT
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(PSID),
    ]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = HRESULT

    advapi32.ConvertSidToStringSidW.argtypes = [PSID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.CreateWellKnownSid.argtypes = [
        ctypes.c_int,
        PSID,
        PSID,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.CreateWellKnownSid.restype = wintypes.BOOL
    advapi32.FreeSid.argtypes = [PSID]
    advapi32.FreeSid.restype = LPVOID

    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SIZE_T),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        LPVOID,
        wintypes.DWORD,
        ULONG_PTR,
        LPVOID,
        SIZE_T,
        LPVOID,
        ctypes.POINTER(SIZE_T),
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [LPVOID]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        LPVOID,
        LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.DuplicateHandle.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    STD_INPUT_HANDLE = ctypes.c_ulong(-10).value
    STD_OUTPUT_HANDLE = ctypes.c_ulong(-11).value
    STD_ERROR_HANDLE = ctypes.c_ulong(-12).value

    def _raise_last_error(action: str) -> NoReturn:
        error = ctypes.get_last_error()
        raise ExecutionSandboxUnavailable(f"{action} failed with Windows error {error}")

    def _check_handle(handle: wintypes.HANDLE, action: str) -> wintypes.HANDLE:
        value = ctypes.cast(handle, ctypes.c_void_p).value
        if value in (None, 0, INVALID_HANDLE_VALUE):
            _raise_last_error(action)
        return handle

    def _hresult_value(result: int) -> int:
        return ctypes.c_uint32(result).value

    def _check_hresult(result: int, action: str) -> None:
        if ctypes.c_long(result).value < 0:
            raise ExecutionSandboxUnavailable(
                f"{action} failed with HRESULT 0x{_hresult_value(result):08X}"
            )

    def _profile_name(workspace: Path) -> str:
        canonical = ntpath.normcase(ntpath.abspath(str(workspace.resolve())))
        digest = hashlib.sha256(canonical.encode("utf-16le")).hexdigest()[:32]
        return f"OpenAppForge-{digest}"

    def _create_or_derive_appcontainer_sid(workspace: Path) -> PSID:
        profile = _profile_name(workspace)
        sid = PSID()
        result = userenv.CreateAppContainerProfile(
            profile,
            "OpenAppForge generated project",
            "Per-workspace AppContainer profile for generated project commands",
            None,
            0,
            ctypes.byref(sid),
        )
        if _hresult_value(result) == HRESULT_ALREADY_EXISTS:
            result = userenv.DeriveAppContainerSidFromAppContainerName(profile, ctypes.byref(sid))
        _check_hresult(result, "AppContainer profile creation")
        if not sid:
            raise ExecutionSandboxUnavailable("AppContainer profile creation returned no SID")
        return sid

    def _sid_to_string(sid: PSID) -> str:
        rendered = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(rendered)):
            _raise_last_error("AppContainer SID conversion")
        try:
            value = rendered.value
            if not value:
                raise ExecutionSandboxUnavailable("AppContainer SID conversion returned an empty SID")
            return value
        finally:
            if rendered:
                kernel32.LocalFree(rendered)

    def _is_inside(path: Path, root: Path) -> bool:
        return _resolved_inside(path, root)

    def _system_roots() -> list[Path]:
        values = [
            os.environ.get("SYSTEMROOT"),
            os.environ.get("WINDIR"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("PROGRAMW6432"),
        ]
        return [Path(value).resolve() for value in values if value]

    def _acl_root_for_path(path: Path) -> Path:
        resolved = path.resolve()
        parent = resolved if resolved.is_dir() else resolved.parent
        if parent.name.casefold() in {"bin", "cmd", "scripts"}:
            return parent.parent
        return parent

    def _approved_toolchain_roots(workspace: Path) -> list[Path]:
        roots: list[Path] = []
        for candidate in _safe_external_path_entries(
            workspace,
            approved_windows_path_entries(workspace),
        ):
            root = _acl_root_for_path(candidate)
            if any(_is_inside(root, system_root) for system_root in _system_roots()):
                continue
            if not any(ntpath.normcase(str(existing)) == ntpath.normcase(str(root)) for existing in roots):
                roots.append(root)
        for python_root in (Path(sys.base_prefix), Path(sys.prefix)):
            if not python_root.exists() or _is_inside(python_root, workspace):
                continue
            if any(_is_inside(python_root, system_root) for system_root in _system_roots()):
                continue
            if not any(
                ntpath.normcase(str(existing)) == ntpath.normcase(str(python_root.resolve()))
                for existing in roots
            ):
                roots.append(python_root.resolve())
        return roots

    def _grant_acl(path: Path, sid: str, permission: str) -> None:
        resolved = path.resolve()
        if not resolved.exists():
            raise ExecutionSandboxUnavailable(f"sandbox ACL target does not exist: {resolved}")
        system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
        icacls = Path(system_root) / "System32" / "icacls.exe"
        if not icacls.is_file():
            raise ExecutionSandboxUnavailable("Windows icacls.exe is unavailable")
        completed = subprocess.run(
            [
                str(icacls),
                str(resolved),
                "/grant:r",
                f"*{sid}:(OI)(CI){permission}",
                "/T",
                "/Q",
                "/L",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=120,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            raise ExecutionSandboxUnavailable(
                f"could not grant AppContainer {permission} access to {resolved}"
            )

    def _prepare_acl(workspace: Path, sandbox_home: Path, sid: str) -> None:
        _grant_acl(workspace, sid, "M")
        _grant_acl(sandbox_home, sid, "M")
        for root in _approved_toolchain_roots(workspace):
            _grant_acl(root, sid, "RX")

    def _resolve_target(workspace: Path, raw: str) -> Path:
        if any(separator in raw for separator in ("/", "\\")):
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = workspace / candidate
            resolved = candidate.resolve()
        else:
            located = shutil.which(raw, path=os.environ.get("PATH", ""))
            if not located:
                raise FileNotFoundError(raw)
            resolved = Path(located).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(raw)

        approved_roots = [
            workspace.resolve(),
            *_safe_external_path_entries(
                workspace,
                approved_windows_path_entries(workspace),
            ),
        ]
        if not any(_is_inside(resolved, root) for root in approved_roots):
            raise ExecutionSandboxUnavailable(
                "command executable is outside the workspace and approved Windows toolchains"
            )
        return resolved

    def _powershell_batch_wrapper(sandbox_home: Path) -> Path:
        script = sandbox_home / "invoke-batch-launcher.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\r\n"
            "if ($args.Count -lt 1) { throw 'missing batch target' }\r\n"
            "$target = [string]$args[0]\r\n"
            "$targetArgs = @()\r\n"
            "if ($args.Count -gt 1) { $targetArgs = @($args[1..($args.Count - 1)]) }\r\n"
            "& $target @targetArgs\r\n"
            "if ($null -eq $LASTEXITCODE) { exit 0 }\r\n"
            "exit $LASTEXITCODE\r\n",
            encoding="utf-8-sig",
        )
        return script

    def _application_and_command_line(
        workspace: Path,
        sandbox_home: Path,
        command: list[str],
    ) -> tuple[Path, str]:
        target = _resolve_target(workspace, command[0])
        suffix = target.suffix.casefold()
        if suffix in {".cmd", ".bat"}:
            system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
            powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            if not powershell.is_file():
                raise ExecutionSandboxUnavailable("Windows PowerShell is required for batch launchers")
            wrapper = _powershell_batch_wrapper(sandbox_home)
            argv = [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper),
                str(target),
                *command[1:],
            ]
            return powershell, subprocess.list2cmdline(argv)
        return target, subprocess.list2cmdline([str(target), *command[1:]])

    def _internet_client_capability() -> tuple[ctypes.Array[ctypes.c_char], SID_AND_ATTRIBUTES]:
        size = wintypes.DWORD(0)
        advapi32.CreateWellKnownSid(
            WIN_CAPABILITY_INTERNET_CLIENT_SID,
            None,
            None,
            ctypes.byref(size),
        )
        if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or size.value == 0:
            _raise_last_error("internetClient capability sizing")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.CreateWellKnownSid(
            WIN_CAPABILITY_INTERNET_CLIENT_SID,
            None,
            ctypes.cast(buffer, PSID),
            ctypes.byref(size),
        ):
            _raise_last_error("internetClient capability creation")
        attribute = SID_AND_ATTRIBUTES(ctypes.cast(buffer, PSID), SE_GROUP_ENABLED)
        return buffer, attribute

    def _duplicate_inheritable(handle: wintypes.HANDLE, label: str) -> wintypes.HANDLE:
        current = kernel32.GetCurrentProcess()
        duplicate = wintypes.HANDLE()
        if not kernel32.DuplicateHandle(
            current,
            handle,
            current,
            ctypes.byref(duplicate),
            0,
            True,
            DUPLICATE_SAME_ACCESS,
        ):
            _raise_last_error(f"{label} handle duplication")
        return duplicate

    def _standard_handles() -> tuple[wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE]:
        null_input = _check_handle(
            kernel32.CreateFileW(
                "NUL",
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None,
            ),
            "NUL input creation",
        )
        try:
            stdin_handle = _duplicate_inheritable(null_input, "stdin")
        finally:
            kernel32.CloseHandle(null_input)
        stdout_handle = _duplicate_inheritable(
            _check_handle(kernel32.GetStdHandle(STD_OUTPUT_HANDLE), "stdout lookup"),
            "stdout",
        )
        stderr_handle = _duplicate_inheritable(
            _check_handle(kernel32.GetStdHandle(STD_ERROR_HANDLE), "stderr lookup"),
            "stderr",
        )
        return stdin_handle, stdout_handle, stderr_handle

    def _create_job(memory_mb: int, max_processes: int, cpu_rate: int) -> wintypes.HANDLE:
        job = _check_handle(kernel32.CreateJobObjectW(None, None), "Job Object creation")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            | JOB_OBJECT_LIMIT_JOB_MEMORY
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        limits.BasicLimitInformation.ActiveProcessLimit = max_processes
        limits.JobMemoryLimit = memory_mb * 1024 * 1024
        if not kernel32.SetInformationJobObject(
            job,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            kernel32.CloseHandle(job)
            _raise_last_error("Job Object resource-limit setup")

        ui_limits = JOBOBJECT_BASIC_UI_RESTRICTIONS(JOB_OBJECT_UILIMIT_ALL)
        if not kernel32.SetInformationJobObject(
            job,
            JOB_OBJECT_BASIC_UI_RESTRICTIONS_CLASS,
            ctypes.byref(ui_limits),
            ctypes.sizeof(ui_limits),
        ):
            kernel32.CloseHandle(job)
            _raise_last_error("Job Object UI restriction setup")

        cpu_limits = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(
            JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
            cpu_rate,
        )
        if not kernel32.SetInformationJobObject(
            job,
            JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION_CLASS,
            ctypes.byref(cpu_limits),
            ctypes.sizeof(cpu_limits),
        ):
            kernel32.CloseHandle(job)
            _raise_last_error("Job Object CPU-limit setup")
        return job

    def _run_process(
        workspace: Path,
        application: Path,
        command_line: str,
        appcontainer_sid: PSID,
        *,
        allow_network: bool,
        memory_mb: int,
        max_processes: int,
        cpu_rate: int,
    ) -> int:
        stdin_handle, stdout_handle, stderr_handle = _standard_handles()
        inherited_handles = (wintypes.HANDLE * 3)(stdin_handle, stdout_handle, stderr_handle)
        capability_buffer = None
        capability_array = None
        if allow_network:
            capability_buffer, capability = _internet_client_capability()
            capability_array = (SID_AND_ATTRIBUTES * 1)(capability)
            capability_pointer = ctypes.cast(capability_array, ctypes.POINTER(SID_AND_ATTRIBUTES))
            capability_count = 1
        else:
            capability_pointer = None
            capability_count = 0
        security_capabilities = SECURITY_CAPABILITIES(
            appcontainer_sid,
            capability_pointer,
            capability_count,
            0,
        )

        attribute_size = SIZE_T(0)
        kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(attribute_size))
        if not attribute_size.value:
            for handle in (stdin_handle, stdout_handle, stderr_handle):
                kernel32.CloseHandle(handle)
            _raise_last_error("process attribute-list sizing")
        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        attribute_list = ctypes.cast(attribute_buffer, LPVOID)
        if not kernel32.InitializeProcThreadAttributeList(
            attribute_list,
            2,
            0,
            ctypes.byref(attribute_size),
        ):
            for handle in (stdin_handle, stdout_handle, stderr_handle):
                kernel32.CloseHandle(handle)
            _raise_last_error("process attribute-list initialization")
        job = None
        process_info = PROCESS_INFORMATION()
        try:
            if not kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(security_capabilities),
                ctypes.sizeof(security_capabilities),
                None,
                None,
            ):
                _raise_last_error("AppContainer process attribute setup")
            if not kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(inherited_handles, LPVOID),
                ctypes.sizeof(inherited_handles),
                None,
                None,
            ):
                _raise_last_error("inherited-handle restriction setup")

            startup = STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = stdin_handle
            startup.StartupInfo.hStdOutput = stdout_handle
            startup.StartupInfo.hStdError = stderr_handle
            startup.lpAttributeList = attribute_list
            mutable_command = ctypes.create_unicode_buffer(command_line)
            job = _create_job(memory_mb, max_processes, cpu_rate)
            if not kernel32.CreateProcessW(
                str(application),
                mutable_command,
                None,
                None,
                True,
                EXTENDED_STARTUPINFO_PRESENT
                | CREATE_SUSPENDED
                | CREATE_UNICODE_ENVIRONMENT
                | CREATE_NO_WINDOW,
                None,
                str(workspace),
                ctypes.byref(startup.StartupInfo),
                ctypes.byref(process_info),
            ):
                _raise_last_error("AppContainer process creation")
            if not kernel32.AssignProcessToJobObject(job, process_info.hProcess):
                kernel32.TerminateProcess(process_info.hProcess, 126)
                _raise_last_error("AppContainer Job Object assignment")
            if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
                kernel32.TerminateProcess(process_info.hProcess, 126)
                _raise_last_error("AppContainer process resume")
            kernel32.CloseHandle(process_info.hThread)
            process_info.hThread = None
            wait_result = kernel32.WaitForSingleObject(process_info.hProcess, INFINITE)
            if wait_result != WAIT_OBJECT_0:
                kernel32.TerminateProcess(process_info.hProcess, 126)
                raise ExecutionSandboxUnavailable(
                    f"AppContainer process wait failed with result {wait_result}"
                )
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code)):
                _raise_last_error("AppContainer exit-code collection")
            return int(exit_code.value)
        finally:
            if process_info.hThread:
                kernel32.CloseHandle(process_info.hThread)
            if process_info.hProcess:
                kernel32.CloseHandle(process_info.hProcess)
            if job:
                # KILL_ON_JOB_CLOSE terminates any children that outlived the command.
                kernel32.CloseHandle(job)
            kernel32.DeleteProcThreadAttributeList(attribute_list)
            for handle in (stdin_handle, stdout_handle, stderr_handle):
                kernel32.CloseHandle(handle)
            del capability_buffer, capability_array, attribute_buffer

    def run_appcontainer_command(
        workspace: Path,
        sandbox_home: Path,
        command: list[str],
        *,
        allow_network: bool,
        memory_mb: int = 4096,
        max_processes: int = 64,
        cpu_rate: int = 8000,
    ) -> int:
        workspace = workspace.resolve()
        sandbox_home = sandbox_home.resolve()
        if not workspace.is_dir():
            raise ExecutionSandboxUnavailable("workspace is missing or is not a directory")
        sandbox_home.mkdir(parents=True, exist_ok=True)
        if not command:
            raise ExecutionSandboxUnavailable("a target command is required")
        if not 256 <= memory_mb <= 32768:
            raise ExecutionSandboxUnavailable("Windows sandbox memory must be between 256 and 32768 MiB")
        if not 1 <= max_processes <= 512:
            raise ExecutionSandboxUnavailable("Windows sandbox process limit must be between 1 and 512")
        if not 100 <= cpu_rate <= 10000:
            raise ExecutionSandboxUnavailable("Windows sandbox CPU rate must be between 100 and 10000")

        sid = _create_or_derive_appcontainer_sid(workspace)
        try:
            sid_string = _sid_to_string(sid)
            _prepare_acl(workspace, sandbox_home, sid_string)
            try:
                application, command_line = _application_and_command_line(
                    workspace,
                    sandbox_home,
                    command,
                )
            except FileNotFoundError:
                print(f"command not found: {command[0]}", file=sys.stderr)
                return 127
            return _run_process(
                workspace,
                application,
                command_line,
                sid,
                allow_network=allow_network,
                memory_mb=memory_mb,
                max_processes=max_processes,
                cpu_rate=cpu_rate,
            )
        finally:
            advapi32.FreeSid(sid)
