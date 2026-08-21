from __future__ import annotations

import asyncio
import logging
import threading

from .bus import StatusBus
from .settings import LiveSettings

try:
    from PySide6.QtCore import Qt, QTimer, Signal, Slot
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel

    _PYSIDE_AVAILABLE = True
except ImportError:
    _PYSIDE_AVAILABLE = False
    # 佔位，避免 class 定義時 NameError；實際功能在 _PYSIDE_AVAILABLE=False 時不使用
    Qt = QTimer = QApplication = QWidget = QVBoxLayout = QLabel = object  # type: ignore[assignment]

    def Signal(*args, **kwargs):  # type: ignore[no-redef]
        return None

    def Slot(*args, **kwargs):  # type: ignore[no-redef]
        def _decorator(fn):
            return fn

        return _decorator

try:
    import pystray  # type: ignore
    from PIL import Image, ImageDraw  # type: ignore

    _PYSTRAY_AVAILABLE = True
except ImportError:
    _PYSTRAY_AVAILABLE = False
    Image = ImageDraw = object  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)


class HotkeyManager:
    """全域快捷鍵：push-to-talk（按住 Ctrl+Alt+Space）與快速靜音（Ctrl+Alt+M）。

    依賴 pynput；未安裝時 no-op。狀態直接寫 live.listening + bus 事件。
    """

    PUSH_KEYS = {"ctrl", "alt", "space"}
    MUTE_COMBO = "<ctrl>+<alt>+m"
    TOGGLE_PET = "<ctrl>+<alt>+h"

    def __init__(self, bus: StatusBus, live: LiveSettings, pet_widget=None) -> None:
        self.bus = bus
        self.live = live
        self.pet_widget = pet_widget
        self._listener = None
        self._pressed: set[str] = set()
        self._push_active = False
        self._prev_listening: bool | None = None
        self._available = False
        try:
            import pynput.keyboard  # type: ignore  # noqa: F401

            self._available = True
        except ImportError:
            LOGGER.warning("pynput 未安裝，全域快捷鍵不可用：pip install \"ai-talking-flower[pet]\"")

    def _key_name(self, key) -> str | None:
        try:
            from pynput.keyboard import Key, KeyCode  # type: ignore

            if key in (Key.ctrl_l, Key.ctrl_r, Key.ctrl):
                return "ctrl"
            if key in (Key.alt_l, Key.alt_r, Key.alt_gr, Key.alt):
                return "alt"
            if key == Key.space:
                return "space"
            if isinstance(key, KeyCode) and key.char is not None:
                c = key.char.lower()
                if c in ("m", "h"):
                    return c
            return None
        except Exception:
            return None

    def start(self) -> None:
        if not self._available:
            return

        from pynput import keyboard  # type: ignore

        def on_press(key):
            name = self._key_name(key)
            if name is not None:
                self._pressed.add(name)
            # push-to-talk：三鍵齊按進入 push 模式
            if self.PUSH_KEYS.issubset(self._pressed) and not self._push_active:
                self._push_active = True
                self._prev_listening = self.live.listening
                self.live.listening = True
                self.live.manual_busy = False
                self.bus.publish({"type": "hotkey", "action": "push_down"})
                LOGGER.info("push-to-talk 按下（暫時啟用聆聽）")
            # 快速靜音與 pet 顯隱用單次觸發，這裡只在按下瞬間判斷（配合釋放去抖）
            if name == "m" and self.PUSH_KEYS.issubset(self._pressed):
                pass  # push 期間忽略 m
            elif name == "m" and {"ctrl", "alt"}.issubset(self._pressed):
                # Ctrl+Alt+M 切換靜音
                self.live.listening = not self.live.listening
                self.bus.publish({"type": "hotkey", "action": "mute_toggle", "listening": self.live.listening})
                self.bus.publish({"type": "paused" if not self.live.listening else "resumed"})
                LOGGER.info("快速靜音切換：listening=%s", self.live.listening)

        def on_release(key):
            name = self._key_name(key)
            if name is not None:
                self._pressed.discard(name)
            if self._push_active and "space" not in self._pressed:
                self._push_active = False
                if self._prev_listening is not None:
                    self.live.listening = self._prev_listening
                    self._prev_listening = None
                self.bus.publish({"type": "hotkey", "action": "push_up"})
                LOGGER.info("push-to-talk 釋放")

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()
        LOGGER.info("全域快捷鍵已啟動：按住 Ctrl+Alt+Space 說話，Ctrl+Alt+M 靜音")

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


def _create_tray_image(size: int = 64) -> "Image.Image":
    if not _PYSTRAY_AVAILABLE:
        raise RuntimeError("Pillow/pystray 未安裝")
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 粉色花朵簡化圖示
    draw.ellipse([8, 8, size - 8, size - 8], fill=(255, 110, 163, 255))
    draw.ellipse([size // 2 - 10, size // 2 - 10, size // 2 + 10, size // 2 + 10], fill=(255, 220, 100, 255))
    return img


class FlowerPetWidget(QWidget):
    """透明 always-on-top 小窗：顯示花花狀態、音量與快捷提示。"""

    # bus 事件轉 Signal，避免跨執行緒直接操作 UI
    bus_event = Signal(dict)

    def __init__(self, bus: StatusBus, live: LiveSettings, parent=None) -> None:
        super().__init__(parent)
        self.bus = bus
        self.live = live
        self._state = "等待說話"
        self._rms = 0.0
        self._setup_ui()
        self._setup_bus_bridge()
        self._setup_auto_hide_timer()

    def _setup_ui(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(220, 88)
        # 預設右下角
        self._move_to_corner()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        self.lbl_flower = QLabel("🌸 花花", self)
        self.lbl_flower.setStyleSheet("color: white; font-size: 15px; font-weight: 700;")

        self.lbl_state = QLabel("等待說話", self)
        self.lbl_state.setStyleSheet("color: #ffd1e6; font-size: 12px;")

        self.lbl_rms = QLabel("", self)
        self.lbl_rms.setStyleSheet("color: #9da2b8; font-size: 11px; font-family: monospace;")

        self.lbl_hint = QLabel("按住 Ctrl+Alt+Space 說話  •  點擊展開控制台", self)
        self.lbl_hint.setStyleSheet("color: #6b7086; font-size: 10px;")
        self.lbl_hint.setWordWrap(True)

        for w in (self.lbl_flower, self.lbl_state, self.lbl_rms, self.lbl_hint):
            layout.addWidget(w)

        self.setStyleSheet(
            "QWidget { background: rgba(22,23,31,0.88); border: 1px solid rgba(255,255,255,0.08); border-radius: 14px; }"
        )
        # 拖曳支援
        self._drag_pos = None

    def _move_to_corner(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        self.move(geom.right() - self.width() - 18, geom.bottom() - self.height() - 48)

    def _setup_bus_bridge(self) -> None:
        self.bus_event.connect(self._on_bus_event)
        # 訂閱 bus：在 loop 執行緒用 asyncio.create_task 包裝
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _subscribe():
            queue = await self.bus.subscribe()
            while True:
                event = await queue.get()
                # 跨執行緒投遞到 Qt 主執行緒
                try:
                    self.bus_event.emit(dict(event))
                except RuntimeError:
                    break

        asyncio.create_task(_subscribe())

    def _setup_auto_hide_timer(self) -> None:
        self._rms_timer = QTimer(self)
        self._rms_timer.timeout.connect(self._fade_rms)
        self._rms_timer.start(180)

    @Slot(dict)
    def _on_bus_event(self, event: dict) -> None:
        t = event.get("type", "")
        if t == "state":
            self._state = event.get("state", self._state)
            self.lbl_state.setText(self._state)
            # 說話中時邊框發光
            if "說話" in self._state:
                self.setStyleSheet(
                    "QWidget { background: rgba(82,30,56,0.92); border: 1px solid #ff6ea3; border-radius: 14px; }"
                )
            else:
                self.setStyleSheet(
                    "QWidget { background: rgba(22,23,31,0.88); border: 1px solid rgba(255,255,255,0.08); border-radius: 14px; }"
                )
        elif t == "tts_rms":
            self._rms = float(event.get("rms", 0.0))
            bars = int(self._rms * 18)
            self.lbl_rms.setText("▁" * max(0, bars) + f"  rms {self._rms:.3f}")
        elif t == "audio":
            # 麥克風音量條（與 tts_rms 互補）
            rms = float(event.get("rms", 0.0))
            thr = float(event.get("threshold", 0.008))
            pct = min(1.0, rms / max(thr, 1e-6) * 0.6)
            self.lbl_rms.setText(f"mic {'█' * int(pct * 12):12s} {rms:.3f}")
        elif t == "reminder":
            self.lbl_state.setText(f"⏰ {event.get('text', '')[:18]}")

    def _fade_rms(self) -> None:
        self._rms *= 0.88
        if self._rms < 0.005 and "說話" not in self._state:
            self.lbl_rms.setText("")

    # 拖曳
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self._open_console()

    def _open_console(self) -> None:
        import webbrowser

        webbrowser.open("http://127.0.0.1:7860")





class FlowerTray:
    """系統匣：常駐、右鍵選單、顯示/隱藏小窗。"""

    def __init__(self, pet_widget: FlowerPetWidget | None = None) -> None:
        self.pet_widget = pet_widget
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def _build_menu(self) -> pystray.Menu:
        def _show(icon, item):
            if self.pet_widget is not None:
                self.pet_widget.show()

        def _hide(icon, item):
            if self.pet_widget is not None:
                self.pet_widget.hide()

        def _quit(icon, item):
            icon.stop()
            if self.pet_widget is not None:
                QApplication.instance().quit()  # type: ignore[union-attr]

        return pystray.Menu(
            pystray.MenuItem("顯示花花", _show, default=True),
            pystray.MenuItem("隱藏", _hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("結束", _quit),
        )

    def start(self) -> None:
        if not _PYSTRAY_AVAILABLE:
            LOGGER.warning("pystray 未安裝，系統匣不可用")
            return

        def _run():
            self._icon = pystray.Icon(
                "flower",
                _create_tray_image(),
                "花花 - 閒聊花花",
                menu=self._build_menu(),
            )
            self._icon.run()

        self._thread = threading.Thread(target=_run, name="flower-tray", daemon=True)
        self._thread.start()
        LOGGER.info("系統匣已啟動")

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass


def run_pet(bus: StatusBus, live: LiveSettings) -> int:
    """在主執行緒啟動 Qt 事件循環（阻塞）。供 --pet 模式使用。"""
    if not _PYSIDE_AVAILABLE:
        LOGGER.error("PySide6 未安裝：pip install \"ai-talking-flower[pet]\"")
        return 1

    app = QApplication.instance() or QApplication([])
    pet = FlowerPetWidget(bus, live)
    pet.show()

    tray = FlowerTray(pet)
    if _PYSTRAY_AVAILABLE:
        tray.start()

    hotkeys = HotkeyManager(bus, live, pet)
    hotkeys.start()

    return app.exec()
