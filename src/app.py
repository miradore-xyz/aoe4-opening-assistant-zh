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
import shutil
import sys
import traceback
import winsound
import webbrowser
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QEvent, QLockFile, QLocale, QTranslator, QSize, QPointF, QRectF
from PyQt5.QtGui import QFont, QIcon, QKeySequence, QPainter, QPixmap, QColor, QPolygonF, QPen, QLinearGradient, QPainterPath
from PyQt5.QtWidgets import (
    QApplication, QWidget, QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFormLayout, QSpinBox, QCheckBox, QLineEdit, QTextEdit,
    QListWidget, QListWidgetItem, QTabWidget, QFileDialog, QMessageBox, QScrollArea,
    QSplitter, QKeySequenceEdit, QFrame, QGroupBox, QSizeGrip, QSlider, QProgressBar, QBoxLayout, QSizePolicy, QLayout, QStackedWidget,
)

from core import (CIVS, FIELDS, DEFAULTS, DEFAULT_HOTKEYS, HOTKEY_LABELS, Clock, NoteRenderer,
                  atomic_json, load_settings, read_order, validate_order, timer_steps, timed_ids,
                  timestamp, civilization_text)
from native import GlobalHotkeys, Speech
from online_voice import OnlineSpeech, CHINESE_VOICES
from windowing import FramelessWindow, WindowBar, FramelessDialog, DialogBar
from branding import brand_mark
from themed_buttons import TapestryAction
from choice_controls import ClickComboBox
from editor_actions import StepActions
from order_actions import time_caption, manual_notes, save_actions
from analysis_page import AnalysisPage
from edit_menus import install_chinese_edit_menus
from version import APP_VERSION
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
    slider = QSlider(Qt.Horizontal)
    slider.setRange(low, high)
    slider.setValue(widget.value())
    slider.valueChanged.connect(widget.setValue)
    widget.valueChanged.connect(slider.setValue)
    widget.setFixedWidth(85)
    row.addWidget(slider, 1)
    row.addWidget(widget)
    return frame


def spin(low, high, value, suffix=""):
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setValue(value)
    widget.setSuffix(suffix)
    widget.setButtonSymbols(QSpinBox.NoButtons)
    return widget


def action_icon(kind, color="#c6c7cb"):
    """直接绘制清晰的操作图标：previous=上一步，next=下一步，play=开始，pause=暂停。"""
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    if kind == "pause":
        painter.drawRoundedRect(6, 4, 4, 16, 1, 1)
        painter.drawRoundedRect(14, 4, 4, 16, 1, 1)
    else:
        points = [(7, 4), (19, 12), (7, 20)] if kind != "previous" else [(17, 4), (5, 12), (17, 20)]
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        if kind in ("previous", "next"):
            painter.drawRect(2 if kind == "previous" else 20, 4, 2, 16)
    painter.end()
    return QIcon(pixmap)


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
        self.data = copy.deepcopy(data) if data else {"name": "我的中文开局", "civilization": "Order of the Dragon",
                "author": "本地玩家", "source": "个人练习提示", "build_order": [self.blank()]}
        self.index = 0
        self.busy = False
        shell = QVBoxLayout(self)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)
        self.dialog_bar = DialogBar(self, '编辑开局')
        shell.addWidget(self.dialog_bar)
        content = QWidget()
        shell.addWidget(content, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 14, 20, 18)
        layout.setSpacing(12)
        form = QFormLayout()
        self.name = QLineEdit(self.data["name"])
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
        form.addRow("开局名称", self.name)
        form.addRow("适用文明", self.civ)
        form.addRow("作者署名", self.author)
        form.addRow("来源说明", self.source)
        layout.addLayout(form)
        layout.addWidget(label("时间可留空。资源栏填写村民人数，-1 表示不指定。提示每行一条；原有 @图标路径@ 标记可保留。", True))
        split = QSplitter()
        self.steps = QListWidget()
        self.steps.setObjectName("EditorSteps")
        self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        split.addWidget(self.steps)
        body = QWidget()
        right = QVBoxLayout(body)
        fields = QGridLayout()
        fields.setHorizontalSpacing(20)
        fields.setVerticalSpacing(8)
        fields.setColumnStretch(0, 1)
        fields.setColumnStretch(1, 1)
        self.time = QLineEdit()
        self.time.setPlaceholderText("例如 3:20（3分20秒），也可留空")
        time_row = QHBoxLayout()
        time_row.addWidget(label("时间"))
        time_row.addWidget(self.time, 1)
        right.addLayout(time_row)
        self.step_actions = StepActions()
        self.step_actions.set_civilization(self.civ.currentData())
        right.addWidget(self.step_actions)
        self.numbers = {}
        for i, (key, text) in enumerate(FIELDS.items()):
            w = spin(-1, 9999 if key != "age" else 4, -1)
            w.setSpecialValueText("不指定")
            w.setFixedWidth(96)
            w.setFixedHeight(max(32, w.fontMetrics().height() + 12))
            self.numbers[key] = w
            pair = QWidget()
            pair_layout = QHBoxLayout(pair)
            pair_layout.setContentsMargins(0, 0, 0, 0)
            pair_layout.setSpacing(8)
            caption = label(text)
            caption.setWordWrap(False)
            caption.setFixedWidth(76)
            pair_layout.addWidget(caption)
            pair_layout.addWidget(w)
            fields.addWidget(pair, i // 2, i % 2, Qt.AlignLeft | Qt.AlignVCenter)
        right.addLayout(fields)
        right.addWidget(label("补充提示（每行一条）"))
        self.notes = QTextEdit()
        self.notes.setAcceptRichText(False)
        right.addWidget(self.notes)
        body_scroll = QScrollArea()
        body_scroll.setWidgetResizable(True)
        body_scroll.setWidget(body)
        split.addWidget(body_scroll)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)
        actions = QHBoxLayout()
        for caption, fn in [("添加时间点", self.add), ("复制时间点", self.duplicate), ("移除时间点", self.remove),
                         ("上移", lambda: self.reorder(-1)), ("下移", lambda: self.reorder(1))]:
            actions.addWidget(button(caption, fn))
        layout.addLayout(actions)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(button("取消", self.reject))
        save = TapestryAction("保存到本地开局库", self.save, "castle")
        save.setObjectName("Primary")
        bottom.addWidget(save)
        layout.addLayout(bottom)
        self.steps.currentRowChanged.connect(self.select)
        self.time.textChanged.connect(self.update_time_caption)
        self.civ.currentIndexChanged.connect(lambda: self.step_actions.set_civilization(self.civ.currentData()))
        self.refresh()
        self.show_step()

    @staticmethod
    def blank():
        return {"population_count": -1, "villager_count": -1, "age": -1,
                "resources": {"food": -1, "wood": -1, "gold": -1, "stone": -1}, "notes": ["检查造村民、补房、补兵和金矿。"]}

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


class Overlay(QWidget):
    """游戏上方的小巧置顶窗口。穿透开关仅由控制面板和全局快捷键管理。"""
    def __init__(self, manager):
        super().__init__(None)
        self.manager = manager
        self.setWindowTitle("中文开局悬浮窗")
        self.setWindowIcon(manager.windowIcon())
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.drag = None
        self.applying_size = True
        self.resize_save = QTimer(self)
        self.resize_save.setSingleShot(True)
        self.resize_save.timeout.connect(self.save_size)
        self.setMinimumSize(280, 160)
        self.setMaximumSize(900, 1400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(5)
        row = QHBoxLayout()
        row.setSpacing(7)
        self.civ_image = game_image("civilization_flag/CivIcon-OrderOfTheDragonAoE4.webp", 19)
        row.addWidget(self.civ_image)
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
        self.clock_label.setStyleSheet("font-family:'Segoe UI';font-size:20px;color:#f4f4f5;")
        row.addWidget(self.clock_label)
        self.voice_dot = QLabel("●")
        self.voice_dot.setToolTip("中文朗读状态")
        self.voice_dot.setStyleSheet("color:#22c55e;font-size:8px;")
        row.addWidget(self.voice_dot)
        layout.addLayout(row)
        timing = QHBoxLayout()
        self.step_count = QLabel("01 / 11")
        self.step_count.setStyleSheet("font-family:'Segoe UI';font-size:10px;color:#d5d5d8;")
        self.status = label("")
        self.status.setStyleSheet("font-size:10px;color:#a1a1aa;")
        self.status.setAlignment(Qt.AlignRight)
        timing.addWidget(self.step_count)
        timing.addStretch()
        timing.addWidget(self.status)
        layout.addLayout(timing)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(2)
        self.progress.setStyleSheet("QProgressBar{background:#303034;border:0;border-radius:1px;} QProgressBar::chunk{background:#22c55e;border-radius:1px;}")
        layout.addWidget(self.progress)
        self.alert = label("")
        self.alert.setStyleSheet("color:#bbf7d0;background:#18271e;border:1px solid #2c5138;border-radius:5px;padding:4px 7px;font-size:11px;")
        self.alert.hide()
        layout.addWidget(self.alert)
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
        self.scroll.setMinimumHeight(25)
        layout.addWidget(self.scroll, 1)
        self.reminder = label("")
        self.reminder.setStyleSheet("color:#c6c6cc;background:#202023;border-radius:5px;padding:5px 7px;font-size:11px;")
        layout.addWidget(self.reminder)
        self.controls = QHBoxLayout()
        self.previous = icon_button("previous", "上一步", lambda: manager.step(-1))
        self.next = icon_button("next", "下一步", lambda: manager.step(1))
        self.pause = icon_button("play", "开始计时与提醒", manager.toggle_clock)
        self.pause.setObjectName("OverlayPlay")
        self.pause.setFixedSize(34, 34)
        self.pause.setIconSize(QSize(16, 16))
        self.pause.setStyleSheet("QPushButton#OverlayPlay{background:#d5d5d8;border:1px solid #bbf7d0;border-radius:17px;padding:0;} QPushButton#OverlayPlay:hover{background:#bbf7d0;border-color:#f4f4f5;} QPushButton#OverlayPlay:pressed{background:#c4c4c9;} QPushButton#OverlayPlay:disabled{background:#3f3f46;border-color:#52525b;}")
        for control in (self.previous, self.next):
            control.setObjectName("OverlayStep")
            control.setFixedSize(30, 30)
            control.setIconSize(QSize(16, 16))
            control.setStyleSheet("QPushButton#OverlayStep{background:transparent;border:1px solid transparent;border-radius:15px;padding:0;} QPushButton#OverlayStep:hover{background:#27272a;border-color:#36363b;} QPushButton#OverlayStep:pressed{background:#303034;} QPushButton#OverlayStep:disabled{background:transparent;border-color:transparent;}")
        self.controls.setSpacing(9)
        # 左右占位对称，播放键始终位于窗口中线。
        self.controls.addSpacing(27)
        self.controls.addStretch()
        self.controls.addWidget(self.previous)
        self.controls.addWidget(self.pause)
        self.controls.addWidget(self.next)
        self.controls.addStretch()
        self.size_grip = Grip(self)
        self.size_grip.setToolTip("拖动右下角调整悬浮窗大小，松开后自动保存。")
        self.size_grip.setAccessibleName("调整悬浮窗大小")
        self.size_grip.setFixedSize(18, 18)
        self.controls.addWidget(self.size_grip, 0, Qt.AlignBottom)
        layout.addLayout(self.controls)
        self.apply_settings()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        opacity = self.manager.settings["opacity"] / 100
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        top, bottom = QColor("#1c1c1f"), QColor("#101012")
        top.setAlphaF(opacity)
        bottom.setAlphaF(opacity)
        gradient.setColorAt(0, top)
        gradient.setColorAt(1, bottom)
        p.setBrush(gradient)
        p.setPen(QPen(QColor("#36363b"), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "resize_save") and not self.applying_size:
            self.resize_save.start(250)

    def save_size(self):
        self.manager.settings.update(width=self.width(), height=self.height())
        self.manager.width_input.setValue(self.width())
        self.manager.height_input.setValue(self.height())
        self.manager.save_settings()

    def eventFilter(self, watched, event):
        if watched is self.title:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.drag = event.globalPos() - self.pos()
                return True
            if event.type() == QEvent.MouseMove and self.drag is not None:
                self.move(event.globalPos() - self.drag)
                return True
            if event.type() == QEvent.MouseButtonRelease and self.drag is not None:
                self.drag = None
                self.manager.settings.update(x=self.x(), y=self.y())
                self.manager.save_settings()
                self.manager.position_fields()
                return True
        return super().eventFilter(watched, event)

    def apply_settings(self):
        self.applying_size = True
        settings = self.manager.settings
        visible = self.isVisible()
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        if settings["passthrough"]:
            flags |= Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        if self.windowFlags() != flags:
            self.setWindowFlags(flags)
        # 仅背景透明；正文和按钮保持清晰，不再随整个窗口一起变淡。
        self.setWindowOpacity(1.0)
        self.update()
        self.resize(settings["width"], settings["height"])
        self.notes.setFont(QFont("Microsoft YaHei UI", settings["font_size"]))
        # 段落自身控制字号，避免控件级字号覆盖补充说明的较小文字。
        self.notes.setStyleSheet("color:#f4f4f5;background:transparent;")
        # 同时覆盖窗口全部离屏的情况，支持多显示器和负坐标。
        screens = QApplication.screens()
        x, y = settings["x"], settings["y"]
        if not any(s.availableGeometry().adjusted(-80, -40, 0, 0).contains(x, y) for s in screens):
            rect = QApplication.primaryScreen().availableGeometry()
            x, y = rect.left() + 40, rect.top() + 40
        self.move(x, y)
        if visible:
            self.show()
        self.applying_size = False

    def render(self):
        manager = self.manager
        data = manager.order
        if data is None:
            return
        ids = manager.ids
        current = data["build_order"][ids[-1]]
        self.title.setText(self.title.fontMetrics().elidedText(data["name"], Qt.ElideRight, max(60, self.width() - 128)))
        self.title.setToolTip(data["name"] + "\n按住标题可移动悬浮窗。")
        from aoe4.aoe4_civ_icon import aoe4_civilization_icon
        civ = data["civilization"]
        civ = civ[0] if isinstance(civ, list) else civ
        if civ in aoe4_civilization_icon:
            self.civ_image.setPixmap(game_pixmap("civilization_flag/" + aoe4_civilization_icon[civ][1], 19))
            self.civ_image.show()
        else:
            self.civ_image.hide()
        state = "进行中" if manager.clock.running else "已暂停"
        mode = "自动切步" if manager.settings["auto"] else "手动步骤"
        self.clock_label.setText(timestamp(manager.clock.seconds))
        self.step_count.setText(f"{ids[0]+1:02d} / {len(data['build_order']):02d}")
        self.status.setText(f"{state} · {mode}")
        self.voice_dot.setStyleSheet(f"color:{'#4ade80' if manager.settings['speech'] else '#71717a'};font-size:8px;")
        self.voice_dot.setToolTip("中文朗读已开启" if manager.settings["speech"] else "中文朗读已关闭，可在主界面开启")
        self.progress.setRange(0, len(data["build_order"]))
        self.progress.setValue(ids[-1] + 1)
        self.alert.setText(manager.step_alert)
        self.alert.setVisible(bool(manager.step_alert))
        self.resources.set_step(current)
        sections = []
        main_size = manager.settings["font_size"]
        detail_size = max(6, main_size - 1)
        # 仅悬浮正文图标随字号缩放；主界面与编辑器仍用原来的图标尺寸。
        icon_size = max(10, round(main_size * 4 / 3 + 2))
        for index in ids:
            notes = data["build_order"][index]["notes"]
            # 同时发生的每个步骤都保留首段主字号，其余段落降低一级。
            for paragraph, line in enumerate(notes):
                size = main_size if paragraph == 0 else detail_size
                color = "#f4f4f5" if paragraph == 0 else "#a1a1aa"
                sections.append(
                    f'<p style="margin:1px 0 3px 0;line-height:125%;'
                    f'font-size:{size}pt;color:{color};">{manager.renderer.html(line, size=icon_size)}</p>'
                )
        self.notes.setText("".join(sections))
        description = "暂停计时与提醒" if manager.clock.running else "开始计时与提醒"
        self.pause.setIcon(action_icon("pause" if manager.clock.running else "play", "#161618"))
        self.pause.setToolTip(description)
        self.pause.setAccessibleName(description)
        self.pause.setEnabled(manager.clock.running or manager.voice_ready())
        self.previous.setEnabled(ids[0] > 0)
        self.next.setEnabled(ids[-1] < len(data["build_order"]) - 1)
        reminder = manager.reminder_text if manager.settings["reminders"] else ""
        self.reminder.setVisible(bool(reminder))
        self.reminder.setText(reminder)

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class Manager(FramelessWindow):
    def __init__(self, application, data_dir, state_file=None):
        super().__init__()
        self.setObjectName("Manager")
        self.application = application
        self.data_dir = Path(data_dir)
        self.orders_dir = self.data_dir / "开局库"
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "设置.json"
        self.settings, warning = load_settings(self.settings_path)
        self.state_file = state_file
        self.clock = Clock()
        self.order = None
        self.order_path = None
        self.ids = [0]
        self.reminder_text = ""
        self.step_alert = ""
        self.last_reminder = 0
        self.last_second = -1
        self._online_ready = False
        self._online_job = None
        self._audition_after_prepare = False
        self.renderer = NoteRenderer(ASSETS)
        self.setWindowTitle("帝国开局助手")
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
        for index, name in ((0, '开局库'), (3, '战绩分析'), (1, '设置'), (2, '帮助')):
            item = button(name, lambda checked=False, i=index: self.navigate(i))
            item.setObjectName("QuietNav")
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
        heading.setContentsMargins(24, 18, 24, 12)
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
        analysis_layout.setContentsMargins(24, 0, 24, 12)
        self.analysis_page = AnalysisPage(self.data_dir)
        analysis_layout.addWidget(self.analysis_page)
        self.tabs.addTab(analysis_container, '战绩分析')
        self.status_bar = label(warning or "", True)
        self.status_bar.setWordWrap(False)
        self.status_bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        status_strip = QWidget()
        footer = QHBoxLayout(status_strip)
        footer.setContentsMargins(16, 5, 16, 7)
        footer.addWidget(self.status_bar, 1)
        self.voice_badge = ui_text("", "Muted")
        self.voice_badge.setWordWrap(False)
        self.voice_badge.setStyleSheet("font-size:12px;background:transparent;padding:0 6px;")
        self.voice_badge.hide()
        self.window_bar.status_layout.addWidget(self.voice_badge)
        layout.addWidget(status_strip)
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
        """只在尺寸变化时重排，固定滚动条预留宽度以避免显隐触发循环。"""
        # 卡片使用紧凑尺寸，宽窗增加列数，不再把国旗和正文放大填满空间。
        slot = 216
        flag_height = round((slot - 22) * 256 / 455)
        grid = QSize(slot, flag_height + 124)
        item_size = QSize(slot - 16, flag_height + 108)
        if self.library.gridSize() != grid:
            self.library.setGridSize(grid)
        for number in range(self.library.count()):
            item = self.library.item(number)
            if item.sizeHint() != item_size:
                item.setSizeHint(item_size)

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
                 ("使用帮助", "快速上手与资料来源"), ('战绩分析', '')]
        self.page_title.setText(names[index][0])
        self.page_subtitle.setText(names[index][1])
        self.page_heading.setVisible(index not in (0, 3))
        if index == 0:
            self.work_views.setCurrentIndex(0)
        for item in self.navigation:
            item.setChecked(item.property('pageIndex') == index)

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
        marker = self.data_dir / ".已初始化"
        if not marker.exists():
            for path in (ROOT / "示例开局").glob("*.json"):
                destination = self.orders_dir / path.name
                if not destination.exists():
                    shutil.copyfile(path, destination)
            marker.write_text("中文助手独立数据目录，不读取英文原版设置。", encoding="utf-8")

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
        self.library.setResizeMode(QListWidget.Adjust)
        self.library.setMovement(QListWidget.Static)
        self.library.setUniformItemSizes(False)
        self.library.setSpacing(0)
        self.library.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 保留固定的窄滚动槽，内容从一行变多行时不会改变卡片可用宽度。
        self.library.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        # 保留槽位的固定宽度，只隐藏绘制，避免显隐引起卡片循环重排。
        self.library.verticalScrollBar().setStyleSheet("""
            QScrollBar:vertical {border:0;background:transparent;width:7px;margin:2px;}
            QScrollBar::handle:vertical, QScrollBar::handle:vertical:hover,
            QScrollBar::handle:vertical:pressed {background:transparent;min-height:26px;}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {height:0;}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {background:transparent;}
        """)
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
        self.work_layout.addWidget(self.hero_card)

        self.options_panel = QFrame()
        self.options_panel.setObjectName("SessionOptions")
        options = QHBoxLayout(self.options_panel)
        options.setContentsMargins(12, 8, 12, 8)
        options.setSpacing(20)
        self.quick_speech = QCheckBox("语音朗读")
        self.quick_speech.setChecked(self.settings['speech'])
        self.quick_speech.toggled.connect(self.change_speech)
        self.passthrough_check = QCheckBox("鼠标穿透")
        self.passthrough_check.toggled.connect(self.change_passthrough)
        self.auto_check = QCheckBox("自动切步")
        self.auto_check.setChecked(self.settings['auto'])
        self.auto_check.toggled.connect(self.change_auto)
        for toggle in (self.quick_speech, self.passthrough_check, self.auto_check):
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
        self.console_clock.setStyleSheet("font-family:'Segoe UI';font-size:28px;color:#f4f4f5;font-weight:300;")
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
        self.pause_button = icon_button("play", "开始计时", self.toggle_clock, "dragon")
        self.pause_button.setObjectName("StartButton")
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
        outer.setContentsMargins(24, 0, 24, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 9, 0)
        layout.setSpacing(17)
        # 语音放在最前；开关和语音参数即时保存，不再依赖底部的应用按钮。
        voice, voice_layout = card()
        voice_layout.setContentsMargins(18, 14, 18, 14)
        voice_layout.setSpacing(8)
        voice_header = QHBoxLayout()
        voice_header.addWidget(game_image("unit_worker/monk-3.webp", 40))
        voice_titles = QVBoxLayout()
        voice_titles.setSpacing(3)
        voice_titles.addWidget(ui_text("中文语音提醒", "SectionTitle"))
        voice_titles.addWidget(ui_text("开始计时、切换步骤和循环检查时朗读。", "Muted"))
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
        self.rate_input = spin(-5, 5, self.settings["rate"])
        self.rate_input.setFixedWidth(80)
        self.rate_input.setToolTip("负数更慢，正数更快。")
        voice_parameters.addWidget(self.rate_input)
        self.volume_input.valueChanged.connect(self.change_audio_preferences)
        self.rate_input.valueChanged.connect(self.change_audio_preferences)
        voice_layout.addLayout(voice_parameters)
        self.voice_status = ui_text("联网生成时发送播报文字；缓存后可断网播放，无需登录。", "Muted")
        voice_layout.addWidget(self.voice_status)
        layout.addWidget(voice)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        self.settings_columns = columns
        appearance = QGroupBox("悬浮窗显示")
        form = QFormLayout(appearance)
        form.setVerticalSpacing(11)
        form.setHorizontalSpacing(12)
        self.font_input = spin(6, 28, self.settings["font_size"], " 磅")
        self.ui_font_input = spin(11, 18, self.settings["ui_font_size"], " 像素")
        self.width_input = spin(280, 900, self.settings["width"], " 像素")
        self.height_input = spin(160, 1400, self.settings["height"], " 像素")
        self.opacity_input = spin(35, 100, self.settings["opacity"], "%")
        self.x_input = spin(-30000, 30000, self.settings["x"])
        self.y_input = spin(-30000, 30000, self.settings["y"])
        self.timing_input = ClickComboBox()
        self.timing_input.addItem("结束时刻（原版）", "ending")
        self.timing_input.addItem("开始时刻", "starting")
        self.timing_input.setCurrentIndex(self.timing_input.findData(self.settings["timing"]))
        for caption, widget in [("主界面字号", self.ui_font_input), ("悬浮提示字号", self.font_input), ("窗口宽度", self.width_input), ("窗口高度", self.height_input), ("不透明度", self.opacity_input),
                             ("横向位置", self.x_input), ("纵向位置", self.y_input), ("导入时间解释", self.timing_input)]:
            form.addRow(caption, widget)
        columns.addWidget(appearance, 1)
        reminders = QGroupBox("循环检查提醒")
        form = QFormLayout(reminders)
        self.reminders_check = QCheckBox("计时运行时启用，暂停时停止")
        self.reminders_check.setChecked(self.settings["reminders"])
        self.sound_check = QCheckBox("到点和循环检查时播放提示音")
        self.sound_check.setChecked(self.settings["sound"])
        self.sound_check.toggled.connect(self.change_sound)
        self.interval_input = spin(15, 600, self.settings["reminder_seconds"], " 秒")
        self.reminder_input = QTextEdit(self.settings["reminder_text"])
        self.reminder_input.setAcceptRichText(False)
        self.reminder_input.setFixedHeight(105)
        form.addRow(self.reminders_check)
        form.addRow(self.sound_check)
        form.addRow("提醒间隔", self.interval_input)
        form.addRow("检查内容", self.reminder_input)
        # 游戏内四项习惯检查用原生图标展示，避免重复堆积文字。
        game_checks = QHBoxLayout()
        for name, path in [("造村民", "unit_worker/villager.webp"), ("补房", "building_economy/house.webp"),
                           ("补兵", "unit_infantry/man-at-arms-1.webp"), ("金矿", "resource/resource_gold.webp")]:
            picture = game_image(path, 27)
            picture.setToolTip(name)
            game_checks.addWidget(picture)
            game_checks.addWidget(ui_text(name, "Muted"))
        game_checks.addStretch()
        form.addRow(game_checks)
        columns.addWidget(reminders, 1)
        layout.addLayout(columns)
        hotkeys = QGroupBox("全局快捷键（点击输入框后按组合键）")
        form = QFormLayout(hotkeys)
        form.addRow(label("Ctrl＝控制键，Alt＝换档键，Shift＝上档键，F6～F11＝功能键。留空可禁用一项。", True))
        self.key_inputs = {}
        for action, caption in HOTKEY_LABELS.items():
            widget = QKeySequenceEdit(QKeySequence(self.settings["hotkeys"][action]))
            widget.setToolTip("按下组合键；使用退格键清除。只接受单组组合键。")
            self.key_inputs[action] = widget
            form.addRow(caption, widget)
        form.addRow(button("恢复默认快捷键", self.default_keys))
        layout.addWidget(hotkeys)
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
        layout.setContentsMargins(24, 0, 24, 12)
        help_text = QTextEdit()
        help_text.setReadOnly(True)
        help_text.setPlainText(
            f"帝国开局助手 {APP_VERSION}\n\n"
            "使用方法\n\n1. 在「开局库」点击开局封面进入演练；标题行的「悬浮窗」可显示或隐藏置顶提示。\n"
            "2. 游戏开始后点击指令下方的播放图标。城主宫殿清单默认手动切换，不规定升本时刻。\n"
            "点击演练区齿轮可展开朗读、鼠标穿透与自动切步选项；左上返回按钮回到开局库。\n"
            "3. 按住悬浮窗标题拖动位置；拖动悬浮窗右下角调整宽度和高度，也可在设置页输入尺寸。\n"
            "4. 鼠标穿透开启后，可在本面板关闭，或使用对应全局快捷键恢复。\n"
            "5. 导入原版 RTS Overlay（即时战略悬浮工具）的帝国时代4开局 JSON（结构化数据）文件。\n"
            "6. 使用编辑器修改提示和资源村民分配；导出后仍可由原版读取。\n\n"
            "计时与暂停\n\n计时从手动开始时刻计算，不读取游戏时间。游戏暂停或加载时，请同时暂停本助手。\n"
            "原版帝国时代4开局默认把时间视为步骤的结束时刻；也可在设置改为开始时刻。\n"
            "含完整、递增时间的开局默认自动切换并提醒；没有完整时间的开局使用手动步骤。\n"
            "相同时间的多条步骤会合并显示。到点会显示步骤提醒，提示音可在设置中关闭。\n"
            "手动点击步骤会关闭自动切换；按上一步／下一步在本版始终切换步骤。\n"
            "到达最后一步后计时与循环提醒继续，便于中后期检查经济。\n\n"
            "功能边界\n\n本助手不分析屏幕、不读取游戏内存、不检测村民或人口、不自动操作游戏。\n"
            "所有数字是开局作者填写的目标；循环提醒按时间触发，不知道你是否已经完成。\n"
            "建议使用游戏的无边框窗口模式。独占全屏下能否置顶、游戏内全局热键是否有效，需要实际检查。\n"
            "本版支持可设置的键盘全局组合键；原版的鼠标侧键组合没有移植。\n\n"
            "中文与示例\n\n菜单、帮助、设置和内置提示已中文化。外部开局的名称、作者和正文保留原文，不自动翻译。\n"
            "龙之骑士团示例是可编辑的操作检查清单，不是经实战验证的固定人数开局。\n"
            "城主宫殿对应 Burgrave Palace（城主宫殿），不要与施瓦本宫殿混淆。\n"
            "该文明的镀金村民更贵、采集更高效；不要机械套用普通文明的村民配比。\n"
            "没有写死村民生产周期、经济加成百分比或地标折扣；具体数据以已安装游戏为准。\n"
            "版本与资料核对见随包「版本与来源说明」。\n\n"
            "中文朗读\n\n在「设置」选择晓晓、云希、晓伊在线声音，或本机已安装的中文系统声音。\n"
            "在线声音免登录；准备时将播报文字发送至微软服务，缓存完成后即可断网播放。\n"
            "开启朗读后，先准备本局语音再开始计时；修改文字、音色或语速需要重新准备。\n"
            "若在线准备失败，可关闭朗读继续使用文字提示，或改用系统声音。\n\n"
            "保存位置\n\n便携版资料位于程序旁的「用户数据」；安装版使用个人应用资料目录，升级时沿用原位置。\n"
            "导入只复制到本地库；不会覆盖外部原文件。网络入口打开 AoE4 Guides（帝国时代4开局指南），\n"
            "下载 RTS Overlay（即时战略悬浮工具）格式的开局后导入；社区上传与点赞尚未接入。\n\n"
            "原作与许可\n\n基于 CraftySalamander 的 RTS_Overlay：\nhttps://github.com/CraftySalamander/RTS_Overlay\n"
            "上游提交：aaee02c95e47e4aadb7c3269fd07fb12b5871620\n"
            "沿用开局校验、计时步骤选择、图标标记解析和图标资源；中文窗口、编辑器、保存和朗读为本次修改。\n"
            "按 GNU GPL 第三版（通用公共许可证）分发，完整对应源码与许可证随包提供。\n"
            "游戏名称和图标归相应权利人所有；此助手是非官方工具。"
        )
        layout.addWidget(help_text)
        layout.addWidget(button("检查软件更新", lambda: self.updates.check(manual=True)))
        layout.addWidget(button("打开本助手数据目录", lambda: os.startfile(str(self.data_dir))))
        self.tabs.addTab(page, "中文帮助与署名")

    def position_fields(self):
        self.x_input.setValue(self.settings["x"])
        self.y_input.setValue(self.settings["y"])

    def save_settings(self):
        try:
            atomic_json(self.settings_path, self.settings)
        except OSError:
            self.status_bar.setText("设置未能写入。请将整个便携文件夹放到有写入权限的位置。")

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

    def apply_settings(self):
        values = {key: widget.keySequence().toString(QKeySequence.PortableText) for key, widget in self.key_inputs.items()}
        try:
            parsed = [self.hotkeys.parse(value) for value in values.values() if value]
            if len(parsed) != len(set(parsed)):
                raise ValueError("不同功能不能使用相同快捷键。")
        except ValueError as error:
            message(self, str(error), "设置没有保存")
            return
        for key, widget in [("font_size", self.font_input), ("ui_font_size", self.ui_font_input), ("width", self.width_input), ("height", self.height_input),
                            ("opacity", self.opacity_input), ("x", self.x_input), ("y", self.y_input),
                            ("reminder_seconds", self.interval_input), ("volume", self.volume_input), ("rate", self.rate_input)]:
            self.settings[key] = widget.value()
        self.settings.update(timing=self.timing_input.currentData(), reminders=self.reminders_check.isChecked(), sound=self.sound_check.isChecked(),
                             reminder_text=self.reminder_input.toPlainText(), speech=self.speech_check.isChecked(),
                             voice=self.voice_input.currentData() or "", hotkeys=values)
        if not self.settings["speech"]:
            self.stop_voice()
        self.last_reminder = int(self.clock.seconds)
        self.reminder_text = ""
        self.step_alert = ""
        self.save_settings()
        self.apply_ui_font()
        self.register_hotkeys()
        self.overlay.apply_settings()
        self.update_display()
        self.status_bar.setText("设置已保存到本助手独立数据目录。")
        self.prepare_voice()

    def change_auto(self, enabled):
        if enabled and self.order and not timer_steps(self.order):
            self.auto_check.blockSignals(True)
            self.auto_check.setChecked(False)
            self.auto_check.blockSignals(False)
            self.settings["auto"] = False
            self.status_bar.setText("此开局没有完整且递增的时间，已使用手动切换。")
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
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(self.settings['ui_font_size'])
        self.application.setFont(font)
        self.application.setStyleSheet(scaled)

    def change_passthrough(self, enabled):
        self.settings["passthrough"] = enabled
        self.overlay.apply_settings()
        self.overlay.render()
        self.save_settings()

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

    def change_audio_preferences(self, value):
        changed_rate = self.settings["rate"] != self.rate_input.value()
        self.settings.update(volume=self.volume_input.value(), rate=self.rate_input.value())
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
        """提前准备逐步提示、相同时间的合并提示、循环检查和试听。"""
        texts = [VOICE_TEST, self.settings["reminder_text"]]
        groups = {}
        if self.order:
            for step in self.order["build_order"]:
                notes = [self.renderer.spoken(note) for note in step["notes"]]
                texts.append("。".join(notes))
                if step.get("time"):
                    groups.setdefault(step["time"], []).extend(notes)
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
            self.voice_status.setText(f"本局语音已缓存 · {len(texts)} 段 · 游戏中无需联网或合成")
            self.voice_badge.setText("在线语音已就绪")
            self.voice_badge.hide()
            if self._audition_after_prepare:
                self._audition_after_prepare = False
                self.emit_voice(VOICE_TEST)
        else:
            self.voice_status.setText("正在准备本局自然语音，完成后即可开始计时。")
            self.voice_badge.setText("正在准备语音")
            self.voice_badge.show()
            self._online_job = self.online.prepare(texts, voice_id, rate)
        self.update_display()

    def on_online_progress(self, job, done, total):
        if job == self._online_job:
            self.voice_status.setText(f"在线语音准备中 · {done} / {total} 段")
            self.voice_badge.setText(f"准备语音 {done}/{total}")
            self.voice_badge.show()

    def on_online_prepared(self, job):
        if job != self._online_job or self.settings["voice_engine"] != "online":
            return
        self._online_job = None
        self._online_ready = True
        self.voice_badge.setText("在线语音已就绪")
        self.voice_badge.hide()
        self.voice_status.setText("本局语音缓存完成 · 开始、切步和循环检查直接播放本地音频")
        if self._audition_after_prepare:
            self._audition_after_prepare = False
            self.emit_voice(VOICE_TEST)
        self.update_display()

    def change_sound(self, enabled):
        self.settings["sound"] = bool(enabled)
        self.save_settings()

    def on_voices(self, values):
        self.voice_input.blockSignals(True)
        self.voice_input.clear()
        for voice_id, name in values:
            translated = name.replace("Microsoft", "微软").replace("Desktop", "桌面声音").replace("Huihui", "慧慧").replace("Chinese (Simplified)", "简体中文").replace("Chinese (Traditional)", "繁体中文")
            self.voice_input.addItem(translated, voice_id)
        if not values:
            self.voice_input.addItem("没有找到可用的中文系统声音", "")
            if self.settings["voice_engine"] == "system":
                self.voice_status.setText("没有系统中文声音，可选择在线自然语音。")
        else:
            index = self.voice_input.findData(self.settings["voice"])
            if index < 0:
                index = next((i for i, (_, name) in enumerate(values) if "huihui" in name.lower()), 0)
            self.voice_input.setCurrentIndex(index)
            self.settings["voice"] = values[index][0]
            if self.settings["voice_engine"] == "system":
                self.voice_status.setText(f"中文声音已就绪 · 本机共有 {len(values)} 个声音 · 开关即时生效")
        self.voice_input.blockSignals(False)

    def on_speech_started(self, text):
        self.last_voice_text = text
        self.voice_badge.hide()
        self.voice_status.setText("语音已触发 · " + text[:42])

    def speech_problem(self, text):
        self.voice_status.setText(text)
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
                        self.voice_input.currentData(), self.volume_input.value(), self.rate_input.value())

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
            self.status_bar.setText("以下文件未载入，原文件保留：" + "；".join(errors))
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

    def choose_order(self, item, previous=None):
        if item is None:
            return
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
        self.last_reminder = 0
        self.reminder_text = ""
        self.step_alert = ""
        self.settings["last_order"] = str(self.order_path)
        # 本地演示明确标记时间含义，外部导入默认沿用全局原版兼容设置。
        if self.order.get("_zh_timing") in ("starting", "ending"):
            self.settings["timing"] = self.order["_zh_timing"]
            self.timing_input.setCurrentIndex(self.timing_input.findData(self.settings["timing"]))
        automatic = bool(timer_steps(self.order))
        self.auto_check.blockSignals(True)
        self.auto_check.setChecked(automatic)
        self.auto_check.blockSignals(False)
        self.settings["auto"] = automatic
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
            self.session_step.setText(f"{self.ids[0]+1:02d} / {total:02d}")
            self.timeline_count.setText(f"{total} 步")
            self.session_resources.set_step(self.order['build_order'][self.ids[-1]])
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
        self.pause_button.setIcon(action_icon("pause" if self.clock.running else "play", "#f4f4f5"))
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
        self.auto_check.setChecked(False)
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
            self.speak_step()
        self.update_display()

    def reset_clock(self):
        self.clock.pause()
        self.clock.seek(0)
        self.ids = [0]
        self.last_reminder = 0
        self.reminder_text = ""
        self.step_alert = ""
        self.stop_voice()
        self.update_display()

    def tick(self):
        current = int(self.clock.seconds)
        if self.clock.running and self.settings["auto"] and self.order:
            ids = timed_ids(self.order, current, self.settings["timing"] == "starting") or self.ids
            if self.ids != ids:
                self.ids = ids
                self.step_alert = f"到点提醒 · {timestamp(current)} · 第 {ids[0] + 1} 步"
                self.status_bar.setText(self.step_alert)
                self.play_sound()
                self.speak_step()
                self.update_display()
        if self.clock.running and self.settings["reminders"] and current - self.last_reminder >= self.settings["reminder_seconds"]:
            self.last_reminder = current
            self.reminder_text = "循环检查：" + self.settings["reminder_text"]
            self.play_sound()
            if self.settings["speech"]:
                self.emit_voice(self.settings["reminder_text"])
            self.overlay.render()
        if current != self.last_second:
            self.last_second = current
            self.console_clock.setText(timestamp(self.clock.seconds))
            self.overlay.render()
        if self.state_file:
            value = {"step": self.ids, "seconds": current, "running": self.clock.running,
                     "visible": self.overlay.isVisible(), "passthrough": self.settings["passthrough"],
                     "auto": self.settings["auto"], "hotkeys": list(self.hotkeys.registered.values()),
                     "order": self.order["name"] if self.order else "", "voices": self.speech.voices,
                     "reminder": self.reminder_text, "alert": self.step_alert,
                     "size": [self.overlay.width(), self.overlay.height()],
                     "speech_enabled": self.settings["speech"], "last_voice_text": getattr(self, "last_voice_text", ""),
                     "voice_engine": self.settings["voice_engine"], "voice_cached": self._online_ready}
            if value != self._last_state:
                atomic_json(self.state_file, value)
                self._last_state = value

    def play_sound(self):
        if self.settings["sound"]:
            try:
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            except RuntimeError:
                self.status_bar.setText("文字提醒已触发；系统提示音不可用。")

    def toggle_overlay(self):
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
        webbrowser.open("https://aoe4guides.com/")
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
        self.clock.pause()
        self.settings.update(x=self.overlay.x(), y=self.overlay.y())
        self.save_settings()
        self.hotkeys.close()
        self.speech.close()
        self.online.close()
        self.overlay.hide()
        event.accept()
        self.application.quit()


def main():
    parser = argparse.ArgumentParser(description="帝国开局助手")
    parser.add_argument("--data-dir", type=Path, help="独立数据目录（默认：便携程序旁的用户数据）")
    parser.add_argument("--qa-state", type=Path, help="仅验证时使用：将当前状态写入指定文件")
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
    application.setApplicationName("帝国开局助手")
    application.setFont(QFont("Microsoft YaHei UI", 10))
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    data_dir = args.data_dir or default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / ".运行锁"))
    if not lock.tryLock(100):
        message(None, "此数据目录的中文助手已经在运行，请从任务栏打开现有控制面板。")
        return 0

    def report_exception(kind, value, tb):
        (data_dir / "错误日志.txt").write_text("".join(traceback.format_exception(kind, value, tb)), encoding="utf-8")
        message(None, "程序遇到错误，详情已保存在本助手用户数据目录的「错误日志.txt」。", "程序错误")
    sys.excepthook = report_exception
    manager = Manager(application, data_dir, args.qa_state)
    manager.show()
    result = application.exec_()
    lock.unlock()
    return result


if __name__ == "__main__":
    sys.exit(main())
