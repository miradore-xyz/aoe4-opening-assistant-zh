"""开局编辑器的建筑与科技选择器。

Action（动作）独立保存为结构化资料；本组件不会改写用户的提示正文。
Picker（选择弹窗）按文明读取随包目录，使用列表呈现图标、时代与研究位置。
"""
from __future__ import annotations

import copy
import html

from PyQt5.QtCore import Qt, QSize, QRect, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QStyledItemDelegate, QStyle, QVBoxLayout, QWidget,
)

from catalog import catalog_icon, catalog_metadata, coverage_note, load_catalog, technology_buildings
from choice_controls import ClickComboBox
from core import civilization_text
from windowing import FramelessDialog, DialogBar


AGE_NAMES = {1: "黑暗时代", 2: "封建时代", 3: "城堡时代", 4: "帝王时代"}
KIND_NAMES = {"building": "建筑", "technology": "科技"}


def _identity(action):
    """同名图标可能出现在多个文明，身份还需包含类别与文明。"""
    civ = action.get("civilization", "")
    if isinstance(civ, list):
        civ = tuple(civ)
    return action.get("kind"), action.get("id"), civ


def _metadata(action):
    fields = [str(action.get("age_label") or AGE_NAMES.get(action.get("age"), "时代未标注"))]
    location = action.get("location_zh", "")
    if location:
        fields.append(str(location))
    return " · ".join(fields)


def _load_choices(civilization, kind):
    """多文明与通用目录由资料模块取交集，不混入不相关文明项目。"""
    result, seen = [], set()
    for source in load_catalog(civilization, kind):
        entry = copy.deepcopy(source)
        entry["civilization"] = copy.deepcopy(civilization)
        entry["kind"] = kind
        key = _identity(entry)
        if key not in seen:
            result.append(entry)
            seen.add(key)
    return result


class _CountBox(QSpinBox):
    """数量可直接输入或用方向键修改，滚轮只用于浏览页面。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(1, 99)
        self.setButtonSymbols(QSpinBox.NoButtons)
        self.setSuffix(" 座")
        self.setFixedWidth(70)
        self.setFixedHeight(30)

    def wheelEvent(self, event):
        event.ignore()


class _CatalogDelegate(QStyledItemDelegate):
    """固定两行的目录项，避免长说明挤占列表并保持滚动轻量。"""
    def sizeHint(self, option, index):
        return QSize(320, 60)

    def paint(self, painter, option, index):
        entry = index.data(Qt.UserRole) or {}
        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#29382f" if selected else "#202023"))
            painter.drawRoundedRect(rect, 7, 7)
        if option.state & QStyle.State_HasFocus:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#8c9c91"), 1))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 7, 7)
        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon):
            icon.paint(painter, QRect(rect.left() + 10, rect.center().y() - 18, 36, 36))
        text_rect = rect.adjusted(58, 7, -10, -7)
        title_font = QFont(option.font)
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.Medium)
        painter.setFont(title_font)
        painter.setPen(QColor("#f4f4f5"))
        name = str(entry.get("name_zh") or entry.get("id") or "未命名项目")
        painter.drawText(text_rect.left(), text_rect.top(), text_rect.width(), 21,
                         Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(name, Qt.ElideRight, text_rect.width()))
        detail_font = QFont(option.font)
        detail_font.setPixelSize(12)
        painter.setFont(detail_font)
        painter.setPen(QColor("#a4a4ab"))
        painter.drawText(text_rect.left(), text_rect.top() + 23, text_rect.width(), 17,
                         Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(_metadata(entry), Qt.ElideRight, text_rect.width()))
        painter.restore()


class ActionPicker(FramelessDialog):
    """可搜索的建筑或科技目录；双击或按回车确认，Esc 可取消。"""
    def __init__(self, civilization, kind, parent=None):
        super().__init__(parent)
        self.civilization = copy.deepcopy(civilization)
        self.kind = kind
        self.entries = _load_choices(civilization, kind)
        self.building_groups = technology_buildings(civilization, self.entries) if kind == 'technology' else []
        self.setWindowTitle("选择" + KIND_NAMES[kind])
        self.resize(700 if kind == 'technology' else 560, 580)
        self.setMinimumSize(420, 420)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)
        self.dialog_bar = DialogBar(self, '选择' + KIND_NAMES[kind])
        shell.addWidget(self.dialog_bar)
        content = QWidget()
        shell.addWidget(content, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        caption = civilization_text(civilization)
        patch = catalog_metadata().get("patch")
        if patch:
            caption += "\n资料版本 " + str(patch)
        coverage = coverage_note(civilization)
        if coverage:
            caption += "\n" + coverage
        civilization_label = QLabel(caption)
        civilization_label.setObjectName("Muted")
        civilization_label.setTextFormat(Qt.PlainText)
        civilization_label.setWordWrap(True)
        layout.addWidget(civilization_label)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setAccessibleName("搜索" + KIND_NAMES[kind])
        self.search.setPlaceholderText("搜索名称、研究位置或说明")
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.search, 1)
        self.age = ClickComboBox()
        self.age.setAccessibleName("按时代筛选")
        self.age.setToolTip("按可开始建造或研究的时代筛选")
        self.age.addItem("全部时代", 0)
        for number, name in AGE_NAMES.items():
            self.age.addItem(name, number)
        self.age.setFixedWidth(132)
        filters.addWidget(self.age)
        layout.addLayout(filters)
        self.list = QListWidget()
        self.list.setAccessibleName(KIND_NAMES[kind] + "目录")
        self.list.setIconSize(QSize(36, 36))
        self.list.setItemDelegate(_CatalogDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.building_list = None
        if kind == 'technology':
            body = QHBoxLayout()
            body.setSpacing(12)
            sidebar = QVBoxLayout()
            sidebar.setSpacing(7)
            heading = QLabel('研究建筑')
            heading.setObjectName('Muted')
            sidebar.addWidget(heading)
            self.building_list = QListWidget()
            self.building_list.setAccessibleName('按研究建筑分类')
            self.building_list.setFixedWidth(158)
            self.building_list.setIconSize(QSize(24, 24))
            self.building_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.building_list.setStyleSheet('QListWidget::item{padding:7px 5px;border-radius:6px;} QListWidget::item:selected{background:#263a2e;color:#f4f4f5;}')
            all_items = QListWidgetItem('全部科技')
            all_items.setData(Qt.UserRole, '')
            self.building_list.addItem(all_items)
            for group in self.building_groups:
                item = QListWidgetItem(catalog_icon(group), group['title'])
                item.setData(Qt.UserRole, group['id'])
                item.setToolTip(group['title'])
                self.building_list.addItem(item)
            self.building_list.setCurrentRow(0)
            sidebar.addWidget(self.building_list, 1)
            body.addLayout(sidebar)
            body.addWidget(self.list, 1)
            layout.addLayout(body, 1)
            self.building_list.currentRowChanged.connect(self._filter)
        else:
            layout.addWidget(self.list, 1)
        self.empty = QLabel()
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.description = QLabel()
        self.description.setObjectName("Muted")
        self.description.setTextFormat(Qt.PlainText)
        self.description.setWordWrap(True)
        self.description.setMinimumHeight(42)
        self.description.setMaximumHeight(70)
        layout.addWidget(self.description)
        bottom = QHBoxLayout()
        self.quantity = _CountBox()
        self.quantity.setAccessibleName("建造数量")
        if kind == "building":
            bottom.addWidget(QLabel("建造数量"))
            bottom.addWidget(self.quantity)
        else:
            self.quantity.hide()
        bottom.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self.add_button = QPushButton("添加" + KIND_NAMES[kind])
        self.add_button.setObjectName("Primary")
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self._accept_selection)
        bottom.addWidget(self.add_button)
        layout.addLayout(bottom)
        self.search.textChanged.connect(self._filter)
        self.age.currentIndexChanged.connect(self._filter)
        self.list.currentItemChanged.connect(self._show_description)
        self.list.itemDoubleClicked.connect(self._accept_selection)
        self._filter()
        self.search.setFocus(Qt.OtherFocusReason)

    def _filter(self, *_):
        current = self.list.currentItem()
        selected_id = _identity(current.data(Qt.UserRole)) if current else None
        self.list.clear()
        query = self.search.text().strip().casefold()
        age = self.age.currentData()
        group_key = self.building_list.currentItem().data(Qt.UserRole) if self.building_list and self.building_list.currentItem() else ''
        allowed = next((group['technology_ids'] for group in self.building_groups if group['id'] == group_key), None)
        selected = None
        for entry in self.entries:
            if allowed is not None and entry['id'] not in allowed:
                continue
            searchable = " ".join(str(entry.get(key, "")) for key in
                                  ("name_zh", "id", "location_zh", "description_zh", "category_zh")).casefold()
            if query and query not in searchable:
                continue
            if age and entry.get("age") != age:
                continue
            item = QListWidgetItem(catalog_icon(entry), str(entry.get("name_zh", "")))
            item.setData(Qt.UserRole, entry)
            item.setToolTip(_metadata(entry) + '\n' + str(entry.get("description_zh", "")))
            self.list.addItem(item)
            if selected_id == _identity(entry):
                selected = item
        if selected:
            self.list.setCurrentItem(selected)
        elif self.list.count():
            self.list.setCurrentRow(0)
        has_items = self.list.count() > 0
        self.list.setVisible(has_items)
        self.empty.setVisible(not has_items)
        if not self.entries:
            self.empty.setText("此文明暂无可选择的" + KIND_NAMES[self.kind] + "资料。可继续在提示中自行填写。")
        else:
            self.empty.setText("没有符合条件的项目，可更换搜索词或时代。")
        self.add_button.setEnabled(has_items)
        self._show_description()

    def _show_description(self, *_):
        item = self.list.currentItem()
        self.description.setText(str(item.data(Qt.UserRole).get("description_zh", "")) if item else "")

    def _accept_selection(self, *_):
        if self.list.currentItem() is not None:
            self.accept()

    def selected_action(self):
        """返回完整动作快照，断网或目录更新后仍能显示原开局。"""
        item = self.list.currentItem()
        if item is None:
            return None
        entry = copy.deepcopy(item.data(Qt.UserRole))
        entry["count"] = self.quantity.value() if self.kind == "building" else 1
        return entry


class StepActions(QWidget):
    """步骤内紧凑动作清单：建筑可调整数量，科技保持一次研究。"""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._civilization = "Generic"
        self._actions = []
        self._available = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("建筑与科技")
        header.addWidget(title)
        header.addStretch()
        for kind, caption in (("building", "+ 建筑"), ("technology", "+ 科技")):
            control = QPushButton(caption)
            control.setObjectName("Quiet")
            control.setFixedHeight(31)
            control.setToolTip("从当前文明的目录添加" + KIND_NAMES[kind])
            control.setAccessibleName("添加" + KIND_NAMES[kind])
            control.clicked.connect(lambda _=False, selected=kind: self._pick(selected))
            header.addWidget(control)
        layout.addLayout(header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(42)
        self.scroll.setMaximumHeight(100)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.rows = QWidget()
        self.row_layout = QVBoxLayout(self.rows)
        self.row_layout.setContentsMargins(0, 0, 5, 0)
        self.row_layout.setSpacing(5)
        self.row_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.rows)
        layout.addWidget(self.scroll)
        self._render()

    def set_civilization(self, civilization):
        self._civilization = copy.deepcopy(civilization)
        self._available = {(entry.get("kind"), entry.get("id")) for kind in KIND_NAMES
                           for entry in _load_choices(self._civilization, kind)}
        self._render()

    def set_actions(self, actions):
        """切换步骤时装入数据，不发出用户修改信号。"""
        self._actions = copy.deepcopy(actions or [])
        for action in self._actions:
            if action.get("kind") == "technology":
                action["count"] = 1
        self._render()

    def actions(self):
        return copy.deepcopy(self._actions)

    def _pick(self, kind):
        picker = ActionPicker(self._civilization, kind, self)
        if picker.exec_() != QDialog.Accepted:
            return
        action = picker.selected_action()
        if action is not None:
            self.add_action(action)

    def add_action(self, action):
        """合并同文明的重复建筑；同一科技只记录一次。"""
        action = copy.deepcopy(action)
        if action.get("kind") == "technology":
            action["count"] = 1
        for existing in self._actions:
            if _identity(existing) == _identity(action):
                if action.get("kind") == "building":
                    existing["count"] = min(99, int(existing.get("count", 1)) + int(action.get("count", 1)))
                    self._render()
                    self.changed.emit()
                return
        self._actions.append(action)
        self._render()
        self.changed.emit()

    def _remove(self, index):
        self._actions.pop(index)
        self._render()
        self.changed.emit()

    def _set_count(self, index, count):
        self._actions[index]["count"] = count
        self.changed.emit()

    def _render(self):
        self.scroll.setFixedHeight(42 if not self._actions else min(100, len(self._actions) * 46 + 4))
        while self.row_layout.count():
            item = self.row_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not self._actions:
            empty = QLabel("添加本时刻要建造的建筑或研究的科技")
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            empty.setMinimumHeight(36)
            self.row_layout.addWidget(empty)
            return
        for index, action in enumerate(self._actions):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(8, 3, 5, 3)
            line.setSpacing(9)
            row.setStyleSheet("QWidget{background:#19191c;border-radius:6px;} QLabel{background:transparent;}")
            icon = QLabel()
            icon.setFixedSize(32, 32)
            # 以屏幕物理像素请求图标，避免高缩放显示时先缩小再放大。
            ratio = self.devicePixelRatioF()
            picture = catalog_icon(action).pixmap(round(32 * ratio), round(32 * ratio))
            picture.setDevicePixelRatio(ratio)
            icon.setPixmap(picture)
            line.addWidget(icon)
            text = QLabel()
            name = str(action.get("name_zh") or action.get("id") or "未命名项目")
            detail = _metadata(action)
            if (action.get("kind"), action.get("id")) not in self._available:
                detail += " · 其他文明／目录外项目"
            text.setText("<span style='font-size:13px;color:#f4f4f5'>" + html.escape(name) +
                         "</span><br><span style='font-size:11px;color:#a4a4ab'>" + html.escape(detail) + "</span>")
            text.setMinimumWidth(0)
            text.setWordWrap(True)
            text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            text.setToolTip(name + "\n" + detail + "\n" + str(action.get("description_zh", "")))
            line.addWidget(text, 1)
            if action.get("kind") == "building":
                quantity = _CountBox()
                quantity.setValue(int(action.get("count", 1)))
                quantity.setAccessibleName(name + "的建造数量")
                quantity.valueChanged.connect(lambda count, current=index: self._set_count(current, count))
                line.addWidget(quantity)
            remove = QPushButton("×")
            remove.setObjectName("Quiet")
            remove.setFixedSize(28, 28)
            remove.setStyleSheet("padding:0;font-size:18px;")
            remove.setToolTip("移除" + name)
            remove.setAccessibleName("移除" + name)
            remove.clicked.connect(lambda _=False, current=index: self._remove(current))
            line.addWidget(remove)
            self.row_layout.addWidget(row)
