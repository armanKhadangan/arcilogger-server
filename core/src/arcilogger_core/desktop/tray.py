"""The status window's icon in the notification area, next to the clock (Wave 5 addendum C, C2).

The icon has the colour of the window's level (``desktop\\icons\\tray-<level>.ico`` of the
program, drawn by ``tools/icons/build-icons.mjs``) and its tooltip says the level in both
languages. A left click shows the window; a right click opens :data:`MENU`. When the state turns
critical a notification ("balloon") says so once, next to the taskbar flash of the window.

Like the window, the icon changes nothing: its commands show the window, open the web interface,
list the backups (:mod:`.backups`) or close the icon itself. The commands reach the Tk thread
through ``on_command`` (a queue there): Tk is driven from one thread only.

Windows calls go through :mod:`ctypes` (standard library only, as :mod:`.winapi`). The icon lives
in its own thread with a hidden window and message loop:

* ``TaskbarCreated`` (Explorer started again) adds the icon again;
* the registered message :data:`SHOW_MESSAGE` lets a second start of the program show the open
  window (:func:`show_running`). Both are let through the message filter of this window: the
  logon task runs it elevated and Explorer, or a start from the Start menu, is not.

On any other platform :func:`start` answers ``None`` (no icon) and :func:`show_running` ``False``:
the window then behaves as before (closing it ends the program).
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal, Protocol

from arcilogger_core.desktop.status import LEVEL_TEXTS, TITLE, Level

Command = Literal["show", "web", "backups", "quit"]

#: The right-click menu, top to bottom; ``None`` is a separator.
MENU: Final[tuple[tuple[Command, str] | None, ...]] = (
    ("show", "Durum penceresini göster / Show the status window"),
    ("web", "Web arayüzünü aç / Open the web interface"),
    ("backups", "Yedekler… / Backups…"),
    None,
    ("quit", "Simgeyi kapat / Close the icon"),
)
#: Window class of the icon's hidden window (found by a second start).
WINDOW_CLASS: Final = "ARCILOGGER-Durum-Tray"
#: Registered message that asks the running program to show its window.
SHOW_MESSAGE: Final = "ARCILOGGER-Durum-Show"
#: Longest tooltip Windows shows (``szTip`` holds 128 characters with the terminating zero).
TIP_LIMIT: Final = 127
#: Longest notification title and text (``szInfoTitle`` 64, ``szInfo`` 256 with the zero).
TITLE_LIMIT: Final = 63
TEXT_LIMIT: Final = 255


class TrayIcon(Protocol):
    """What the window uses of the icon (a test passes its own)."""

    def set(self, level: Level, tip: str) -> None: ...

    def notify(self, title: str, text: str) -> None: ...

    def close(self) -> None: ...


def tooltip(level: Level) -> str:
    """ "ARCILOGGER Durum — Çalışıyor / Running" (shortened to what Windows shows)."""
    tr, en = LEVEL_TEXTS[level]
    return _cut(f"{TITLE} — {tr} / {en}", TIP_LIMIT)


def critical_notice(headline_tr: str, headline_en: str) -> tuple[str, str]:
    """Title and text of the notification when the state turns critical."""
    tr, en = LEVEL_TEXTS["critical"]
    return (
        _cut(f"ARCILOGGER — {tr} / {en}", TITLE_LIMIT),
        _cut(f"{headline_tr}\n{headline_en}", TEXT_LIMIT),
    )


def icon_file(icons: Path, level: Level) -> Path:
    return icons / f"tray-{level}.ico"


def program_icons() -> Path:
    """``<program>\\desktop\\icons``: the window runs ``<program>\\venv\\Scripts\\pythonw.exe``."""
    return Path(sys.executable).resolve().parents[2] / "desktop" / "icons"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(
        _LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class _WndClassEx(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        )

    class _Guid(ctypes.Structure):
        _fields_ = (
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        )

    class _NotifyIconData(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", _Guid),
            ("hBalloonIcon", wintypes.HICON),
        )

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _register_class = _user32.RegisterClassExW
    _register_class.argtypes = [ctypes.POINTER(_WndClassEx)]
    _register_class.restype = wintypes.ATOM
    _create_window = _user32.CreateWindowExW
    _create_window.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    _create_window.restype = wintypes.HWND
    _destroy_window = _user32.DestroyWindow
    _destroy_window.argtypes = [wintypes.HWND]
    _destroy_window.restype = wintypes.BOOL
    _default_proc = _user32.DefWindowProcW
    _default_proc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _default_proc.restype = _LRESULT
    _get_message = _user32.GetMessageW
    _get_message.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _get_message.restype = wintypes.BOOL
    _translate = _user32.TranslateMessage
    _translate.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _translate.restype = wintypes.BOOL
    _dispatch = _user32.DispatchMessageW
    _dispatch.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _dispatch.restype = _LRESULT
    _post_message = _user32.PostMessageW
    _post_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _post_message.restype = wintypes.BOOL
    _post_quit = _user32.PostQuitMessage
    _post_quit.argtypes = [ctypes.c_int]
    _post_quit.restype = None
    _register_message = _user32.RegisterWindowMessageW
    _register_message.argtypes = [wintypes.LPCWSTR]
    _register_message.restype = wintypes.UINT
    _find_window = _user32.FindWindowW
    _find_window.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _find_window.restype = wintypes.HWND
    _filter_message = _user32.ChangeWindowMessageFilterEx
    _filter_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, wintypes.LPVOID]
    _filter_message.restype = wintypes.BOOL
    _load_image = _user32.LoadImageW
    _load_image.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _load_image.restype = wintypes.HANDLE
    _load_icon = _user32.LoadIconW
    _load_icon.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
    _load_icon.restype = wintypes.HICON
    _destroy_icon = _user32.DestroyIcon
    _destroy_icon.argtypes = [wintypes.HICON]
    _destroy_icon.restype = wintypes.BOOL
    _metrics = _user32.GetSystemMetrics
    _metrics.argtypes = [ctypes.c_int]
    _metrics.restype = ctypes.c_int
    _create_menu = _user32.CreatePopupMenu
    _create_menu.argtypes = []
    _create_menu.restype = wintypes.HMENU
    _append_menu = _user32.AppendMenuW
    _append_menu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    _append_menu.restype = wintypes.BOOL
    _track_menu = _user32.TrackPopupMenu
    _track_menu.argtypes = [
        wintypes.HMENU,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.LPVOID,
    ]
    _track_menu.restype = wintypes.BOOL
    _destroy_menu = _user32.DestroyMenu
    _destroy_menu.argtypes = [wintypes.HMENU]
    _destroy_menu.restype = wintypes.BOOL
    _cursor_pos = _user32.GetCursorPos
    _cursor_pos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _cursor_pos.restype = wintypes.BOOL
    _set_foreground = _user32.SetForegroundWindow
    _set_foreground.argtypes = [wintypes.HWND]
    _set_foreground.restype = wintypes.BOOL
    _notify_icon = _shell32.Shell_NotifyIconW
    _notify_icon.argtypes = [wintypes.DWORD, ctypes.POINTER(_NotifyIconData)]
    _notify_icon.restype = wintypes.BOOL
    _module_handle = _kernel32.GetModuleHandleW
    _module_handle.argtypes = [wintypes.LPCWSTR]
    _module_handle.restype = wintypes.HMODULE

    WM_NULL: Final = 0x0000
    WM_DESTROY: Final = 0x0002
    WM_CLOSE: Final = 0x0010
    WM_LBUTTONUP: Final = 0x0202
    WM_RBUTTONUP: Final = 0x0205
    WM_APP: Final = 0x8000
    #: The icon's callback message and the "something to apply" message of :class:`_Tray`.
    WM_TRAY: Final = WM_APP + 1
    WM_APPLY: Final = WM_APP + 2
    NIM_ADD: Final = 0
    NIM_MODIFY: Final = 1
    NIM_DELETE: Final = 2
    NIF_MESSAGE: Final = 0x01
    NIF_ICON: Final = 0x02
    NIF_TIP: Final = 0x04
    NIF_INFO: Final = 0x10
    NIIF_ERROR: Final = 0x03
    IMAGE_ICON: Final = 1
    LR_LOADFROMFILE: Final = 0x10
    SM_CXSMICON: Final = 49
    SM_CYSMICON: Final = 50
    IDI_APPLICATION: Final = 32512
    MF_STRING: Final = 0x0000
    MF_SEPARATOR: Final = 0x0800
    TPM_RIGHTBUTTON: Final = 0x0002
    TPM_NONOTIFY: Final = 0x0080
    TPM_RETURNCMD: Final = 0x0100
    MSGFLT_ALLOW: Final = 1
    ICON_ID: Final = 1

    class _Tray:
        """The icon and its thread; :func:`start` makes one."""

        def __init__(self, on_command: Callable[[Command], None], icons: Path) -> None:
            self._on_command = on_command
            self._icons = icons
            self._lock = threading.Lock()
            self._level: Level = "unknown"
            self._tip = tooltip("unknown")
            self._balloon: tuple[str, str] | None = None
            self._handles: dict[Level, int] = {}
            self._loaded: set[int] = set()  # from the program's files: destroyed at the end
            self._hwnd = 0
            self._added = False
            self._ready = threading.Event()
            self._started = False
            self._proc = _WNDPROC(self._window_proc)  # kept: Windows calls it until the end
            self._taskbar_created = _register_message("TaskbarCreated")
            self._show = _register_message(SHOW_MESSAGE)
            self._thread = threading.Thread(target=self._run, name="arcilogger-tray", daemon=True)

        # -- called from the Tk thread --------------------------------------------------------

        def start(self, timeout: float = 5.0) -> bool:
            self._thread.start()
            self._ready.wait(timeout)
            return self._started

        def set(self, level: Level, tip: str) -> None:
            with self._lock:
                self._level, self._tip = level, _cut(tip, TIP_LIMIT)
            self._post(WM_APPLY)

        def notify(self, title: str, text: str) -> None:
            with self._lock:
                self._balloon = (_cut(title, TITLE_LIMIT), _cut(text, TEXT_LIMIT))
            self._post(WM_APPLY)

        def close(self) -> None:
            self._post(WM_CLOSE)
            self._thread.join(timeout=2)

        def _post(self, message: int) -> None:
            if self._hwnd:
                _post_message(self._hwnd, message, 0, 0)

        # -- the icon's own thread --------------------------------------------------------------

        def _run(self) -> None:
            try:
                instance = _module_handle(None)
                window_class = _WndClassEx()
                window_class.cbSize = ctypes.sizeof(_WndClassEx)
                window_class.lpfnWndProc = self._proc
                window_class.hInstance = instance
                window_class.lpszClassName = WINDOW_CLASS
                _register_class(ctypes.byref(window_class))  # 0 again in a later start: fine
                # A hidden top-level window (never shown): a message-only window would not get
                # the TaskbarCreated broadcast.
                hwnd = _create_window(
                    0, WINDOW_CLASS, TITLE, 0, 0, 0, 0, 0, None, None, instance, None
                )
                if not hwnd:
                    return
                self._hwnd = hwnd
                for message in (self._taskbar_created, self._show):
                    _filter_message(hwnd, message, MSGFLT_ALLOW, None)
                self._started = self._add()
            finally:
                self._ready.set()
            if not self._started:
                _destroy_window(self._hwnd)
                return
            message = wintypes.MSG()
            while _get_message(ctypes.byref(message), None, 0, 0) > 0:
                _translate(ctypes.byref(message))
                _dispatch(ctypes.byref(message))

        def _icon(self, level: Level) -> int:
            if level not in self._handles:
                width, height = _metrics(SM_CXSMICON), _metrics(SM_CYSMICON)
                path = str(icon_file(self._icons, level))
                handle = _load_image(None, path, IMAGE_ICON, width, height, LR_LOADFROMFILE)
                if handle:
                    self._loaded.add(handle)
                # Without the program's icons (development): Windows' own (shared) icon.
                self._handles[level] = handle or _load_icon(None, IDI_APPLICATION)
            return self._handles[level]

        def _data(self, flags: int) -> _NotifyIconData:
            data = _NotifyIconData()
            data.cbSize = ctypes.sizeof(_NotifyIconData)
            data.hWnd = self._hwnd
            data.uID = ICON_ID
            data.uFlags = flags
            data.uCallbackMessage = WM_TRAY
            with self._lock:
                data.hIcon = self._icon(self._level)
                data.szTip = self._tip
            return data

        def _add(self) -> bool:
            data = self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP)
            self._added = bool(_notify_icon(NIM_ADD, ctypes.byref(data)))
            return self._added

        def _apply(self) -> None:
            data = self._data(NIF_ICON | NIF_TIP)
            with self._lock:
                balloon, self._balloon = self._balloon, None
            if balloon is not None:
                data.uFlags |= NIF_INFO
                data.szInfoTitle, data.szInfo = balloon
                data.dwInfoFlags = NIIF_ERROR
            _notify_icon(NIM_MODIFY, ctypes.byref(data))

        def _menu(self) -> None:
            menu = _create_menu()
            try:
                for index, item in enumerate(MENU, start=1):
                    if item is None:
                        _append_menu(menu, MF_SEPARATOR, 0, None)
                    else:
                        _append_menu(menu, MF_STRING, index, item[1])
                point = wintypes.POINT()
                _cursor_pos(ctypes.byref(point))
                # Without the foreground the menu would not close on a click elsewhere.
                _set_foreground(self._hwnd)
                chosen = _track_menu(
                    menu,
                    TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                    point.x,
                    point.y,
                    0,
                    self._hwnd,
                    None,
                )
                _post_message(self._hwnd, WM_NULL, 0, 0)
            finally:
                _destroy_menu(menu)
            item = MENU[chosen - 1] if 0 < chosen <= len(MENU) else None
            if item is not None:
                self._on_command(item[0])

        def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
            if message == WM_TRAY:
                event = lparam & 0xFFFF
                if event == WM_LBUTTONUP:
                    self._on_command("show")
                elif event == WM_RBUTTONUP:
                    self._menu()
                return 0
            if message == WM_APPLY:
                self._apply()
                return 0
            if message == self._show:
                self._on_command("show")
                return 0
            if message == self._taskbar_created:
                self._add()  # Explorer started again: the old icon is gone
                return 0
            if message == WM_CLOSE:
                _destroy_window(hwnd)
                return 0
            if message == WM_DESTROY:
                if self._added:
                    data = self._data(0)
                    _notify_icon(NIM_DELETE, ctypes.byref(data))
                for handle in self._loaded:
                    _destroy_icon(handle)
                self._loaded.clear()
                self._handles.clear()
                _post_quit(0)
                return 0
            return int(_default_proc(hwnd, message, wparam, lparam))

    def start(on_command: Callable[[Command], None], icons: Path | None = None) -> TrayIcon | None:
        """The icon, shown; ``None`` when Windows did not take it (no notification area)."""
        tray = _Tray(on_command, icons if icons is not None else program_icons())
        return tray if tray.start() else None

    def show_running() -> bool:
        """Ask the program that already shows the icon to show its window."""
        hwnd = _find_window(WINDOW_CLASS, None)
        return bool(hwnd) and bool(_post_message(hwnd, _register_message(SHOW_MESSAGE), 0, 0))

else:

    def start(on_command: Callable[[Command], None], icons: Path | None = None) -> TrayIcon | None:
        return None

    def show_running() -> bool:
        return False
