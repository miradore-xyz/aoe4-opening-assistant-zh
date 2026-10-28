"""悬浮正文真实文档行距与Lucide（线条图标库）资源核对；不联网、不播放。"""
import hashlib
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QEvent
from PyQt5.QtGui import QTextDocument
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from app_fonts import register_ui_fonts
from test_overlay_controls import Panel
from visual import STYLE


class OverlayTextFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        register_ui_fonts()
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.panel = Panel()
        self.overlay = self.panel.overlay

    def tearDown(self):
        QTest.qWait(20)
        self.overlay.resize_save.stop()
        self.overlay.lock_handle.cleanup()
        self.overlay.hide()
        self.overlay.goals.hide()
        self.overlay.deleteLater()
        self.panel.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def layout_document(self, notes, width, size):
        self.panel.settings.update(width=width, font_size=size)
        self.panel.order['build_order'][0]['notes'] = notes
        self.panel.ids = [0]
        self.overlay.apply_settings()
        self.overlay.show()
        self.overlay.render()
        QTest.qWait(5)
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(self.overlay.notes.font())
        document.setHtml(self.overlay.notes.text())
        document.setTextWidth(self.overlay.notes.width())
        document.documentLayout().documentSize()
        return document

    def test_real_wrapped_cjk_lines_are_compact_without_overlapping_at_three_fonts_and_widths(self):
        """比较实际QTextLine步进与字形行高，不能只用CSS数字通过测试。"""
        # 得意黑14像素在500宽度可容纳一段短句；重复夹具确保九组均实际折行。
        phrase = '保持村民持续生产，食物资源充足后先补房屋，再派新村民采集黄金，侦察兵继续探索周边。' * 3
        for size in (14, 18, 24):
            for width in (280, 355, 500):
                with self.subTest(size=size, width=width):
                    document = self.layout_document([phrase], width, size)
                    block = document.begin()
                    while block.isValid() and not block.text().startswith('保持村民'):
                        block = block.next()
                    layout = block.layout()
                    self.assertGreater(layout.lineCount(), 1)
                    for number in range(layout.lineCount()-1):
                        line, next_line = layout.lineAt(number), layout.lineAt(number+1)
                        advance = next_line.y()-line.y()
                        self.assertGreaterEqual(advance, line.height()-.5, '字形行框不能重叠')
                        self.assertLessEqual(advance, line.height()*1.3, '自然折行不能再接近两行高')

    def test_explicit_breaks_paragraphs_and_inline_images_remain_complete(self):
        notes = ['作者第一行\n作者第二行\n\n作者空行后的内容',
                 '@unit_worker/villager.webp@ 村民继续采集 @resource/resource_gold.webp@ 黄金，' * 4]
        for size in (14, 18, 24):
            document = self.layout_document(notes, 280, size)
            text = document.toPlainText()
            self.assertIn('作者第一行\n作者第二行\n\n作者空行后的内容', text)
            self.assertEqual(text.count('\ufffc'), 8, '全部资源内联图仍须存在')
            images = []
            block = document.begin()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.charFormat().isImageFormat():
                        images.append(fragment.charFormat().toImageFormat().height())
                    iterator += 1
                layout = block.layout()
                for number in range(layout.lineCount()-1):
                    line, next_line = layout.lineAt(number), layout.lineAt(number+1)
                    self.assertGreaterEqual(next_line.y()-line.y(), line.height()-.5,
                                            '图片行不能因收紧折行而互相覆盖')
                block = block.next()
            self.assertEqual(len(images), 8)
            self.assertTrue(all(height == size+2 for height in images))
            self.assertEqual(self.panel.order['build_order'][0]['notes'], notes)

    def test_official_lock_svg_files_and_all_existing_source_digests_are_preserved(self):
        root = Path(__file__).resolve().parent / '界面素材/图标/Lucide'
        metadata = json.loads((root/'来源.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['commit'], '1802f33f6c270395fcd981067f22719140e2ffbc')
        self.assertEqual(len(metadata['icons']), 26)
        names = {row['name'] for row in metadata['icons']}
        self.assertTrue({'play','pause','x','lock-keyhole','lock-keyhole-open'} <= names)
        for row in metadata['icons']:
            raw = (root/(row['name']+'.svg')).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row['sha256'])
            self.assertEqual(len(raw), row['bytes'])
            self.assertIn(b'stroke-width="2"', raw)
            self.assertIn(b'stroke="currentColor"', raw)
        self.assertEqual(hashlib.sha256((root/'LICENSE').read_bytes()).hexdigest(), metadata['license_sha256'])


if __name__ == '__main__':
    unittest.main()
