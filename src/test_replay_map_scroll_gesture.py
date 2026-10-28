"""真实复盘页滚轮手势：普通滚页、Ctrl缩放、边界及触控板事件保真。"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from replay_page import ReplayPage
from replay_map_view import ReplayMapView
from test_replay_fullscreen_state import fullscreen_report
from visual import STYLE


SOURCE = Path(__file__).resolve().parent
OUTPUT = SOURCE.parents[2] / "work/本地复盘验证/1.9.6正式发行/地图滚轮手势"


def send_wheel(viewport, *, angle=-120, pixel=0, modifiers=Qt.NoModifier,
               phase=Qt.NoScrollPhase, inverted=False, source=Qt.MouseEventNotSynthesized,
               buttons=Qt.NoButton, position=None):
    position = position or QPoint(viewport.width() // 3, viewport.height() // 3)
    event = QWheelEvent(QPointF(position), QPointF(viewport.mapToGlobal(position)),
                        QPoint(0, pixel), QPoint(0, angle), buttons, modifiers, phase, inverted, source)
    QApplication.sendEvent(viewport, event)
    QApplication.processEvents()
    return event


class WheelProbe(QObject):
    def __init__(self):
        super().__init__()
        self.events = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel:
            self.events.append({"pixel": [event.pixelDelta().x(), event.pixelDelta().y()],
                                "angle": [event.angleDelta().x(), event.angleDelta().y()],
                                "phase": int(event.phase()), "inverted": event.inverted(),
                                "source": int(event.source()), "buttons": int(event.buttons()),
                                "modifiers": int(event.modifiers())})
        return False


class ReplayMapScrollGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.application.setStyleSheet(STYLE)
        OUTPUT.mkdir(parents=True, exist_ok=True)
        cls.records = []
        cls.source_hashes = {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
                             for name in ("replay_page.py", "replay_map_view.py")}

    @classmethod
    def tearDownClass(cls):
        after = {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() for name in cls.source_hashes}
        (OUTPUT / "实际Qt滚轮手势验收.json").write_text(json.dumps({
            "source_before": cls.source_hashes, "source_after": after,
            "source_unchanged": after == cls.source_hashes,
            "test_scope": "自有离屏真实ReplayPage，唯一网格画布与合成回放操作点；不访问网络",
            "records": cls.records}, ensure_ascii=False, indent=2), encoding="utf8")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = ReplayPage(self.directory.name)
        self.page.resize(1100, 740)
        self.page._show_report(fullscreen_report())
        self.page.show()
        QTest.qWait(30)
        self.extra_views = []
        self.assertIsNone(self.page.map_view.official_map)
        self.assertIs(self.page.map_view.map_stack.currentWidget(), self.page.map_view.canvas)
        self.assertTrue(self.page.map_view.events)
        self.assertGreater(self.page.scroll.verticalScrollBar().maximum(), 0)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        for view in self.extra_views:
            view.cleanup()
            view.close()
            view.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.directory.cleanup()

    def mode(self, code):
        view = self.page.map_view
        self.assertEqual(code, "records", "已移除的参考地形模式不能作为网格交互夹具")
        view.view_mode.setCurrentIndex(view.view_mode.findData(code))
        QTest.qWait(15)
        self.page.scroll.verticalScrollBar().setValue(0)
        self.application.processEvents()
        return view.canvas

    def test_plain_wheel_scrolls_real_page_without_zoom_for_grid_canvas(self):
        for code in ("records",):
            with self.subTest(code=code):
                canvas = self.mode(code)
                bar = self.page.scroll.verticalScrollBar()
                before = canvas.transform().m11()
                self.page.grab().save(str(OUTPUT / f"{code}-普通滚轮前.png"))
                event = send_wheel(canvas.viewport())
                self.assertGreater(bar.value(), 0)
                self.assertEqual(canvas.transform().m11(), before)
                self.assertTrue(event.isAccepted())
                self.page.grab().save(str(OUTPUT / f"{code}-普通滚轮后.png"))
                self.records.append({"mode": code, "plain_wheel_page_scroll": bar.value(),
                                     "scale_before": before, "scale_after": canvas.transform().m11()})

    def test_ctrl_wheel_zooms_at_cursor_without_scrolling_page(self):
        for code in ("records",):
            with self.subTest(code=code):
                canvas = self.mode(code)
                viewport = canvas.viewport()
                point = QPoint(viewport.width() // 3, viewport.height() // 3)
                anchor = canvas.mapToScene(point)
                before = canvas.transform().m11()
                bar = self.page.scroll.verticalScrollBar()
                page_position = bar.value()
                send_wheel(viewport, angle=120, modifiers=Qt.ControlModifier, position=point)
                self.assertAlmostEqual(canvas.transform().m11(), before * 1.2)
                self.assertEqual(bar.value(), page_position)
                returned = canvas.mapFromScene(anchor)
                drift = (returned - point).manhattanLength()
                self.assertLessEqual(drift, 1, "只容许Qt滚动条的一像素量化，不可随缩放漂移")
                self.records.append({"mode": code, "ctrl_scale_ratio": canvas.transform().m11() / before,
                                     "page_scroll_unchanged": True, "anchor_drift_logical_px": drift})

    def test_page_scroll_limits_never_turn_plain_wheel_into_zoom(self):
        for code in ("records",):
            canvas = self.mode(code)
            bar = self.page.scroll.verticalScrollBar()
            for value, angle in ((0, 120), (bar.maximum(), -120)):
                with self.subTest(code=code, edge=value):
                    bar.setValue(value)
                    before = canvas.transform().m11()
                    send_wheel(canvas.viewport(), angle=angle)
                    self.assertEqual(bar.value(), value)
                    self.assertEqual(canvas.transform().m11(), before)

    def test_forwarded_touchpad_payload_preserves_pixels_angle_phase_direction_and_source(self):
        for code in ("records",):
            canvas = self.mode(code)
            probe = WheelProbe()
            target = self.page.scroll.viewport()
            target.installEventFilter(probe)
            try:
                for phase, pixel, angle in ((Qt.ScrollBegin, 0, 0), (Qt.ScrollUpdate, -27, -120),
                                            (Qt.ScrollEnd, 0, 0)):
                    send_wheel(canvas.viewport(), pixel=pixel, angle=angle, phase=phase,
                               inverted=True, source=Qt.MouseEventSynthesizedBySystem,
                               modifiers=Qt.ShiftModifier, buttons=Qt.MiddleButton)
                    observed = probe.events[-1]
                    self.assertEqual(observed, {"pixel": [0, pixel], "angle": [0, angle],
                                               "phase": int(phase), "inverted": True,
                                               "source": int(Qt.MouseEventSynthesizedBySystem),
                                               "buttons": int(Qt.MiddleButton), "modifiers": int(Qt.ShiftModifier)})
                self.assertEqual(len(probe.events), 3)
                self.records.append({"mode": code, "forwarded_touchpad_events": probe.events})
            finally:
                target.removeEventFilter(probe)

    def test_fullscreen_plain_wheel_still_zooms_grid(self):
        for code in ("records",):
            with self.subTest(code=code):
                self.mode(code)
                self.page._expand_map()
                QTest.qWait(20)
                dialog = self.page._map_dialog
                view = dialog.map_view
                canvas = view.canvas
                before = canvas.transform().m11()
                send_wheel(canvas.viewport(), angle=120)
                self.assertAlmostEqual(canvas.transform().m11(), before * 1.2)
                dialog.reject()
                self.application.processEvents()

    def test_standalone_plain_wheel_keeps_existing_zoom_behavior(self):
        view = ReplayMapView(self.page.spatial_model, map_data_dir=self.directory.name)
        self.extra_views.append(view)
        view.resize(760, 600)
        view.show()
        QTest.qWait(15)
        for code in ("records",):
            view.view_mode.setCurrentIndex(view.view_mode.findData(code))
            self.application.processEvents()
            canvas = view.canvas
            before = canvas.transform().m11()
            send_wheel(canvas.viewport(), angle=120)
            self.assertAlmostEqual(canvas.transform().m11(), before * 1.2)


if __name__ == "__main__":
    unittest.main()
