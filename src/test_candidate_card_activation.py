"""候选卡片整卡单击与键盘激活：一次操作仅查询一个精确编号，不依赖昵称。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QFontDatabase, QKeyEvent
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QPushButton

from analysis_page import AnalysisPage
from visual import STYLE


class CandidateCardActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.page = AnalysisPage(self.folder.name)
        self.page.show()
        self.identity_patch = patch.object(self.page, "_start_identities")
        self.identity_patch.start()
        self.players = [{"profile_id": identity, "name": "同名玩家", "game_level": level, "country": "cn"}
                        for identity, level in ((7, 22), (8, 40), (9, 16))]

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.identity_patch.stop()
        self.folder.cleanup()

    def prepare(self, width):
        self.page.resize(width, 650)
        self.page._job_result(self.players, "search")
        QTest.qWait(15)
        choices = self.page.player_choices
        item = choices.item(1)
        card = choices.itemWidget(item)
        return choices, card

    def assert_one_exact_request(self, action, identity=8):
        with patch.object(self.page, "_job") as job, patch.object(self.page.client, "fetch_profile") as fetch, \
             patch.object(self.page.client, "search_players") as search:
            action()
            job.assert_called_once()
            self.assertEqual(job.call_args.args[1], "profile")
            job.call_args.args[0]()
            fetch.assert_called_once_with(identity)
            search.assert_not_called()

    def test_avatar_name_details_and_blank_card_each_query_exact_id_once(self):
        for width in (760, 1100):
            for part in ("avatar", "name", "details", "blank"):
                with self.subTest(width=width,part=part):
                    choices, card = self.prepare(width)
                    if part == "blank":
                        point = card.mapTo(choices.viewport(), QPoint(card.width() - 3, card.height() - 3))
                    else:
                        widget = getattr(card, part)
                        point = widget.mapTo(choices.viewport(), widget.rect().center())
                    self.assertIs(choices.itemAt(point), choices.item(1))
                    activated = QSignalSpy(choices.candidateActivated)
                    self.assert_one_exact_request(lambda: QTest.mouseClick(choices.viewport(), Qt.LeftButton, pos=point))
                    self.assertEqual(len(activated), 1)
                    self.assertTrue(choices.isHidden())

    def test_keyboard_arrows_return_enter_and_space_query_exact_id_once(self):
        for width in (760, 1100):
            for key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                with self.subTest(width=width,key=key):
                    choices, _card = self.prepare(width)
                    choices.setFocus(Qt.TabFocusReason)
                    QTest.keyClick(choices, Qt.Key_Right)
                    self.assertEqual(choices.currentItem().data(Qt.UserRole), 8)
                    activated = QSignalSpy(choices.candidateActivated)
                    self.assert_one_exact_request(lambda: QTest.keyClick(choices, key))
                    self.assertEqual(len(activated), 1)

    def test_removed_button_has_no_widget_or_layout_slot_and_cards_keep_focus_hover(self):
        for width in (760, 1100):
            choices, _card = self.prepare(width)
            self.assertFalse(hasattr(self.page, "choose_button"))
            self.assertFalse(any(button.text() == "查看选中玩家" for button in self.page.findChildren(QPushButton)))
            layout = self.page.layout()
            self.assertIs(layout.itemAt(layout.indexOf(choices) + 1).widget(), self.page.scroll)
            self.assertEqual(choices.focusPolicy(), Qt.StrongFocus)
            self.assertEqual(choices.viewport().cursor().shape(), Qt.PointingHandCursor)
            self.assertIn("QListWidget::item:hover", choices.styleSheet())
            self.assertIn("空格", choices.accessibleDescription())

    def test_right_click_blank_viewport_busy_and_held_key_do_not_query(self):
        choices, card = self.prepare(760)
        point = card.mapTo(choices.viewport(), card.rect().center())
        with patch.object(self.page, "_job") as job:
            QTest.mouseClick(choices.viewport(), Qt.RightButton, pos=point)
            QTest.mouseClick(choices.viewport(), Qt.LeftButton, pos=QPoint(0, 0))
            self.application.sendEvent(choices, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " ", True, 2))
            self.page.busy = True
            QTest.mouseClick(choices.viewport(), Qt.LeftButton, pos=point)
            self.page.busy = False
            job.assert_not_called()

    def test_search_input_return_and_toolbar_query_keep_the_existing_exact_lookup(self):
        for action in ("return", "button"):
            with self.subTest(action=action):
                self.prepare(760)
                self.page.query.setText("8")
                if action == "return":
                    self.page.query.setFocus()
                    activate = lambda: QTest.keyClick(self.page.query, Qt.Key_Return)
                else:
                    activate = lambda: QTest.mouseClick(self.page.query_button, Qt.LeftButton)
                self.assert_one_exact_request(activate)


if __name__ == "__main__":
    unittest.main()
