"""悬浮窗默认角落与多屏可见范围测试，不读取或控制游戏。"""
import unittest

from PyQt5.QtCore import QPoint, QRect, QSize

from overlay_geometry import fit_overlay_geometry


class OverlayGeometryTests(unittest.TestCase):
    def test_first_open_has_equal_sixteen_pixel_top_and_right_margins(self):
        area = QRect(0, 0, 1920, 1040)
        result = fit_overlay_geometry([area], QSize(330, 220))
        self.assertEqual(result, QRect(1574, 16, 330, 220))
        self.assertEqual(area.right() - result.right(), 16)
        self.assertEqual(result.top() - area.top(), 16)

    def test_default_uses_the_screen_containing_the_assistant(self):
        areas = [QRect(0, 0, 1920, 1040), QRect(1920, 100, 1280, 720)]
        result = fit_overlay_geometry(areas, QSize(330, 220), preferred_point=QPoint(2300, 400))
        self.assertEqual(result, QRect(2854, 116, 330, 220))
        self.assertTrue(areas[1].contains(result))

    def test_negative_monitor_coordinates_are_preserved(self):
        areas = [QRect(0, 0, 1920, 1040), QRect(-1280, -200, 1280, 1024)]
        result = fit_overlay_geometry(areas, QSize(330, 220), preferred_point=QPoint(-600, 200))
        self.assertEqual(result, QRect(-346, -184, 330, 220))
        self.assertTrue(areas[1].contains(result))

    def test_manual_position_is_not_reset_on_later_show(self):
        area = QRect(0, 0, 1920, 1040)
        result = fit_overlay_geometry([area], QSize(330, 220), QPoint(120, 350), margin=0)
        self.assertEqual(result, QRect(120, 350, 330, 220))

    def test_saved_corner_inside_screen_does_not_allow_right_or_bottom_overflow(self):
        area = QRect(0, 0, 1920, 1040)
        result = fit_overlay_geometry([area], QSize(330, 220), QPoint(1800, 1000), margin=0)
        self.assertEqual(result, QRect(1590, 820, 330, 220))
        self.assertTrue(area.contains(result))

    def test_larger_window_is_fitted_to_available_area_including_taskbar(self):
        area = QRect(0, 40, 800, 560)
        result = fit_overlay_geometry([area], QSize(900, 1400))
        self.assertEqual(result, QRect(16, 56, 768, 528))
        self.assertTrue(area.contains(result))

    def test_very_small_screen_does_not_produce_negative_dimensions(self):
        area = QRect(10, 20, 24, 18)
        result = fit_overlay_geometry([area], QSize(330, 220))
        self.assertTrue(area.contains(result))
        self.assertGreater(result.width(), 0)
        self.assertGreater(result.height(), 0)

    def test_removed_monitor_falls_back_to_current_assistant_screen(self):
        areas = [QRect(0, 0, 1920, 1040), QRect(-1280, 0, 1280, 1024)]
        result = fit_overlay_geometry(areas, QSize(330, 220), QPoint(4000, 1000), QPoint(-600, 400), margin=0)
        self.assertTrue(areas[1].contains(result))

    def test_straddling_window_uses_screen_with_largest_visible_part(self):
        areas = [QRect(0, 0, 1920, 1040), QRect(1920, 0, 1280, 1024)]
        result = fit_overlay_geometry(areas, QSize(330, 220), QPoint(1870, 300), margin=0)
        self.assertEqual(result.x(), 1920)
        self.assertTrue(areas[1].contains(result))

    def test_qt_logical_coordinates_are_not_multiplied_for_display_scaling(self):
        area = QRect(0, 0, 1280, 680)
        result = fit_overlay_geometry([area], QSize(330, 220))
        self.assertEqual(result, QRect(934, 16, 330, 220))


if __name__ == "__main__":
    unittest.main()
