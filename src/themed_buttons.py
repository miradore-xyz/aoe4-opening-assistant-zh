"""中性动作按钮：保留标准按钮的键盘、无障碍与点击语义。"""

from app_fonts import make_ui_font, register_ui_fonts
from PyQt5.QtCore import Qt, QSize, QRectF
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen
from PyQt5.QtWidgets import QPushButton, QStyle, QStyleOptionButton

from visual import GOLD, PALETTE


class TapestryAction(QPushButton):
    """简洁中性按钮；保留 variant（旧纹样名）参数以兼容已有调用。"""

    def __init__(self, caption, callback, variant='knight', parent=None):
        super().__init__(caption, parent)
        self.variant = variant
        self._keyboard_focus_visible = False
        self.setObjectName('TapestryAction')
        # 背景和边框由下方统一自绘，避免全局 QPushButton 样式在圆角下再垫一层矩形。
        self.setStyleSheet('QPushButton {background:transparent;border:0;padding:0;}')
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setIconSize(QSize(18, 18))
        font = make_ui_font()
        font.setPixelSize(14)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        self.setMinimumHeight(36)
        if caption:
            self.setAccessibleName(caption.replace('&', ''))
            self.setToolTip(caption.replace('&', ''))
        if callback is not None:
            self.clicked.connect(callback)

    def sizeHint(self):
        """按文字与图标计算建议宽度，固定尺寸和布局仍可覆盖建议值。"""
        metrics = QFontMetrics(self.font())
        label_width = metrics.size(Qt.TextShowMnemonic, self.text()).width()
        icon_width = self.iconSize().width() if not self.icon().isNull() else 0
        gap = 8 if label_width and icon_width else 0
        return QSize(max(36, label_width + icon_width + gap + 30),
                     max(36, metrics.height() + 14, self.iconSize().height() + 14))

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        # 自绘只改变外观，点击、键盘激活与辅助技术语义继续由 QPushButton 提供。
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        enabled = self.isEnabled()
        down = self.isDown()
        checked = self.isCheckable() and self.isChecked()
        hovered = enabled and self.underMouse()
        primary = self.objectName() in ('Primary', 'StartButton') or bool(self.property('primaryAction'))
        quiet = self.objectName() == 'Quiet'
        compact = not self.text() or rect.width() < 86
        radius = min(8.0, rect.height() / 2)  # 主次按钮保持相同圆角，只用明暗区分操作层级。

        if not enabled:
            background, border, foreground = '#19191c', '#29292e', '#62626c'
        elif primary:
            background = '#bfbfc6' if down else '#ffffff' if hovered else '#e8e8eb'
            border, foreground = background, '#141416'
        elif checked:
            background = '#242429' if down else '#38383F' if hovered else '#2D2D33'
            border, foreground = '#6B6B75', '#F4F4F5'
        else:
            background = '#18181b' if down else '#2b2b30' if hovered else '#202023'
            if quiet and not hovered and not down:
                background = PALETTE['base']
            border = '#51515a' if hovered else '#38383f'
            foreground = '#f4f4f5'
        painter.setBrush(QColor(background))
        painter.setPen(QPen(QColor(border), 1))
        painter.drawRoundedRect(rect, radius, radius)

        # 由标准样式选项读取焦点与布局方向，兼容鼠标、键盘和辅助技术。
        option = QStyleOptionButton()
        self.initStyleOption(option)
        inset = 6 if compact else 12
        content = rect.adjusted(inset, 4, -inset, -4)
        font = self.font()
        painter.setFont(font)
        metrics = QFontMetrics(font)
        label = self.text()
        has_icon = not self.icon().isNull()
        icon_size = self.iconSize().boundedTo(QSize(max(0, int(content.width())), max(0, int(content.height()))))
        gap = 8 if label and has_icon else 0
        label_available = max(0, int(content.width()) - (icon_size.width() + gap if has_icon else 0))
        label = metrics.elidedText(label, Qt.ElideRight, label_available)
        label_width = min(label_available, metrics.size(Qt.TextShowMnemonic, label).width())
        group_width = label_width + (icon_size.width() + gap if has_icon else 0)
        x = content.center().x() - group_width / 2
        offset = 1 if down else 0
        if has_icon:
            icon_rect = QRectF(x + offset, content.center().y() - icon_size.height() / 2 + offset,
                               icon_size.width(), icon_size.height())
            if option.direction == Qt.RightToLeft and label:
                icon_rect.moveLeft(x + label_width + gap + offset)
            mode = QIcon.Normal if enabled else QIcon.Disabled
            state = QIcon.On if checked else QIcon.Off
            if primary and enabled:
                # 白底主动作使用深色线条图标，避免传入的浅色图标消失。
                ratio = self.devicePixelRatioF()
                pixels = QSize(max(1, round(icon_size.width() * ratio)),
                               max(1, round(icon_size.height() * ratio)))
                symbol = self.icon().pixmap(pixels, mode, state)
                tint = QPainter(symbol)
                tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
                tint.fillRect(symbol.rect(), QColor(foreground))
                tint.end()
                painter.drawPixmap(icon_rect, symbol, QRectF(symbol.rect()))
            else:
                self.icon().paint(painter, icon_rect.toRect(), Qt.AlignCenter, mode, state)
            if option.direction != Qt.RightToLeft:
                x += icon_size.width() + gap
        if label:
            label_rect = QRectF(x + offset, content.top() + offset, label_width, content.height())
            painter.setPen(QColor(foreground))
            mnemonic = (Qt.TextShowMnemonic if self.style().styleHint(QStyle.SH_UnderlineShortcut, option, self)
                        else Qt.TextHideMnemonic)
            painter.drawText(label_rect, Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine | mnemonic, label)
        if enabled and self.hasFocus() and self._keyboard_focus_visible:
            # 键盘导航保留清晰的实线焦点环，圆角跟随按钮外形。
            # 焦点环只绘制在按钮内部，不改变尺寸，避免聚焦后布局或文字跳动。
            focus_rect = rect.adjusted(3, 3, -3, -3)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor('#54545e' if primary else GOLD), 2, Qt.SolidLine))
            focus_radius = max(1, radius - 3)
            painter.drawRoundedRect(focus_rect, focus_radius, focus_radius)

    def mousePressEvent(self, event):
        # 已通过键盘聚焦的按钮再次被鼠标点击时，不会重新触发 focusInEvent，需在这里切换。
        self._keyboard_focus_visible = False
        super().mousePressEvent(event)
        self.update()

    def keyPressEvent(self, event):
        # 鼠标聚焦后直接使用空格或回车，也应重新获得键盘操作的可见焦点提示。
        if event.key() not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
            self._keyboard_focus_visible = True
        super().keyPressEvent(event)
        self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def focusInEvent(self, event):
        if event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason):
            self._keyboard_focus_visible = True
        elif event.reason() == Qt.MouseFocusReason:
            self._keyboard_focus_visible = False
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()
