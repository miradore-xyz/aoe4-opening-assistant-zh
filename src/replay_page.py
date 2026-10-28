"""赛后复盘页面：主动获取公开回放或选择本地文件，并保存可删除的报告。

ReplayPage=复盘页面，worker=后台解析任务，coverage=已识别数据范围。
联网仅由公开对局的分析按钮触发，不访问游戏进程、记录键鼠或修改原录像。
"""
from __future__ import annotations

import math
import re
import json
import threading
from functools import lru_cache
from pathlib import Path
from queue import Empty, SimpleQueue

from PyQt5.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QKeySequence, QPainter, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QBoxLayout, QHBoxLayout, QHeaderView, QLabel,
    QLayout, QLineEdit, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QPushButton, QShortcut,
)

from choice_controls import ClickComboBox
from catalog import CATALOG_ROOT
from match_analysis import CIV_NAMES, map_name, mode_name
from replay_parser import ReplayError, parse_replay
from replay_analysis import CATEGORY_LABELS, ReplayLibrary, analyze_replay
from public_replay import PublicReplayClient, PublicReplayError
from rounded_corners import make_corner_covers, position_corner_covers
from replay_diagnostics import record_public_replay_failure
from match_detail import MatchDetailError
from match_detail_client import MatchDetailClient
from match_detail_view import MatchDetailView
from apm_chart import APMCard
from replay_spatial import build_spatial_model
from replay_map_view import ReplayMapView
from replay_placement_markers import placement_description
from replay_analysis_sources import collect_analysis_sources
from player_labels import player_labels
from windowing import DialogBar, FramelessDialog
from visual import line_icon


# 同级卡片的横向和纵向间距固定一致；文字行距、卡片内边距另按各自层级保留。
REPLAY_CARD_GAP = 16


def _text(value="", role=None):
    """录像中的玩家名等内容始终作为纯文本显示。"""
    label = QLabel(str(value))
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    if role:
        label.setObjectName(role)
    return label


def _button(value, callback, primary=False):
    button = QPushButton(value)
    button.setObjectName("Primary" if primary else "Quiet")
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumHeight(30)
    button.clicked.connect(callback)
    return button


def _combo(name):
    combo = ClickComboBox()
    combo.setAccessibleName(name)
    combo.setMinimumWidth(0)
    combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    combo.view().setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return combo


def _time(value):
    try:
        seconds = max(0, int(float(value)))
        return f"{seconds // 60}:{seconds % 60:02d}"
    except (ValueError, TypeError, OverflowError):
        return "时间未知"


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _display_detail(event):
    """默认仅显示动作信息；原始实体和类型编号留在证据提示中。"""
    if event.get("display_detail") is not None:
        return str(event["display_detail"])
    detail = str(event.get("detail") or "")
    detail = re.sub(r"；编号 [^；]+", "", detail)
    return "；".join(part for part in detail.split("；")
                    if not part.startswith(("对象类型编号", "目标实体编号")))


def _event_tooltip(event):
    values = [str(event.get("label") or "未识别指令"), str(event.get("detail") or "")]
    if event.get("type_id") is not None:
        values.append(f"原始指令类型 {event['type_id']}")
    if event.get("tick") is not None:
        values.append(f"逻辑时刻 {event['tick']}")
    if event.get("offset") is not None:
        values.append(f"录像字节位置 {event['offset']}")
    if event.get("payload_decoded") is False:
        values.append("这条指令的参数尚未完整识别。")
    return "\n".join(value for value in values if value)


@lru_cache(maxsize=1)
def _civilization_labels():
    """只使用随包资料中的精确别名，不按未知词根猜测文明。"""
    aliases = dict(CIV_NAMES)
    try:
        document = json.loads((CATALOG_ROOT / "catalog.json").read_text(encoding="utf-8"))
        for civilization in document.get("civilizations", {}).values():
            name = civilization.get("name_zh")
            if name:
                for key in ("id", "name", "slug", "attribName", "name_zh", "source_name_zh"):
                    value = civilization.get(key)
                    if value:
                        aliases[str(value).casefold()] = str(name)
    except (OSError, ValueError, TypeError):
        pass
    return aliases


def _civilization_name(value):
    return _civilization_labels().get(str(value).strip().casefold(), "未确认") if value else "未提供"


class _ReplaySignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)
    finished = pyqtSignal()


class _RoundedMapCard(QFrame):
    """地图保持零内边距铺满；四个12像素小角用平滑曲线遮住方角。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # QRegion硬遮罩会把曲线离散成斜切/锯齿；小角覆盖不会改变地图视口缓存策略。
        self._corners = make_corner_covers(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        position_corner_covers(self, self._corners)
        button = getattr(self, "fullscreen_button", None)
        if button is not None:
            button.move(max(0, self.width() - button.width() - 12), 12)
            button.raise_()


class _ReplayMapDialog(FramelessDialog):
    """真正全屏的大图宿主；返回时将完整观看状态交还原复盘。"""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        # 大图始终铺满矩形窗口，不需要通用圆角弹窗的逐像素透明合成。
        # 在创建原生窗口前关闭透明层，避免全屏地图仍走 Windows 分层窗口路径。
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor("#0F0F11"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setStyleSheet("QDialog#FramelessDialog {background:#0F0F11; border:0;}")
        self.setWindowTitle("二维复盘地图")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumSize(620, 400)
        self._finished = False
        self._pending_view_state = None
        self._logical_camera = None
        self._restored_camera = None
        self.view_state = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # 旧程序化入口保留为隐藏子控件，真实显示与键盘入口仅由地图及Esc/F11承担。
        self.return_button = _button("← 返回复盘  Esc", self.reject, True)
        self.return_button.setParent(self)
        self.return_button.hide()
        self.fullscreen_button = _button("退出全屏  F11", self.toggle_fullscreen)
        self.fullscreen_button.setParent(self)
        self.fullscreen_button.hide()
        self.map_view = ReplayMapView(model, self, map_data_dir=getattr(parent, 'data_dir', None))
        self.map_view.set_fullscreen_mode(True)
        layout.addWidget(self.map_view, 1)
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._restore_pending_state)
        self._fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        self._fullscreen_shortcut.setAutoRepeat(False)
        self._fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        self._return_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._return_shortcut.setAutoRepeat(False)
        self._return_shortcut.activated.connect(self.reject)

    def paintEvent(self, event):
        # 明确填满宿主背景；不使用圆角透明边缘，也不修改地图视口的局部重绘策略。
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0F0F11"))

    def restore_view_state(self, state):
        """等待全屏布局获得真实尺寸后恢复镜头，避免初始小画布覆盖缩放比例。"""
        self._pending_view_state = state
        self._restore_timer.start(0)

    def _restore_pending_state(self):
        if self._finished or self._pending_view_state is None:
            return
        state, self._pending_view_state = self._pending_view_state, None
        self.layout().activate()
        self.map_view.restore_view_state(state)
        self._logical_camera = state.get("camera")
        self._restored_camera = self.map_view.capture_view_state().get("camera")

    def _capture_view_state(self):
        state = self.map_view.capture_view_state()
        # 全屏布局结束还可能再量化一个滚动条像素；不能把它误认成用户移动镜头。
        # 实际拖动或缩放仍保存新镜头，不因往返而恢复旧的观看位置。
        camera, restored = state.get("camera"), self._restored_camera
        same_camera = camera == restored
        if camera and restored and not same_camera:
            tolerance = 1.01 / max(.000001, self.map_view.canvas.transform().m11())
            same_camera = (all(camera.get(key) == restored.get(key) for key in ("extent", "zoom", "user_camera"))
                           and all(abs(value - saved) <= tolerance
                                   for value, saved in zip(camera["center"], restored["center"])))
        if self._logical_camera is not None and same_camera:
            state["camera"] = dict(self._logical_camera)
        return state

    def toggle_fullscreen(self):
        """再次按F11与Esc一致，交还状态并返回嵌入页面，不保留普通大图浮窗。"""
        if not self._finished:
            self.reject()

    def keyPressEvent(self, event):
        # 焦点尚在迁移时也让单次按键返回；长按重复不进入QDialog默认Esc关闭逻辑。
        if event.key() in (Qt.Key_F11, Qt.Key_Escape):
            if not event.isAutoRepeat():
                self.toggle_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def done(self, result):
        if self._finished:
            return
        self._finished = True
        self.view_state = self._pending_view_state or self._capture_view_state()
        self._pending_view_state = None
        self._restore_timer.stop()
        self.map_view.cleanup()
        super().done(result)


class ReplayPage(QWidget):
    """获取、导入、打开与删除均由用户点击触发；解析结果保存在本地。"""

    # 账号编号保持Python整数；姓名仅供显示，不作为查询身份。
    player_query_requested = pyqtSignal(object, str)
    PAGE_SIZE = 12

    def __init__(self, data_dir, parent=None):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.library = ReplayLibrary(self.data_dir)
        self.report = None
        self.report_id = None
        self.busy = False
        self.page_index = 0
        self.filtered_events = []
        self._player_labels = {}
        self._signals = None
        self.map_view = None
        self.spatial_model = None
        self._map_dialog = None
        self._syncing_time = False
        self._public_context = None
        self._context_pending = False
        self.analysis_sources = None
        self._analysis_token = 0
        self._analysis_jobs = {}
        # 补源线程仅写纯数据队列；Qt定时器在主线程交付，父页面销毁不再留下原生发送端。
        self._analysis_results = SimpleQueue()
        self._analysis_delivery_timer = QTimer(self)
        self._analysis_delivery_timer.setInterval(20)
        self._analysis_delivery_timer.timeout.connect(self._deliver_analysis_results)
        self._map_size_timer = QTimer(self)
        self._map_size_timer.setSingleShot(True)
        self._map_size_timer.timeout.connect(self._resize_map)
        self._fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        self._fullscreen_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._fullscreen_shortcut.setAutoRepeat(False)
        self._fullscreen_shortcut.activated.connect(self._toggle_map_fullscreen)
        self.setObjectName("ReplayPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 隐藏旧导入/归档工具宿主，保留后台与命令行入口，不再占用页面空间。
        self.legacy_tools = QWidget(self)
        tools = QHBoxLayout(self.legacy_tools)
        tools.setSpacing(8)
        self.import_button = _button("打开游戏回放", self.choose_replay, True)
        self.import_button.setToolTip("选择 .rec 游戏回放或官网下载的 ZIP 压缩回放；不是录屏视频。")
        tools.addWidget(self.import_button)
        self.official_replays = _button("官网获取回放 ↗", lambda: QDesktopServices.openUrl(QUrl("https://www.ageofempires.com/stats/ageiv/")))
        self.official_replays.setToolTip("官网搜索玩家，展开具体对局，点击玩家旁的胶片播放图标下载。能否下载由官网该场公开录像状态决定。")
        tools.addWidget(self.official_replays)
        self.archive = _combo("已保存的本地复盘")
        self.archive.setMinimumContentsLength(10)
        self.archive.setSizeAdjustPolicy(ClickComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.archive.activated.connect(self.open_archive)
        tools.addWidget(self.archive, 1)
        self.open_button = _button("打开", self.open_archive)
        tools.addWidget(self.open_button)
        self.delete_button = _button("删除报告", self.delete_archive)
        self.delete_button.setToolTip("仅删除本地复盘报告，不删除原录像。")
        tools.addWidget(self.delete_button)
        self.legacy_tools.hide()

        self.notice = _text("", "Muted")
        self.notice.setAccessibleName("对局回放处理状态")
        self.notice.hide()
        self.notice.setParent(self)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(REPLAY_CARD_GAP)
        self._page_body_layout = self.body_layout
        self.body_layout.setAlignment(Qt.AlignTop)
        self.body_layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        self._show_empty()
        self._refresh_archives()

    def _show_empty(self):
        self._analysis_token += 1
        self.analysis_sources = None
        self._cleanup_maps()
        self.body_layout = self._page_body_layout
        _clear(self.body_layout)
        self.report = self.report_id = self.match_detail = self.detail_view = self.raw_report = self.apm_card = None
        self._player_labels = {}
        self._public_context = None
        self._context_pending = False
        self.spatial_model = {"events": [], "players": [], "map_name": None}
        self._create_dashboard(self.spatial_model)

    def _panel(self, title, name="ReplaySideCard"):
        frame = QFrame()
        frame.setObjectName(name)
        frame.setMinimumWidth(0)
        box = QVBoxLayout(frame)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(12)
        heading = _text(title)
        heading.setStyleSheet("font-size:14px;font-weight:600;color:#f4f4f5;")
        heading.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        frame.heading = heading
        box.addWidget(heading)
        return frame, box

    def _create_dashboard(self, model):
        """首屏地图铺满圆角容器，仅保留浮动全屏入口；下方数据区域独立延伸。"""
        self.stage = QWidget()
        self.stage.setMinimumWidth(0)
        self.stage.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.stage_layout = QBoxLayout(QBoxLayout.LeftToRight, self.stage)
        self.stage_layout.setContentsMargins(0, 0, 0, 0)
        self.stage_layout.setSpacing(REPLAY_CARD_GAP)
        self.map_card = _RoundedMapCard()
        self.map_card.setObjectName("ReplayMapCard")
        self.map_card.setMinimumWidth(0)
        map_box = QVBoxLayout(self.map_card)
        map_box.setContentsMargins(0, 0, 0, 0)
        map_box.setSpacing(0)
        self.map_view = ReplayMapView(model, self.map_card, map_data_dir=self.data_dir)
        self.map_view.set_dashboard_mode(True)
        self.map_view.timeSelected.connect(self._seek_time)
        map_box.addWidget(self.map_view, 1)
        self.expand_map_button = _button("", self._expand_map)
        self.expand_map_button.setParent(self.map_card)
        self.expand_map_button.setObjectName("MapFullscreen")
        self.expand_map_button.setFixedSize(36, 36)
        self.expand_map_button.setIcon(line_icon("maximize", "#e4e4e7"))
        self.expand_map_button.setIconSize(QSize(18, 18))
        self.expand_map_button.setToolTip("全屏（F11）")
        self.expand_map_button.setAccessibleName("全屏（F11）")
        self.expand_map_button.setStyleSheet("QPushButton{padding:0;background:#202023;border:1px solid #3a3a42;border-radius:8px;}"
                                           "QPushButton:hover{background:#303036;border-color:#62626d;}"
                                           "QPushButton:pressed{background:#18181b;}QPushButton:focus{border-color:#b2b2bb;}")
        self.map_card.fullscreen_button = self.expand_map_button
        self.expand_map_button.raise_()
        self.stage_layout.addWidget(self.map_card, 2)
        self.sidebar = QWidget()
        self.sidebar.setMinimumWidth(0)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(REPLAY_CARD_GAP)
        self.summary_card, summary = self._panel("对局信息")
        summary.setSpacing(8)
        self.summary_name = _text()
        self.summary_name.setMinimumWidth(0)
        self.summary_name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.summary_name.setStyleSheet("font-size:18px;font-weight:600;color:#f4f4f5;")
        summary.addWidget(self.summary_name)
        self.summary_info = _text("", "Muted")
        self.summary_info.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.summary_info.setStyleSheet("font-size:12px;color:#929299;")
        summary.addWidget(self.summary_info)
        self.notice.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        summary.addWidget(self.notice)
        side.addWidget(self.summary_card)
        self.options_card, options = self._panel("显示选项")
        self.options_card.setMinimumHeight(96)
        options.addWidget(_text("显示玩家", "Muted"))
        self.grid_player_filter = _combo("网格显示玩家")
        self.grid_player_filter.currentIndexChanged.connect(self._grid_player_changed)
        options.addWidget(self.grid_player_filter)
        self.placement_markers_toggle = QCheckBox("显示建筑与器械")
        self.placement_markers_toggle.setChecked(True)
        self.placement_markers_toggle.toggled.connect(self._placement_visibility_changed)
        options.addWidget(self.placement_markers_toggle)
        self.operation_points_toggle = QCheckBox("显示操作点")
        self.operation_points_toggle.setChecked(True)
        self.operation_points_toggle.toggled.connect(self._operation_visibility_changed)
        options.addWidget(self.operation_points_toggle)
        self.grid_event_detail = _text("", "Muted")
        self.grid_event_detail.hide()
        options.addWidget(self.grid_event_detail)
        self.map_view.eventSelected.connect(self._grid_event_selected)
        options.addStretch()
        side.addWidget(self.options_card, 1)
        self.stage_layout.addWidget(self.sidebar, 1)
        self.body_layout.addWidget(self.stage)
        # 数据区只是透明宿主；APM自带背景，其他展开入口保持为卡片外的兄弟内容。
        self.analysis_card = QWidget()
        self.analysis_card.setObjectName("ReplayDataArea")
        analysis = QVBoxLayout(self.analysis_card)
        analysis.setContentsMargins(0, 0, 0, 0)
        analysis.setSpacing(0)
        analysis.setAlignment(Qt.AlignTop)
        self.analysis_placeholder = _text("", "Muted")
        self.analysis_placeholder.setStyleSheet("font-size:12px;color:#929299;")
        self.analysis_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        analysis.addWidget(self.analysis_placeholder)
        self.analysis_placeholder.hide()  # 初始无静态提示行；仅实际加载或失败时显示状态。
        self.analysis_content = QWidget()
        self.analysis_content.setObjectName("ReplayAnalysisContent")
        self.analysis_layout = QVBoxLayout(self.analysis_content)
        self.analysis_layout.setContentsMargins(0, 0, 0, 0)
        self.analysis_layout.setSpacing(REPLAY_CARD_GAP)
        analysis.addWidget(self.analysis_content)
        self.body_layout.addWidget(self.analysis_card)
        self.setStyleSheet("""
            QFrame#ReplayMapCard, QFrame#ReplaySideCard {
                background:#161619;border:0;border-radius:12px;
            }
            QWidget#ReplayDataArea, QWidget#ReplayAnalysisContent {background:transparent;border:0;}
            QFrame#ReplaySideCard QPushButton {padding:6px 10px;min-height:20px;}
        """)
        self._refresh_grid_players()
        self._update_summary({})
        self._map_size_timer.start(0)

    def _refresh_grid_players(self):
        """右侧筛选使用真实玩家编号，与嵌入和全屏画布共享同一筛选值。"""
        combo = self.grid_player_filter
        combo.blockSignals(True)
        combo.clear()
        for index in range(self.map_view.player_filter.count()):
            combo.addItem(self.map_view.player_filter.itemText(index),
                          self.map_view.player_filter.itemData(index))
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self.grid_event_detail.clear()
        self.grid_event_detail.hide()

    def _placement_visibility_changed(self, visible):
        for view in (self.map_view, self._map_dialog.map_view if self._map_dialog else None):
            if view is not None:
                view.set_placement_markers_visible(visible)

    def _operation_visibility_changed(self, visible):
        for view in (self.map_view, self._map_dialog.map_view if self._map_dialog else None):
            if view is not None:
                view.set_operation_points_visible(visible)

    def _grid_player_changed(self):
        value = self.grid_player_filter.currentData()
        for view in (self.map_view, self._map_dialog.map_view if self._map_dialog else None):
            if view is not None:
                index = view.player_filter.findData(value)
                if index >= 0:
                    view.player_filter.setCurrentIndex(index)

    def _grid_event_selected(self, event):
        """仅说明该条指令的目标，不把单位实例编号宣称为已定位角色。"""
        action = (placement_description(event) if event.get("action") == "place_building" else
                  event.get("label", "操作"))
        self.grid_event_detail.setText(
            f"{_time(event.get('time'))} · {event.get('player_name', '')}\n"
            f"{action} · 目标 {event.get('x', 0):g}, {event.get('y', 0):g}")
        self.grid_event_detail.show()

    def _update_summary(self, values):
        value = values.get("map") or values.get("map_name")
        selected = bool(values.get("game_id")) or self.report is not None
        self.summary_name.setText(map_name(value) if value else
                                  "地图名称未提供" if selected else "尚未选择对局")
        lines = []
        if values.get("mode"):
            lines.append(mode_name(values["mode"]))
        duration = values.get("duration", values.get("duration_seconds"))
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0:
            lines.append(_time(duration))
        if values.get("game_id"):
            lines.append(f"对局 {values['game_id']}")
        self.summary_info.setText(" · ".join(lines) if lines else "从玩家查询打开一场对局。")
        self._resize_notice()

    def _preview_public_context(self, game_id, context):
        """只预览本场已知地图；旧报告留在后台，不混入新对局可见数据。"""
        if self._map_dialog is not None:
            self._map_dialog.close()
        self._analysis_token += 1
        self.analysis_sources = None
        self._public_context = dict(context)
        self._public_context["game_id"] = game_id
        self._context_pending = True
        self._update_summary(self._public_context)
        self.analysis_content.hide()
        self.analysis_placeholder.setText("正在读取本场回放数据…")
        self.analysis_placeholder.show()
        self.spatial_model = {"events": [], "players": [], "map_name": context.get("map")}
        self.map_view.set_model(self.spatial_model)
        self._refresh_grid_players()
        self._map_size_timer.start(0)

    def _set_notice(self, message="", error=False):
        self.notice.setText(str(message))
        self.notice.setStyleSheet("color:#e6b8ad;" if error else "")
        self.notice.setToolTip(str(message))
        self.notice.setVisible(bool(message))
        self._resize_notice()

    def _resize_notice(self):
        # QLabel的sizeHint会保留旧窄行宽的换行高度；按卡片当前行宽刷新自然高度。
        # 卡片高度只由实际可见文本及8像素间距组成，空状态没有固定占位。
        if not hasattr(self, "summary_info"):
            return
        box = self.summary_card.layout()
        margins = box.contentsMargins()
        width = max(1, self.summary_card.width() - margins.left() - margins.right())
        labels = [self.summary_card.heading, self.summary_name, self.summary_info]
        if self.notice.text():
            labels.append(self.notice)
        for label in labels:
            label.setFixedHeight(max(0, label.heightForWidth(width)))
        if not self.notice.text():
            self.notice.setFixedHeight(0)
        height = margins.top() + margins.bottom() + sum(label.height() for label in labels)
        height += max(0, len(labels) - 1) * box.spacing()
        self.summary_card.setFixedHeight(height)

    def _set_busy(self, value):
        self.busy = bool(value)
        self.import_button.setEnabled(not value)
        self.import_button.setText("正在处理…" if value else "打开游戏回放")
        self.archive.setEnabled(not value and self.archive.count() > 0)
        self.open_button.setEnabled(not value and self.archive.count() > 0)
        self.delete_button.setEnabled(not value and self.archive.count() > 0)

    def _refresh_archives(self, selected=None):
        previous = selected if selected is not None else self.archive.currentData()
        self.archive.blockSignals(True)
        self.archive.clear()
        try:
            for item in self.library.list():
                name = str(item.get("name") or "本地录像")
                players = item.get("player_names") or []
                caption = f"{name} · {' / '.join(str(name) for name in players[:2])}" if players else name
                self.archive.addItem(caption, item["id"])
                self.archive.setItemData(self.archive.count() - 1, caption, Qt.ToolTipRole)
        except (OSError, ValueError, TypeError):
            self._set_notice("本地复盘列表读取失败，请检查数据目录。", True)
        index = self.archive.findData(previous)
        if index >= 0:
            self.archive.setCurrentIndex(index)
        self.archive.setPlaceholderText("还没有保存的复盘")
        self.archive.blockSignals(False)
        self._set_busy(self.busy)

    def choose_replay(self):
        if self.busy:
            return
        # playback（回放目录）只作为文件选择器起始位置，不扫描或自动读取正在进行的对局。
        playback = Path.home() / "Documents/My Games/Age of Empires IV/playback"
        path, _ = QFileDialog.getOpenFileName(
            self, "选择游戏回放记录（不是视频）", str(playback) if playback.is_dir() else "",
            "帝国时代4回放记录 (*.rec *.aoe4replay *.gz *.zip AgeIV_Replay_*);;所有文件 (*)",
        )
        if path:
            self.import_replay(path)

    def import_replay(self, path):
        """公开入口便于测试；解析、分析和归档都在后台完成。"""
        if self.busy or not path:
            return
        def import_file():
            parsed = parse_replay(Path(path))
            return self.library.save(analyze_replay(parsed))
        self._job(import_file, "正在离线读取录像…")

    def import_public_match(self, profile_id, game_id, match_metadata=None):
        """一键获取单场公开回放并分析，不要求玩家自行下载、解压或设置游戏。"""
        if self.busy:
            return
        context = dict(match_metadata) if isinstance(match_metadata, dict) else {}
        self._preview_public_context(game_id, context)

        def import_match(progress):
            try:
                download = PublicReplayClient(self.data_dir).fetch_match(profile_id, game_id)
            except Exception as error:
                record_public_replay_failure(self.data_dir, profile_id, game_id, "download", error)
                raise
            requested_profile = download.get("requested_profile_id", profile_id)
            progress("正在分析公开回放…")
            try:
                parsed = parse_replay(Path(download["path"]))
            except Exception as error:
                record_public_replay_failure(self.data_dir, profile_id, game_id, "parse", error)
                # 下载缓存由下载器维护；解析不支持时保留文件，允许以后重新分析。
                if isinstance(error, ReplayError):
                    raise ReplayError(f"录像已下载，但暂时无法解析：{error}。本地录像已保留，可稍后重试。") from error
                raise
            source = dict(parsed.get("source") or {})
            source.update(origin="aoe4world-public-replay", game_id=download["game_id"],
                          profile_id=requested_profile,
                          download_profile_id=download.get("download_profile_id", download["profile_id"]),
                          name=f"对局 {download['game_id']} · 公开回放")
            parsed["source"] = source
            metadata = dict(parsed.get("metadata") or {})
            # 网站资料仅补缺失字段，不覆盖录像实际记录的时长，也不按昵称猜玩家归属。
            if not metadata.get("map") and isinstance(context.get("map"), str):
                metadata["map"] = context["map"][:300]
            duration = context.get("duration")
            if metadata.get("duration") is None and isinstance(duration, (int, float)) and not isinstance(duration, bool):
                if math.isfinite(duration) and duration > 0:
                    metadata.update(duration=duration, duration_source="public_match")
            parsed["metadata"] = metadata
            matches = [player for player in metadata.get("players") or []
                       if isinstance(player, dict) and player.get("profile_id") is not None
                       and str(player["profile_id"]) == str(requested_profile)
                       and player.get("id") is not None]
            focus = matches[0]["id"] if len(matches) == 1 else None
            progress("正在生成对局报告…")
            result = self.library.save(analyze_replay(parsed, focus_player_id=focus))
            progress("正在读取经济与关键事件…")
            try:
                result["match_detail"] = MatchDetailClient(self.data_dir).fetch(profile_id, game_id)
            except (MatchDetailError, OSError) as error:
                # 详细统计不可用时仍保留成功解析的原回放报告。
                result["detail_warning"] = str(error)
            result["public_match"] = {"cached": bool(download.get("cached")), "game_id": download["game_id"]}
            return result

        self._job(import_match, "正在获取公开回放并分析…", with_progress=True)

    def _job(self, function, message, *, with_progress=False):
        """解析和打开报告共用后台执行器，界面信号只在主线程处理。"""
        if self.busy:
            return
        self._set_busy(True)
        self._set_notice(message)
        signals = _ReplaySignals()
        self._signals = signals
        # 绑定 QObject（界面对象）方法，页面销毁后 Qt 会自动断开信号。
        signals.result.connect(self._import_result)
        signals.error.connect(self._job_error)
        signals.progress.connect(self._set_notice)
        signals.finished.connect(self._job_finished)

        def work():
            try:
                signals.result.emit(function(signals.progress.emit) if with_progress else function())
            except (ReplayError, PublicReplayError) as error:
                signals.error.emit(str(error))
            except (OSError, ValueError) as error:
                signals.error.emit(f"录像导入失败：{error}")
            except Exception:
                signals.error.emit("录像解析失败，现有报告仍可查看。")
            finally:
                signals.finished.emit()

        # daemon（守护线程）不会因正在解析而阻止退出软件。
        threading.Thread(target=work, name="LocalReplayParse", daemon=True).start()

    def import_path(self, path):
        """命令行手动传入的录像文件与文件选择器共用相同导入流程。"""
        self.import_replay(path)

    def _job_error(self, message):
        self._set_notice(message, True)
        if self._context_pending:
            self.analysis_placeholder.setText("本场回放数据暂不可用。")
            self.analysis_placeholder.show()
            self.analysis_content.hide()

    def _job_finished(self):
        self._set_busy(False)

    def _import_result(self, result):
        report = result["report"]
        self._show_report(report, result["id"], detail=result.get("match_detail"))
        self._refresh_archives(result["id"])
        public = result.get("public_match")
        if public:
            action = "已从缓存分析" if public.get("cached") else "已下载并分析"
            self._set_notice(f"{action}对局 {public['game_id']}。" +
                             (" " + result["detail_warning"] if result.get("detail_warning") else ""))
        else:
            self._set_notice("已更新原复盘。" if result.get("updated") else
                             "已打开已有复盘。" if result.get("duplicate") else "")

    def open_archive(self, *args):
        if self.busy:
            return
        report_id = self.archive.currentData()
        if not report_id:
            return
        def read_saved():
            report = self.library.load(report_id)
            source = report.get("source") or {}
            detail = None
            if source.get("profile_id") and source.get("game_id"):
                detail = MatchDetailClient(self.data_dir).load_cached(source["profile_id"], source["game_id"])
            return {"id": report_id, "report": report, "duplicate": False, "match_detail": detail}
        self._job(read_saved, "正在打开本地复盘…")

    def delete_archive(self):
        if self.busy:
            return
        report_id = self.archive.currentData()
        if not report_id:
            return
        try:
            deleted = self.library.delete(report_id)
        except (OSError, ValueError):
            self._set_notice("报告删除失败，请检查数据目录。", True)
            return
        if deleted:
            if report_id == self.report_id:
                self._show_empty()
            self._refresh_archives()
            self._set_notice()

    def _show_report(self, report, report_id=None, detail=None):
        if self._map_dialog is not None:
            dialog, self._map_dialog = self._map_dialog, None
            dialog.close()
        self.report, self.report_id = report, report_id
        self._analysis_token += 1
        self.analysis_sources = None
        self._player_labels = player_labels(report.get("players") or [])
        self.page_index = 0
        source, metadata = report.get("source") or {}, report.get("metadata") or {}
        coverage = report.get("coverage") or {}
        context = self._public_context if (self._public_context and
                  str(source.get("game_id")) == str(self._public_context.get("game_id"))) else None
        self._context_pending = False
        self._public_context = context
        self.spatial_model = build_spatial_model(report, detail)
        if self.map_view is None:
            self.body_layout = self._page_body_layout
            _clear(self.body_layout)
            self._create_dashboard(self.spatial_model)
        else:
            self.map_view.set_model(self.spatial_model)
        self._refresh_grid_players()
        values = dict(context or {})
        values.update(metadata)
        # 归档未记录地图名时只补同局公开详情，不让已打开的报告显示为尚未选择。
        if (not (values.get("map") or values.get("map_name")) and isinstance(detail, dict)
                and source.get("game_id") is not None
                and str(detail.get("game_id")) == str(source.get("game_id"))):
            values["map_name"] = detail.get("map_name")
        if source.get("game_id"):
            values["game_id"] = source["game_id"]
        self._update_summary(values)
        self.match_detail, self.detail_view = detail, None
        self.map_view.set_analysis_context(report, detail)
        # 补源独立于导入任务；后台结果仅更新分析，不重置地图或播放。
        self._start_analysis_sources(allow_network=True)
        self.raw_report = None
        _clear(self.analysis_layout)
        self.analysis_content.show()
        self.analysis_placeholder.hide()
        outer_layout = self.analysis_layout

        # APM（每分钟操作次数）只取本场公开摘要；无来源值时显示缺失，不由部分指令推算。
        focus_id = source.get("profile_id") or (detail.get("focus_profile_id") if isinstance(detail, dict) else None)
        self.apm_card = APMCard(detail, source.get("game_id"), focus_id, self.analysis_content)
        self.apm_card.playerActivated.connect(self._request_apm_player)
        outer_layout.addWidget(self.apm_card, 0, Qt.AlignLeft)

        if detail:
            self.detail_toggle = _button("经济与事件", self._toggle_detail_view)
            self.detail_toggle.setCheckable(True)
            outer_layout.addWidget(self.detail_toggle)
            self.detail_view = MatchDetailView(detail, self)
            self.detail_view.timeSelected.connect(self._seek_time)
            outer_layout.addWidget(self.detail_view)
            self.detail_view.hide()
        self.raw_toggle = _button("玩家与原始指令（部分识别）", self._toggle_raw_report)
        self.raw_toggle.setCheckable(True)
        outer_layout.addWidget(self.raw_toggle)
        self.raw_report = QWidget()
        self.body_layout = QVBoxLayout(self.raw_report)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.raw_report)
        self.raw_report.hide()

        scope = QFrame()
        scope.setObjectName("Card")
        scope_layout = QVBoxLayout(scope)
        scope_layout.setContentsMargins(12, 10, 12, 10)
        scope_layout.setSpacing(5)
        analyzed = coverage.get("commands_analyzed", 0)
        known = coverage.get("commands_known", 0)
        self.coverage_label = _text(f"玩家发令 {analyzed} 条 · 类型识别 {known} / {analyzed} 条", "Muted")
        scope_line = QHBoxLayout()
        scope_line.addWidget(self.coverage_label, 1)
        self.scope_button = _button("解析范围", self._toggle_scope)
        self.scope_button.setCheckable(True)
        scope_line.addWidget(self.scope_button)
        scope_layout.addLayout(scope_line)
        self.scope_detail = _text(self._coverage_details(coverage), "Muted")
        self.scope_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.scope_detail.hide()
        scope_layout.addWidget(self.scope_detail)
        self.body_layout.addWidget(scope)

        self.body_layout.addWidget(_text("玩家指令对比", "SectionTitle"))
        self.players_table = self._players_table(report.get("players") or [])
        self.body_layout.addWidget(self.players_table)

        self.findings_frame = QWidget()
        self.findings_layout = QVBoxLayout(self.findings_frame)
        self.findings_layout.setContentsMargins(0, 0, 0, 0)
        self.findings_layout.setSpacing(8)
        self.body_layout.addWidget(self.findings_frame)
        self._render_findings()

        self.body_layout.addWidget(_text("指令时间线", "SectionTitle"))
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.player_filter = _combo("筛选时间线玩家")
        self.player_filter.setMinimumContentsLength(10)
        self.player_filter.setSizeAdjustPolicy(ClickComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.player_filter.setMaximumWidth(260)
        self.player_filter.addItem("所有玩家", None)
        for player in report.get("players") or []:
            label = self._player_labels.get(player["id"], str(player.get("name") or f"玩家 {player['id']}"))
            self.player_filter.addItem(label, player["id"])
            self.player_filter.setItemData(self.player_filter.count() - 1, label, Qt.ToolTipRole)
        if report.get("focus_player_id") is not None:
            self.player_filter.setCurrentIndex(max(0, self.player_filter.findData(report["focus_player_id"])))
        self.category_filter = _combo("筛选指令类别")
        self.category_filter.addItem("全部指令", None)
        categories = {event.get("category") for event in report.get("timeline") or []}
        for category in CATEGORY_LABELS:
            if category in categories:
                self.category_filter.addItem(CATEGORY_LABELS[category], category)
        for category in sorted(categories - set(CATEGORY_LABELS), key=str):
            if category:
                self.category_filter.addItem(str(category), category)
        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("搜索指令或详情")
        self.keyword.setAccessibleName("搜索指令或详情")
        self.keyword.setClearButtonEnabled(True)
        self.keyword.setMinimumWidth(0)
        filters.addWidget(self.player_filter)
        filters.addWidget(self.category_filter)
        filters.addWidget(self.keyword, 1)
        self.body_layout.addLayout(filters)
        self.timeline_table = self._table(["时间", "玩家", "指令", "详情"], [], 32)
        self.timeline_table.setColumnWidth(0, 64)
        self.timeline_table.setColumnWidth(1, 190)
        self.timeline_table.setColumnWidth(2, 120)
        self.timeline_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.timeline_table.itemSelectionChanged.connect(self._show_event_detail)
        self.body_layout.addWidget(self.timeline_table)
        self.event_detail = _text("", "Muted")
        self.event_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.event_detail.hide()
        self.body_layout.addWidget(self.event_detail)
        pages = QHBoxLayout()
        self.event_count = _text("", "Muted")
        pages.addWidget(self.event_count, 1)
        self.previous_button = _button("上一页", lambda: self._change_page(-1))
        self.next_button = _button("下一页", lambda: self._change_page(1))
        self.page_label = _text("", "Muted")
        pages.addWidget(self.previous_button)
        pages.addWidget(self.page_label)
        pages.addWidget(self.next_button)
        self.body_layout.addLayout(pages)
        self.body_layout.addStretch()
        self.player_filter.currentIndexChanged.connect(self._filter_events)
        self.category_filter.currentIndexChanged.connect(self._filter_events)
        self.keyword.textChanged.connect(self._filter_events)
        self._filter_events()
        self.body_layout = self._page_body_layout
        self.scroll.verticalScrollBar().setValue(0)
        self._map_size_timer.start(0)

    def _request_apm_player(self, profile_id, name):
        """只转发当前对局图表；已卸载或隐藏的旧图表不得查询上一场玩家。"""
        if (self.sender() is not self.apm_card or self._context_pending or self.report is None
                or isinstance(profile_id, bool) or not isinstance(profile_id, int)):
            return
        player = next((player for player in self.apm_card.chart.players
                       if player["profile_id"] == profile_id), None)
        if player is not None:
            self.player_query_requested.emit(profile_id, player["name"])

    def _resize_map(self):
        if self.map_view is None or not hasattr(self, "stage"):
            return
        width = self.scroll.viewport().width()
        wide = width >= 920
        if self.apm_card is not None:
            # 图表恢复整行，与其它数据控件共用内容区宽度。
            self.apm_card.setFixedWidth(width)
        self.stage_layout.setDirection(QBoxLayout.LeftToRight if wide else QBoxLayout.TopToBottom)
        self.stage_layout.setStretch(0, 2 if wide else 0)
        self.stage_layout.setStretch(1, 1 if wide else 0)
        # 只按视口尺寸决定地图最小高度，不读地图y位置，也不再锁定map_view高度。
        height = max(460, min(760, self.scroll.viewport().height() - 8)) if wide else max(320, min(540, round(width * .72), self.scroll.viewport().height() - 8))
        self.map_card.setMinimumHeight(height)
        self.stage_layout.invalidate()
        self.body_layout.activate()
        self._resize_notice()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_map()

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._map_size_timer.start(0)
        return super().eventFilter(watched, event)

    def _seek_time(self, seconds):
        """以秒联动地图与图表，不按不同来源的总时长拉伸时间。"""
        if self._syncing_time:
            return
        self._syncing_time = True
        try:
            if self.map_view is not None:
                self.map_view.seek(seconds, emit=False)
            if self._map_dialog is not None:
                self._map_dialog.map_view.seek(seconds, emit=False)
            if self.detail_view is not None:
                self.detail_view.focus_time(seconds)
        finally:
            self._syncing_time = False

    def _toggle_map_fullscreen(self):
        """焦点尚未交给大图时，第二次F11也返回，而不是再次展开或唤起浮窗。"""
        if self._map_dialog is not None:
            self._map_dialog.toggle_fullscreen()
        else:
            self._expand_map()

    def _expand_map(self):
        if self.map_view is None:
            return
        if self._map_dialog is not None:
            if not self._map_dialog.isFullScreen():
                self._map_dialog.showFullScreen()
            self._map_dialog.raise_()
            self._map_dialog.activateWindow()
            return
        state = self.map_view.capture_view_state()
        self.map_view.pause()
        dialog = _ReplayMapDialog(self.spatial_model, self)
        self._map_dialog = dialog
        if not self._context_pending:
            dialog.map_view.set_analysis_context(self.report, self.match_detail, sources=self.analysis_sources)
        dialog.map_view.analysisDataRequested.connect(self._request_analysis_sources)
        dock = dialog.map_view.advanced_analysis
        if dock is not None and hasattr(dock, "set_loading"):
            dock.set_loading(self._analysis_token in self._analysis_jobs)
        dialog.map_view.timeSelected.connect(self._seek_time)
        dialog.finished.connect(lambda result, value=dialog: self._map_closed(value))
        dialog.showFullScreen()
        dialog.restore_view_state(state)
        dialog.map_view.canvas.setFocus()

    def _request_analysis_sources(self):
        if not self._context_pending:
            self._start_analysis_sources(allow_network=True)

    def _start_analysis_sources(self, *, allow_network=False):
        """同一份报告只并行一个补源任务；切换对局后丢弃旧结果。"""
        if not isinstance(self.report, dict) or self._analysis_token in self._analysis_jobs:
            return
        token, report = self._analysis_token, self.report
        self._analysis_jobs[token] = report
        self._analysis_set_loading(True)
        self._analysis_delivery_timer.start()
        results, data_dir, collector = self._analysis_results, self.data_dir, collect_analysis_sources

        def run():
            try:
                result = collector(report, data_dir, allow_network=allow_network)
            except Exception as exc:
                result = {"errors": [str(exc)]}
            # 此闭包不持有页面、Qt对象或信号；页面消失后仍可安全结束。
            results.put((token, report, result))

        threading.Thread(target=run, daemon=True, name="ReplayAnalysisSources").start()

    def _deliver_analysis_results(self):
        """仅由GUI子定时器取出后台纯数据，保留原报告身份与代次门控。"""
        while True:
            try:
                token, report, result = self._analysis_results.get_nowait()
            except Empty:
                break
            try:
                self._analysis_sources_ready(token, report, result)
            finally:
                self._analysis_sources_finished(token)

    def _analysis_set_loading(self, loading):
        view = self._map_dialog.map_view if self._map_dialog is not None else None
        dock = view.advanced_analysis if view is not None else None
        if dock is not None and hasattr(dock, "set_loading"):
            dock.set_loading(loading)

    def _analysis_sources_ready(self, token, report, result):
        if token != self._analysis_token or report is not self.report or not isinstance(result, dict):
            return
        self.analysis_sources = result
        for view in (self.map_view, self._map_dialog.map_view if self._map_dialog is not None else None):
            if view is not None:
                view.set_analysis_context(self.report, self.match_detail, sources=result)

    def _analysis_sources_finished(self, token):
        self._analysis_jobs.pop(token, None)
        if token == self._analysis_token:
            self._analysis_set_loading(False)
        if not self._analysis_jobs:
            self._analysis_delivery_timer.stop()

    def _map_closed(self, dialog):
        if self._map_dialog is dialog:
            self._map_dialog = None
            if self.map_view is not None and dialog.view_state is not None:
                state = dict(dialog.view_state)
                if not self.isVisible():
                    state["playing"] = False
                self.map_view.restore_view_state(state)
                for toggle, visible in ((self.placement_markers_toggle, self.map_view.placement_markers_visible),
                                        (self.operation_points_toggle, self.map_view.operation_points_visible)):
                    previous = toggle.blockSignals(True)
                    toggle.setChecked(visible)
                    toggle.blockSignals(previous)
                self._seek_time(self.map_view.current_time)
                if self.isVisible():
                    self.expand_map_button.setFocus()

    def _cleanup_maps(self):
        self._map_size_timer.stop()
        if self._map_dialog is not None:
            dialog = self._map_dialog
            self._map_dialog = None
            dialog.close()
        if self.map_view is not None:
            self.map_view.cleanup()
            self.map_view = None
        self.spatial_model = None

    def closeEvent(self, event):
        self._analysis_token += 1
        self._analysis_delivery_timer.stop()
        self._analysis_jobs.clear()
        # 晚到任务仍持旧队列，不再向已关闭页面交付，也不妨碍以后重新打开。
        self._analysis_results = SimpleQueue()
        self._cleanup_maps()
        super().closeEvent(event)

    def hideEvent(self, event):
        if self.map_view is not None:
            self.map_view.pause()
        if self._map_dialog is not None:
            self._map_dialog.close()
        super().hideEvent(event)

    def _toggle_detail_view(self, checked):
        if self.detail_view is not None:
            self.detail_view.setVisible(checked)
            self.analysis_placeholder.hide()

    def _toggle_raw_report(self, checked):
        if self.raw_report is not None:
            self.raw_report.setVisible(checked)
            self.analysis_placeholder.hide()

    def _coverage_details(self, coverage):
        warnings = coverage.get("warnings") or []
        unknown = coverage.get("unknown_types") or {}
        if isinstance(unknown, dict):
            unknown_text = "、".join(f"{key}（{count} 条）" for key, count in unknown.items())
        elif isinstance(unknown, list):
            unknown_text = "、".join(str(value) for value in unknown)
        else:
            unknown_text = str(unknown) if unknown else ""
        lines = ["指令记录不等于全部键鼠操作；当前报告不还原完整视野与交战过程。"]
        original_total = coverage.get("commands_total")
        identified = coverage.get("commands_identified")
        decoded = coverage.get("commands_decoded")
        if original_total is not None:
            lines.append(f"原始记录 {original_total} 条；已排除控制与周期记录 {coverage.get('system_commands', 0)} 条。")
        if identified is not None:
            lines.append(f"原始记录中类型已识别 {identified} 条（包含控制与周期记录）。")
        if decoded is not None:
            lines.append(f"玩家指令参数完整解析 {decoded} 条。")
        if unknown_text:
            lines.append(f"未识别指令类型：{unknown_text}")
        lines.extend(str(warning) for warning in warnings)
        return "\n".join(lines)

    def _toggle_scope(self):
        self.scope_detail.setVisible(self.scope_button.isChecked())

    def _players_table(self, players):
        labels = player_labels(players)
        groups = ["production", "construction", "movement", "combat", "cancel"]
        # 只保留分析引擎实际提供的类别，避免把未支持的指标显示成零。
        present = set().union(*(set(player.get("counts") or {}) for player in players)) if players else set()
        ordered = [key for key in groups if key in present]
        ordered += [key for key in CATEGORY_LABELS if key in present and key not in ordered]
        ordered = ordered[:5]
        short_labels = {"production": "生产", "construction": "建造", "movement": "移动",
                        "combat": "作战", "cancel": "取消", "ability": "能力",
                        "other": "其他", "unknown": "未识别"}
        headers = ["玩家", "文明", "指令记录"] + [short_labels.get(key, "其他") for key in ordered]
        rows = []
        for player in players:
            name = labels.get(player.get("id"), str(player.get("name") or f"玩家 {player.get('id', '?')}"))
            civilization = player.get("civilization")
            civ = _civilization_name(civilization)
            rows.append([name, civ, player.get("command_count", 0)] + [player.get("counts", {}).get(key, 0) for key in ordered])
        table = self._table(headers, rows, 32)
        for row, player in enumerate(players):
            table.item(row, 0).setData(Qt.UserRole, player.get("id"))
            raw = player.get("civilization")
            if raw:
                table.item(row, 1).setToolTip(f"录像中的原始文明标识：{raw}")
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.setColumnWidth(1, 120)
        for index in range(2, len(headers)):
            table.setColumnWidth(index, 95 if index == 2 else 70)
        for index, key in enumerate(ordered, 3):
            table.horizontalHeaderItem(index).setToolTip(CATEGORY_LABELS.get(key, "其他指令"))
        return table

    def _render_findings(self, player_id=None):
        _clear(self.findings_layout)
        findings = [finding for finding in self.report.get("findings") or []
                    if player_id is None or finding.get("player_id") in (None, player_id)]
        if not findings:
            return
        self.findings_layout.addWidget(_text("复盘线索", "SectionTitle"))
        for finding in findings[:3]:
            card = QFrame()
            card.setObjectName("Card")
            column = QVBoxLayout(card)
            column.setContentsMargins(12, 10, 12, 10)
            column.setSpacing(5)
            line = QHBoxLayout()
            player_name = self._player_labels.get(finding.get("player_id"), "")
            title = finding.get("title") or "复盘线索"
            line.addWidget(_text(f"{player_name} · {title}" if player_name else title), 1)
            confidence = "指令记录" if finding.get("confidence") == "recorded" else "待复盘确认"
            line.addWidget(_text(confidence, "Muted"))
            evidence = finding.get("evidence") or []
            if evidence:
                first = evidence[0]
                button = _button(f"查看 {_time(first.get('time'))}", lambda checked=False, value=first: self._jump_evidence(value))
                line.addWidget(button)
            column.addLayout(line)
            column.addWidget(_text(finding.get("body") or "", "Muted"))
            if finding.get("action"):
                column.addWidget(_text(finding["action"]))
            self.findings_layout.addWidget(card)

    def _jump_evidence(self, evidence):
        self.player_filter.blockSignals(True)
        index = self.player_filter.findData(evidence.get("player_id"))
        self.player_filter.setCurrentIndex(max(0, index))
        self.player_filter.blockSignals(False)
        self.category_filter.blockSignals(True)
        self.category_filter.setCurrentIndex(0)
        self.category_filter.blockSignals(False)
        self.keyword.blockSignals(True)
        self.keyword.clear()
        self.keyword.blockSignals(False)
        self._filter_events()
        offset = evidence.get("offset")
        index = next((i for i, event in enumerate(self.filtered_events)
                      if offset is not None and event.get("offset") == offset), None)
        if index is None:
            time = evidence.get("time")
            index = next((i for i, event in enumerate(self.filtered_events) if event.get("time") == time), 0)
        self.page_index = index // self.PAGE_SIZE
        self._render_timeline()
        if self.timeline_table.rowCount():
            self.timeline_table.selectRow(index % self.PAGE_SIZE)
        self.scroll.ensureWidgetVisible(self.timeline_table)

    def _filter_events(self, *args):
        player = self.player_filter.currentData()
        self.player_filter.setToolTip(self.player_filter.currentText())
        category = self.category_filter.currentData()
        keyword = self.keyword.text().strip().casefold()
        self.filtered_events = [event for event in self.report.get("timeline") or []
                                if (player is None or event.get("player_id") == player)
                                and (category is None or event.get("category") == category)
                                and (not keyword or keyword in " ".join(
                                    [self._event_player_label(event)] + [str(event.get(key) or "") for key in
                                     ("player_name", "label", "detail")]).casefold())]
        self.page_index = 0
        self._render_findings(player)
        self._render_timeline()

    def _change_page(self, delta):
        page_count = max(1, math.ceil(len(self.filtered_events) / self.PAGE_SIZE))
        self.page_index = min(page_count - 1, max(0, self.page_index + delta))
        self._render_timeline()

    def _render_timeline(self):
        total = len(self.filtered_events)
        page_count = max(1, math.ceil(total / self.PAGE_SIZE))
        self.page_index = min(self.page_index, page_count - 1)
        start = self.page_index * self.PAGE_SIZE
        events = self.filtered_events[start:start + self.PAGE_SIZE]
        table = self.timeline_table
        table.setRowCount(len(events))
        for index, event in enumerate(events):
            label = event.get("label") if event.get("known") is not False else "未识别指令"
            values = [_time(event.get("time")), self._event_player_label(event),
                      label or "未识别指令", _display_detail(event) or "—"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(_event_tooltip(event) if column in (2, 3) else str(value))
                if column == 0:
                    item.setData(Qt.UserRole, event)
                table.setItem(index, column, item)
            table.setRowHeight(index, 32)
        table.setFixedHeight(32 + 32 * max(1, len(events)))
        self.event_detail.hide()
        self.event_count.setText(f"{total} 条记录" if total else "没有符合筛选条件的指令")
        self.page_label.setText(f"{self.page_index + 1} / {page_count}")
        self.previous_button.setEnabled(self.page_index > 0)
        self.next_button.setEnabled(self.page_index + 1 < page_count)

    def _event_player_label(self, event):
        """显示标签按局内编号查找；昵称仅作缺失映射时的原文回退，不参与关联。"""
        return self._player_labels.get(event.get("player_id"), str(event.get("player_name") or "玩家未知"))

    def _show_event_detail(self):
        row = self.timeline_table.currentRow()
        if row < 0 or self.timeline_table.item(row, 0) is None:
            self.event_detail.hide()
            return
        event = self.timeline_table.item(row, 0).data(Qt.UserRole) or {}
        label = event.get("label") if event.get("known") is not False else "未识别指令"
        values = [_time(event.get("time")), self._event_player_label(event),
                  str(label or "未识别指令"), _display_detail(event)]
        self.event_detail.setText(" · ".join(value for value in values if value))
        self.event_detail.setToolTip(self._event_player_label(event) + "\n" + _event_tooltip(event))
        self.event_detail.show()
        if event.get("time") is not None:
            self._seek_time(event["time"])

    def _table(self, headers, rows, row_height):
        table = QTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().hide()
        table.setFrameShape(QFrame.NoFrame)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.horizontalHeader().setHighlightSections(False)
        table.horizontalHeader().setMinimumSectionSize(45)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.horizontalHeader().setFixedHeight(30)
        table.setStyleSheet("QTableWidget {background:#161619;border:0;border-radius:12px;outline:0;}"
                            "QTableWidget::item {padding:7px 10px;border-bottom:1px solid #27272C;}"
                            "QTableWidget::item:selected {background:#303037;}"
                            "QHeaderView::section {background:#161619;color:#929299;border:0;padding:10px;}"
                            "QTableCornerButton::section {background:#161619;border:0;}")
        for index, row in enumerate(rows):
            table.setRowHeight(index, row_height)
            for column, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(index, column, item)
        table.setFixedHeight(32 + row_height * max(1, len(rows)))
        table.setMinimumWidth(0)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return table
