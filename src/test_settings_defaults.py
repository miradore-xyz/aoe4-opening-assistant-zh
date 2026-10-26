"""旧默认的精确迁移、三档语速与自定义全局快捷键；不登记真实系统按键。"""
import ctypes
from ctypes import wintypes
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt5.QtWidgets import QApplication
from core import DEFAULT_HOTKEYS, atomic_json, load_settings
from native import GlobalHotkeys


class SettingsDefaultsTests(unittest.TestCase):
    def test_new_or_partial_hotkeys_stay_empty_and_old_presets_migrate_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '设置.json'
            self.assertEqual(set(load_settings(path)[0]['hotkeys'].values()), {''})
            atomic_json(path, {'hotkeys': {'previous':'Ctrl+Alt+F6', 'next':'Ctrl+Shift+J', 'pause':''}})
            settings, _ = load_settings(path)
            self.assertEqual(settings['hotkeys']['previous'], '')
            self.assertEqual(settings['hotkeys']['next'], 'Ctrl+Shift+J')
            self.assertEqual(settings['hotkeys']['visible'], '')
            settings['hotkeys']['previous'] = 'Ctrl+Alt+F6'
            atomic_json(path, settings)
            self.assertEqual(load_settings(path)[0]['hotkeys']['previous'], 'Ctrl+Alt+F6',
                             '新版用户主动设置同一组合后，不应再次被迁移删除')

    def test_neutral_background_migrates_exact_old_default_and_preserves_custom(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '设置.json'
            for color, expected in [('#101B17','#1b1b1e'), ('#101b18','#101b18'), ('#903311','#903311')]:
                atomic_json(path, {'overlay_color':color})
                result, _ = load_settings(path)
                self.assertEqual(result['overlay_color'], expected)
            result.update(overlay_color='#101b17')
            atomic_json(path, result)
            self.assertEqual(load_settings(path)[0]['overlay_color'], '#101b17')

    def test_saved_rates_select_nearest_of_three_presets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '设置.json'
            for value, expected in [(-5,-2),(-2,-2),(-1,0),(0,0),(1,0),(2,2),(5,2)]:
                atomic_json(path, {'rate':value})
                self.assertEqual(load_settings(path)[0]['rate'], expected)

    def test_empty_defaults_never_register_and_custom_combination_still_dispatches(self):
        application = QApplication.instance() or QApplication([])
        received = []
        with patch('native.ctypes.WinDLL') as win_dll:
            win_dll.return_value.RegisterHotKey.return_value = True
            keys = GlobalHotkeys(application, received.append)
            try:
                self.assertEqual(keys.apply(DEFAULT_HOTKEYS), [])
                win_dll.return_value.RegisterHotKey.assert_not_called()
                self.assertEqual(keys.registered, {})
                self.assertEqual(keys.apply({'next':'Ctrl+Shift+J'}), [])
                win_dll.return_value.RegisterHotKey.assert_called_once()
                message = wintypes.MSG()
                message.message = 0x0312
                message.wParam = next(iter(keys.registered))
                self.assertTrue(keys.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(message))[0])
                self.assertEqual(received, ['next'])
            finally:
                keys.close()
                application.removeNativeEventFilter(keys)
