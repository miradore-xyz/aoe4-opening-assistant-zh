"""真实书架网格验证：对称边距、整数列宽、筛选重排和卡片操作。"""
from pathlib import Path
import unittest

from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest

from default_openings import seed_default_openings
import test_brand_name as brand_fixture
from visual import Resources
from core import FIELDS


class LibraryGridAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        brand_fixture.BrandNameTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        brand_fixture.BrandNameTests.tearDownClass.__func__(cls)

    def setUp(self):
        brand_fixture.BrandNameTests.setUp(self)
        seed_default_openings(self.manager.data_dir, self.manager.orders_dir)
        self.manager.refresh_library()
        self.manager.show()

    def tearDown(self):
        brand_fixture.BrandNameTests.tearDown(self)

    def cards(self):
        library = self.manager.library
        delegate = library.itemDelegate()
        return [(item, delegate.card_rect(library.visualItemRect(item), library.indexFromItem(item)))
                for item in (library.item(i) for i in range(library.count())) if not item.isHidden()]

    def test_full_rows_fill_width_with_matching_heading_and_footer_edges(self):
        """760 至 1920 像素均分余数；卡片上限244，满行左右误差不超过2像素。"""
        for width in (760, 1100, 1440, 1920):
            with self.subTest(width=width):
                manager = self.manager
                manager.resize(width, 800)
                QTest.qWait(50)
                library = manager.library
                library.verticalScrollBar().setValue(0)
                cards = self.cards()
                first_y = cards[0][1].top()
                first_row = [rect for _, rect in cards if rect.top() == first_y]
                left = first_row[0].left()
                right = library.viewport().width() - first_row[-1].right() - 1
                self.assertLessEqual(abs(left - right), 2)
                self.assertLessEqual(left, 2)
                self.assertLessEqual(right, 2)
                self.assertLessEqual(max(rect.width() for rect in first_row), 244)
                self.assertLessEqual(max(rect.width() for rect in first_row) - min(rect.width() for rect in first_row), 1)
                for previous, following in zip(first_row, first_row[1:]):
                    self.assertEqual(following.left() - previous.right() - 1, 16)
                origin = library.viewport().mapTo(manager, QPoint())
                self.assertEqual(origin.x() + left, manager.home_layout.contentsMargins().left() + 1)
                footer_right = manager.resume_button.mapTo(manager, QPoint(manager.resume_button.width(), 0)).x()
                self.assertLessEqual(abs(origin.x() + first_row[-1].right() + 1 - footer_right), 2)
                self.assertFalse(library.dragEnabled())
                self.assertEqual(library.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
                self.assertEqual(library.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
                # 第二行与第一行共享列起点，最后一行不足列数时保持左对齐。
                last_y = max(rect.top() for _, rect in cards)
                tail = [rect for _, rect in cards if rect.top() == last_y]
                self.assertEqual(tail[0].left(), first_row[0].left())
                for index, rect in enumerate(tail):
                    self.assertEqual(rect.left(), first_row[index].left())

    def test_hidden_scrollbar_reaches_last_card_and_filter_resets_positions(self):
        manager = self.manager
        manager.resize(760, 650)
        QTest.qWait(50)
        library = manager.library
        bar = library.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        viewport = library.viewport()
        wheel = QWheelEvent(QPointF(100, 100), QPointF(viewport.mapToGlobal(QPoint(100, 100))),
                            QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                            Qt.ScrollUpdate, False)
        self.application.sendEvent(viewport, wheel)
        self.assertGreater(bar.value(), 0)
        bar.setValue(bar.maximum())
        QTest.qWait(20)
        self.assertLessEqual(self.cards()[-1][1].bottom(), viewport.height())
        self.assertLessEqual(viewport.height() - self.cards()[-1][1].bottom(), 18)
        manager.search.setText("中国")
        QTest.qWait(50)
        cards = self.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0][1].topLeft(), QPoint())
        self.assertLessEqual(cards[0][1].width(), 244)
        self.assertEqual(bar.maximum(), 0)
        manager.search.clear()
        QTest.qWait(50)
        self.assertEqual(self.cards()[0][1].topLeft(), QPoint())
        self.assertGreater(bar.maximum(), 0)

    def test_shifted_card_close_removes_correct_order_without_opening_session(self):
        manager = self.manager
        manager.resize(760, 700)
        QTest.qWait(50)
        library = manager.library
        item, card = self.cards()[1]
        original = Path(item.data(Qt.UserRole))
        count = library.count()
        target = library.itemDelegate().remove_rect(card).center()
        QTest.mouseClick(library.viewport(), Qt.LeftButton, pos=target)
        QTest.qWait(50)
        self.assertEqual(library.count(), count - 1)
        self.assertFalse(original.exists())
        self.assertEqual(manager.work_views.currentIndex(), 0)
        # 普通区域仍能进入演练；返回书架不能恢复成旧的固定槽排版。
        QTest.mouseClick(library.viewport(), Qt.LeftButton, pos=self.cards()[1][1].center())
        QTest.qWait(30)
        self.assertEqual(manager.work_views.currentIndex(), 1)
        manager.show_library()
        QTest.qWait(30)
        cards = self.cards()
        row = [rect for _, rect in cards if rect.top() == cards[0][1].top()]
        self.assertEqual(row[0].left(), 0)
        self.assertEqual(row[-1].right() + 1, library.viewport().width())

    def test_resources_reuse_shared_villager_labels(self):
        for key in ("food", "wood", "gold", "stone"):
            self.assertEqual(Resources.definitions[key][0], FIELDS[key])
        self.assertEqual([Resources.definitions[key][0] for key in ("food", "wood", "gold", "stone")],
                         ["吃肉村民", "采木村民", "采金村民", "采石村民"])


if __name__ == "__main__":
    unittest.main()
