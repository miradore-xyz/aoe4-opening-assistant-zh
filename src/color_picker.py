"""软件内选色器：统一无边框标题栏与深色控件，不调用系统选色窗口。

HSV（色相、饱和度、明度）用于平面选色；保存值仍为兼容的六位RGB色值。
弹窗内实时预览，确认后才提交；取消、关闭或Esc不会改动已有颜色。
"""
import re

from PyQt5.QtCore import Qt, QRectF, pyqtSignal
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
                             QSlider, QToolButton, QVBoxLayout, QWidget)

from windowing import DialogBar, FramelessDialog


class _ColorPlane(QWidget):
    """以水平饱和度、垂直明度选色，支持拖动与局部方向键微调。"""
    changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hue, self.saturation, self.value = 0, 0, 0
        self.setMinimumHeight(194)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName("颜色选择区域")
        self.setToolTip("拖动选择颜色；方向键可微调")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(box, 9, 9)
        painter.save()
        painter.setClipPath(path)
        hue = QLinearGradient(box.topLeft(), box.topRight())
        hue.setColorAt(0, QColor("white"))
        hue.setColorAt(1, QColor.fromHsv(self.hue, 255, 255))
        painter.fillRect(box, hue)
        value = QLinearGradient(box.topLeft(), box.bottomLeft())
        value.setColorAt(0, QColor(0, 0, 0, 0))
        value.setColorAt(1, QColor(0, 0, 0, 255))
        painter.fillRect(box, value)
        painter.restore()
        painter.setPen(QPen(QColor("#45454b"), 1))
        painter.drawPath(path)
        x = box.left() + box.width() * self.saturation
        y = box.top() + box.height() * (1 - self.value)
        painter.setPen(QPen(QColor("#101014"), 4))
        painter.drawEllipse(QRectF(x - 5, y - 5, 10, 10))
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawEllipse(QRectF(x - 5, y - 5, 10, 10))

    def _select(self, point):
        self.saturation = max(0.0, min(1.0, (point.x() - 1) / max(1, self.width() - 2)))
        self.value = 1 - max(0.0, min(1.0, (point.y() - 1) / max(1, self.height() - 2)))
        self.changed.emit(self.saturation, self.value)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self._select(event.pos())
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._select(event.pos())
            event.accept()

    def keyPressEvent(self, event):
        changes = {Qt.Key_Left: (-.01, 0), Qt.Key_Right: (.01, 0),
                   Qt.Key_Up: (0, .01), Qt.Key_Down: (0, -.01)}
        if event.key() not in changes:
            return super().keyPressEvent(event)
        ds, dv = changes[event.key()]
        self.saturation = max(0.0, min(1.0, self.saturation + ds))
        self.value = max(0.0, min(1.0, self.value + dv))
        self.changed.emit(self.saturation, self.value)
        self.update()
        event.accept()


class ColorPickerDialog(FramelessDialog):
    """沿用应用弹窗语言，色板、色相条与六位色值双向同步。"""

    def __init__(self, initial, parent=None, title="选择颜色"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(448, 480)
        self.setMinimumSize(400, 470)
        self._color = QColor(initial)
        if not self._color.isValid():
            self._color = QColor("#1b1b1e")
        self._initial = QColor(self._color)
        self._hue = max(0, self._color.hsvHue())
        shell = QVBoxLayout(self)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)
        self.dialog_bar = DialogBar(self, title)
        self.dialog_bar.minimize_button.hide()
        self.dialog_bar.maximize_button.hide()
        shell.addWidget(self.dialog_bar)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(14)
        shell.addWidget(content, 1)
        self.plane = _ColorPlane()
        layout.addWidget(self.plane, 1)
        self.hue = QSlider(Qt.Horizontal)
        self.hue.setRange(0, 359)
        self.hue.setAccessibleName("色相")
        self.hue.setToolTip("拖动切换色相")
        self.hue.setFixedHeight(24)
        self.hue.setStyleSheet(
            "QSlider::groove:horizontal{height:10px;border:0;border-radius:5px;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #ff0000,stop:0.167 #ffff00,"
            "stop:0.333 #00ff00,stop:0.5 #00ffff,stop:0.667 #0000ff,stop:0.833 #ff00ff,stop:1 #ff0000);}"
            "QSlider::sub-page:horizontal,QSlider::add-page:horizontal{background:transparent;}"
            "QSlider::handle:horizontal{width:12px;margin:-4px 0;border-radius:6px;"
            "border:2px solid #f4f4f5;background:#202024;}"
        )
        layout.addWidget(self.hue)
        presets = QHBoxLayout()
        presets.setSpacing(10)
        for name, value in (("黑灰", "#1b1b1e"), ("浅灰", "#d4d4d8"), ("白色", "#f4f4f5"),
                            ("金色", "#dfb85c"), ("红色", "#db807a"), ("绿色", "#4aba86"),
                            ("蓝色", "#659fd7"), ("紫色", "#ac8fd3")):
            swatch = QToolButton()
            swatch.setFixedSize(30, 30)
            swatch.setAccessibleName(name)
            swatch.setToolTip(name + "  " + value.upper())
            swatch.setStyleSheet("QToolButton{background:" + value + ";border:1px solid #55555d;border-radius:7px;}"
                                "QToolButton:hover,QToolButton:focus{border:2px solid #f4f4f5;}")
            swatch.clicked.connect(lambda _checked=False, color=value: self._set_color(QColor(color)))
            presets.addWidget(swatch)
        presets.addStretch()
        layout.addLayout(presets)
        row = QHBoxLayout()
        row.setSpacing(12)
        old = QPushButton("当前")
        old.setAccessibleName("恢复当前颜色")
        old.setToolTip("恢复打开窗口时的颜色")
        old.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        foreground = "#111114" if self._initial.lightnessF() > .55 else "#f4f4f5"
        old.setStyleSheet("QPushButton{background:" + self._initial.name() + ";color:" + foreground +
                         ";border:1px solid #4b4b53;border-radius:8px;}")
        # 最小窗口不能把放大后的字挤回40像素高度，尺寸按当前字体建议值计算。
        old.setMinimumSize(max(62, old.sizeHint().width()), max(40, old.sizeHint().height()))
        old.clicked.connect(lambda: self._set_color(self._initial))
        row.addWidget(old)
        self.preview = QLabel()
        self.preview.setFixedSize(44, 40)
        self.preview.setAccessibleName("新颜色预览")
        row.addWidget(self.preview)
        row.addSpacing(4)
        row.addWidget(QLabel("色值"))
        self.hex_input = QLineEdit()
        self.hex_input.setAccessibleName("六位颜色值")
        self.hex_input.setPlaceholderText("例如 #1B1B1E")
        self.hex_input.setMaxLength(7)
        self.hex_input.setMinimumHeight(max(40, self.hex_input.sizeHint().height()))
        row.addWidget(self.hex_input, 1)
        layout.addLayout(row)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        self.accept_button = QPushButton("使用颜色")
        self.accept_button.setObjectName("Primary")
        self.accept_button.setDefault(True)
        self.accept_button.clicked.connect(self.accept)
        for button in (self.cancel_button, self.accept_button):
            button.setMinimumSize(88, 36)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.plane.changed.connect(self._plane_changed)
        self.hue.valueChanged.connect(self._hue_changed)
        self.hex_input.textChanged.connect(self._hex_changed)
        self._set_color(self._color)

    def _set_color(self, color, update_text=True):
        """统一同步显示；保留灰阶颜色的色相，方便从黑白继续选彩色。"""
        self._color = QColor(color)
        if self._color.hsvHue() >= 0:
            self._hue = self._color.hsvHue()
        self.plane.hue = self._hue
        self.plane.saturation = self._color.hsvSaturationF()
        self.plane.value = self._color.valueF()
        blocked = self.hue.blockSignals(True)
        self.hue.setValue(self._hue)
        self.hue.blockSignals(blocked)
        if update_text:
            blocked = self.hex_input.blockSignals(True)
            self.hex_input.setText(self._color.name().upper())
            self.hex_input.blockSignals(blocked)
        self.preview.setStyleSheet("QLabel{background:" + self._color.name() + ";border:1px solid #55555d;border-radius:8px;}")
        self.accept_button.setEnabled(True)
        self.plane.update()

    def _plane_changed(self, saturation, value):
        self._set_color(QColor.fromHsvF(self._hue / 360, saturation, value))

    def _hue_changed(self, value):
        self._hue = value
        self._plane_changed(self.plane.saturation, self.plane.value)

    def _hex_changed(self, text):
        match = re.fullmatch(r"#?([0-9a-fA-F]{6})", text.strip())
        if match:
            self._set_color(QColor("#" + match.group(1)), update_text=False)
        else:
            self.accept_button.setEnabled(False)

    @staticmethod
    def getColor(initial, parent=None, title="选择颜色"):
        """兼容原有调用的模态入口：取消返回无效颜色，不改变设置。"""
        dialog = ColorPickerDialog(initial, parent, title)
        try:
            return QColor(dialog._color) if dialog.exec_() == dialog.Accepted else QColor()
        finally:
            dialog.deleteLater()
