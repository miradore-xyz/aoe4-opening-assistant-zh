"""悬浮窗的小型独立锁柄：主体穿透游戏，只有锁槽保留鼠标与键盘操作。

handle=锁柄，anchor=锁槽；只绘制当前开锁或闭锁图标，操作说明交给无障碍名称与悬停提示。
"""
from __future__ import annotations

import weakref
from pathlib import Path
import sys

from app_fonts import make_ui_font, register_ui_fonts
from PyQt5 import sip
from PyQt5.QtCore import QEvent, QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt5.QtWidgets import QPushButton
from visual import line_icon


def _alive(widget):
    return widget is not None and not sip.isdeleted(widget)


def overlay_icon_directory():
    """沿用现有设计资源路径；SVG（矢量图）及MIT许可一起随目录打包。"""
    root = Path(sys._MEIPASS) / "assets/design" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "界面素材"
    return root / "overlay-icons"


class OverlayLockHandle(QPushButton):
    """附着锁槽的独立工具窗；cleanup（清理）可重复调用，供助手退出时统一关闭。"""

    def __init__(self, overlay, anchor_widget, on_toggle, parent=None):
        super().__init__(parent if parent is not None else overlay)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("OverlayLockHandle")
        font = make_ui_font()
        font.setPixelSize(12)
        self.setFont(font)
        self.setStyleSheet("font-family:'Smiley Sans';font-size:12px;")
        self._overlay = weakref.ref(overlay)
        self._anchor = weakref.ref(anchor_widget)
        self._on_toggle = on_toggle
        self._cleaned = False
        self._locked = True
        self._hovered = False
        self._keyboard_focus = False
        self._activity = 0.0
        self._preferred = QSize(28, 28)
        self.resize(self._preferred)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(120)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._animation_value)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self.sync_position)
        self.clicked.connect(self._activate)
        overlay.installEventFilter(self)
        anchor_widget.installEventFilter(self)
        overlay.destroyed.connect(self.cleanup)
        if anchor_widget is not overlay:
            anchor_widget.destroyed.connect(self.cleanup)
        self.set_locked(True)
        self._schedule_sync()

    def sizeHint(self):
        return QSize(28, 28)

    def set_locked(self, locked):
        """同步主体状态；不会自行修改主体的穿透标志或移动主体。"""
        if self._cleaned:
            return
        self._locked = bool(locked)
        self.setText("")
        # 与播放/关闭共用官方Lucide原稿，只替换金色，锁定/解锁以真实形状区分。
        self.setIcon(line_icon("lock-keyhole" if self._locked else "lock-keyhole-open", "#e5c88e"))
        current = "已锁定" if self._locked else "已解锁"
        action = "解锁悬浮窗" if self._locked else "锁定悬浮窗"
        self.setAccessibleName(f"{action}（当前{current}）")
        self.setToolTip(f"当前{current}，点击{action}。" +
                        ("只有此按钮接收点击，其他区域的鼠标点击直接交给游戏。"
                         if self._locked else "解锁时可操作、拖动和调整悬浮窗。"))
        self.update()

    def _activate(self, _checked=False):
        if not self._cleaned and self._on_toggle is not None:
            self._on_toggle()

    def _schedule_sync(self):
        if not self._cleaned:
            self._sync_timer.start(0)

    def eventFilter(self, watched, event):
        if self._cleaned:
            return False
        kind = event.type()
        if kind in (QEvent.Hide, QEvent.Close):
            self._sync_timer.stop()
            self.hide()
        elif kind in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.WindowStateChange,
                      QEvent.ParentChange, QEvent.LayoutRequest, QEvent.WinIdChange):
            self._schedule_sync()
        return False

    def sync_position(self):
        """只移动这个小工具窗；可供外部布局完成后主动同步一次。"""
        if self._cleaned:
            return
        overlay, anchor = self._overlay(), self._anchor()
        if not _alive(overlay) or not _alive(anchor):
            self.cleanup()
            return
        if (not overlay.isVisible() or overlay.isMinimized()
                or not anchor.isVisibleTo(overlay) or anchor.width() <= 0 or anchor.height() <= 0):
            self.hide()
            return
        slot = QRect(anchor.mapToGlobal(QPoint()), anchor.size())
        width, height = min(self._preferred.width(), slot.width()), min(self._preferred.height(), slot.height())
        target = QRect(slot.x() + (slot.width() - width) // 2,
                       slot.y() + (slot.height() - height) // 2, width, height)
        if self.geometry() != target:
            self.setGeometry(target)
        if not self.isVisible():
            self.show()

    def _animation_value(self, value):
        self._activity = float(value)
        self.update()

    def _animate_state(self):
        if self._cleaned:
            return
        target = 2.0 if self.isDown() else 1.0 if self._hovered or (self.hasFocus() and self._keyboard_focus) else 0.0
        self._animation.stop()
        self._animation.setStartValue(self._activity)
        self._animation.setEndValue(target)
        self._animation.start()

    def enterEvent(self, event):
        self._hovered = True
        self._animate_state()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._animate_state()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._keyboard_focus = False
        super().mousePressEvent(event)
        self._animate_state()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._animate_state()

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(event)
        self._animate_state()

    def focusOutEvent(self, event):
        self._keyboard_focus = False
        super().focusOutEvent(event)
        self._animate_state()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)
        self._animate_state()

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        self._animate_state()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        active = min(1.0, self._activity)
        pressed = max(0.0, self._activity - 1.0)
        # 极低透明度保留独立原生窗口的完整点击槽，不画常驻胶囊边框或底板。
        painter.fillRect(self.rect(), QColor(12, 27, 40, 1))
        bounds = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        if active or pressed:
            background = QColor(20, 39, 54, round(95 * active + 35 * pressed))
            painter.setPen(Qt.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(bounds.adjusted(1, 1, -1, -1), 7, 7)
        if self.hasFocus() and self._keyboard_focus:
            painter.setPen(QPen(QColor("#ffe4a3"), 1, Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(bounds.adjusted(2, 2, -2, -2), 5, 5)
        # 沿用独立点击窗及其动画，只替换18像素图形，不改变命中、穿透或同步位置。
        edge = min(18, self.width(), self.height())
        painter.setOpacity(1 - pressed * .16)
        self.icon().paint(painter, QRect((self.width()-edge)//2, (self.height()-edge)//2, edge, edge),
                          Qt.AlignCenter, QIcon.Normal)
    def cleanup(self, *_args):
        """退出或锁槽销毁时隐藏并删除工具窗；重复调用不会访问已销毁的 Qt 对象。"""
        if self._cleaned:
            return
        self._cleaned = True
        self._on_toggle = None
        if not _alive(self):
            return
        self._sync_timer.stop()
        self._animation.stop()
        for reference in (self._overlay, self._anchor):
            widget = reference()
            if _alive(widget):
                widget.removeEventFilter(self)
                try:
                    widget.destroyed.disconnect(self.cleanup)
                except (TypeError, RuntimeError):
                    pass
        self.hide()
        self.deleteLater()
