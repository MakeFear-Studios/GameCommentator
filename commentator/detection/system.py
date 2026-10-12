"""Windows calls for detection and window tracking.

Read-only: lists processes and windows and reads window positions. Never opens
handles on the game process or sends it input.
"""

import ctypes
from dataclasses import dataclass

import psutil
import win32con
import win32gui
import win32process

from commentator.detection.matcher import ProcessInfo, WindowInfo

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def enable_dpi_awareness() -> str:
    """Make window positions use real pixels under display scaling. Call before any UI."""
    user32 = ctypes.windll.user32
    try:
        if user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return "per_monitor_v2"
    except AttributeError:  # Older than Windows 10 1703
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return "per_monitor"
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()
        return "system"


def list_processes() -> list[ProcessInfo]:
    return [
        ProcessInfo(pid=p.info["pid"], name=p.info["name"] or "")
        for p in psutil.process_iter(["pid", "name"])
    ]


def list_windows() -> list[WindowInfo]:
    """Visible, titled, top-level, non-tool windows: the ones a game could be playing in."""
    windows: list[WindowInfo] = []

    def visit(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        windows.append(WindowInfo(hwnd, pid, title, right - left, bottom - top))
        return True

    win32gui.EnumWindows(visit, None)
    return windows


def is_window(hwnd: int) -> bool:
    return bool(win32gui.IsWindow(hwnd))


@dataclass(frozen=True)
class WindowState:
    exists: bool
    minimized: bool = False
    foreground: bool = False
    # Client area (the game picture, no title bar) in screen pixels.
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0


def window_state(hwnd: int) -> WindowState:
    if not win32gui.IsWindow(hwnd):
        return WindowState(exists=False)
    minimized = bool(win32gui.IsIconic(hwnd))
    foreground = win32gui.GetForegroundWindow() == hwnd
    if minimized:
        return WindowState(exists=True, minimized=True, foreground=foreground)
    _, _, width, height = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    return WindowState(True, False, foreground, left, top, width, height)
