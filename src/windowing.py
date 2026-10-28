"""自绘桌面窗口框架：保留系统拖动习惯，标题栏与界面采用同一视觉语言。

FramelessWindow=无边框窗口，WindowBar=自绘标题栏，fallback=兼容性替代实现。
FramelessDialog=保留模态语义的无边框弹窗，DialogBar=弹窗标题栏。
主窗口背景仍由主界面绘制；弹窗统一使用近黑底色和细边圆角。
"""
from __future__ import annotations

import sys

from PyQt5.QtCore import Qt, QEvent, QPoint, QRect, QRectF, QSize
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QApplication, QAbstractButton, QDialog, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
    QWidget,
)


class _FramelessBehavior:
    """共享窗口行为；普通 Python 混入类不叠加 QWidget 与 QDialog 的 Qt 基类。"""

    RESIZE_MARGIN = 8  # 逻辑像素会随 Windows 显示缩放自动换算。

    def __init__(self, parent=None, flags=Qt.Window):
        super().__init__(parent, flags | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._resize_state = None
        self._edge_cursor_widget = None
        self._edge_cursor_previous = None
        self._windowing_active = True
        # 应用级过滤器只处理本窗口的子控件，弹窗与其他顶层窗口完全跳过。
        QApplication.instance().installEventFilter(self)
        QApplication.instance().aboutToQuit.connect(self.cleanup_windowing)

    def cleanup_windowing(self):
        """退出清理接口；派生类覆盖 closeEvent 时也可直接调用，重复调用安全。"""
        if not self._windowing_active:
            return
        self._windowing_active = False
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
            try:
                application.aboutToQuit.disconnect(self.cleanup_windowing)
            except (TypeError, RuntimeError):
                pass
        self._finish_edge_resize()
        self._clear_edge_cursor()

    def showEvent(self, event):
        # close 后重新 show 的窗口仍然可拖动缩放。
        if not self._windowing_active:
            application = QApplication.instance()
            self._windowing_active = True
            application.installEventFilter(self)
            application.aboutToQuit.connect(self.cleanup_windowing)
        super().showEvent(event)

    def closeEvent(self, event):
        super().closeEvent(event)

    def event(self, event):
        kind = event.type()
        if kind == QEvent.DeferredDelete and getattr(self, "_windowing_active", False):
            self.cleanup_windowing()
        result = super().event(event)
        if kind == QEvent.Close and event.isAccepted() and getattr(self, "_windowing_active", False):
            # 即使派生类完全覆盖 closeEvent，也在已接受关闭后清理；忽略关闭则保留交互。
            self.cleanup_windowing()
        return result

    def _resize_edges(self, position):
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edges()
        point = QPoint(position)
        if not self.rect().contains(point):
            return Qt.Edges()
        edges = Qt.Edges()
        if point.x() < self.RESIZE_MARGIN:
            edges |= Qt.LeftEdge
        elif point.x() >= self.width() - self.RESIZE_MARGIN:
            edges |= Qt.RightEdge
        if point.y() < self.RESIZE_MARGIN:
            edges |= Qt.TopEdge
        elif point.y() >= self.height() - self.RESIZE_MARGIN:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _edge_cursor(edges):
        """把边缘组合映射为水平、垂直或对角缩放光标。"""
        if ((edges & Qt.TopEdge and edges & Qt.LeftEdge)
                or (edges & Qt.BottomEdge and edges & Qt.RightEdge)):
            return Qt.SizeFDiagCursor
        if ((edges & Qt.TopEdge and edges & Qt.RightEdge)
                or (edges & Qt.BottomEdge and edges & Qt.LeftEdge)):
            return Qt.SizeBDiagCursor
        if edges & (Qt.LeftEdge | Qt.RightEdge):
            return Qt.SizeHorCursor
        return Qt.SizeVerCursor

    def _clear_edge_cursor(self):
        widget = self._edge_cursor_widget
        previous = self._edge_cursor_previous
        self._edge_cursor_widget = None
        self._edge_cursor_previous = None
        if widget is not None:
            try:
                if previous[0]:
                    widget.setCursor(previous[1])
                else:
                    widget.unsetCursor()
            except RuntimeError:
                pass  # 子控件在换页时已销毁，不再修改它。

    def _set_edge_cursor(self, widget, edges):
        if self._edge_cursor_widget is not widget:
            self._clear_edge_cursor()
            self._edge_cursor_widget = widget
            self._edge_cursor_previous = (
                widget.testAttribute(Qt.WA_SetCursor), widget.cursor(),
            )
        widget.setCursor(self._edge_cursor(edges))

    def _try_system_resize(self, edges):
        """优先由窗口系统缩放，保留系统跨屏与缩放比例处理。"""
        handle = self.windowHandle()
        method = getattr(handle, "startSystemResize", None) if handle else None
        if method is not None:
            try:
                return bool(method(edges))
            except (RuntimeError, TypeError):
                pass
        return False

    def _begin_edge_resize(self, edges, global_position):
        if self._try_system_resize(edges):
            return
        # 旧平台插件或离屏测试没有系统接口时，按初始几何计算替代缩放。
        self._resize_state = (edges, QPoint(global_position), QRect(self.geometry()))
        self.grabMouse()

    def _resize_from_pointer(self, global_position):
        if self._resize_state is None:
            return
        edges, origin, initial = self._resize_state
        delta = global_position - origin
        minimum = self.minimumSize()
        maximum = self.maximumSize()
        result = QRect(initial)
        if edges & Qt.LeftEdge:
            width = max(minimum.width(), min(maximum.width(), initial.width() - delta.x()))
            result.setLeft(initial.right() - width + 1)
        elif edges & Qt.RightEdge:
            width = max(minimum.width(), min(maximum.width(), initial.width() + delta.x()))
            result.setWidth(width)
        if edges & Qt.TopEdge:
            height = max(minimum.height(), min(maximum.height(), initial.height() - delta.y()))
            result.setTop(initial.bottom() - height + 1)
        elif edges & Qt.BottomEdge:
            height = max(minimum.height(), min(maximum.height(), initial.height() + delta.y()))
            result.setHeight(height)
        self.setGeometry(result)

    def _finish_edge_resize(self):
        if self._resize_state is not None:
            self._resize_state = None
            if QWidget.mouseGrabber() is self:
                self.releaseMouse()

    def eventFilter(self, watched, event):
        # 子控件初始化完成时开启鼠标跟踪，边缘无须先按住鼠标即可显示光标。
        if not self._windowing_active:
            return False
        kind = event.type()
        if kind == QEvent.ChildPolished and isinstance(watched, QWidget) and watched.window() is self:
            child = event.child()
            if isinstance(child, QWidget) and child.window() is self:
                child.setMouseTracking(True)
        if not isinstance(watched, QWidget) or watched.window() is not self:
            return False
        if kind in (QEvent.Hide, QEvent.WindowStateChange) and watched is self:
            self._finish_edge_resize()
            self._clear_edge_cursor()
        if kind == QEvent.MouseButtonRelease and self._resize_state is not None:
            if event.button() == Qt.LeftButton:
                self._finish_edge_resize()
                return True
        if kind == QEvent.MouseMove and self._resize_state is not None:
            self._resize_from_pointer(event.globalPos())
            return True
        if kind in (QEvent.MouseMove, QEvent.MouseButtonPress):
            # 标题栏按钮优先于边缘，避免关闭按钮上沿被当作缩放手柄。
            if isinstance(watched, QAbstractButton):
                self._clear_edge_cursor()
                return False
            edges = self._resize_edges(self.mapFromGlobal(event.globalPos()))
            if kind == QEvent.MouseMove:
                if edges and not event.buttons():
                    self._set_edge_cursor(watched, edges)
                elif not self._resize_state:
                    self._clear_edge_cursor()
            elif edges and event.button() == Qt.LeftButton:
                self._begin_edge_resize(edges, event.globalPos())
                return True
        elif kind == QEvent.Leave and self._resize_state is None:
            if watched is self._edge_cursor_widget:
                self._clear_edge_cursor()
        return False


class FramelessWindow(_FramelessBehavior, QWidget):
    """8 个逻辑像素的窗口边缘可缩放，内部控件保留自己的鼠标行为。"""


class FramelessDialog(_FramelessBehavior, QDialog):
    """复用主窗口交互，保留 exec_、accept、reject 与 Esc 的模态对话框。"""

    def __init__(self, parent=None, flags=Qt.Dialog):
        super().__init__(parent, flags)
        self.setObjectName("FramelessDialog")
        self.setAutoFillBackground(False)
        # 精确限定根弹窗，覆盖全局 QDialog 底色，避免透明圆角被矩形背景填满。
        self.setStyleSheet("QDialog#FramelessDialog {background:transparent; border:0;}")

    def done(self, result):
        # QDialog 的确认、取消和 Esc 均经过 done；结束模态循环时撤销应用级过滤器。
        self.cleanup_windowing()
        super().done(result)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#303034"), 1))
        painter.setBrush(QColor("#0F0F11"))
        radius = 0 if self.isMaximized() or self.isFullScreen() else 14
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


class _WindowControl(QAbstractButton):
    """以矢量线条绘制窗口按钮，高分辨率屏幕也保持清晰。"""

    def __init__(self, action, window, parent=None, close_label="关闭软件"):
        super().__init__(parent)
        self.action = action
        self.target = window
        self.close_label = close_label
        self._keyboard_focus = False
        self.setFixedSize(36, 30)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.ArrowCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setObjectName("WindowControl")
        self.refresh_label()

    def sizeHint(self):
        return QSize(36, 30)

    def refresh_label(self):
        names = {
            "minimize": "最小化窗口",
            "maximize": "还原窗口" if self.target.isMaximized() else "最大化窗口",
            "close": self.close_label,
        }
        self.setToolTip(names[self.action])
        self.setAccessibleName(names[self.action])
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 1, -2, -1)
        hovered = self.underMouse()
        if self.action == "close" and (hovered or self.isDown()):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(166, 73, 82, 150 if self.isDown() else 90))
            painter.drawRoundedRect(rect, 7, 7)
        elif hovered or self.isDown():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(242, 241, 237, 30 if self.isDown() else 16))
            painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(QPen(QColor("#f4f4f5" if hovered else "#a1a1aa"), 1.2,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx, cy = self.width() / 2, self.height() / 2
        if self.action == "minimize":
            # 横线与方框、关闭叉共用视觉中线，避免最小化图标显得向下偏移。
            painter.drawLine(QPoint(round(cx - 4.5), round(cy)), QPoint(round(cx + 4.5), round(cy)))
        elif self.action == "close":
            painter.drawLine(QPoint(round(cx - 4), round(cy - 4)), QPoint(round(cx + 4), round(cy + 4)))
            painter.drawLine(QPoint(round(cx + 4), round(cy - 4)), QPoint(round(cx - 4), round(cy + 4)))
        elif self.target.isMaximized():
            # 还原图标的后层只绘制露出的上边、右边，避免线条叠成噪点。
            path = QPainterPath()
            path.moveTo(cx - 2.5, cy - 4.5)
            path.lineTo(cx + 4.5, cy - 4.5)
            path.lineTo(cx + 4.5, cy + 2.5)
            painter.drawPath(path)
            painter.drawRect(QRectF(cx - 4.5, cy - 2.5, 7, 7))
        else:
            painter.drawRect(QRectF(cx - 4.5, cy - 4.5, 9, 9))
        if self.hasFocus() and self._keyboard_focus:
            painter.setPen(QPen(QColor("#d5d5d8"), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        # 键盘导航显示焦点圈；启动、程序设焦点和鼠标点击不额外描边。
        self._keyboard_focus = event.reason() in (
            Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason,
        )
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._keyboard_focus = False
        self.update()
        super().focusOutEvent(event)


class _TitleBarBehavior:
    """主窗口与弹窗共享拖动、双击最大化和还原行为。"""

    def eventFilter(self, watched, event):
        if watched is self.target and event.type() == QEvent.WindowStateChange:
            self._drag_state = None
            self.maximize_button.refresh_label()
        return False

    def toggle_maximized(self):
        self._drag_state = None
        if self.target.isMaximized():
            self.target.showNormal()
        else:
            self.target.showMaximized()
        self.maximize_button.refresh_label()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.target.windowHandle()
            method = getattr(handle, "startSystemMove", None) if handle else None
            # Windows 透明无边框窗体由 Qt 的逻辑坐标路径拖动，移动只改变位置。
            # 标题拖动不依赖系统移动循环及其非客户区/DPI（显示缩放）尺寸换算。
            # 其他平台以及非透明/有边框窗体继续使用系统拖动，保留平台原有行为。
            qt_only_move = (
                sys.platform == "win32"
                and bool(self.target.windowFlags() & Qt.FramelessWindowHint)
                and self.target.testAttribute(Qt.WA_TranslucentBackground)
            )
            if method is not None and not qt_only_move:
                try:
                    if method():
                        event.accept()
                        return
                except (RuntimeError, TypeError):
                    pass
            self._drag_state = (QPoint(event.globalPos()), QPoint(self.target.pos()),
                                event.pos().x() / max(1, self.width()), event.pos().y())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_state is not None and event.buttons() & Qt.LeftButton:
            origin, initial, ratio, vertical_offset = self._drag_state
            delta = event.globalPos() - origin
            if delta.manhattanLength() < QApplication.startDragDistance():
                return
            if self.target.isMaximized():
                # 替代实现也允许把最大化窗口从标题栏拖回普通尺寸。
                self.target.showNormal()
                initial = event.globalPos() - QPoint(round(self.target.width() * ratio), min(vertical_offset, 24))
                origin = QPoint(event.globalPos())
                self._drag_state = (origin, initial, ratio, vertical_offset)
            self.target.move(initial + event.globalPos() - origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_state = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class WindowBar(_TitleBarBehavior, QWidget):
    """54 像素一体式标题栏：轻量品牌、可插入导航和自绘窗口按钮。"""

    def __init__(self, window):
        super().__init__(window)
        self.target = window
        self._drag_state = None
        self.setObjectName("WindowBar")
        self.setFixedHeight(54)
        self.setMouseTracking(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 0, 12, 0)
        row.setSpacing(0)

        # 品牌区整体穿透鼠标事件，图片字标处也能直接拖动窗口。
        brand = QWidget(self)
        brand.setObjectName("WindowBrand")
        brand.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        brand.setFixedWidth(117)
        # 局部导入避免窗口框架单独用于测试时强制载入完整视觉模块。
        from branding import APP_NAME, brand_wordmark
        brand.setAccessibleName(APP_NAME)
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(0)
        brand_row.setAlignment(Qt.AlignVCenter)
        self.title = QLabel(brand)
        self.title.setPixmap(brand_wordmark())
        self.title.setAccessibleName(APP_NAME)
        self.title.setObjectName("WindowBrandTitle")
        self.title.setStyleSheet(
            "background:transparent; color:#f4f4f5; font-size:15px; "
            "font-weight:600; border:0; padding:0;"
        )
        self.title.setFixedSize(117, 26)
        self.title.setWordWrap(False)
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        brand_row.addWidget(self.title)
        # 保留属性供旧调用兼容；品牌收为单行，避免副标题挤占标题栏。
        self.subtitle = QLabel("帝国时代4", brand)
        self.subtitle.setObjectName("WindowBrandSubtitle")
        self.subtitle.hide()
        row.addWidget(brand)
        row.addSpacing(24)

        # 导航按钮由主界面插入，按钮正常接收鼠标；容器空白会冒泡到标题栏。
        self.navigation_container = QWidget(self)
        self.navigation_container.setObjectName("WindowNavigation")
        self.navigation_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.navigation_layout = QHBoxLayout(self.navigation_container)
        self.navigation_layout.setContentsMargins(0, 0, 0, 0)
        self.navigation_layout.setSpacing(8)
        row.addWidget(self.navigation_container)
        row.addStretch(1)
        # 轻量状态区供主界面放置准备进度；空布局不占宽度，避免挤压导航。
        self.status_container = QWidget(self)
        self.status_container.setObjectName("WindowStatus")
        self.status_container.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.status_layout = QHBoxLayout(self.status_container)
        self.status_layout.setContentsMargins(0, 0, 0, 0)
        self.status_layout.setSpacing(0)
        row.addWidget(self.status_container)
        row.addSpacing(12)
        self.minimize_button = _WindowControl("minimize", window, self)
        self.maximize_button = _WindowControl("maximize", window, self)
        self.close_button = _WindowControl("close", window, self)
        row.addWidget(self.minimize_button)
        row.addWidget(self.maximize_button)
        row.addWidget(self.close_button)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)
        window.installEventFilter(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor(242, 241, 237, 12), 1))
        painter.drawLine(24, self.height() - 1, self.width() - 24, self.height() - 1)


class DialogBar(_TitleBarBehavior, QWidget):
    """放在弹窗根布局首行；关闭只取消当前弹窗，不退出主程序。"""

    def __init__(self, window, title):
        super().__init__(window)
        self.target = window
        self._drag_state = None
        self.setObjectName("DialogBar")
        self.setAccessibleName(f"{title}标题栏")
        self.setFixedHeight(46)
        self.setMouseTracking(True)
        self.setStyleSheet(
            "QWidget#DialogBar {background:transparent; border:0;}"
            "QLabel#DialogBarTitle {background:transparent; border:0; padding:0;"
            "color:#f4f4f5; font-size:14px; font-weight:600;}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 8, 0)
        row.setSpacing(0)
        self.title = QLabel(title, self)
        self.title.setObjectName("DialogBarTitle")
        self.title.setWordWrap(False)
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(self.title, 1)
        row.addSpacing(12)
        self.minimize_button = _WindowControl("minimize", window, self)
        self.maximize_button = _WindowControl("maximize", window, self)
        self.close_button = _WindowControl("close", window, self, close_label="关闭窗口")
        row.addWidget(self.minimize_button)
        row.addWidget(self.maximize_button)
        row.addWidget(self.close_button)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.reject)
        window.installEventFilter(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#242427"), 1))
        painter.drawLine(16, self.height() - 1, self.width() - 16, self.height() - 1)
