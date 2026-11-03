"""The few Windows calls of the desktop status window, through :mod:`ctypes` (standard library
only, like :mod:`arcilogger_core.hostinfo`).

* :func:`service_states`: the Service Control Manager, opened with ``SC_MANAGER_CONNECT`` and
  each service with ``SERVICE_QUERY_STATUS`` only. Nothing here can start, stop or change a
  service.
* :func:`is_elevated`, :func:`relaunch_elevated`: the window reads ``state\\`` of the data
  folder, which only administrators may read. Started without administrator rights (the
  Start-menu shortcut), it starts itself again through UAC (``ShellExecuteW`` verb ``runas``);
  the logon task already runs it with the highest rights.
* :func:`single_instance`, :func:`bring_to_front`: one window per session (named mutex
  ``Local\\…``); a second start shows the first window instead.
* :func:`flash`: the taskbar button flashes when the state turns critical (no sound, no focus
  stolen).
* :func:`dpi_aware`: sharp text on scaled screens, as in the Lisans Yöneticisi.

On any other platform (development, Linux CI) every function answers without asking anything:
the services are ``unknown``, the process counts as elevated, there is always one instance.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Final

from arcilogger_core.desktop.status import ServiceState

#: ``dwCurrentState`` of ``SERVICE_STATUS``.
_SCM_STATES: Final[Mapping[int, ServiceState]] = {
    1: "stopped",
    2: "starting",
    3: "stopping",
    4: "running",
    5: "starting",  # continue pending
    6: "stopping",  # pause pending
    7: "paused",
}
ERROR_ACCESS_DENIED: Final = 5
ERROR_ALREADY_EXISTS: Final = 183
ERROR_SERVICE_DOES_NOT_EXIST: Final = 1060


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _ServiceStatus(ctypes.Structure):
        _fields_ = (
            ("dwServiceType", wintypes.DWORD),
            ("dwCurrentState", wintypes.DWORD),
            ("dwControlsAccepted", wintypes.DWORD),
            ("dwWin32ExitCode", wintypes.DWORD),
            ("dwServiceSpecificExitCode", wintypes.DWORD),
            ("dwCheckPoint", wintypes.DWORD),
            ("dwWaitHint", wintypes.DWORD),
        )

    class _FlashInfo(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        )

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _open_manager = _advapi32.OpenSCManagerW
    _open_manager.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _open_manager.restype = wintypes.HANDLE
    _open_service = _advapi32.OpenServiceW
    _open_service.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    _open_service.restype = wintypes.HANDLE
    _query_status = _advapi32.QueryServiceStatus
    _query_status.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ServiceStatus)]
    _query_status.restype = wintypes.BOOL
    _close_service = _advapi32.CloseServiceHandle
    _close_service.argtypes = [wintypes.HANDLE]
    _close_service.restype = wintypes.BOOL

    _create_mutex = _kernel32.CreateMutexW
    _create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _create_mutex.restype = wintypes.HANDLE

    _is_admin = _shell32.IsUserAnAdmin
    _is_admin.argtypes = []
    _is_admin.restype = wintypes.BOOL
    _shell_execute = _shell32.ShellExecuteW
    _shell_execute.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    _shell_execute.restype = ctypes.c_void_p

    _enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _enum_windows = _user32.EnumWindows
    _enum_windows.argtypes = [_enum_proc, wintypes.LPARAM]
    _enum_windows.restype = wintypes.BOOL
    _window_text = _user32.GetWindowTextW
    _window_text.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _window_text.restype = ctypes.c_int
    _is_visible = _user32.IsWindowVisible
    _is_visible.argtypes = [wintypes.HWND]
    _is_visible.restype = wintypes.BOOL
    _show_window = _user32.ShowWindow
    _show_window.argtypes = [wintypes.HWND, ctypes.c_int]
    _show_window.restype = wintypes.BOOL
    _set_foreground = _user32.SetForegroundWindow
    _set_foreground.argtypes = [wintypes.HWND]
    _set_foreground.restype = wintypes.BOOL
    _get_parent = _user32.GetParent
    _get_parent.argtypes = [wintypes.HWND]
    _get_parent.restype = wintypes.HWND
    _flash_window = _user32.FlashWindowEx
    _flash_window.argtypes = [ctypes.POINTER(_FlashInfo)]
    _flash_window.restype = wintypes.BOOL

    SC_MANAGER_CONNECT: Final = 0x0001
    SERVICE_QUERY_STATUS: Final = 0x0004
    SW_SHOWNORMAL: Final = 1
    SW_RESTORE: Final = 9
    FLASHW_TRAY: Final = 0x2
    FLASHW_TIMERNOFG: Final = 0xC

    def service_states(names: Sequence[str]) -> dict[str, ServiceState]:
        manager = _open_manager(None, None, SC_MANAGER_CONNECT)
        if not manager:
            return dict.fromkeys(names, "unknown")
        try:
            states: dict[str, ServiceState] = {}
            for name in names:
                service = _open_service(manager, name, SERVICE_QUERY_STATUS)
                if not service:
                    missing = ctypes.get_last_error() == ERROR_SERVICE_DOES_NOT_EXIST
                    states[name] = "missing" if missing else "unknown"
                    continue
                try:
                    status = _ServiceStatus()
                    if _query_status(service, ctypes.byref(status)):
                        states[name] = _SCM_STATES.get(int(status.dwCurrentState), "unknown")
                    else:
                        states[name] = "unknown"
                finally:
                    _close_service(service)
            return states
        finally:
            _close_service(manager)

    def is_elevated() -> bool:
        return bool(_is_admin())

    def relaunch_elevated(arguments: Sequence[str]) -> bool:
        """Start this Python again with ``arguments`` through UAC; False when the person
        declined or it failed (``ShellExecuteW`` answers a value above 32 on success)."""
        result = _shell_execute(
            None, "runas", sys.executable, subprocess.list2cmdline(arguments), None, SW_SHOWNORMAL
        )
        return (result or 0) > 32

    def single_instance(name: str) -> object | None:
        """A handle to keep while the window is open, or None when another window of this
        session holds the mutex (an elevated one answers a lower process "access denied")."""
        handle = _create_mutex(None, False, name)
        error = ctypes.get_last_error()
        if not handle:
            return None if error == ERROR_ACCESS_DENIED else object()
        if error == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            return None
        return int(handle)

    def release(guard: object) -> None:
        """Give the mutex of :func:`single_instance` back (before starting elevated)."""
        if isinstance(guard, int):
            _kernel32.CloseHandle(guard)

    def bring_to_front(title_prefix: str) -> bool:
        found: list[int] = []

        def visit(hwnd: int, _: int) -> bool:
            buffer = ctypes.create_unicode_buffer(256)
            _window_text(hwnd, buffer, 256)
            if _is_visible(hwnd) and buffer.value.startswith(title_prefix):
                found.append(hwnd)
                return False
            return True

        _enum_windows(_enum_proc(visit), 0)
        if not found:
            return False
        _show_window(found[0], SW_RESTORE)
        _set_foreground(found[0])
        return True

    def flash(window_id: int) -> None:
        """Flash the taskbar button of the Tk window ``window_id`` (``winfo_id``: Tk's frame
        sits inside the top-level window) until it comes to the front."""
        hwnd = _get_parent(window_id) or window_id
        info = _FlashInfo(ctypes.sizeof(_FlashInfo), hwnd, FLASHW_TRAY | FLASHW_TIMERNOFG, 0, 0)
        _flash_window(ctypes.byref(info))

    def dpi_aware() -> None:
        with contextlib.suppress(OSError, AttributeError):  # older Windows: blurry, not broken
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)

else:

    def service_states(names: Sequence[str]) -> dict[str, ServiceState]:
        return dict.fromkeys(names, "unknown")

    def is_elevated() -> bool:
        return True

    def relaunch_elevated(arguments: Sequence[str]) -> bool:
        return False

    def single_instance(name: str) -> object | None:
        return object()

    def release(guard: object) -> None:
        return None

    def bring_to_front(title_prefix: str) -> bool:
        return False

    def flash(window_id: int) -> None:
        return None

    def dpi_aware() -> None:
        return None
