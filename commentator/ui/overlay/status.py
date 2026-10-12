"""Status card: "Waiting for game to start" and "<game> detected"."""

import win32con
import win32gui
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

_MARGIN = 24
_FOUND_VISIBLE_MS = 3000


class StatusOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar entry
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet(
            "#card { background: rgba(18, 18, 22, 225); border-radius: 12px; }"
            "QLabel { color: #f2f2f2; background: transparent; }"
        )
        self._title = QLabel(card)
        self._title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self._detail = QLabel(card)
        self._detail.setStyleSheet("font-size: 12px; color: #b8b8c0;")
        self._detail.setWordWrap(True)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(18, 14, 18, 14)
        inner.setSpacing(4)
        inner.addWidget(self._title)
        inner.addWidget(self._detail)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        self.setFixedWidth(340)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(600)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._after_fade)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

    # Public slots (call on the Qt thread)

    def show_waiting(self, game_names: list[str]) -> None:
        names = ", ".join(game_names) or "no games configured"
        self._show(
            "Waiting for game to start…",
            f"Watching for: {names}\nPlay in borderless windowed mode.",
        )
        self._place_in_corner()

    def show_found(self, game_name: str, game_rect: tuple[int, int, int, int] | None = None) -> None:
        """`game_rect` is the game's client area (left, top, width, height) in physical pixels."""
        self._show(f"{game_name} detected", "Commentary attached to the game window.")
        if game_rect and game_rect[2] > 0 and game_rect[3] > 0:
            self._place_over_game(game_rect)
        else:
            self._place_in_corner()
        self._hide_timer.start(_FOUND_VISIBLE_MS)

    # Internals

    def _show(self, title: str, detail: str) -> None:
        self._hide_timer.stop()
        self._fade.stop()
        self._title.setText(title)
        self._detail.setText(detail)
        self.adjustSize()
        self.setWindowOpacity(1.0)
        self.show()
        self._make_click_through()

    def _place_in_corner(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - _MARGIN, screen.bottom() - self.height() - _MARGIN)

    def _place_over_game(self, game_rect: tuple[int, int, int, int]) -> None:
        # Bottom centre of the game, where subtitles will go later. Positioned with Win32 in
        # physical pixels so it lines up on monitors with different scaling.
        left, top, width, height = game_rect
        scale = self.devicePixelRatioF()
        own_width, own_height = int(self.width() * scale), int(self.height() * scale)
        x = left + (width - own_width) // 2
        y = top + height - own_height - int(_MARGIN * 3 * scale)
        flags = win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
        win32gui.SetWindowPos(int(self.winId()), win32con.HWND_TOPMOST, x, y, 0, 0, flags)

    def _fade_out(self) -> None:
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _after_fade(self) -> None:
        if self.windowOpacity() == 0.0:
            self.hide()

    def _make_click_through(self) -> None:
        # Qt's flag alone doesn't always pass clicks through on Windows; set the styles too.
        hwnd = int(self.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        style |= win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
