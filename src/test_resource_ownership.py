"""资源栏真实QObject所有权：未指定项不显示，全部子项随资源栏销毁。"""
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import sip
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication
from visual import Resources


class ResourceOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def test_unspecified_cells_stay_hidden_and_every_cell_dies_with_resource_bar(self):
        resource = Resources(Path())
        cells = list(resource.cells.values())
        try:
            resource.show()
            self.application.processEvents()
            self.assertTrue(all(not frame.isVisible() for frame, _, _ in cells))
            self.assertTrue(all(frame.parentWidget() is resource for frame, _, _ in cells))
            resource.set_step({"resources": {"food": 6}, "age": 1})
            self.application.processEvents()
            for key, (frame, _, _) in resource.cells.items():
                self.assertEqual(frame.isVisible(), key in ("food", "age"), key)
            resource.set_step({})
            self.assertTrue(all(frame.isHidden() for frame, _, _ in cells))
        finally:
            resource.close()
            resource.deleteLater()
            self.application.sendPostedEvents(None, QEvent.DeferredDelete)
            self.application.processEvents()
        self.assertTrue(sip.isdeleted(resource))
        self.assertTrue(all(sip.isdeleted(child) for cell in cells for child in cell))


if __name__ == "__main__":
    unittest.main()
