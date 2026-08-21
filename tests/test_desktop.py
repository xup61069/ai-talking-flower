from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class DesktopImportTests(unittest.TestCase):
    def test_import_without_optional_deps(self) -> None:
        # 桌面前端依賴為可選；無 PySide6/pynput 時模組仍應可 import
        import talking_flower.desktop as d

        self.assertTrue(hasattr(d, "FlowerPetWidget"))
        self.assertTrue(hasattr(d, "FlowerTray"))
        self.assertTrue(hasattr(d, "HotkeyManager"))
        self.assertTrue(hasattr(d, "run_pet"))

    def test_hotkey_manager_no_pynput_is_noop(self) -> None:
        from talking_flower.bus import StatusBus
        from talking_flower.settings import SettingsStore, LiveSettings

        store = SettingsStore(Path("config.toml"))
        live = LiveSettings(store)
        bus = StatusBus()
        from talking_flower.desktop import HotkeyManager
        import importlib.util

        # 真實環境若已安裝 pynput 則跳過 no-op 斷言
        if importlib.util.find_spec("pynput") is not None:
            self.skipTest("pynput 已安裝，跳過 no-op 測試")
        manager = HotkeyManager(bus, live)
        self.assertFalse(manager._available)
        manager.start()  # 不拋錯
        manager.stop()


class MainPetFlagTests(unittest.TestCase):
    def test_build_parser_has_pet_flag(self) -> None:
        from talking_flower.main import build_parser

        parser = build_parser()
        args = parser.parse_args([])
        self.assertFalse(args.pet)
        args = parser.parse_args(["--pet"])
        self.assertTrue(args.pet)


if __name__ == "__main__":
    unittest.main()
