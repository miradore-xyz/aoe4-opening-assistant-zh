"""《帝国时代4》中文开局助手：本地窗口、开局编辑器、可选中文朗读。

派生自 CraftySalamander / RTS_Overlay，按 GNU GPL 第三版发布。
本文件英文标识符：overlay=悬浮窗，manager=控制面板，editor=编辑器，preview=预览。
所有新增功能和中文界面修改日期：2026-10-25。
"""
from __future__ import annotations

import argparse
import copy
import html
import json
import os
import sys
import traceback
from pathlib import Path

from app_fonts import make_ui_font, register_ui_fonts
from PyQt5.QtCore import Qt, QTimer, QEvent, QLockFile, QLocale, QTranslator, QSize, QPoint, QPointF, QRectF
from PyQt5.QtGui import QFont, QFontInfo, QIcon, QKeySequence, QPainter, QPixmap, QColor, QPolygonF, QPen, QLinearGradient, QPainterPath
from PyQt5.QtWidgets import (
    QApplication, QWidget, QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFormLayout, QSpinBox, QCheckBox, QLineEdit, QTextEdit,
    QListWidget, QListWidgetItem, QTabWidget, QFileDialog, QMessageBox, QScrollArea,
    QSplitter, QKeySequenceEdit, QFrame, QGroupBox, QSizeGrip, QSlider, QProgressBar, QBoxLayout, QSizePolicy, QLayout, QStackedWidget, QToolTip,
)

from core import (CIVS, FIELDS, DEFAULTS, DEFAULT_HOTKEYS, HOTKEY_LABELS, Clock, NoteRenderer,
                  atomic_json, load_settings, read_order, validate_order, timer_steps, timed_ids, timeline_selection,
                  timestamp, civilization_text, overlay_end_deadline)
from native import GlobalHotkeys, Speech
from online_voice import OnlineSpeech, CHINESE_VOICES
from windowing import FramelessWindow, WindowBar, FramelessDialog, DialogBar
from color_picker import ColorPickerDialog
from development_placeholder import DevelopmentPlaceholder
from branding import APP_NAME, brand_mark
from themed_buttons import TapestryAction
from choice_controls import ClickComboBox, ClickSlider, ClickSpinBox
from editor_actions import StepActions
from order_actions import time_caption, manual_notes, save_actions
from analysis_page import AnalysisPage
from replay_page import ReplayPage
from notes_icons import IconNotesEdit
from overlay_geometry import fit_overlay_widget
from overlay_effects import install_hud_outline, overlay_style, style_backdrop, configure_hud_shadow
from overlay_lock import OverlayLockHandle
from overlay_goals import GoalHUD, fading_backdrop
from edit_menus import install_chinese_edit_menus
from version import APP_VERSION, APP_CODENAME
from default_openings import seed_default_openings
from visual import (STYLE as DESIGN_STYLE, card, text as ui_text, crest, Switch, StepDelegate, LibraryDelegate,
                    Grip, Resources, CoverDelegate, dark_titlebar, line_icon, game_pixmap, paint_game_surface,
                    paint_tapestry, TapestryButton)

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
ASSETS = Path(sys._MEIPASS) / "assets" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "upstream" / "docs" / "assets"
STYLE = DESIGN_STYLE
QCheckBox = Switch
VOICE_TEST = "中文开局助手。检查造村民，提前补房，持续补兵，检查金矿。"


def default_data_dir():
    """安装版沿用安装器保存的数据位置，便携版仍使用程序旁的用户数据。"""
    try:
        saved = json.loads((ROOT / "data-location.json").read_text(encoding="utf-8-sig"))
        directory = Path(saved["data_dir"])
        if directory.is_absolute():
            return directory
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return ROOT / "用户数据"


def resource_step(order, ids):
    """同组条件提示只补充已知值，未指定人数不擦掉同一时刻的分配。"""
    result = copy.deepcopy(order["build_order"][ids[0]])
    for index in ids[1:]:
        step = order["build_order"][index]
        for key in ("food", "wood", "gold", "stone", "builder"):
            value = step.get("resources", {}).get(key, -1)
            if value >= 0:
                result.setdefault("resources", {})[key] = value
        for key in ("population_count", "villager_count", "age"):
            if step.get(key, -1) >= 0:
                result[key] = step[key]
    return result


def label(text, muted=False):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.PlainText)
    if muted:
        widget.setObjectName("Muted")
    return widget


def button(text, callback, tip=""):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    widget.setToolTip(tip or text)
    return widget


def game_image(path, size=32):
    image = QLabel()
    image.setPixmap(game_pixmap(path, size))
    image.setFixedSize(size, size)
    return image


def slider_field(widget, low, high):
    """把可精确输入的数字框与滑杆并列，避免声音设置中的超长输入框。"""
    frame = QWidget()
    row = QHBoxLayout(frame)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(16)
    slider = ClickSlider(Qt.Horizontal)
    # 给圆形把手完整的垂直空间；默认滑杆的矮尺寸会裁掉边框。
    slider.setFixedHeight(32)
    slider.setStyleSheet(
        "QSlider {background:transparent;}"
        "QSlider::groove:horizontal {height:4px;background:#39393f;border:0;border-radius:2px;}"
        "QSlider::sub-page:horizontal {background:#B8B8C0;border-radius:2px;}"
        "QSlider::handle:horizontal {width:14px;height:14px;background:#f4f4f5;"
        "border:2px solid #B8B8C0;border-radius:9px;margin:-7px 0;}"
        "QSlider::handle:horizontal:hover {background:#ffffff;border-color:#E0E0E4;}"
    )
    slider.setRange(low, high)
    slider.setValue(widget.value())
    slider.valueChanged.connect(widget.setValue)
    widget.valueChanged.connect(slider.setValue)
    widget.setFixedWidth(85)
    row.addWidget(slider, 1)
    row.addWidget(widget)
    return frame


def spin(low, high, value, suffix=""):
    widget = ClickSpinBox()
    widget.setRange(low, high)
    widget.setValue(value)
    widget.setSuffix(suffix)
    widget.setButtonSymbols(QSpinBox.NoButtons)
    return widget


def fixed_color_icon(pixmap):
    """显式提供所有图标状态，避免Qt把等待语音时的金色控件自动灰化。"""
    icon = QIcon()
    for mode in (QIcon.Normal, QIcon.Disabled, QIcon.Active, QIcon.Selected):
        for state in (QIcon.Off, QIcon.On):
            icon.addPixmap(pixmap, mode, state)
    return icon


def action_icon(kind, color="#c6c7cb"):
    """绘制操作图标：previous=上一步，next=下一步，play=开始，pause=暂停，lock/unlock=闭锁/开锁。"""
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    if kind in ("lock", "unlock"):
        painter.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(6, 10, 12, 10), 2, 2)
        shackle = QPainterPath()
        if kind == "lock":
            shackle.moveTo(8, 10)
            shackle.lineTo(8, 7)
            shackle.cubicTo(8, 2, 16, 2, 16, 7)
            shackle.lineTo(16, 10)
        else:
            shackle.moveTo(16, 10)
            shackle.lineTo(16, 7)
            shackle.cubicTo(16, 2, 8, 2, 8, 7)
        painter.drawPath(shackle)
        painter.setBrush(QColor(color))
        painter.drawEllipse(QPointF(12, 14), 1, 1)
        painter.drawLine(QPointF(12, 15), QPointF(12, 17))
    elif kind == "pause":
        painter.drawRoundedRect(6, 4, 4, 16, 1, 1)
        painter.drawRoundedRect(14, 4, 4, 16, 1, 1)
    else:
        points = [(7, 4), (19, 12), (7, 20)] if kind != "previous" else [(17, 4), (5, 12), (17, 20)]
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        if kind in ("previous", "next"):
            painter.drawRect(2 if kind == "previous" else 20, 4, 2, 16)
    painter.end()
    return fixed_color_icon(pixmap)


def icon_button(kind, description, callback, tapestry_variant=None):
    widget = TapestryAction("", callback, tapestry_variant) if tapestry_variant else button("", callback, description)
    widget.setAccessibleName(description)
    widget.setIcon(action_icon(kind))
    widget.setIconSize(QSize(22, 22))
    widget.setFixedSize(40, 34)
    widget.setStyleSheet("padding:4px; border-radius:5px;")
    return widget


def message(parent, text, title="提示"):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.PlainText)
    box.setText(text)
    box.addButton("确定", QMessageBox.AcceptRole)
    box.exec_()


def confirm(parent, text, action="覆盖保存"):
    box = QMessageBox(parent)
    box.setWindowTitle("请确认")
    box.setTextFormat(Qt.PlainText)
    box.setText(text)
    yes = box.addButton(action, QMessageBox.AcceptRole)
    box.addButton("取消", QMessageBox.RejectRole)
    box.exec_()
    return box.clickedButton() is yes


def open_file_dialog(parent, saving=False, suggested=""):
    dialog = QFileDialog(parent, "导出开局" if saving else "导入开局")
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.setNameFilter("开局数据文件 (*.json)")
    for role, name in [(QFileDialog.LookIn, "位置"), (QFileDialog.FileName, "文件名"),
                       (QFileDialog.FileType, "文件类型"), (QFileDialog.Accept, "保存" if saving else "导入"),
                       (QFileDialog.Reject, "取消")]:
        dialog.setLabelText(role, name)
    dialog.setAcceptMode(QFileDialog.AcceptSave if saving else QFileDialog.AcceptOpen)
    dialog.setFileMode(QFileDialog.AnyFile if saving else QFileDialog.ExistingFile)
    dialog.setDefaultSuffix("json")
    if suggested:
        dialog.selectFile(suggested)
    return dialog.selectedFiles()[0] if dialog.exec_() else ""


class Editor(FramelessDialog):
    """表单编辑原开局格式，保留未编辑的扩展字段与图标标记。"""
    def __init__(self, owner, data=None):
        super().__init__(owner)
        self.setWindowTitle("编辑中文开局与提示")
        self.resize(880, 700)
        self.setMinimumSize(760, 560)
        self.data = copy.deepcopy(data) if data is not None else {"name": "", "civilization": "Order of the Dragon",
                "author": "", "source": "", "build_order": [self.blank()]}
        self.index = 0
        self.busy = False
        shell = QVBoxLayout(self)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)
        self.dialog_bar = DialogBar(self, '编辑开局')
        shell.addWidget(self.dialog_bar)
        content = QWidget()
        content.setObjectName("OpeningEditorContent")
        # 编辑区使用独立紧凑比例；数值框不沿用全局大内边距，避免固定高度裁掉底边。
        content.setStyleSheet("""
            QWidget#OpeningEditorContent QWidget {font-size:12px;}
            QWidget#OpeningEditorContent QLineEdit,
            QWidget#OpeningEditorContent QComboBox {padding:5px 8px;min-height:0;border-radius:6px;}
            QWidget#OpeningEditorContent QSpinBox {padding:3px 6px;min-height:0;border-radius:6px;}
            QWidget#OpeningEditorContent QPushButton {padding:5px 10px;min-height:0;border-radius:8px;}
            QWidget#OpeningEditorContent QTextEdit {padding:6px 8px;border-radius:6px;}
            QWidget#OpeningEditorContent QLineEdit[requiredMissing="true"],
            QWidget#OpeningEditorContent QLineEdit[requiredMissing="true"]:focus {
                border:1px solid #dc7777;background:#21191c;
            }
            QWidget#OpeningEditorContent QLabel[requiredMissing="true"] {color:#ef9393;}
        """)
        shell.addWidget(content, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(9)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        self.name = QLineEdit(self.data.get("name", ""))
        self.name.setPlaceholderText("请输入开局名称")
        self.civ = ClickComboBox()
        for key, text in CIVS.items():
            if key in ('Any', 'Generic'):
                continue  # 旧格式兼容别名保留在读取层，界面只提供一个“通用”。
            self.civ.addItem(text, key)
        civ = self.data.get("civilization")
        self.original_civilization = copy.deepcopy(civ)
        if isinstance(civ, list):
            self.civ.addItem(civilization_text(civ) + "（原多文明）", civ)
        self.civ.setCurrentIndex(max(0, self.civ.findData('any' if civ in ('Any', 'Generic') else civ)))
        self.author = QLineEdit(str(self.data.get("author", "")))
        self.source = QLineEdit(str(self.data.get("source", "")))
        self.author.setPlaceholderText("请输入作者署名")
        self.source.setPlaceholderText("选填")
        self.required_labels = {}
        for row, left_name, left, right_name, right_widget in [
                (0, "开局名称", self.name, "适用文明", self.civ),
                (1, "作者署名", self.author, "来源说明", self.source)]:
            for column, caption, field in [(0, left_name, left), (2, right_name, right_widget)]:
                required = field in (self.name, self.author)
                title = label(caption + (" *" if required else ""))
                title.setWordWrap(False)
                if required:
                    self.required_labels[field] = title
                title.setBuddy(field)
                form.addWidget(title, row, column)
                field.setMinimumWidth(0)
                field.setFixedHeight(32)
                field.setAccessibleName(caption + ("（必填）" if required else ""))
                form.addWidget(field, row, column + 1)
        layout.addLayout(form)
        split = QSplitter()
        split.setObjectName("OpeningEditorSplit")
        split.setHandleWidth(20)
        self.steps = QListWidget()
        self.steps.setObjectName("EditorSteps")
        self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.steps.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.steps.setMinimumWidth(120)
        self.steps.setMaximumWidth(210)
        self.steps.setContextMenuPolicy(Qt.CustomContextMenu)
        self.steps.customContextMenuRequested.connect(self.show_step_menu)
        self.steps.setToolTip("右键管理时间点；Insert 添加，Ctrl+D 复制，Delete 移除，Alt+↑/↓ 移动")
        self.steps.setAccessibleDescription("右键可添加、复制、移除或移动时间点；快捷键仅在时间点列表获得焦点时生效。")
        from PyQt5.QtWidgets import QShortcut
        self.step_shortcuts = {}
        for key, shortcut, callback in self.step_command_specs():
            control = QShortcut(QKeySequence(shortcut), self.steps)
            control.setContext(Qt.WidgetShortcut)
            control.activated.connect(callback)
            self.step_shortcuts[key] = control
        split.addWidget(self.steps)
        body = QWidget()
        body.setObjectName("OpeningStepBody")
        right = QVBoxLayout(body)
        right.setContentsMargins(2, 0, 2, 0)
        right.setSpacing(8)
        fields = QGridLayout()
        fields.setHorizontalSpacing(16)
        fields.setVerticalSpacing(6)
        fields.setColumnStretch(0, 1)
        fields.setColumnStretch(1, 1)
        self.time = QLineEdit()
        self.time.setFixedHeight(32)
        self.time.setPlaceholderText("例如 00:10")
        self.time.setAccessibleName("时间（必填）")
        time_row = QHBoxLayout()
        time_label = label("时间 *")
        time_label.setWordWrap(False)
        time_label.setBuddy(self.time)
        self.required_labels[self.time] = time_label
        time_row.addWidget(time_label)
        time_row.addWidget(self.time, 1)
        right.addLayout(time_row)
        self.step_actions = StepActions()
        self.step_actions.set_civilization(self.civ.currentData())
        right.addWidget(self.step_actions)
        self.numbers = {}
        # 人口仍是原攻略格式的一部分；编辑界面不再提供该项，避免保存时覆写旧值。
        editor_fields = ((key, text) for key, text in FIELDS.items() if key != "population_count")
        for i, (key, text) in enumerate(editor_fields):
            w = spin(-1, 9999 if key != "age" else 4, -1)
            w.setSpecialValueText("不指定")
            w.setFixedWidth(88)
            w.setFixedHeight(30)
            w.setToolTip(text + "；不指定时留为 -1")
            self.numbers[key] = w
            pair = QWidget()
            pair_layout = QHBoxLayout(pair)
            pair_layout.setContentsMargins(0, 0, 0, 0)
            pair_layout.setSpacing(8)
            caption = label(text)
            caption.setWordWrap(False)
            caption.setFixedWidth(62)
            pair_layout.addWidget(caption)
            pair_layout.addWidget(w)
            fields.addWidget(pair, i // 2, i % 2, Qt.AlignLeft | Qt.AlignVCenter)
        right.addLayout(fields)
        self.notes = IconNotesEdit(civilization_provider=lambda: self.civ.currentData())
        self.notes.setAccessibleName("当前时间节点的补充说明")
        self.notes.setPlaceholderText("当前时间节点的补充说明\n简述操作要点与注意事项。")
        self.notes.setMinimumHeight(80)
        right.addWidget(self.notes, 1)
        body_scroll = QScrollArea()
        body_scroll.setObjectName("OpeningStepScroll")
        body_scroll.setWidgetResizable(True)
        body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body_scroll.setWidget(body)
        split.addWidget(body_scroll)
        # 分隔槽保留完整拖拽命中区，居中的细线只负责视觉边界。
        handle = split.handle(1)
        gutter = QVBoxLayout(handle)
        gutter.setContentsMargins(0, 8, 0, 8)
        divider = QFrame(handle)
        divider.setObjectName("OpeningEditorDivider")
        divider.setFixedWidth(1)
        divider.setStyleSheet("background:#36363d;border:0;")
        divider.setAttribute(Qt.WA_TransparentForMouseEvents)
        gutter.addWidget(divider, 1, Qt.AlignHCenter)
        split.setStretchFactor(1, 3)
        split.setSizes([170, 650])
        layout.addWidget(split, 1)
        bottom = QHBoxLayout()
        bottom.addStretch()
        cancel = button("取消", self.reject)
        cancel.setFixedHeight(36)
        bottom.addWidget(cancel)
        save = TapestryAction("保存到本地开局库", self.save, "castle")
        save.setObjectName("Primary")
        save.setFixedHeight(36)
        compact_font = make_ui_font()
        compact_font.setPixelSize(12)
        save.setFont(compact_font)
        bottom.addWidget(save)
        layout.addLayout(bottom)
        self.steps.currentRowChanged.connect(self.select)
        self.time.textChanged.connect(self.update_time_caption)
        for field in self.required_labels:
            field.textChanged.connect(self.update_required_fields)
        self.civ.currentIndexChanged.connect(lambda: self.step_actions.set_civilization(self.civ.currentData()))
        self.refresh()
        self.show_step()
        self.update_required_fields()

    @staticmethod
    def blank():
        return {"population_count": -1, "villager_count": -1, "age": -1,
                "resources": {"food": -1, "wood": -1, "gold": -1, "stone": -1}, "notes": [""]}

    @staticmethod
    def time_seconds(value):
        """复用计时器的完整时间规则；空值不补成零秒。"""
        if not isinstance(value, str) or not value.strip():
            return None
        parsed = timer_steps({"build_order": [{"time": value.strip(), "notes": []}]})
        return parsed[0]["time_sec"] if parsed else None

    def time_error(self, value, index):
        if not isinstance(value, str) or not value.strip():
            return "请填写时间，例如 00:10"
        seconds = self.time_seconds(value)
        if seconds is None:
            return "时间格式为分:秒，例如 00:10；秒数应为 00–59"
        if index > 0:
            previous = self.time_seconds(self.data["build_order"][index - 1].get("time"))
            if previous is not None and seconds < previous:
                return "时间不能早于上一个时间点"
        return ""

    def update_required_fields(self):
        """必填项就地提示，填写有效内容后立即恢复正常样式。"""
        errors = {self.name: "" if self.name.text().strip() else "请填写开局名称",
                  self.author: "" if self.author.text().strip() else "请填写作者署名",
                  self.time: self.time_error(self.time.text(), self.index)}
        for field, title in self.required_labels.items():
            missing = bool(errors[field])
            field.setToolTip(errors[field])
            for widget in (field, title):
                if widget.property("requiredMissing") != missing:
                    widget.setProperty("requiredMissing", missing)
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
                    widget.update()

    def step_command_specs(self):
        """右键菜单和列表快捷键复用原来的提交、选择和移动行为。"""
        return [("添加时间点", "Insert", self.add), ("复制时间点", "Ctrl+D", self.duplicate),
                ("移除时间点", "Delete", self.remove), ("上移", "Alt+Up", lambda: self.reorder(-1)),
                ("下移", "Alt+Down", lambda: self.reorder(1))]

    def step_commands_enabled(self, selected=True):
        row, count = self.steps.currentRow(), len(self.data["build_order"])
        selected = selected and 0 <= row < count
        return {"添加时间点": True, "复制时间点": selected, "移除时间点": selected and count > 1,
                "上移": selected and row > 0, "下移": selected and row < count - 1}

    def update_step_shortcuts(self):
        enabled = self.step_commands_enabled()
        for caption, shortcut in self.step_shortcuts.items():
            shortcut.setEnabled(enabled[caption])

    def build_step_menu(self, position):
        """先选中指针下的时间点；空白处只提供添加，不误操作旧的选择。"""
        from PyQt5.QtWidgets import QMenu
        from edit_menus import style_edit_menu
        item = self.steps.itemAt(position)
        if item is not None:
            self.steps.setCurrentItem(item)
        menu = QMenu(self.steps)
        style_edit_menu(menu)
        enabled = self.step_commands_enabled(item is not None)
        for caption, shortcut, callback in self.step_command_specs():
            if caption in ("移除时间点", "上移"):
                menu.addSeparator()
            action = menu.addAction(caption)
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.WidgetShortcut)
            action.setEnabled(enabled[caption])
            action.triggered.connect(callback)
        return menu

    def show_step_menu(self, position):
        menu = self.build_step_menu(position)
        menu.exec_(self.steps.viewport().mapToGlobal(position))
        menu.deleteLater()

    def commit(self):
        step = self.data["build_order"][self.index]
        if self.time.text().strip():
            step["time"] = self.time.text().strip()
        else:
            step.pop("time", None)
        for key, widget in self.numbers.items():
            target = step["resources"] if key in ("food", "wood", "gold", "stone", "builder") else step
            target[key] = widget.value()
        save_actions(step, self.step_actions.actions(), self.notes.toPlainText().splitlines())

    def update_time_caption(self):
        item = self.steps.item(self.index)
        if item is not None:
            item.setText(time_caption(self.time.text()))

    def refresh(self):
        self.busy = True
        self.steps.clear()
        for step in self.data["build_order"]:
            self.steps.addItem(time_caption(step.get('time', '')))
        self.steps.setCurrentRow(self.index)
        self.busy = False
        self.update_step_shortcuts()

    def show_step(self):
        step = self.data["build_order"][self.index]
        self.time.setText(step.get("time", ""))
        for key, widget in self.numbers.items():
            source = step.get("resources", {}) if key in ("food", "wood", "gold", "stone", "builder") else step
            widget.setValue(source.get(key, -1))
        self.notes.setPlainText("\n".join(manual_notes(step)))
        self.step_actions.set_actions(step.get('_zh_actions', []))

    def select(self, index):
        if not self.busy and index >= 0:
            self.commit()
            self.index = index
            self.show_step()
            self.update_step_shortcuts()

    def add(self):
        self.commit()
        self.index += 1
        self.data["build_order"].insert(self.index, self.blank())
        self.refresh()
        self.show_step()

    def duplicate(self):
        self.commit()
        self.data["build_order"].insert(self.index + 1, copy.deepcopy(self.data["build_order"][self.index]))
        self.index += 1
        self.refresh()
        self.show_step()

    def remove(self):
        if len(self.data["build_order"]) == 1:
            message(self, "至少保留一个步骤。")
            return
        self.data["build_order"].pop(self.index)
        self.index = min(self.index, len(self.data["build_order"]) - 1)
        self.refresh()
        self.show_step()

    def reorder(self, direction):
        destination = self.index + direction
        if 0 <= destination < len(self.data["build_order"]):
            self.commit()
            steps = self.data["build_order"]
            steps[self.index], steps[destination] = steps[destination], steps[self.index]
            self.index = destination
            self.refresh()
            self.show_step()

    def save(self):
        self.commit()
        self.update_required_fields()
        for field in (self.name, self.author):
            if not field.text().strip():
                field.setFocus(Qt.OtherFocusReason)
                return
        for index, step in enumerate(self.data["build_order"]):
            if self.time_error(step.get("time"), index):
                # 当前输入已提交；直接载入首个错误步骤，避免选择信号重复提交旧内容。
                self.index = index
                self.refresh()
                self.show_step()
                self.update_required_fields()
                self.time.setFocus(Qt.OtherFocusReason)
                return
        selected_civilization = self.civ.currentData()
        if selected_civilization == 'any' and self.original_civilization in ('Any', 'Generic'):
            selected_civilization = self.original_civilization
        self.data.update(name=self.name.text().strip(), civilization=selected_civilization,
                         author=self.author.text(), source=self.source.text())
        # 用户修改提示后不自动声称它仍然是已翻译或已核实的默认内容。
        self.data["_zh_language"] = "user"
        try:
            validate_order(self.data)
        except ValueError as error:
            message(self, str(error), "无法保存")
            return
        self.accept()


class OverlayCornerGrip(QSizeGrip):
    """仅在解锁时显示的细金色角标，保留原生拖拽尺寸行为。"""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor("#efd6a2" if self.underMouse() else "#ad965f")
        color.setAlpha(235 if self.underMouse() else 145)
        painter.setPen(QPen(color, 1.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(7, 15), QPointF(15, 15))
        painter.drawLine(QPointF(15, 7), QPointF(15, 15))
        painter.end()


class Overlay(QWidget):
    """透明游戏提示层；主体锁定穿透，小锁独立接收点击以便随时解锁。"""
    def __init__(self, manager):
        super().__init__(None)
        self.manager = manager
        self.setWindowTitle("中文开局悬浮窗")
        self.setWindowIcon(manager.windowIcon())
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.drag = None
        self._needs_initial_corner = True
        self.applying_size = True
        self.resize_save = QTimer(self)
        self.resize_save.setSingleShot(True)
        self.resize_save.timeout.connect(self.save_size)
        self.setMinimumSize(280, 72)
        self.setMaximumSize(900, 1400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 23)
        layout.setSpacing(5)
        row = QHBoxLayout()
        self.header_layout = row
        row.setSpacing(4)
        self.title = QLabel("龙之骑士团 · 开局提示")
        self.title.setStyleSheet("color:#d5d5d8;font-size:12px;font-weight:500;")
        self.title.setMinimumWidth(0)
        self.title.setFixedHeight(20)
        from PyQt5.QtWidgets import QSizePolicy
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.title.setToolTip("按住这里拖动悬浮窗；松开后自动保存位置。")
        self.title.installEventFilter(self)
        row.addWidget(self.title, 1)
        self.clock_label = QLabel("00:00")
        self.clock_label.setStyleSheet("font-family:'Sen','Source Han Sans SC','Microsoft YaHei UI';font-size:16px;color:#f4f4f5;")
        self.clock_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row.addWidget(self.clock_label)
        self.voice_dot = QLabel("●")
        self.voice_dot.setToolTip("中文朗读状态")
        self.voice_dot.setStyleSheet("color:#22c55e;font-size:8px;")
        row.addWidget(self.voice_dot)
        self.controls = QWidget(self)
        self.controls_layout = QBoxLayout(QBoxLayout.LeftToRight, self.controls)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_layout.setSpacing(0)
        self.start_button = QPushButton()
        self.start_button.setObjectName("OverlayStart")
        self.start_button.setFixedSize(28, 28)
        start_policy = self.start_button.sizePolicy()
        start_policy.setRetainSizeWhenHidden(True)
        self.start_button.setSizePolicy(start_policy)
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.setFocusPolicy(Qt.StrongFocus)
        self.start_button.setAccessibleName("开始计时并自动锁定悬浮窗")
        self.start_button.setToolTip("开始后自动切换并朗读；点击金色小锁可解锁。")
        play_image = QPixmap(32, 32)
        play_image.fill(Qt.transparent)
        play_image.setDevicePixelRatio(2)
        play_painter = QPainter(play_image)
        play_painter.setRenderHint(QPainter.Antialiasing)
        play_path = QPainterPath()
        play_path.moveTo(5, 3)
        play_path.lineTo(13, 8)
        play_path.lineTo(5, 13)
        play_path.closeSubpath()
        play_painter.setPen(Qt.NoPen)
        play_painter.setBrush(QColor("#e5c88e"))
        play_painter.drawPath(play_path)
        play_painter.end()
        self.start_button.setIcon(fixed_color_icon(play_image))
        self.start_button.setIconSize(QSize(18, 18))
        self.start_button.setStyleSheet(
            "QPushButton#OverlayStart{background:#010c1b28;border:1px solid transparent;"
            "border-radius:7px;padding:0;}"
            "QPushButton#OverlayStart:hover{background:rgba(20,39,54,100);}"
            "QPushButton#OverlayStart:pressed{background:rgba(20,39,54,145);}"
            "QPushButton#OverlayStart:focus{border-color:#e6cd91;}"
        )
        self.start_button.clicked.connect(self.start_session)
        self.controls_layout.addWidget(self.start_button)
        # 锁槽只负责排版；真正接收点击的是独立的小窗，主体可保持鼠标穿透。
        self.lock_anchor = QWidget()
        self.lock_anchor.setFixedSize(28, 28)
        self.controls_layout.addWidget(self.lock_anchor)
        self.close_button = QPushButton()
        self.close_button.setObjectName("OverlayClose")
        self.close_button.setFixedSize(28, 28)
        close_policy = self.close_button.sizePolicy()
        close_policy.setRetainSizeWhenHidden(True)
        self.close_button.setSizePolicy(close_policy)
        self.close_button.setFocusPolicy(Qt.StrongFocus)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setAccessibleName("关闭悬浮窗并暂停计时")
        self.close_button.setToolTip("关闭悬浮窗并暂停计时；可从主面板或快捷键恢复。")
        close_image = QPixmap(32, 32)
        close_image.fill(Qt.transparent)
        close_image.setDevicePixelRatio(2)
        close_painter = QPainter(close_image)
        close_painter.setRenderHint(QPainter.Antialiasing)
        close_painter.setPen(QPen(QColor("#e5c88e"), 1.5, Qt.SolidLine, Qt.RoundCap))
        close_painter.drawLine(QPointF(4, 4), QPointF(12, 12))
        close_painter.drawLine(QPointF(12, 4), QPointF(4, 12))
        close_painter.end()
        self.close_button.setIcon(fixed_color_icon(close_image))
        self.close_button.setIconSize(QSize(18, 18))
        self.close_button.setStyleSheet(
            "QPushButton#OverlayClose {background:transparent;border:1px solid transparent;"
            "border-radius:5px;padding:0;}"
            "QPushButton#OverlayClose:hover {background:#35353b;}"
            "QPushButton#OverlayClose:pressed {background:#45454c;}"
            "QPushButton#OverlayClose:focus {border-color:#8b8b94;}"
        )
        self.close_button.clicked.connect(self.close)
        self.controls_layout.addWidget(self.close_button)
        row.addWidget(self.controls)
        layout.addLayout(row)
        self.resources = Resources(ASSETS)
        layout.addWidget(self.resources)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.RichText)
        self.notes.setAlignment(Qt.AlignTop)
        self.notes.setMargin(0)
        self.notes.setOpenExternalLinks(False)
        self.scroll.setWidget(self.notes)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(25)
        layout.addWidget(self.scroll, 1)
        self.size_grip = OverlayCornerGrip(self)
        self.size_grip.setToolTip("解锁后拖动此角调整大小；也可在设置中输入尺寸。")
        self.size_grip.setAccessibleName("调整悬浮窗大小")
        self.size_grip.setFixedSize(18, 18)
        # 缩放角覆盖在边缘，不另留一整行空白，也不把播放按钮压到正文底部。
        self.lock_handle = OverlayLockHandle(self, self.lock_anchor, self.toggle_lock, parent=self)
        self.goals = GoalHUD(manager.renderer, parent=self)
        self.apply_settings()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.notes.text():
            # 单一局部暗衬跟随真实正文高度，不逐行涂黑、不绘制边框或空白大底板。
            tail = 12 if self.visual_style == "b" else 23
            height = min(self.height(), self.scroll.y() + min(self.notes.height(), self.scroll.viewport().height()) + tail)
            p.drawPixmap(0, 0, self.content_backdrop(self.width(), height))
            opacity = self.manager.settings["opacity"]
            if self.visual_style == "b" and opacity > 0:
                p.setPen(QPen(QColor(232, 236, 220, round(24 * opacity / 100)), 1))
                y = self.scroll.y() - 6
                p.drawLine(QPointF(17, y), QPointF(self.width() - 17, y))

    def content_backdrop(self, width, height):
        """缓存四边渐隐的半透明深蓝暗衬；只按Qt逻辑尺寸绘制，不采样游戏。"""
        ratio = self.devicePixelRatioF()
        settings = self.manager.settings
        color, opacity = settings.get("overlay_color", DEFAULTS["overlay_color"]), settings["opacity"]
        key = (width, height, ratio, self.visual_style, color, opacity)
        if getattr(self, "_backdrop_key", None) != key:
            picture = style_backdrop(width, height, ratio, self.visual_style,
                                     background_color=color, opacity=opacity)
            self._backdrop_key, self._backdrop = key, picture
        return self._backdrop

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "size_grip"):
            self.size_grip.move(max(0, self.width() - 19), max(0, self.height() - 19))
        if getattr(self, "visual_style", "a") == "c" and hasattr(self, "controls"):
            self.controls.move(max(0, self.width() - 34), 7)
        automatic = event.size() == getattr(self, "_last_programmatic_size", None)
        if (hasattr(self, "resize_save") and not self.applying_size and not automatic
                and event.size() == self.size()):
            self._configured_height = self.height()
            self._content_size_key = None
            self.resize_save.start(250)

    def fit_on_screen(self, use_default=False):
        """限位只保存位置与宽度，不把内容自适应高度固化为用户偏好。"""
        requested_height = getattr(self, "_configured_height", self.manager.settings["height"])
        result = fit_overlay_widget(self, self.manager, use_default=use_default)
        self._last_programmatic_size = self.size()
        self._content_size_key = None
        self.manager.settings["height"] = requested_height
        return result

    def showEvent(self, event):
        # 每次应用会话首次显示靠助手所在屏幕右上角；之后恢复保留手动位置。
        use_default = self._needs_initial_corner
        self._needs_initial_corner = False
        self.fit_on_screen(use_default=use_default)
        super().showEvent(event)
        if self.notes.text():
            self.fit_content_height(self.notes.text())
        self.sync_goals()

    def hideEvent(self, event):
        if hasattr(self, "goals"):
            self.goals.hide()
        super().hideEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "goals"):
            screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
            self.goals.sync_screen(screen)

    def sync_goals(self):
        """村民与时代目标独立位于屏幕上方中央，正文区只显示阶段文字。"""
        if not hasattr(self, "goals") or not self.manager.order:
            return
        selected, _, _ = self.stage_groups(self.manager.order, self.manager.ids)
        self.goals.set_step(resource_step(self.manager.order, selected or self.manager.ids),
                            font_size=self.manager.settings["font_size"])
        self.goals.set_style(self.manager.settings.get("overlay_style", "a"))
        self.goals.set_appearance(self.manager.settings)
        if self.isVisible():
            screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
            self.goals.show_for_screen(screen)
        else:
            self.goals.hide()

    def save_size(self):
        requested_height = getattr(self, "_configured_height", self.height())
        self.fit_on_screen()
        self.manager.settings.update(width=self.width(), height=requested_height)
        self.manager.save_settings()

    def eventFilter(self, watched, event):
        if watched is self.title:
            if self.manager.settings["passthrough"]:
                return False
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.drag = event.globalPos() - self.pos()
                return True
            if event.type() == QEvent.MouseMove and self.drag is not None:
                self.move(event.globalPos() - self.drag)
                return True
            if event.type() == QEvent.MouseButtonRelease and self.drag is not None:
                self.drag = None
                self.fit_on_screen()
                self.manager.settings.update(x=self.x(), y=self.y())
                self.manager.save_settings()
                self.manager.position_fields()
                return True
        return super().eventFilter(watched, event)

    def apply_settings(self):
        self.applying_size = True
        self.resize_save.stop()
        settings = self.manager.settings
        self.apply_visual_style(settings.get("overlay_style", "a"))
        self._configured_height = settings["height"]
        self._content_size_key = None
        visible = self.isVisible()
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        if settings["passthrough"]:
            flags |= Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
            self.drag = None
        if self.windowFlags() != flags:
            self.setWindowFlags(flags)
        # 仅背景透明；正文和按钮保持清晰，不再随整个窗口一起变淡。
        self.setWindowOpacity(1.0)
        self.update()
        self.resize(settings["width"], settings["height"])
        self.apply_appearance()
        self.sync_lock_state()
        self.move(settings["x"], settings["y"])
        # 对完整窗口矩形限位，避免仅左上角可见而正文或关闭按钮越界。
        self.fit_on_screen()
        if visible:
            self.show()
        self.applying_size = False

    def apply_appearance(self):
        """即时外观刷新不重设位置、窗口标志、锁定或演练计时。"""
        settings = self.manager.settings
        wanted = overlay_style(settings.get("overlay_style", "a"))
        if getattr(self, "visual_style", None) != wanted:
            self.apply_visual_style(wanted)
        self._backdrop_key = None
        self._content_size_key = None
        overlay_font = make_ui_font()
        overlay_font.setPixelSize(settings["font_size"])
        self.notes.setFont(overlay_font)
        # 控件默认字号也声明像素，防止主界面的全局样式覆盖；段落保留各自层级。
        self.notes.setStyleSheet(f"color:{self.text_color('#f4f4f5')};background:transparent;font-size:{settings['font_size']}px;")
        self.apply_text_colors()
        for widget in (self.title, self.clock_label, self.voice_dot, self.notes):
            configure_hud_shadow(widget, settings.get("text_shadow", True),
                                 settings.get("text_shadow_strength", 75))
        self.goals.set_appearance(settings)
        self.setWindowOpacity(1.0)
        self.update()

    def apply_visual_style(self, value):
        """只调整视觉和排版，保留锁柄的独立原生窗口及主体穿透标志。"""
        self.visual_style = overlay_style(value)
        self._backdrop_key = None
        self._content_size_key = None
        rail = self.visual_style == "c"
        self.header_layout.removeWidget(self.controls)
        self.controls_layout.setDirection(QBoxLayout.TopToBottom if rail else QBoxLayout.LeftToRight)
        self.controls.setFixedSize(28, 84) if rail else self.controls.setFixedSize(84, 28)
        if rail:
            self.controls.move(max(0, self.width() - 34), 7)
            self.layout().setContentsMargins(0, 9, 38, 22)
            self.header_layout.setContentsMargins(53, 0, 0, 0)
        else:
            self.header_layout.addWidget(self.controls)
            self.header_layout.setContentsMargins(0, 0, 0, 0)
            self.layout().setContentsMargins(17, 10, 17, 13 if self.visual_style == "b" else 23)
        self.controls.show()
        # 侧轨显示计划节点时刻，头部仍保留实际计时，不能因样式切换丢失信息。
        self.clock_label.show()
        self.voice_dot.show()
        self.apply_text_colors()
        self.layout().setSpacing(9 if self.visual_style == "b" else 6)
        self.layout().activate()
        if hasattr(self, "goals"):
            self.goals.set_style(self.visual_style)
        if hasattr(self, "lock_handle"):
            self.lock_handle.sync_position()
        self.update()

    def text_color(self, default):
        """自定义颜色只用于文字，空设置保留各层原配色。"""
        return self.manager.settings.get("overlay_text_color") or default

    def apply_text_colors(self):
        """标题和计时不依赖阶段正文，换样式后也保持所选文字颜色。"""
        self.title.setStyleSheet(f"color:{self.text_color('#c6d0c7')};font-size:11px;font-weight:400;background:transparent;")
        self.clock_label.setStyleSheet(f"font-family:'Sen','Source Han Sans SC','Microsoft YaHei UI';font-size:14px;color:{self.text_color('#e6ebe2')};background:transparent;")

    def toggle_lock(self):
        """小锁独立接收鼠标；锁定主体时仍可直接解锁。"""
        self.manager.change_passthrough(not self.manager.settings["passthrough"])

    def sync_lock_state(self):
        locked = self.manager.settings["passthrough"]
        if hasattr(self, "lock_handle"):
            self.lock_handle.set_locked(locked)
        self.size_grip.setVisible(not locked)
        self.start_button.setVisible(not locked)
        self.close_button.setVisible(not locked)
        running = self.manager.clock.running
        continuing = self.manager.clock.seconds > 0
        action = "暂停计时" if running else "继续计时并自动锁定悬浮窗" if continuing else "开始计时并自动锁定悬浮窗"
        self.start_button.setIcon(action_icon("pause" if running else "play", "#e5c88e"))
        self.start_button.setAccessibleName(action)
        self.start_button.setToolTip(action)
        self.start_button.setEnabled(running or self.manager.voice_ready())

    def start_session(self):
        """解锁时开始、继续或暂停；开始和继续沿用自动锁定。"""
        self.manager.toggle_clock()

    def notify_locked(self):
        if self.isVisible():
            QToolTip.showText(self.lock_handle.mapToGlobal(QPoint(0, self.lock_handle.height() + 4)),
                "已锁定：点击金色小锁解锁后，可拖动或调整面板。", self.lock_handle, self.lock_handle.rect(), 2500)

    @staticmethod
    def stage_groups(data, selected_ids):
        """显示目标时间组及其前一组；手动跳转不依赖上次看过的内容。

        同刻节点与继承该时刻的无时间条件属于一组。全无时间的旧稿
        继续按手动节点显示，不能凭空给条件提示发明完成时间。
        """
        entries = data.get("build_order", [])
        selected = sorted({index for index in selected_ids if isinstance(index, int) and 0 <= index < len(entries)})
        if not selected:
            return [], [], False
        steps = timer_steps(data)
        timed = bool(steps)
        if len(steps) == len(entries) and all("time_sec" in step for step in steps):
            groups = []
            last_time = None
            for index, step in enumerate(steps):
                if not groups or step["time_sec"] != last_time:
                    groups.append([])
                    last_time = step["time_sec"]
                groups[-1].append(index)
            current_index = next(number for number, group in enumerate(groups) if selected[0] in group)
            return groups[current_index], groups[current_index - 1] if current_index else [], timed
        return selected, [selected[0] - 1] if selected[0] else [], timed

    def stage_html(self, data, indices, *, previous=False, timed=False, history_level=0):
        """每组带自己的资源建议；历史递减，文字与图标始终由安全渲染器生成。"""
        history_level = history_level or int(previous)
        main_size = self.manager.settings["font_size"]
        detail_size = max(8, round(main_size * 12 / 14))
        previous_size = main_size if main_size <= 14 else max(8, main_size - 2)
        caption_size = max(9, main_size - 4)
        stage_time = ""
        for index in indices:
            value = (data["build_order"][index].get("time") or "").strip()
            parts = value.split(":")
            if len(parts) == 2 and all(part.isdecimal() for part in parts):
                stage_time = timestamp(int(parts[0]) * 60 + int(parts[1]))
                break
        caption = "上上阶段" if history_level == 2 else "上一阶段" if history_level else "当前阶段"
        color = self.text_color("#c1cbbf" if history_level else "#e5c88e")
        role = "previous_previous" if history_level == 2 else "previous" if history_level else "current"
        sections = []
        for index in indices:
            step = data["build_order"][index]
            if timed and not (step.get("time") or "").strip():
                sections.append(f'<p style="margin:3px 0 1px 0;font-size:{previous_size if history_level else detail_size}px;'
                                f'color:{self.text_color(color if history_level else "#c5c8d2")};">条件提示</p>')
            paragraphs = list(step["notes"])
            generated = step.get("_zh_action_notes", [])
            has_generated = bool(generated and paragraphs[-len(generated):] == generated)
            if has_generated:
                paragraphs = paragraphs[:-len(generated)] + ["　".join(generated)]
            for paragraph, line in enumerate(paragraphs):
                generated_line = has_generated and paragraph == len(paragraphs) - 1
                size = detail_size if generated_line else previous_size if history_level else main_size
                text_color = self.text_color("#dbe2d7" if history_level else "#f2f4e9")
                icon_size = max(19, round(main_size * 19 / 14)) if generated_line else max(10, size + 2)
                sections.append(
                    f'<p style="margin:0 0 8px 0;line-height:165%;font-size:{size}px;'
                    f'color:{text_color};">{self.manager.renderer.html(line, size=icon_size)}</p>'
                )
        content = "".join(sections)
        if self.visual_style == "c":
            # Qt富文本表格负责固定时间列，避免用空格、字体宽度猜测对齐。
            short_caption = "上上" if history_level == 2 else "之前" if history_level else "当前"
            return (f'<div data-stage="{role}"><table width="100%" cellspacing="0" cellpadding="0"><tr>'
                    f'<td width="31" valign="top" align="right"><p style="margin:0;font-size:{max(9, caption_size-1)}px;color:{color};">'
                    f'{short_caption}<br>{stage_time}</p></td><td width="9"></td>'
                    f'<td width="1" bgcolor="{("#bba879" if not history_level else "#687d6b") if self.manager.settings["opacity"] else "transparent"}"></td><td width="12"></td>'
                    f'<td valign="top">{content}</td></tr></table></div>')
        marker = "● " if self.visual_style == "b" else "— "
        heading = caption + (" · " + stage_time if stage_time else "")
        return (f'<div data-stage="{role}"><p style="margin:0 0 8px 0;font-size:{caption_size}px;'
                f'font-weight:500;color:{color};">{marker}{heading}</p>{content}</div>')

    def fit_content_height(self, body):
        """按正文自然高度收紧或增高，不把自动高度写成用户的拖动尺寸。"""
        if self.resize_save.isActive():
            return  # 等待手动调整保存，避免把尚未保存的手动高度替换成自动高度。
        key = (self.width(), self.manager.settings["font_size"], self._configured_height, body, self.visual_style,
               self.manager.clock.running, self.manager.settings["passthrough"])
        if getattr(self, "_content_size_key", None) == key:
            return
        self._content_size_key = key
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.layout().activate()
        margins = self.layout().contentsMargins()
        # 直接询问真实QLabel在该宽度的换行高度，避免另建文档的字距差导致末行裁切。
        text_width = max(100, self.scroll.width() - self.scroll.frameWidth() * 2)
        text_height = max(0, self.notes.heightForWidth(text_width))
        items = [self.layout().itemAt(index) for index in range(self.layout().count())
                 if not self.layout().itemAt(index).isEmpty()]
        chrome = margins.top() + margins.bottom() + self.layout().spacing() * max(0, len(items) - 1)
        chrome += sum(item.sizeHint().height() for item in items if item.widget() is not self.scroll)
        natural = chrome + text_height + 2
        lower = min(area.height(), max(72, chrome + 25))
        # 完整三阶段优先，自然高度至多四分之三屏；短内容仍按实际高度收紧。
        # 极长作者正文保留在滚动区，解锁后可查看，不通过删字或摘要假装完整。
        cap = min(area.height(), max(lower, round(area.height() * .75) + 18))
        height = min(cap, max(lower, natural))
        applying = self.applying_size
        self.applying_size = True
        try:
            self.setMinimumHeight(lower)
            self._last_programmatic_size = QSize(min(self.width(), area.width()), height)
            self.resize(min(self.width(), area.width()), height)
            self.move(min(max(self.x(), area.left()), area.right() - self.width() + 1),
                      min(max(self.y(), area.top()), area.bottom() - self.height() + 1))
        finally:
            self.applying_size = applying

    def render(self):
        manager = self.manager
        data = manager.order
        if data is None:
            return
        ids = manager.ids
        current_ids, previous_ids, has_timeline = self.stage_groups(data, ids)
        self.current_stage_ids, self.previous_stage_ids = current_ids, previous_ids
        self.previous_previous_stage_ids = self.stage_groups(data, previous_ids)[1] if previous_ids else []
        current = resource_step(data, current_ids or ids)
        self.layout().activate()
        self.title.setText(self.title.fontMetrics().elidedText(data["name"], Qt.ElideRight, max(0, self.title.width())))
        self.title.setToolTip(data["name"] + "\n按住标题可移动悬浮窗。")
        self.clock_label.setText(timestamp(manager.clock.seconds))
        self.voice_dot.setStyleSheet(f"color:{'#4ade80' if manager.settings['speech'] else '#71717a'};font-size:8px;")
        self.voice_dot.setToolTip("中文朗读已开启" if manager.settings["speech"] else "中文朗读已关闭，可在主界面开启")
        self.resources.set_step(current)
        self.resources.hide()  # 目标已改为屏幕上方居中的独立提示条。
        self.sync_goals()
        # 当前阶段置顶，保留前两组；进入新组时最老一组自然退出，不积累全文。
        sections = [self.stage_html(data, current_ids or ids, timed=has_timeline)]
        # Qt 的 hr（水平分隔线）会保留整行默认字高；用细字高分段避免额外空白。
        separator = '<p style="margin:0;font-size:16px;line-height:100%;"> </p>'
        if previous_ids:
            sections.append(separator)
            sections.append(self.stage_html(data, previous_ids, previous=True, timed=has_timeline))
        if self.previous_previous_stage_ids:
            sections.append(separator)
            sections.append(self.stage_html(data, self.previous_previous_stage_ids, timed=has_timeline, history_level=2))
        body = "".join(sections)
        if self.notes.text() != body:
            self.notes.setText(body)
        self.sync_lock_state()
        self.fit_content_height(body)
        stage_key = (id(data), tuple(current_ids))
        if getattr(self, "_stage_key", None) != stage_key:
            self.scroll.verticalScrollBar().setValue(0)
            self._stage_key = stage_key

    def closeEvent(self, event):
        event.ignore()
        self.drag = None
        self.manager.clock.pause()
        self.manager.stop_voice()
        self.hide()
        self.manager.update_display()


class Manager(FramelessWindow):
    def __init__(self, application, data_dir, state_file=None):
        super().__init__()
        self.setObjectName("Manager")
        self.application = application
        self.data_dir = Path(data_dir)
        self.orders_dir = self.data_dir / "开局库"
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "设置.json"
        self.settings, warning = load_settings(self.settings_path, logical_dpi=self.logicalDpiY())
        self.state_file = state_file
        self.clock = Clock()
        self.order = None
        self.order_path = None
        self.ids = [0]
        self._manual_timeline_ids = None
        self._overlay_completion_handled = False
        self._overlay_completion_hidden = False
        self.step_alert = ""
        self.last_second = -1
        self._online_ready = False
        self._online_job = None
        self._audition_after_prepare = False
        self.renderer = NoteRenderer(ASSETS)
        self.setWindowTitle(APP_NAME)
        self.setAccessibleName(APP_NAME)
        self.setWindowIcon(QIcon(brand_mark(64)))
        self.resize(1000, 680)
        self.setMinimumSize(760, 520)
        self.seed()
        shell = QVBoxLayout(self)
        shell.setSizeConstraint(QLayout.SetNoConstraint)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)
        self.window_bar = WindowBar(self)
        shell.addWidget(self.window_bar)
        self.navigation = []
        for index, name in ((0, '开局库'), (3, '玩家查询'), (4, '对局分析'), (1, '设置'), (5, '个人'), (2, '关于')):
            item = button(name, lambda checked=False, i=index: self.navigate(i))
            item.setObjectName("QuietNav")
            # 标题栏与品牌采用固定字体尺度，正文调到18像素时六个导航仍完整显示。
            item.setStyleSheet("font-size:13px;")
            item.setCheckable(True)
            item.setProperty('pageIndex', index)
            self.navigation.append(item)
            self.window_bar.navigation_layout.addWidget(item)
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        shell.addWidget(workspace, 1)
        self.page_heading = QWidget()
        heading = QVBoxLayout(self.page_heading)
        self.page_heading_layout = heading
        heading.setContentsMargins(32, 22, 32, 12)
        heading.setSpacing(4)
        self.page_title = ui_text("开局工作台", "PageTitle")
        self.page_subtitle = ui_text("", "Muted")
        heading.addWidget(self.page_title)
        heading.addWidget(self.page_subtitle)
        layout.addWidget(self.page_heading)
        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
        layout.addWidget(self.tabs, 1)
        self.make_orders()
        self.make_settings()
        self.make_help()
        analysis_container = QWidget()
        analysis_layout = QVBoxLayout(analysis_container)
        self.analysis_layout = analysis_layout
        analysis_layout.setContentsMargins(32, 22, 32, 12)
        self.analysis_page = AnalysisPage(self.data_dir)
        analysis_layout.addWidget(self.analysis_page)
        self.tabs.addTab(analysis_container, '玩家查询')
        replay_container = QWidget()
        self.replay_layout = QVBoxLayout(replay_container)
        self.replay_layout.setContentsMargins(32, 0, 32, 12)
        self.replay_page = ReplayPage(self.data_dir)
        self.replay_layout.addWidget(self.replay_page)
        self.tabs.addTab(replay_container, '对局分析')
        # 新占位页只追加内部索引，保留既有设置、关于、查询及分析的路由编号。
        self.personal_page, self.personal_layout = self.make_development_page("个人")
        self.online_orders_page, self.online_orders_layout = self.make_development_page("在线开局库")
        self.analysis_page.public_match_requested.connect(self.open_match_analysis)
        # 保留内部状态接口，成功消息不再显示，也不占用主窗口底部空间。
        self.status_bar = label(warning or "", True)
        self.status_bar.setParent(self)
        self.status_bar.hide()
        self._problem_dialog = None
        self.voice_badge = ui_text("", "Muted")
        self.voice_badge.setWordWrap(False)
        self.voice_badge.setStyleSheet("font-size:12px;background:transparent;padding:0 6px;")
        self.voice_badge.hide()
        self.window_bar.status_layout.addWidget(self.voice_badge)
        self.overlay = Overlay(self)
        self.speech = Speech()
        self.speech.voices_ready.connect(self.on_voices)
        self.speech.problem.connect(self.speech_problem)
        self.speech.started.connect(self.on_speech_started)
        self.online = OnlineSpeech(self.data_dir / "语音缓存", self)
        self.online.progress.connect(self.on_online_progress)
        self.online.prepared.connect(self.on_online_prepared)
        self.online.problem.connect(self.speech_problem)
        self.online.started.connect(self.on_speech_started)
        self.hotkeys = GlobalHotkeys(application, self.on_hotkey)
        self.register_hotkeys()
        self.speech.start()
        self.refresh_library()
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(100)
        self._last_state = None
        self.navigate(0)
        self.apply_ui_font()
        from update_ui import UpdateController
        self.updates = UpdateController(self)
        if warning:
            self.show_problem(warning, "资料读取提示")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 0 if self.isMaximized() else 14
        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)
        p.setClipPath(shape)
        paint_game_surface(p, rect)
        p.setClipping(False)
        p.setPen(QPen(QColor('#303034'), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, radius, radius)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "work_layout") and hasattr(self, "settings_columns"):
            self.reflow()

    def layout_library(self):
        """按整行分配卡片宽度与间距，余数均分到各列，尾行保持左对齐。"""
        from math import ceil
        available = max(1, self.library.viewport().width())
        gap, maximum_card = 16, 244
        columns = max(1, ceil((available + gap) / (maximum_card + gap)))
        card_width, remainder = divmod(max(1, available - (columns - 1) * gap), columns)
        widths = [card_width + (column < remainder) for column in range(columns)]
        height = CoverDelegate.CARD_HEIGHT + gap
        # 固定网格会把不能整除的宽度全部留在右端；使用逐列尺寸精确铺满。
        if self.library.gridSize().isValid():
            self.library.setGridSize(QSize())
        visible = 0
        positions = []
        x = 0
        for number in range(self.library.count()):
            item = self.library.item(number)
            if item.isHidden():
                continue
            column = visible % columns
            trailing_gap = gap if column + 1 < columns else 0
            item_size = QSize(widths[column] + trailing_gap, height)
            if item.sizeHint() != item_size:
                item.setSizeHint(item_size)
            if item.data(CoverDelegate.CELL_GAP_ROLE) != trailing_gap:
                item.setData(CoverDelegate.CELL_GAP_ROLE, trailing_gap)
            positions.append((item, QPointF(x, (visible // columns) * height).toPoint()))
            visible += 1
            x = x + item_size.width() if visible % columns else 0
        self.library.doItemsLayout()
        # Qt 默认图标排版按最大槽宽换行，不能均分每列的整数余数。
        # 显式设置内容坐标；控件已关闭拖拽，用户仍只选择或打开开局。
        for item, position in positions:
            self.library.setPositionForIndex(position, self.library.indexFromItem(item))
        scrollbar = self.library.verticalScrollBar()
        scrollbar.setPageStep(self.library.viewport().height())
        scrollbar.setRange(0, max(0, ceil(visible / columns) * height - self.library.viewport().height()))

    def schedule_library_layout(self):
        """一次事件循环内的连续尺寸通知合并成一次更新。"""
        if hasattr(self, 'library_layout_timer') and not self.library_layout_timer.isActive():
            self.library_layout_timer.start()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize and hasattr(self, 'library') and watched is self.library.viewport():
            self.schedule_library_layout()
        return super().eventFilter(watched, event)

    def reflow(self):
        compact = self.width() < 880
        short = self.height() < 610
        margin = 20 if compact else 32
        self.home_layout.setContentsMargins(margin, 22, margin, 12)
        self.page_heading_layout.setContentsMargins(margin, 22, margin, 12)
        self.analysis_layout.setContentsMargins(margin, 22, margin, 12)
        self.replay_layout.setContentsMargins(margin, 0, margin, 12)
        self.settings_outer_layout.setContentsMargins(margin, 0, margin, 12)
        self.help_outer_layout.setContentsMargins(margin, 0, margin, 12)
        self.personal_layout.setContentsMargins(margin, 0, margin, 12)
        self.online_orders_layout.setContentsMargins(margin, 0, margin, 12)
        self.work_layout.setContentsMargins(margin, 16, margin, 12)
        self.work_layout.setSpacing(14 if short else 20)
        self.step_rail.setFixedWidth(220 if compact else 280)
        self.next_step_hint.setVisible(not short and bool(self.order) and self.ids[-1] + 1 < len(self.order['build_order']))
        self.settings_columns.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)
        self.schedule_library_layout()

    def resize_command(self):
        """短指令下的操作紧随正文；长指令保留可滚动区域，不让播放键远离内容。"""
        if not self.order or not self.preview_card.isVisible():
            return
        desired = self.preview.heightForWidth(max(100, self.command_scroll.viewport().width())) + 10
        limit = max(88, min(240, self.preview_card.height() - 190))
        self.command_scroll.setFixedHeight(min(limit, max(88, desired)))

    def navigate(self, index):
        self.tabs.setCurrentIndex(index)
        names = [("开局库", ""), ("设置", "声音、悬浮显示与操作习惯"),
                 ("关于", ""), ('玩家查询', ''), ('对局分析', ''), ("个人", ""), ("在线开局库", "")]
        self.page_title.setText(names[index][0])
        self.page_subtitle.setText(names[index][1])
        self.page_heading.setVisible(index not in (0, 3))
        if index == 1:
            # 隐藏期间宽窄列切换后，清除旧高度分配，避免语音卡被空白撑高。
            settings_layout = self.settings_scroll.widget().layout()
            settings_layout.invalidate()
            settings_layout.activate()
        if index == 0:
            self.work_views.setCurrentIndex(0)
        active_navigation = 0 if index == 6 else index
        for item in self.navigation:
            item.setChecked(item.property('pageIndex') == active_navigation)

    def open_match_analysis(self, profile_id, game_id, context):
        """只切换主页面并打开所选对局，保留玩家查询的搜索、筛选与滚动位置。"""
        self.navigate(4)
        self.replay_page.import_public_match(profile_id, game_id, context)

    def open_replay(self, path=None):
        """显式本地录像入口与单场公开分析共用同一个独立页面。"""
        self.navigate(4)
        if path:
            self.replay_page.import_path(path)

    def open_session(self, item=None):
        """进入当前开局；选择同一卡片时保留已开始的演练进度。"""
        if self.order:
            self.tabs.setCurrentIndex(0)
            self.work_views.setCurrentIndex(1)
            self.update_display()
            QTimer.singleShot(0, self.resize_command)

    def show_library(self):
        self.work_views.setCurrentIndex(0)
        self.layout_library()

    def seed(self):
        seed_default_openings(self.data_dir, self.orders_dir)

    def make_orders(self):
        self.work_views = QStackedWidget()

        def tool(caption, callback, icon):
            control = button("", callback, caption)
            control.setObjectName("Quiet")
            control.setAccessibleName(caption)
            control.setIcon(line_icon(icon))
            control.setIconSize(QSize(18, 18))
            control.setFixedSize(36, 36)
            control.setStyleSheet("padding:7px;")
            return control

        # 首页只做开局选择，不混入当前指令、步骤和播放器。
        home = QWidget()
        self.collection_panel = home
        self.home_layout = QVBoxLayout(home)
        self.home_layout.setContentsMargins(32, 22, 32, 12)
        self.home_layout.setSpacing(20)
        self.library_layout = self.home_layout
        heading = QHBoxLayout()
        heading.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(ui_text("我的开局", "LibraryHeading"))
        titles.addWidget(ui_text("选择一套开局，进入你的节奏。", "Muted"))
        heading.addLayout(titles, 1)
        import_control = TapestryAction("本地导入", self.import_dialog, "castle")
        import_control.setIcon(line_icon("import", "#f4f4f5"))
        import_control.setIconSize(QSize(18, 18))
        import_control.setFixedHeight(36)
        import_control.setToolTip("选择本地开局文件导入")
        import_control.setAccessibleName("本地导入")
        new_control = TapestryAction("新建开局", self.new_order, "knight")
        new_control.setObjectName("Primary")
        new_control.setIcon(line_icon("new", "#f4f4f5"))
        new_control.setIconSize(QSize(16, 16))
        new_control.setFixedHeight(36)
        heading.addWidget(import_control)
        heading.addWidget(new_control)
        self.home_layout.addLayout(heading)
        filters = QHBoxLayout()
        filters.setSpacing(8)
        filters.addWidget(ui_text("全部开局", "SectionTitle"))
        self.collection_count = ui_text("0", "Muted")
        filters.addWidget(self.collection_count)
        self.undo_button = button("撤销移除", self.undo_remove)
        self.undo_button.setObjectName("Quiet")
        self.undo_button.setStyleSheet("border:0;background:transparent;font-size:12px;padding:4px 8px;")
        self.undo_button.hide()
        filters.addWidget(self.undo_button)
        filters.addStretch()
        self.search = QLineEdit()
        self.search.setObjectName("HomeSearch")
        self.search.setPlaceholderText("搜索开局或文明")
        self.search.setAccessibleName("搜索开局或文明")
        self.search.addAction(line_icon("search"), QLineEdit.LeadingPosition)
        self.search.setMaximumWidth(280)
        self.search.setFixedHeight(36)
        self.search.textChanged.connect(self.filter_orders)
        filters.addWidget(self.search)
        self.home_layout.addLayout(filters)
        self.library = QListWidget()
        self.library_layout_timer = QTimer(self)
        self.library_layout_timer.setSingleShot(True)
        self.library_layout_timer.setInterval(0)
        self.library_layout_timer.timeout.connect(self.layout_library)
        self.library.setObjectName("CoverLibrary")
        self.library.setMouseTracking(True)
        self.library.setViewMode(QListWidget.IconMode)
        self.library.setResizeMode(QListWidget.Fixed)
        self.library.setMovement(QListWidget.Free)
        self.library.setDragDropMode(QListWidget.NoDragDrop)
        self.library.setUniformItemSizes(False)
        self.library.setSpacing(0)
        self.library.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 隐藏滚动条并不预留右侧槽位，滚轮和键盘仍可浏览后面的开局。
        self.library.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.library.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        delegate = CoverDelegate(self.library)
        delegate.remove_requested.connect(self.remove_order)
        delegate.open_requested.connect(self.open_session)
        self.library.setItemDelegate(delegate)
        self.library.currentItemChanged.connect(self.choose_order)
        self.library.itemActivated.connect(self.open_session)
        self.home_layout.addWidget(self.library, 1)
        home_footer = QHBoxLayout()
        online_control = TapestryAction("从网络获取开局", self.open_online_orders, "castle")
        self.online_orders_button = online_control
        online_control.setIcon(line_icon("online"))
        online_control.setObjectName("Quiet")
        home_footer.addWidget(online_control)
        home_footer.addStretch()
        self.resume_button = TapestryAction("继续演练", self.open_session, "dragon")
        self.resume_button.setIcon(line_icon("chevron"))
        self.resume_button.setObjectName("Quiet")
        home_footer.addWidget(self.resume_button)
        self.home_layout.addLayout(home_footer)
        self.work_views.addWidget(home)

        # 演练页是独立的一项任务，操作附属于当前指令，而不是贯穿全屏的播放条。
        detail = QWidget()
        self.work_layout = QVBoxLayout(detail)
        self.work_layout.setContentsMargins(32, 16, 32, 12)
        self.work_layout.setSpacing(20)
        self.hero_card = QFrame()
        self.hero_card.setObjectName("DetailHeader")
        header = QHBoxLayout(self.hero_card)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)
        header.addWidget(tool("返回开局库", self.show_library, "back"))
        self.hero_image = game_image("landmark_hre/burgrave-palace.webp", 40)
        header.addWidget(self.hero_image)
        self.order_info = label("请选择开局")
        self.order_info.setTextFormat(Qt.RichText)
        self.order_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header.addWidget(self.order_info, 1)
        edit_control = tool("编辑所选开局", self.edit_order, "edit")
        export_control = tool("导出所选开局", self.export_dialog, "export")
        header.addWidget(edit_control)
        header.addWidget(export_control)
        self.show_button = TapestryAction("悬浮窗", self.toggle_overlay, "knight")
        self.show_button.setIcon(line_icon("window"))
        self.show_button.setObjectName("Quiet")
        self.show_button.setCheckable(True)
        self.show_button.setFixedHeight(36)
        header.addWidget(self.show_button)
        self.passthrough_check = QPushButton()
        self.passthrough_check.setObjectName("Quiet")
        self.passthrough_check.setCheckable(True)
        self.passthrough_check.setFixedSize(36, 36)
        self.passthrough_check.setIconSize(QSize(18, 18))
        self.passthrough_check.setStyleSheet("padding:0;")
        self.passthrough_check.setCursor(Qt.PointingHandCursor)
        self.passthrough_check.toggled.connect(self.change_passthrough)
        self.sync_overlay_lock_control()
        header.addWidget(self.passthrough_check)
        self.work_layout.addWidget(self.hero_card)

        self.options_panel = QFrame()
        self.options_panel.setObjectName("SessionOptions")
        options = QHBoxLayout(self.options_panel)
        options.setContentsMargins(12, 8, 12, 8)
        options.setSpacing(20)
        self.quick_speech = QCheckBox("语音朗读")
        self.quick_speech.setChecked(self.settings['speech'])
        self.quick_speech.toggled.connect(self.change_speech)
        self.auto_check = QCheckBox("自动切步")
        self.auto_check.setChecked(self.settings['auto'])
        self.auto_check.toggled.connect(self.change_auto)
        for toggle in (self.quick_speech, self.auto_check):
            options.addWidget(toggle)
        options.addStretch()
        self.options_panel.hide()
        self.work_layout.addWidget(self.options_panel)
        body = QHBoxLayout()
        body.setSpacing(20)
        self.preview_card, focus = card(name="FocusPanel")
        focus.setContentsMargins(24, 22, 24, 22)
        focus.setSpacing(16)
        self.console = QFrame()
        self.console.setObjectName("FocusStatus")
        clock_row = QHBoxLayout(self.console)
        clock_row.setContentsMargins(0, 0, 0, 0)
        clock_row.setSpacing(12)
        self.console_clock = QLabel("00:00")
        self.console_clock.setStyleSheet("font-family:'Sen','Source Han Sans SC','Microsoft YaHei UI';font-size:28px;color:#f4f4f5;font-weight:300;")
        clock_row.addWidget(self.console_clock)
        self.console_state = ui_text("已暂停", "Muted")
        clock_row.addWidget(self.console_state)
        clock_row.addStretch()
        self.options_button = tool("演练选项", lambda: self.options_panel.setVisible(not self.options_panel.isVisible()), "settings")
        clock_row.addWidget(self.options_button)
        focus.addWidget(self.console)
        current = QHBoxLayout()
        current.addWidget(ui_text("当前步骤", "Muted"))
        self.session_step = ui_text("01 / 01", "Muted")
        self.session_step.setStyleSheet("color:#d5d5d8;font-size:13px;")
        current.addWidget(self.session_step)
        current.addStretch()
        focus.addLayout(current)
        self.command_scroll = QScrollArea()
        self.command_scroll.setWidgetResizable(True)
        self.command_scroll.setMinimumHeight(88)
        self.preview = QLabel()
        self.preview.setObjectName("PreviewText")
        self.preview.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.RichText)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.command_scroll.setWidget(self.preview)
        self.command_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        focus.addWidget(self.command_scroll)
        self.session_resources = Resources(ASSETS)
        self.session_resources.columns = 4
        focus.addWidget(self.session_resources)
        controls = QHBoxLayout()
        controls.setSpacing(10)
        previous = icon_button("previous", "上一步", lambda: self.step(-1))
        previous.setObjectName("Quiet")
        previous.setFixedSize(38, 40)
        controls.addWidget(previous)
        # 普通图标按钮保留原始金色；主纹样按钮会自行染色，不适用于播放控件。
        self.pause_button = icon_button("play", "开始计时", self.toggle_clock)
        self.pause_button.setObjectName("StartButton")
        self.pause_button.setStyleSheet(
            "QPushButton#StartButton{background:#242428;border:1px solid #6d6248;border-radius:7px;padding:4px;}"
            "QPushButton#StartButton:hover{background:#303035;border-color:#bda675;}"
            "QPushButton#StartButton:pressed{background:#19191C;}"
            "QPushButton#StartButton:disabled{background:#202023;border-color:#44443F;}"
            "QPushButton#StartButton:focus{border:2px solid #e5c88e;}"
        )
        self.pause_button.setFixedSize(64, 40)
        controls.addWidget(self.pause_button)
        following = icon_button("next", "下一步", lambda: self.step(1))
        following.setObjectName("Quiet")
        following.setFixedSize(38, 40)
        controls.addWidget(following)
        controls.addStretch()
        controls.addWidget(tool("计时归零", self.reset_clock, "reset"))
        focus.addLayout(controls)
        focus.addStretch(1)
        self.next_step_hint = ui_text("", "Muted")
        self.next_step_hint.setWordWrap(True)
        self.next_step_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        focus.addWidget(self.next_step_hint)
        body.addWidget(self.preview_card, 1)
        self.step_rail = QFrame()
        self.step_rail.setObjectName("StepRail")
        self.step_rail.setFixedWidth(280)
        rail = QVBoxLayout(self.step_rail)
        rail.setContentsMargins(8, 16, 0, 0)
        rail.setSpacing(18)
        step_heading = QHBoxLayout()
        step_heading.addWidget(ui_text("全部步骤", "SectionTitle"))
        step_heading.addStretch()
        self.timeline_count = ui_text("", "Muted")
        step_heading.addWidget(self.timeline_count)
        rail.addLayout(step_heading)
        self.steps_list = QListWidget()
        self.steps_list.setObjectName("Timeline")
        self.steps_list.setMinimumHeight(56)
        self.steps_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.steps_list.setItemDelegate(StepDelegate(self.steps_list))
        self.steps_list.itemClicked.connect(self.click_step)
        rail.addWidget(self.steps_list, 1)
        body.addWidget(self.step_rail)
        self.work_layout.addLayout(body, 1)
        self.work_views.addWidget(detail)
        self.action_buttons = [import_control, online_control, new_control, edit_control, export_control]
        self.tabs.addTab(self.work_views, "开局库")

    def make_settings(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        self.settings_outer_layout = outer
        outer.setContentsMargins(32, 0, 32, 12)
        scroll = QScrollArea()
        self.settings_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 9, 0)
        layout.setSpacing(17)
        # 语音放在最前；开关和语音参数即时保存，不再依赖底部的应用按钮。
        voice, voice_layout = card()
        voice_layout.setContentsMargins(22, 22, 22, 22)
        voice_layout.setSpacing(15)
        voice_header = QHBoxLayout()
        voice_symbol = QLabel()
        voice_symbol.setPixmap(line_icon("sound", "#B8B8C0").pixmap(24, 24))
        voice_symbol.setFixedSize(30, 30)
        voice_header.addWidget(voice_symbol)
        voice_titles = QVBoxLayout()
        voice_titles.setSpacing(3)
        voice_titles.addWidget(ui_text("中文语音提醒", "SectionTitle"))
        voice_titles.addWidget(ui_text("开始计时和切换步骤时朗读当前指令。", "Muted"))
        voice_header.addLayout(voice_titles, 1)
        self.speech_check = QCheckBox("开启朗读")
        self.speech_check.setChecked(self.settings["speech"])
        self.speech_check.toggled.connect(self.change_speech)
        voice_header.addWidget(self.speech_check)
        voice_layout.addLayout(voice_header)
        engine_row = QHBoxLayout()
        engine_row.addWidget(ui_text("声音来源", "Muted"))
        self.engine_input = ClickComboBox()
        self.engine_input.addItem("在线自然语音 · 免登录", "online")
        self.engine_input.addItem("系统中文声音", "system")
        self.engine_input.setCurrentIndex(self.engine_input.findData(self.settings["voice_engine"]))
        self.engine_input.currentIndexChanged.connect(self.change_voice_engine)
        engine_row.addWidget(self.engine_input, 1)
        self.prepare_button = TapestryAction("准备本局语音", lambda: self.prepare_voice(force=True), "castle")
        engine_row.addWidget(self.prepare_button)
        voice_layout.addLayout(engine_row)
        voice_controls = QHBoxLayout()
        self.voice_input = ClickComboBox()
        self.voice_input.addItem("正在检查中文声音…", "")
        self.voice_input.currentIndexChanged.connect(self.change_voice)
        voice_controls.addWidget(self.voice_input, 1)
        self.online_input = ClickComboBox()
        for voice_id, caption in CHINESE_VOICES:
            self.online_input.addItem(caption, voice_id)
        self.online_input.setCurrentIndex(self.online_input.findData(self.settings["online_voice"]))
        self.online_input.currentIndexChanged.connect(self.change_online_voice)
        voice_controls.addWidget(self.online_input, 1)
        self.voice_input.setVisible(self.settings["voice_engine"] == "system")
        self.online_input.setVisible(self.settings["voice_engine"] == "online")
        self.prepare_button.setVisible(self.settings["voice_engine"] == "online")
        audition = TapestryAction("试听中文", self.test_voice, "dragon")
        audition.setObjectName("Primary")
        voice_controls.addWidget(audition)
        voice_layout.addLayout(voice_controls)
        voice_parameters = QHBoxLayout()
        voice_parameters.setSpacing(18)
        voice_parameters.addWidget(ui_text("音量", "Muted"))
        self.volume_input = spin(0, 100, self.settings["volume"], "%")
        voice_parameters.addWidget(slider_field(self.volume_input, 0, 100), 1)
        voice_parameters.addWidget(ui_text("语速", "Muted"))
        self.rate_input = ClickComboBox()
        for caption, value in (("默认", 0), ("快", 2), ("慢", -2)):
            self.rate_input.addItem(caption, value)
        self.rate_input.setCurrentIndex(self.rate_input.findData(self.settings["rate"]))
        self.rate_input.setAccessibleName("播报语速")
        self.rate_input.setFixedWidth(104)
        voice_parameters.addWidget(self.rate_input)
        self.volume_input.valueChanged.connect(self.change_audio_preferences)
        self.rate_input.currentIndexChanged.connect(self.change_audio_preferences)
        voice_layout.addLayout(voice_parameters)
        self.voice_status = ui_text("联网生成时发送播报文字；缓存后可断网播放，无需登录。", "Muted")
        voice_layout.addWidget(self.voice_status)
        layout.addWidget(voice)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        self.settings_columns = columns
        appearance = QGroupBox("悬浮窗显示")
        self.appearance_group = appearance
        form = QFormLayout(appearance)
        form.setVerticalSpacing(16)
        form.setHorizontalSpacing(18)
        self.font_input = spin(8, 80, self.settings["font_size"], " 像素")
        self.ui_font_input = spin(11, 18, self.settings["ui_font_size"], " 像素")
        self.opacity_input = spin(0, 100, self.settings["opacity"], "%")
        self.opacity_input.setToolTip("只调整背景；0%完全透明，文字和图标不随背景变淡。修改立即保存。")
        self._overlay_color_value = QColor(self.settings.get("overlay_color", DEFAULTS["overlay_color"])).name()
        self.overlay_color_input = button("", self.choose_overlay_color)
        self.overlay_color_input.setToolTip("选择提示层和目标条的背景颜色，修改立即保存。")
        self.refresh_overlay_color_button()
        self._overlay_text_color_value = self.settings.get("overlay_text_color", "")
        self.overlay_text_color_input = button("", self.choose_overlay_text_color)
        self.overlay_text_color_input.setToolTip("选择标题、计时、阶段正文及目标数字的颜色，修改立即保存。")
        self.default_text_color_button = button("默认", self.reset_overlay_text_color)
        self.default_text_color_button.setAccessibleName("恢复默认文字配色")
        self.default_text_color_button.setToolTip("恢复原有分层文字颜色，不改变其他外观设置。")
        text_color_row = QHBoxLayout()
        text_color_row.setSpacing(6)
        text_color_row.addWidget(self.overlay_text_color_input, 1)
        text_color_row.addWidget(self.default_text_color_button)
        self.refresh_overlay_text_color_button()
        self.text_shadow_check = QCheckBox("启用黑色文字阴影")
        self.text_shadow_check.setChecked(self.settings.get("text_shadow", True))
        self.text_shadow_strength_input = spin(0, 100, self.settings.get("text_shadow_strength", 75), "%")
        self.text_shadow_strength_input.setEnabled(self.text_shadow_check.isChecked())
        self.text_shadow_strength_input.setToolTip("只改变文字后方的黑色阴影，0%无阴影；不影响文字本身透明度。")
        self.font_input.setToolTip("悬浮正文与目标条的像素字号，修改立即保存，不重置演练。")
        self.restore_appearance_button = button("恢复默认外观", self.restore_overlay_appearance)
        self.restore_appearance_button.setToolTip("恢复14像素、默认背景65%和黑色阴影；保留样式、位置及宽度。")
        self.overlay_style_input = ClickComboBox()
        for caption, value in (("字幕", "a"), ("侧栏", "c"), ("浮层", "b")):
            self.overlay_style_input.addItem(caption, value)
        self.overlay_style_input.setCurrentIndex(self.overlay_style_input.findData(self.settings.get("overlay_style", "a")))
        self.overlay_style_input.setToolTip("选择后立即应用并保存，同时更新右上提示与屏幕中上方目标条。")
        self.overlay_style_input.currentIndexChanged.connect(self.change_overlay_style)
        self.x_input = spin(-30000, 30000, self.settings["x"])
        self.y_input = spin(-30000, 30000, self.settings["y"])
        self.timing_input = ClickComboBox()
        self.timing_input.addItem("按完成时间提前提示", "ending")
        self.timing_input.addItem("按开始时间提示", "starting")
        self.timing_input.setCurrentIndex(self.timing_input.findData(self.settings["timing"]))
        self.timing_input.setToolTip("例如攻略写00:30建造房屋：按开始时间，会在00:30切到这条提示；"
                                     "按完成时间，会提前显示，到00:30后切到下一条。"
                                     "这里只切换提示，不会检测房屋是否建成。")
        for caption, widget in [("悬浮样式", self.overlay_style_input), ("主界面字号", self.ui_font_input), ("悬浮提示字号", self.font_input), ("文字颜色", text_color_row), ("背景颜色", self.overlay_color_input), ("背景不透明度", self.opacity_input),
                             ("文字阴影", self.text_shadow_check), ("阴影强度", self.text_shadow_strength_input),
                             ("横向位置", self.x_input), ("纵向位置", self.y_input), ("攻略时间含义", self.timing_input)]:
            form.addRow(caption, widget)
        form.addRow(self.restore_appearance_button)
        for widget in (self.font_input, self.opacity_input, self.text_shadow_strength_input):
            widget.valueChanged.connect(self.change_overlay_appearance)
        self.text_shadow_check.toggled.connect(self.change_overlay_appearance)
        columns.addWidget(appearance, 1)
        hotkeys = QGroupBox("全局快捷键")
        self.hotkeys_group = hotkeys
        form = QFormLayout(hotkeys)
        form.setVerticalSpacing(16)
        form.setHorizontalSpacing(18)
        self.key_inputs = {}
        for action, caption in HOTKEY_LABELS.items():
            widget = QKeySequenceEdit(QKeySequence(self.settings["hotkeys"][action]))
            widget.findChild(QLineEdit).setPlaceholderText("未设置")
            widget.setToolTip("按下组合键；使用退格键清除。只接受单组组合键。")
            self.key_inputs[action] = widget
            form.addRow(caption, widget)
        clear_keys = button("清空全部快捷键", self.default_keys)
        clear_keys.setToolTip("立即清空并停用已登记的全部全局快捷键。")
        form.addRow(clear_keys)
        right_column = QWidget()
        right_column.setObjectName("SettingsRightColumn")
        self.settings_right_column = right_column
        right_stack = QVBoxLayout(right_column)
        right_stack.setContentsMargins(0, 0, 0, 0)
        right_stack.setSpacing(16)
        right_stack.addWidget(hotkeys)
        software_data = QGroupBox("软件与数据")
        software_data.setObjectName("SoftwareDataGroup")
        self.software_data_group = software_data
        utilities = QVBoxLayout(software_data)
        utilities.setSpacing(8)
        self.check_updates_button = button("检查软件更新", lambda: self.updates.check(manual=True))
        self.data_folder_button = button("打开助手数据目录", lambda: os.startfile(str(self.data_dir)))
        utilities.addWidget(self.check_updates_button)
        utilities.addWidget(self.data_folder_button)
        right_stack.addWidget(software_data)
        right_stack.addStretch(1)
        # 右列保留两个独立分类；窄窗沿用外层布局规则，整体排到显示设置下方。
        columns.addWidget(right_column, 1)
        layout.addLayout(columns)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.hotkey_status = label("", True)
        outer.addWidget(self.hotkey_status)
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        save = TapestryAction("应用并保存设置", self.apply_settings, "castle")
        save.setObjectName("Primary")
        apply_row.addWidget(save)
        outer.addLayout(apply_row)
        self.tabs.addTab(page, "设置")

    def make_help(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.help_outer_layout = layout
        layout.setContentsMargins(32, 0, 32, 12)
        self.help_scroll = QScrollArea()
        self.help_scroll.setWidgetResizable(True)
        self.help_scroll.setFrameShape(QFrame.NoFrame)
        self.help_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.help_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setContentsMargins(0, 2, 0, 14)
        sections.setSpacing(20)
        paragraphs = [
            (f"{APP_NAME} {APP_VERSION} · {APP_CODENAME}", "为《帝国时代 IV》玩家提供开局提示、中文朗读、公开战绩查询与赛后回放分析。"),
            ("使用声明", "本程序不读取或修改游戏运行时数据，不注入游戏，不提供自动操作游戏的脚本功能。公开回放由用户点选后从游戏官网获取，并在本地分析。\n我希望帮助玩家在公平竞技中思考、练习与成长。游戏的意义不止于胜负，更在于探索、进步和乐趣。"),
            ("资料与隐私", "开局、设置、下载的公开回放和分析报告保存在助手数据目录，更新时保留。个人资料不会随安装包或源码包发布。\n在线中文朗读会将播报文字发送给微软语音服务，缓存后可离线播放。"),
            ("原作与许可", "本项目基于 CraftySalamander 的 RTS_Overlay（即时战略悬浮工具）。\n按 GNU GPL 第三版（通用公共许可证）分发，对应源码在本项目公开仓库提供，许可证与第三方署名随程序保留。游戏名称和图标归原权利人所有；本项目未经游戏官方背书。"),
        ]
        for title, paragraph in paragraphs:
            section = QVBoxLayout()
            section.setSpacing(7)
            section.addWidget(ui_text(title, "SectionTitle"))
            text = QLabel("<p style='margin:0;line-height:160%;'>" + html.escape(paragraph).replace("\n", "<br>") + "</p>")
            text.setWordWrap(True)
            text.setTextFormat(Qt.RichText)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            text.setStyleSheet("font-family:'Sen','Source Han Sans SC','Microsoft YaHei UI';font-size:12px;color:#b8bac2;background:transparent;border:0;")
            section.addWidget(text)
            sections.addLayout(section)
        links = QHBoxLayout()
        links.setSpacing(20)
        for caption, url in (("原项目与许可证", "https://github.com/CraftySalamander/RTS_Overlay"),
                             ("GitHub 源码", "https://github.com/miradore-xyz/aoe4-opening-assistant-zh"),
                             ("Gitee 源码", "https://gitee.com/code_liujiaxin/aoe4-opening-assistant-zh")):
            source_link = QLabel(f'<a style="color:#78c99c;" href="{url}">{caption}</a>')
            source_link.setOpenExternalLinks(True)
            links.addWidget(source_link)
        links.addStretch()
        sections.addLayout(links)
        sections.addStretch(1)
        self.help_scroll.setWidget(content)
        layout.addWidget(self.help_scroll)
        self.tabs.addTab(page, "关于")

    def make_development_page(self, title):
        """未来入口共用居中的矢量施工图标，不创建账号或网络下载功能。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 0, 32, 12)
        layout.addWidget(DevelopmentPlaceholder(page), 1)
        self.tabs.addTab(page, title)
        return page, layout

    def position_fields(self):
        self.x_input.setValue(self.settings["x"])
        self.y_input.setValue(self.settings["y"])

    def save_settings(self):
        try:
            atomic_json(self.settings_path, self.settings)
            return True
        except OSError:
            problem = "设置未能写入。请将整个便携文件夹放到有写入权限的位置。"
            self.status_bar.setText(problem)
            self.show_problem(problem, "设置保存失败")
            return False

    def show_problem(self, text, title="提示"):
        """重要错误使用可关闭的界面内弹窗，主窗口底部始终不显示文字。"""
        if self._problem_dialog is not None:
            self._problem_dialog.close()
        dialog = FramelessDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowTitle(title)
        dialog.resize(480, 190)
        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(16, 8, 16, 16)
        outer.addWidget(DialogBar(dialog, title))
        description = ui_text(str(text))
        outer.addWidget(description, 1)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("确定", dialog.accept))
        outer.addLayout(row)
        self._problem_dialog = dialog
        dialog.finished.connect(lambda _result: setattr(self, '_problem_dialog', None)
                                if self._problem_dialog is dialog else None)
        dialog.open()

    def register_hotkeys(self):
        try:
            failed = self.hotkeys.apply(self.settings["hotkeys"])
            self.hotkey_status.setText("快捷键占用或登记失败：" + "、".join(HOTKEY_LABELS[k] for k in failed) if failed else "")
            self.hotkey_status.setVisible(bool(failed))
        except ValueError as error:
            self.hotkey_status.setText(str(error))
            self.hotkey_status.show()

    def on_hotkey(self, action):
        if QApplication.activeModalWidget():
            return
        if self.isActiveWindow() and isinstance(QApplication.focusWidget(), QKeySequenceEdit):
            return
        {"previous": lambda: self.step(-1), "next": lambda: self.step(1), "pause": self.toggle_clock,
         "reset": self.reset_clock, "visible": self.toggle_overlay,
         "passthrough": lambda: self.passthrough_check.setChecked(not self.settings["passthrough"])}[action]()

    def default_keys(self):
        for key, widget in self.key_inputs.items():
            widget.setKeySequence(QKeySequence(DEFAULT_HOTKEYS[key]))
        self.settings["hotkeys"] = DEFAULT_HOTKEYS.copy()
        self.save_settings()
        self.register_hotkeys()

    def change_overlay_style(self, _index=None):
        """样式单独即时保存；不提交其他尚未应用的设置输入。"""
        self.settings["overlay_style"] = overlay_style(self.overlay_style_input.currentData())
        self.save_settings()
        if hasattr(self, "overlay"):
            self.overlay.apply_appearance()
            self.overlay.render()

    def refresh_overlay_color_button(self):
        """按钮同时显示色块与准确色值，便于确认当前选择。"""
        self.overlay_color_input.setText("选择颜色  " + self._overlay_color_value.upper())
        swatch = QPixmap(18, 18)
        swatch.fill(QColor(self._overlay_color_value))
        self.overlay_color_input.setIcon(QIcon(swatch))

    def choose_overlay_color(self):
        color = ColorPickerDialog.getColor(QColor(self._overlay_color_value), self, "选择悬浮窗背景颜色")
        if not color.isValid():
            return
        self._overlay_color_value = color.name()
        self.refresh_overlay_color_button()
        self.change_overlay_appearance()

    def change_overlay_appearance(self, _value=None):
        """仅即时提交显示字段，不提交尺寸等其他尚未应用的设置。"""
        self.settings.update(font_size=self.font_input.value(), overlay_color=self._overlay_color_value,
                             overlay_text_color=self._overlay_text_color_value,
                             opacity=self.opacity_input.value(), text_shadow=self.text_shadow_check.isChecked(),
                             text_shadow_strength=self.text_shadow_strength_input.value())
        self.text_shadow_strength_input.setEnabled(self.settings["text_shadow"])
        self.save_settings()
        if hasattr(self, "overlay"):
            self.overlay.apply_appearance()
            self.overlay.render()

    def refresh_overlay_text_color_button(self):
        """色块显示用户选择；默认状态保留原标题、正文与历史的层次。"""
        selected = self._overlay_text_color_value
        self.overlay_text_color_input.setText("选择颜色  " + selected.upper() if selected else "默认分层配色")
        swatch = QPixmap(18, 18)
        swatch.fill(QColor(selected or "#f2f4e9"))
        self.overlay_text_color_input.setIcon(QIcon(swatch))

    def choose_overlay_text_color(self):
        color = ColorPickerDialog.getColor(QColor(self._overlay_text_color_value or "#f2f4e9"), self, "选择悬浮窗文字颜色")
        if not color.isValid():
            return
        self._overlay_text_color_value = color.name()
        self.refresh_overlay_text_color_button()
        self.change_overlay_appearance()

    def reset_overlay_text_color(self):
        """单独恢复文字配色，不顺带重置用户背景或字号。"""
        self._overlay_text_color_value = ""
        self.refresh_overlay_text_color_button()
        self.change_overlay_appearance()

    def restore_overlay_appearance(self):
        """纠正旧版大字号时不猜用户原意；由用户明确一键恢复默认。"""
        widgets = (self.font_input, self.opacity_input, self.text_shadow_check, self.text_shadow_strength_input)
        blocked = [widget.blockSignals(True) for widget in widgets]
        try:
            self.font_input.setValue(14)
            self.opacity_input.setValue(65)
            self.text_shadow_check.setChecked(True)
            self.text_shadow_strength_input.setValue(75)
            self._overlay_color_value = DEFAULTS["overlay_color"]
            self.refresh_overlay_color_button()
            self._overlay_text_color_value = ""
            self.refresh_overlay_text_color_button()
        finally:
            for widget, previous in zip(widgets, blocked):
                widget.blockSignals(previous)
        self.change_overlay_appearance()

    def apply_settings(self):
        values = {key: widget.keySequence().toString(QKeySequence.PortableText) for key, widget in self.key_inputs.items()}
        try:
            parsed = [self.hotkeys.parse(value) for value in values.values() if value]
            if len(parsed) != len(set(parsed)):
                raise ValueError("不同功能不能使用相同快捷键。")
        except ValueError as error:
            message(self, str(error), "设置没有保存")
            return
        for key, widget in [("font_size", self.font_input), ("ui_font_size", self.ui_font_input),
                            ("opacity", self.opacity_input), ("x", self.x_input), ("y", self.y_input),
                            ("volume", self.volume_input)]:
            self.settings[key] = widget.value()
        self.settings.update(overlay_style=overlay_style(self.overlay_style_input.currentData()),
                             overlay_color=self._overlay_color_value,
                             overlay_text_color=self._overlay_text_color_value,
                             text_shadow=self.text_shadow_check.isChecked(),
                             text_shadow_strength=self.text_shadow_strength_input.value(),
                             rate=self.rate_input.currentData(),
                             timing=self.timing_input.currentData(), speech=self.speech_check.isChecked(),
                             voice=self.voice_input.currentData() or "", hotkeys=values)
        if not self.settings["speech"]:
            self.stop_voice()
        self.step_alert = ""
        saved = self.save_settings()
        self.apply_ui_font()
        self.register_hotkeys()
        self.sync_overlay_lock_control()
        self.overlay.apply_settings()
        self.update_display()
        if saved:
            self.status_bar.clear()
        self.prepare_voice()

    def change_auto(self, enabled):
        self._manual_timeline_ids = None
        if enabled and self.order and not timer_steps(self.order):
            self.auto_check.blockSignals(True)
            self.auto_check.setChecked(False)
            self.auto_check.blockSignals(False)
            self.settings["auto"] = False
            self.status_bar.setText("此开局没有可用的顺序时间节点，当前按条件手动切换。")
        else:
            self.settings["auto"] = enabled
            if enabled and self.order:
                self.ids = timed_ids(self.order, self.clock.seconds, self.settings["timing"] == "starting") or [0]
        self.save_settings()
        self.update_display()

    def apply_ui_font(self):
        import re
        ratio = self.settings["ui_font_size"] / 14
        scaled = re.sub(r'font-size:\s*([0-9.]+)px', lambda m: f'font-size:{max(11, round(float(m[1])*ratio))}px', STYLE)
        font = make_ui_font()
        font.setPixelSize(self.settings['ui_font_size'])
        self.application.setFont(font)
        self.application.setStyleSheet(scaled)

    def change_passthrough(self, enabled):
        self.settings["passthrough"] = bool(enabled)
        self.sync_overlay_lock_control()
        self.overlay.apply_settings()
        self.overlay.render()
        self.save_settings()

    def sync_overlay_lock_control(self):
        """主界面始终保留锁按钮，穿透整个悬浮窗后仍可在此解锁。"""
        locked = self.settings["passthrough"]
        control = self.passthrough_check
        control.blockSignals(True)
        control.setChecked(locked)
        control.blockSignals(False)
        control.setIcon(action_icon("lock" if locked else "unlock"))
        description = ("解锁悬浮窗（当前已锁定，鼠标点击穿透到游戏）" if locked
                       else "锁定悬浮窗（当前可点击、拖动和调整大小）")
        shortcut = self.settings["hotkeys"].get("passthrough", "")
        control.setAccessibleName(description)
        control.setToolTip(description + (f"\n也可按 {shortcut} 切换。" if shortcut else ""))

    def change_speech(self, enabled):
        self.settings["speech"] = bool(enabled)
        for widget in (self.speech_check, self.quick_speech):
            widget.blockSignals(True)
            widget.setChecked(enabled)
            widget.blockSignals(False)
        self.voice_badge.setText("语音朗读已开启" if enabled else "语音朗读已关闭")
        self.voice_badge.hide()
        self.save_settings()
        if hasattr(self, "speech"):
            if enabled:
                if self.settings["voice_engine"] == "online":
                    self.prepare_voice()
                else:
                    self.speak_step()
            else:
                self.stop_voice()
        self.status_bar.setText("语音朗读已开启，立即生效。开始计时或切步即可朗读。" if enabled else "语音朗读已关闭，立即生效。")
        self.update_display()

    def change_voice(self, index):
        value = self.voice_input.currentData()
        if value:
            self.settings["voice"] = value
            self.save_settings()

    def set_voice_status(self, text=""):
        """只按实际需要显示准备或错误信息；缓存完成后收起这行。"""
        self.voice_status.setText(text)
        self.voice_status.setVisible(bool(text))

    def change_audio_preferences(self, value):
        changed_rate = self.settings["rate"] != self.rate_input.currentData()
        self.settings.update(volume=self.volume_input.value(), rate=self.rate_input.currentData())
        self.save_settings()
        if changed_rate:
            if self.settings["voice_engine"] == "online":
                self.stop_voice()
            self.prepare_voice()

    def change_voice_engine(self, index):
        self.settings["voice_engine"] = self.engine_input.currentData()
        offline = self.settings["voice_engine"] == "online"
        self.voice_input.setVisible(not offline)
        self.online_input.setVisible(offline)
        self.prepare_button.setVisible(offline)
        self.voice_badge.setText("在线自然语音" if offline else "系统中文朗读")
        self.save_settings()
        if hasattr(self, "online"):
            self.clock.pause()
            self.stop_voice()
            self.prepare_voice()
            self.update_display()

    def change_online_voice(self, index):
        value = self.online_input.currentData()
        if value is not None:
            self.settings["online_voice"] = value
            self.save_settings()
            if hasattr(self, "online"):
                self.stop_voice()
            self.prepare_voice()

    def voice_texts(self):
        """提前准备逐步提示、相同时间的合并提示和试听。"""
        texts = [VOICE_TEST]
        groups = {}
        if self.order:
            for step in self.order["build_order"]:
                notes = [self.renderer.spoken(note) for note in step["notes"]]
                texts.append("。".join(notes))
            for step in timer_steps(self.order):
                groups.setdefault(step["time_sec"], []).extend(
                    self.renderer.spoken(note) for note in step["notes"])
        texts.extend("。".join(notes) for notes in groups.values())
        return list(dict.fromkeys(text for text in texts if text.strip()))

    def voice_ready(self):
        return (not self.settings["speech"] or self.settings["voice_engine"] != "online"
                or self._online_ready)

    def stop_voice(self):
        self.speech.stop()
        if hasattr(self, "online"):
            self.online.stop()
        self._online_job = None
        self._audition_after_prepare = False

    def emit_voice(self, text):
        if self.settings["voice_engine"] == "online":
            return self.online.say(text, self.settings["online_voice"],
                                   self.settings["volume"], self.settings["rate"])
        self.speech.say(text, self.settings["voice"], self.settings["volume"], self.settings["rate"])

    def prepare_voice(self, force=False):
        if not hasattr(self, "online") or self.settings["voice_engine"] != "online":
            return
        if not self.settings["speech"] and not force:
            return
        self.clock.pause()
        texts = self.voice_texts()
        voice_id, rate = self.settings["online_voice"], self.settings["rate"]
        self._online_ready = self.online.ready_for(texts, voice_id, rate)
        if self._online_ready:
            self.set_voice_status()
            self.voice_badge.setText("在线语音已就绪")
            self.voice_badge.hide()
            if self._audition_after_prepare:
                self._audition_after_prepare = False
                self.emit_voice(VOICE_TEST)
        else:
            self.set_voice_status("正在准备本局自然语音，完成后即可开始计时。")
            self.voice_badge.setText("正在准备语音")
            self.voice_badge.show()
            self._online_job = self.online.prepare(texts, voice_id, rate)
        self.update_display()

    def on_online_progress(self, job, done, total):
        if job == self._online_job:
            self.set_voice_status(f"在线语音准备中 · {done} / {total} 段")
            self.voice_badge.setText(f"准备语音 {done}/{total}")
            self.voice_badge.show()

    def on_online_prepared(self, job):
        if job != self._online_job or self.settings["voice_engine"] != "online":
            return
        self._online_job = None
        self._online_ready = True
        self.voice_badge.setText("在线语音已就绪")
        self.voice_badge.hide()
        self.set_voice_status()
        if self._audition_after_prepare:
            self._audition_after_prepare = False
            self.emit_voice(VOICE_TEST)
        self.update_display()

    def on_voices(self, values):
        self.voice_input.blockSignals(True)
        self.voice_input.clear()
        for voice_id, name in values:
            translated = name.replace("Microsoft", "微软").replace("Desktop", "桌面声音").replace("Huihui", "慧慧").replace("Chinese (Simplified)", "简体中文").replace("Chinese (Traditional)", "繁体中文")
            self.voice_input.addItem(translated, voice_id)
        if not values:
            self.voice_input.addItem("没有找到可用的中文系统声音", "")
            if self.settings["voice_engine"] == "system":
                self.set_voice_status("没有系统中文声音，可选择在线自然语音。")
        else:
            index = self.voice_input.findData(self.settings["voice"])
            if index < 0:
                index = next((i for i, (_, name) in enumerate(values) if "huihui" in name.lower()), 0)
            self.voice_input.setCurrentIndex(index)
            self.settings["voice"] = values[index][0]
            if self.settings["voice_engine"] == "system":
                self.set_voice_status(f"中文声音已就绪 · 本机共有 {len(values)} 个声音 · 开关即时生效")
        self.voice_input.blockSignals(False)

    def on_speech_started(self, text):
        self.last_voice_text = text
        self.voice_badge.hide()
        self.set_voice_status("语音已触发 · " + text[:42])

    def speech_problem(self, text):
        self.set_voice_status(text)
        self.status_bar.setText(text)
        self.voice_badge.setText("语音异常")
        self.voice_badge.show()

    def test_voice(self):
        if self.settings["voice_engine"] == "online":
            self._audition_after_prepare = True
            self.prepare_voice(force=True)
            return
        if not self.speech.voices:
            message(self, "没有可用的中文系统声音，或系统朗读尚未准备就绪。文字功能仍可使用。")
            return
        self.speech.say(VOICE_TEST,
                        self.voice_input.currentData(), self.volume_input.value(), self.rate_input.currentData())

    def speak_step(self):
        if self.settings["speech"] and self.order:
            notes = [self.renderer.spoken(n) for i in self.ids for n in self.order["build_order"][i]["notes"]]
            self.emit_voice("。".join(notes))

    def refresh_library(self, select=None):
        self.library.blockSignals(True)
        self.library.clear()
        errors = []
        for path in sorted(self.orders_dir.glob("*.json")):
            try:
                data = read_order(path)
            except (ValueError, OSError) as error:
                errors.append(f"{path.name}：{error}")
                continue
            item = QListWidgetItem(data["name"] + "\n" + civilization_text(data["civilization"]))
            item.setData(Qt.UserRole, str(path))
            item.setData(Qt.UserRole + 3, {"steps": len(data["build_order"]), "author": data.get("author", ""), "civilization": data["civilization"]})
            from aoe4.aoe4_civ_icon import aoe4_civilization_icon
            civ = data["civilization"]
            civ = civ[0] if isinstance(civ, list) else civ
            item.setData(Qt.UserRole + 2, 'dragon' if civ == 'Order of the Dragon' else 'castle' if civ in ('Generic', 'any') else 'knight')
            if civ in aoe4_civilization_icon:
                item.setIcon(QIcon(str(ASSETS / "aoe4" / "civilization_flag" / aoe4_civilization_icon[civ][1])))
            item.setToolTip(data["name"])
            self.library.addItem(item)
        self.library.blockSignals(False)
        self.collection_count.setText(str(self.library.count()))
        self.resume_button.setEnabled(bool(self.library.count()))
        self.schedule_library_layout()
        wanted = str(select or self.settings["last_order"])
        selected = next((i for i in range(self.library.count()) if self.library.item(i).data(Qt.UserRole) == wanted), 0)
        if self.library.count():
            self.library.setCurrentRow(selected)
        else:
            self.order = None
            self.order_path = None
            self.clock.pause()
            self.clock.seek(0)
            self.stop_voice()
            self.overlay.hide()
            self.steps_list.clear()
            self.order_info.setText("还没有开局，导入或新建一套即可开始。")
            self.preview.setText("")
        if errors:
            problem = "以下文件未载入，原文件保留：" + "；".join(errors)
            self.status_bar.setText(problem)
            self.show_problem(problem, "开局读取失败")
        self.filter_orders(self.search.text())

    def remove_order(self, path):
        source = Path(path)
        if source.parent.resolve() != self.orders_dir.resolve() or not source.exists():
            return
        removed = self.data_dir / "已移除开局"
        removed.mkdir(exist_ok=True)
        destination = removed / source.name
        number = 2
        while destination.exists():
            destination = removed / f"{source.stem}（{number}）.json"
            number += 1
        try:
            source.rename(destination)
        except OSError:
            message(self, "无法移除此开局，请检查文件是否正被其他程序使用。")
            return
        self.last_removed = (source, destination)
        self.refresh_library()
        self.undo_button.show()

    def undo_remove(self):
        if not getattr(self, 'last_removed', None):
            return
        source, removed = self.last_removed
        if source.exists():
            source = self.unique_path(read_order(removed)["name"])
        try:
            removed.rename(source)
        except OSError:
            message(self, "无法恢复开局，请检查数据目录的写入权限。")
            return
        self.last_removed = None
        self.undo_button.hide()
        self.refresh_library(source)
        self.status_bar.setText("已恢复移除的开局。")

    def filter_orders(self, text):
        words = text.lower().split()
        for index in range(self.library.count()):
            item = self.library.item(index)
            item.setHidden(not all(word in item.text().lower() for word in words))
        self.schedule_library_layout()

    def choose_order(self, item, previous=None):
        if item is None:
            return
        restore_completed = self._overlay_completion_hidden
        self._overlay_completion_handled = False
        self._overlay_completion_hidden = False
        self.clock.pause()
        self.clock.seek(0)
        self.stop_voice()
        self.order_path = Path(item.data(Qt.UserRole))
        try:
            self.order = read_order(self.order_path)
        except (ValueError, OSError) as error:
            message(self, str(error), "无法读取开局")
            return
        self.ids = [0]
        self._manual_timeline_ids = None
        self.step_alert = ""
        self.settings["last_order"] = str(self.order_path)
        # 新一局先提供可操作预览；真正开始后自动锁定，不让玩家卡在穿透状态。
        if self.settings["passthrough"]:
            self.settings["passthrough"] = False
            self.sync_overlay_lock_control()
            self.overlay.apply_settings()
        # 本地演示明确标记时间含义，外部导入沿用全局时间解释。
        if self.order.get("_zh_timing") in ("starting", "ending"):
            self.settings["timing"] = self.order["_zh_timing"]
            self.timing_input.setCurrentIndex(self.timing_input.findData(self.settings["timing"]))
        automatic = bool(timer_steps(self.order))
        self.auto_check.blockSignals(True)
        self.auto_check.setChecked(automatic)
        self.auto_check.blockSignals(False)
        self.settings["auto"] = automatic
        if automatic:
            self.ids = timed_ids(self.order, 0, self.settings["timing"] == "starting") or [0]
        language = "内置中文示例" if self.order.get("_zh_language") == "zh-CN" else "正文保留原语言，可在编辑器自行修改"
        title = self.order["name"].replace("龙之骑士团 · ", "")
        self.order_info.setText(
            f'<div style="font-size:20px;font-weight:500;color:#f4f4f5;">{html.escape(title)}</div>'
            f'<div style="font-size:12px;color:#a1a1aa;margin-top:5px;">{html.escape(civilization_text(self.order["civilization"]))} · '
            f'{html.escape(str(self.order.get("author", "未提供作者")))} · {html.escape(language)}</div>')
        self.order_info.setToolTip("来源：" + str(self.order.get("source", "未提供")))
        picture = "landmark_hre/burgrave-palace.webp" if "城主宫殿" in self.order["name"] else "resource/time.webp"
        self.hero_image.setPixmap(game_pixmap(picture, 40))
        civ = self.order['civilization']
        civ = civ[0] if isinstance(civ, list) else civ
        self.hero_card.variant = ('dragon' if civ == 'Order of the Dragon' else
                                  'castle' if civ in ('Generic', 'any') else 'knight')
        self.hero_card.update()
        self.steps_list.clear()
        for index, step in enumerate(self.order["build_order"], 1):
            snippet = self.renderer.spoken(step["notes"][0]) if step["notes"] else "空提示"
            entry = QListWidgetItem(f"{index:02d}   {step.get('time', '')}   {snippet[:50]}")
            entry.setData(Qt.UserRole + 1, {"number": f"{index:02d}", "time": step.get("time", ""), "summary": snippet})
            entry.setToolTip("\n".join(self.renderer.spoken(n) for n in step["notes"]))
            self.steps_list.addItem(entry)
        self.save_settings()
        self.update_display()
        if restore_completed:
            self.overlay.show()
            self.update_display()
        self.prepare_voice()

    def update_display(self):
        if self.order:
            self.overlay.render()
            self.steps_list.setCurrentRow(self.ids[0])
            # 每步第一段为主指令，其余说明稍小；同一时刻合并的步骤也各保留主指令。
            scale = self.settings['ui_font_size'] / 14
            notes = []
            for i in self.ids:
                for number, note in enumerate(self.order['build_order'][i]['notes']):
                    size = round((22 if number == 0 else 14) * scale)
                    color = '#f4f4f5' if number == 0 else '#a1a1aa'
                    notes.append(f'<p style="margin:2px 0 8px 0;line-height:150%;font-size:{size}px;color:{color};">{self.renderer.html(note)}</p>')
            self.preview.setText(''.join(notes))
            QTimer.singleShot(0, self.resize_command)
            command_key = (str(self.order_path), tuple(self.ids))
            if getattr(self, '_command_key', None) != command_key:
                self.command_scroll.verticalScrollBar().setValue(0)
                self._command_key = command_key
            total = len(self.order['build_order'])
            active = (f"{self.ids[0]+1:02d}–{self.ids[-1]+1:02d}" if len(self.ids) > 1
                      else f"{self.ids[0]+1:02d}")
            self.session_step.setText(f"{active} / {total:02d}")
            self.timeline_count.setText(f"{total} 步")
            self.session_resources.set_step(resource_step(self.order, self.ids))
            next_index = self.ids[-1] + 1
            if next_index < total:
                upcoming = self.order['build_order'][next_index]['notes']
                summary = self.renderer.spoken(upcoming[0]) if upcoming else "空提示"
                self.next_step_hint.setText("接下来  " + summary)
                self.next_step_hint.setToolTip(summary)
            self.next_step_hint.setVisible(self.height() >= 610 and next_index < total)
        self.console_clock.setText(timestamp(self.clock.seconds))
        self.console_state.setText("计时进行中" if self.clock.running else "已暂停")
        action = "暂停计时" if self.clock.running else "开始计时"
        self.pause_button.setText("")
        self.pause_button.setIcon(action_icon("pause" if self.clock.running else "play", "#e5c88e"))
        self.pause_button.setToolTip(action)
        self.pause_button.setAccessibleName(action)
        self.pause_button.setEnabled(self.clock.running or self.voice_ready())
        self.show_button.setChecked(self.overlay.isVisible())
        self.show_button.setToolTip("隐藏悬浮窗" if self.overlay.isVisible() else "显示悬浮窗")

    def click_step(self, item):
        self.jump(self.steps_list.row(item))

    def jump(self, index):
        if not self.order:
            return
        # 手动查看某步不再永久关闭自动；到下一个时间组时重新按时钟推进。
        self._manual_timeline_ids = (tuple(timed_ids(
            self.order, self.clock.seconds, self.settings["timing"] == "starting") or self.ids)
            if self.settings["auto"] else None)
        self.ids = [max(0, min(len(self.order["build_order"]) - 1, index))]
        self.step_alert = ""
        self.update_display()
        self.speak_step()

    def step(self, direction):
        self.jump((self.ids[-1] if direction > 0 else self.ids[0]) + direction)

    def toggle_clock(self):
        if not self.order:
            return
        if self.clock.running:
            self.clock.pause()
            self.stop_voice()
        else:
            if not self.voice_ready():
                self.status_bar.setText("本局自然语音正在准备；完成后可开始，关闭朗读则可直接计时。")
                self.prepare_voice()
                return
            self.clock.start()
            if self.settings["auto"]:
                self.ids, self._manual_timeline_ids = timeline_selection(
                    self.order, self.clock.seconds, self.ids, self._manual_timeline_ids,
                    starting=self.settings["timing"] == "starting")
            self.speak_step()
            if not self.settings["passthrough"]:
                self.change_passthrough(True)
            self.overlay.notify_locked()
        self.update_display()

    def reset_clock(self):
        restore_completed = self._overlay_completion_hidden
        self._overlay_completion_handled = False
        self._overlay_completion_hidden = False
        self.clock.pause()
        self.clock.seek(0)
        self._manual_timeline_ids = None
        self.ids = (timed_ids(self.order, 0, self.settings["timing"] == "starting")
                    if self.order and self.settings["auto"] else []) or [0]
        self.step_alert = ""
        self.stop_voice()
        self.update_display()
        if restore_completed and self.order:
            self.overlay.show()
            self.update_display()

    def check_overlay_completion(self):
        """最后作者时间后两分钟仅隐藏一次；计时暂停时不推进截止判定。"""
        if self._overlay_completion_handled or not self.clock.running or not self.order:
            return False
        deadline = overlay_end_deadline(self.order)
        if deadline is None or self.clock.seconds < deadline:
            return False
        self._overlay_completion_handled = True
        self._overlay_completion_hidden = self.overlay.isVisible()
        if self._overlay_completion_hidden:
            self.overlay.hide()
            self.show_button.setChecked(False)
            self.show_button.setToolTip("显示悬浮窗")
        return True

    def tick(self):
        self.check_overlay_completion()
        current = int(self.clock.seconds)
        if self.clock.running and self.settings["auto"] and self.order:
            ids, self._manual_timeline_ids = timeline_selection(
                self.order, current, self.ids, self._manual_timeline_ids,
                starting=self.settings["timing"] == "starting")
            if self.ids != ids:
                self.ids = ids
                self.step_alert = f"到点提醒 · {timestamp(current)} · 第 {ids[0] + 1} 步"
                self.speak_step()
                self.update_display()
        if current != self.last_second:
            self.last_second = current
            self.console_clock.setText(timestamp(self.clock.seconds))
            self.overlay.render()
        if self.state_file:
            value = {"version": APP_VERSION, "codename": APP_CODENAME,
                     "interface_font": {"latin_family": QFontInfo(self.application.font()).family(),
                                        "bundled_files": len(register_ui_fonts()),
                                        "configured_chinese_family": "Source Han Sans SC"},
                     "step": self.ids, "seconds": current, "running": self.clock.running,
                     "overlay_appearance": {"color": self.settings.get("overlay_color", DEFAULTS["overlay_color"]),
                                            "text_color": self.settings.get("overlay_text_color", ""),
                                            "opacity": self.settings["opacity"],
                                            "font_size": self.settings["font_size"],
                                            "text_shadow": self.settings.get("text_shadow", True),
                                            "text_shadow_strength": self.settings.get("text_shadow_strength", 75)},
                     "overlay_style": self.overlay.visual_style,
                     "goal_style": self.overlay.goals.visual_style,
                     "overlay_completion_handled": self._overlay_completion_handled,
                     "visible": self.overlay.isVisible(), "passthrough": self.settings["passthrough"],
                     "auto": self.settings["auto"], "hotkeys": list(self.hotkeys.registered.values()),
                     "order": self.order["name"] if self.order else "", "voices": self.speech.voices,
                     "alert": self.step_alert,
                     "size": [self.overlay.width(), self.overlay.height()],
                     "speech_enabled": self.settings["speech"], "last_voice_text": getattr(self, "last_voice_text", ""),
                     "voice_engine": self.settings["voice_engine"], "voice_cached": self._online_ready}
            # 仅显式验证模式写入地图状态，确认发行程序实际创建了复盘画布。
            replay_page = self.replay_page
            map_view = replay_page.map_view
            if replay_page.report is not None and map_view is not None:
                value["replay_map"] = {"points": len(map_view.events),
                                       "time": map_view.current_time,
                                       "duration": map_view.duration,
                                       "official_maps": len(map_view.official_map.entries),
                                       "reference_view": type(map_view.official_map).__name__,
                                       "map_pack_state": map_view.official_map.catalog.data_status().get("state"),
                                       "reference_style": map_view.official_map.canvas.style,
                                       "canvas_size": [map_view.canvas.width(), map_view.canvas.height()],
                                       "visible": map_view.isVisible()}
            if value != self._last_state:
                atomic_json(self.state_file, value)
                self._last_state = value

    def toggle_overlay(self):
        # 手动显隐优先：截止已经处理过，重新显示不会在下一帧又被隐藏。
        self._overlay_completion_hidden = False
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.render()
            self.overlay.show()
        self.update_display()

    def raise_panel(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def import_dialog(self):
        path = open_file_dialog(self)
        if path:
            try:
                destination = self.import_file(path)
                self.refresh_library(destination)
                self.status_bar.clear()
            except (ValueError, OSError) as error:
                message(self, str(error), "导入失败")

    def open_online_orders(self):
        self.navigate(6)
        self.status_bar.clear()

    def import_file(self, path):
        data = read_order(path)
        destination = self.unique_path(data["name"])
        # 不写入翻译标记，确保导入与导出的全部原始字段保持一致。
        atomic_json(destination, data)
        return destination

    def unique_path(self, name):
        import re
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:90] or "未命名开局"
        if base.upper().split(".")[0] in ["CON", "PRN", "AUX", "NUL"] + [f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)]:
            base = "开局_" + base
        destination = self.orders_dir / (base + ".json")
        number = 2
        while destination.exists():
            destination = self.orders_dir / f"{base}（{number}）.json"
            number += 1
        return destination

    def new_order(self):
        dialog = Editor(self)
        if dialog.exec_():
            destination = self.unique_path(dialog.data["name"])
            try:
                atomic_json(destination, dialog.data)
                self.refresh_library(destination)
            except OSError:
                message(self, "开局未能保存，请检查本助手数据目录的写入权限。", "保存失败")

    def edit_order(self):
        if not self.order:
            return
        # 编辑时暂停本地计时，避免后台步骤切换。
        self.clock.pause()
        self.update_display()
        dialog = Editor(self, self.order)
        if dialog.exec_():
            try:
                atomic_json(self.order_path, dialog.data)
                self.refresh_library(self.order_path)
            except OSError:
                message(self, "开局未能保存，请检查本助手数据目录的写入权限。", "保存失败")

    def export_dialog(self):
        if not self.order:
            return
        path = open_file_dialog(self, True, self.order_path.name)
        if path:
            try:
                atomic_json(path, self.order)
                self.status_bar.setText("开局已导出，保留原版兼容字段。")
            except OSError:
                message(self, "无法写入所选位置，请换一个可写的文件夹。", "导出失败")

    def closeEvent(self, event):
        if self._problem_dialog is not None:
            self._problem_dialog.close()
        self.clock.pause()
        self.tick_timer.stop()
        # 尺寸保存槽依赖主窗口；关闭时停止延迟回调并保存最后实际尺寸。
        closing_height = (self.overlay.height() if self.overlay.resize_save.isActive() else
                          getattr(self.overlay, "_configured_height", self.overlay.height()))
        self.overlay.resize_save.stop()
        self.overlay.lock_handle.cleanup()
        self.overlay.goals.cleanup()
        if hasattr(self, "updates"):
            self.updates._startup_timer.stop()
        self.settings.update(x=self.overlay.x(), y=self.overlay.y(),
                             width=self.overlay.width(), height=closing_height)
        self.save_settings()
        self.hotkeys.close()
        self.speech.close()
        self.online.close()
        self.overlay.hide()
        event.accept()
        self.application.quit()


def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--data-dir", type=Path, help="独立数据目录（默认：便携程序旁的用户数据）")
    parser.add_argument("--qa-state", type=Path, help="仅验证时使用：将当前状态写入指定文件")
    parser.add_argument("--replay", type=Path, help="打开手动指定的本地赛后录像，不修改原文件")
    args = parser.parse_args()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    QLocale.setDefault(QLocale(QLocale.Chinese, QLocale.China))
    application = QApplication(sys.argv[:1])
    translator = QTranslator(application)
    translation = Path(sys._MEIPASS) / "qt_zh_CN.qm" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "qt_zh_CN.qm"
    if translator.load(str(translation)):
        application.installTranslator(translator)
    install_chinese_edit_menus(application)
    application.setApplicationName(APP_NAME)
    application.setApplicationDisplayName(APP_NAME)
    application.setFont(make_ui_font(14))
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    data_dir = args.data_dir or default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / ".运行锁"))
    if not lock.tryLock(100):
        message(None, f"此数据目录的 {APP_NAME} 已经在运行，请从任务栏打开现有控制面板。")
        return 0

    def report_exception(kind, value, tb):
        (data_dir / "错误日志.txt").write_text("".join(traceback.format_exception(kind, value, tb)), encoding="utf-8")
        message(None, "程序遇到错误，详情已保存在本助手用户数据目录的「错误日志.txt」。", "程序错误")
    sys.excepthook = report_exception
    manager = Manager(application, data_dir, args.qa_state)
    manager.show()
    if args.replay:
        # replay（赛后录像）仅由本次显式参数打开，不扫描或跟随正在进行的对局。
        QTimer.singleShot(0, lambda: manager.open_replay(args.replay))
    result = application.exec_()
    lock.unlock()
    return result


if __name__ == "__main__":
    sys.exit(main())
