"""地图右下角的点选对象小卡；只显示传入事件和已核实的名称/属性。

SelectionDetailsCard（点选详情控制器）没有播放/定位信号，不会自行改变时刻或镜头。
官方基础属性与对局当前状态必须由调用方区分；原始命令不猜成实际单位实例。
"""
from __future__ import annotations

import math
from functools import lru_cache

from PyQt5.QtCore import QByteArray, QEvent, QObject, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app_fonts import make_ui_font
from replay_placement_markers import building_metadata
from visual import line_icon


# Lucide target（目标）官方原路径，只替换currentColor；完整ISC许可已随项目Lucide/LICENSE提供。
# 来源：https://github.com/lucide-icons/lucide/blob/main/icons/target.svg
_TARGET_SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"
fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>'''

_STYLE = """
QFrame#ReplaySelectionCard {background:#161619;border:1px solid #303037;border-radius:12px;}
QLabel {background:transparent;border:0;color:#dddde4;font-family:'Smiley Sans';font-size:13px;}
QLabel#SelectionName {font-size:15px;color:#f1f1f5;}
QLabel#SelectionMuted {color:#9999a4;font-size:12px;}
QPushButton#SelectionClose {background:transparent;color:#bdbdc7;border:1px solid transparent;border-radius:6px;
min-height:22px;max-height:22px;min-width:22px;max-width:22px;padding:0;}
QPushButton#SelectionClose:hover {background:#303037;}
QPushButton#SelectionClose:focus {border-color:#8d8d99;}
"""


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clock(value):
    if not _finite(value):
        return "时刻未提供"
    seconds = max(0, int(value))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _text(value):
    if isinstance(value, bool):
        return "是" if value else "否"
    if _finite(value):
        return f"{value:g}"
    return str(value)


class _ElidedLabel(QLabel):
    """短卡显示省略文字，完整名称仍保留在无障碍信息中。"""

    def __init__(self, role=None, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setTextFormat(Qt.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        if role:
            self.setObjectName(role)

    def set_full_text(self, value):
        self._full = str(value or "")
        self.setAccessibleName(self._full)
        self._refresh()

    def _refresh(self):
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(0, self.width())))

    def resizeEvent(self, event):
        self._refresh()
        super().resizeEvent(event)


@lru_cache(maxsize=8)
def _target_pixmap(size=40):
    """缺少精确官方实体图时使用Lucide目标图标，保持原三圆路径。"""
    picture = QPixmap(size*2, size*2)
    picture.setDevicePixelRatio(2)
    picture.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(_TARGET_SVG.replace(b"currentColor", b"#c2c2cc")))
    painter = QPainter(picture)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return picture


class SelectionDetailsCard(QObject):
    """视口内独立的小QFrame（卡片），空白地图不增加透明输入层。"""

    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._viewport = parent
        self._enabled = True
        self._event = None
        self._closed = False
        self._cleaned = False
        self._anchoring = False
        self._anchor = QRect()
        self._available_rect = QRect()
        self._bottom_reserved = 0
        self._property_count = 0
        self.icon_is_official = False
        self.frame = QFrame(parent)
        self.frame.setObjectName("ReplaySelectionCard")
        self.frame.setProperty("replay_selection_card", True)
        self.frame.setStyleSheet(_STYLE)
        self.frame.setAccessibleName("点选对象详情")
        self.frame.setFont(make_ui_font(13))
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(40, 40)
        self.icon.setAccessibleName("对象图标")
        header.addWidget(self.icon)
        title_column = QVBoxLayout()
        title_column.setSpacing(4)
        self.name = _ElidedLabel("SelectionName")
        self.name.setFont(make_ui_font(15))
        self.kind = _ElidedLabel("SelectionMuted")
        title_column.addWidget(self.name)
        title_column.addWidget(self.kind)
        header.addLayout(title_column, 1)
        self.close_button = QPushButton()
        self.close_button.setObjectName("SelectionClose")
        self.close_button.setIcon(line_icon("x", "#bdbdc7"))
        self.close_button.setIconSize(QSize(16, 16))
        self.close_button.setFixedSize(24, 24)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setAccessibleName("关闭点选对象详情")
        self.close_button.setToolTip("")
        self.close_button.clicked.connect(self._close)
        header.addWidget(self.close_button, 0, Qt.AlignTop)
        layout.addLayout(header)
        info = QHBoxLayout()
        info.setSpacing(10)
        self.player = _ElidedLabel()
        self.time = QLabel()
        self.time.setObjectName("SelectionMuted")
        self.time.setTextFormat(Qt.PlainText)
        info.addWidget(self.player, 1)
        info.addWidget(self.time)
        layout.addLayout(info)
        self.status = _ElidedLabel("SelectionMuted")
        layout.addWidget(self.status)
        self.properties = QWidget()
        self.properties_layout = QVBoxLayout(self.properties)
        self.properties_layout.setContentsMargins(0, 0, 0, 0)
        self.properties_layout.setSpacing(6)
        layout.addWidget(self.properties)
        self.properties.hide()
        self._install_filters()
        self.frame.hide()

    def _install_filters(self):
        self.frame.installEventFilter(self)
        for child in self.frame.findChildren(QWidget):
            child.installEventFilter(self)

    def set_event(self, event, names=None, metadata=None):
        """metadata只接受调用方已核实的name/icon/kind/properties；不遍历原始事件字段显示。"""
        if self._cleaned:
            return
        self._event = dict(event) if isinstance(event, dict) else None
        self._closed = False
        if self._event is None:
            self.frame.hide()
            return
        event = self._event
        resolved = {}
        if event.get("action") == "place_building" and event.get("building_type_id") is not None:
            resolved = building_metadata(event["building_type_id"])
        if isinstance(metadata, dict):
            resolved = dict(resolved, **metadata)
        official_name = resolved.get("name_zh") or resolved.get("name")
        if official_name and (resolved.get("known", True) or isinstance(metadata, dict)):
            title = official_name
        else:
            title = str(event.get("label") or "操作目标")
        self.name.set_full_text(title)
        kind = resolved.get("kind")
        kind_text = {"building":"建筑", "unit":"单位"}.get(kind, "操作记录")
        self.kind.set_full_text(kind_text)
        picture = QPixmap(str(resolved["icon"])) if resolved.get("icon") else QPixmap()
        self.icon_is_official = not picture.isNull()
        if self.icon_is_official:
            picture = picture.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            picture.setDevicePixelRatio(2)
        else:
            picture = _target_pixmap()
        self.icon.setPixmap(picture)
        self.icon.setAccessibleName("官方对象图标" if self.icon_is_official else "Lucide目标图标")
        player = names.get(event.get("player_id")) if isinstance(names, dict) else None
        if isinstance(player, dict):
            player = player.get("name")
        self.player.set_full_text(player or event.get("player_name") or "玩家未提供")
        self.time.setText(_clock(event.get("time")))
        if event.get("action") == "place_building":
            status = "放置记录 · 完成状态未知"
        elif event.get("kind") in ("birth", "spawn", "created", "initial"):
            status = "出现记录"
        elif event.get("kind") in ("loss", "death", "destroyed"):
            status = "损失记录"
        else:
            status = "指令记录 · 不代表执行成功"
        self.status.set_full_text(status)
        self._set_properties(resolved.get("properties"))
        self.frame.setAccessibleDescription(f"{title}；{self.player.accessibleName()}；{self.time.text()}；{status}")
        self._install_filters()
        self.reposition(self._available_rect, self._bottom_reserved)
        self.frame.setVisible(self._enabled)
        if self._enabled:
            self.frame.raise_()

    def _set_properties(self, values):
        while self.properties_layout.count():
            item = self.properties_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._property_count = 0
        for item in values if isinstance(values, (list, tuple)) else []:
            if not isinstance(item, dict) or not item.get("label") or item.get("value") is None:
                continue
            value = item["value"]
            if not isinstance(value, (str, int, float, bool)) or isinstance(value, float) and not math.isfinite(value):
                continue
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            label = _ElidedLabel("SelectionMuted")
            label.set_full_text(item["label"])
            value_label = _ElidedLabel()
            unit = str(item.get("unit") or "")
            value_label.set_full_text(_text(value)+(f" {unit}" if unit else ""))
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(label, 1)
            row.addWidget(value_label, 1)
            self.properties_layout.addWidget(row_widget)
            self._property_count += 1
            if self._property_count >= 3:
                break
        self.properties.setVisible(self._property_count > 0)

    def set_enabled(self, enabled):
        self._enabled = bool(enabled) and not self._cleaned
        self.frame.setVisible(self._enabled and self._event is not None and not self._closed)
        if self.frame.isVisible():
            self.reposition(self._available_rect, self._bottom_reserved)
            self.frame.raise_()

    def reposition(self, rect=None, bottom_reserved=0):
        """锚定在右下角时间条上方；尺寸均为逻辑像素。"""
        if self._cleaned:
            return
        available = QRect(rect) if rect is not None else QRect()
        if not available.isValid() and self._viewport is not None:
            available = self._viewport.rect()
        self._available_rect = available
        self._bottom_reserved = max(0, int(bottom_reserved))
        if not available.isValid():
            return
        margin = 12 if available.width() < 800 else 24
        width = max(100, min(300, available.width()-margin*2))
        self.frame.layout().activate()
        height = min(220, max(116, self.frame.layout().sizeHint().height()))
        height = min(height, max(60, available.height()-self._bottom_reserved-margin*2))
        left = available.right()+1-margin-width
        top = available.bottom()+1-self._bottom_reserved-margin-height
        self._anchor = QRect(left, max(available.top()+margin, top), width, height)
        self._anchoring = True
        self.frame.setGeometry(self._anchor)
        self._anchoring = False

    def contains_global(self, position):
        return (not self._cleaned and self.frame.isVisible()
                and self.frame.rect().contains(self.frame.mapFromGlobal(position)))

    def handle_wheel(self, event):
        """供画布入口统一截住卡片范围内滚轮；没有可滚动内容时也不缩放底图。"""
        if not self.contains_global(event.globalPos()):
            return False
        event.accept()
        return True

    def eventFilter(self, watched, event):
        if self._cleaned:
            return False
        if watched is self.frame and event.type() == QEvent.Move and not self._anchoring:
            if self._anchor.isValid() and self.frame.geometry() != self._anchor:
                self._anchoring = True
                self.frame.setGeometry(self._anchor)
                self._anchoring = False
        if event.type() == QEvent.Wheel:
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _close(self):
        self._closed = True
        self.frame.hide()
        self.closed.emit()

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        self._event = None
        self.frame.hide()
        self.frame.deleteLater()
