"""玩家查询页面：手动查询公开记录，不在后台轮询。

AnalysisPage=分析页面，signals=线程完成信号；网络工作使用守护线程，
界面更新回到 Qt 主线程。外部只需 AnalysisPage(data_dir, parent=None)。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QEvent, QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFontMetrics, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView, QBoxLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLayout, QLineEdit, QListView, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizePolicy,
    QStyle, QStyleOptionComboBox, QStylePainter, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from choice_controls import ClickComboBox
from match_analysis import (
    API_SOURCE, MatchError, PublicMatchClient, analyze_games, civilization_name,
    map_name, mode_name, parse_player_input, profile_overview,
)
from match_widgets import (MatchActionHeader, ModeSummaryGrid, RoundedMatchPanel, RoundedMatchTable, style_recent_matches, MATCH_ACTION_SIZE,
                           MATCH_ACTION_PADDING, MATCH_RESULT_COLUMN_WIDTH,
                           MATCH_ACTION_COLUMN_WIDTH, MATCH_METADATA_ROLE, CARD_WIN, CARD_LOSS)
from rank_display import ranked_result
from visual import line_icon, ui_font
from player_avatars import PlayerAvatar
from player_identity import IdentityClient as PlayerIdentityClient, IdentityError
from player_labels import player_display_labels
from scroll_to_top import install_scroll_to_top
from profile_rank_summary import ProfileRankSummary


def _merge_identity(profile, identity):
    """只按明确编号补充公开资料，空头像不覆盖另一来源已有的头像。"""
    merged = dict(profile)
    if not identity or str(identity.get("profile_id")) != str(profile.get("profile_id")):
        return merged
    merged.update({key: value for key, value in identity.items()
                   if value not in (None, "", {}, []) and key != "avatars"
                   and not (key == "name" and profile.get("name"))})
    merged["avatars"] = {**(profile.get("avatars") or {}),
                         **{key: value for key, value in (identity.get("avatars") or {}).items() if value}}
    return merged


def _identity_text(profile):
    """游戏等级与排位段位独立；缺失等级不拿排位积分替代。"""
    countries = {"cn": "中国", "us": "美国", "gb": "英国", "de": "德国", "fr": "法国",
                 "ru": "俄罗斯", "kr": "韩国", "jp": "日本", "tw": "中国台湾", "hk": "中国香港",
                 "ca": "加拿大", "au": "澳大利亚", "br": "巴西", "es": "西班牙", "it": "意大利",
                 "tr": "土耳其", "vn": "越南", "in": "印度", "nl": "荷兰", "pl": "波兰"}
    level = profile.get("game_level")
    values = [f"玩家编号 {profile.get('profile_id')}",
              f"等级 {level}" if isinstance(level, int) and not isinstance(level, bool) else "等级未提供"]
    country = str(profile.get("country") or "").lower()
    if country:
        values.append(countries.get(country, "地区 " + country.upper()))
    platform = profile.get("platform") or ("Steam" if profile.get("steam_id") else "")
    if platform:
        values.append({"steam": "Steam", "xbox": "Xbox"}.get(str(platform).lower(), str(platform)))
    return " · ".join(values)


class _PlayerName(QLabel):
    """长昵称以省略号收尾，文字原值与辅助功能信息仍保持完整。"""
    def paintEvent(self, event):
        painter = QStylePainter(self)
        painter.drawItemText(self.contentsRect(), Qt.AlignLeft | Qt.AlignVCenter,
            self.palette(), self.isEnabled(),
            self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.contentsRect().width()),
            QPalette.WindowText)


class _PlayerChoice(QWidget):
    """同名候选的头像、等级和唯一编号；点击仍交给外层列表处理。"""
    def __init__(self, profile, data_dir, parent=None, *, allow_network=False):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.directory = data_dir
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(10)
        self.avatar = PlayerAvatar(self, size=36)
        layout.addWidget(self.avatar)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.name = _PlayerName()
        self.name.setTextFormat(Qt.PlainText)
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name.setStyleSheet("font-weight:600;color:#f4f4f5;font-size:13px;")
        self.details = QLabel()
        self.details.setTextFormat(Qt.PlainText)
        self.details.setWordWrap(True)
        self.details.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.details.setStyleSheet("color:#929299;font-size:11px;")
        words.addWidget(self.name)
        words.addWidget(self.details)
        layout.addLayout(words, 1)
        self.update_profile(profile, allow_network=allow_network)

    def update_profile(self, profile, *, allow_network=True):
        self.profile = dict(profile)
        self.name.setText(str(profile.get("name") or "未提供昵称"))
        self.details.setText(_identity_text(profile))
        self.setToolTip(self.name.text() + "\n" + self.details.text())
        self.avatar.set_profile(profile, self.directory, allow_network=allow_network)


class _PlayerChoiceList(QListWidget):
    """原生候选网格，保留方向键、回车和辅助功能的列表语义。"""
    resized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setSpacing(6)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setCursor(Qt.PointingHandCursor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


def _text(value, role=None):
    widget = QLabel(str(value))
    widget.setWordWrap(True)
    if role:
        widget.setObjectName(role)
    return widget


def _clear_layout(layout):
    """递归移除旧内容；嵌套布局中的姓名、资料也必须退出可见树。

    控件仍由页面父对象持有，仅takeAt（移出布局）不会隐藏或销毁它。
    先隐藏再延迟删除，避免同一轮查询返回时旧内容仍覆盖新资料。
    """
    while layout.count():
        item = layout.takeAt(0)
        widget, child_layout = item.widget(), item.layout()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        elif child_layout is not None:
            _clear_layout(child_layout)
            child_layout.deleteLater()


def _button(value, callback, primary=False):
    button = QPushButton(value)
    button.setCursor(Qt.PointingHandCursor)
    button.setObjectName("Primary" if primary else "Quiet")
    button.setMinimumHeight(32)
    button.clicked.connect(callback)
    return button


def _duration(value):
    if value is None:
        return "未提供"
    seconds = int(value)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _time(value, full=False):
    try:
        time = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
        return time.strftime("%Y-%m-%d %H:%M" if full else "%m-%d %H:%M")
    except (ValueError, TypeError):
        return "未提供时间"


class _QuerySignals(QObject):
    result = pyqtSignal(object, str)
    error = pyqtSignal(str)
    finished = pyqtSignal()


class _IdentitySignals(QObject):
    ready = pyqtSignal(object, int)


class _ReadableTableDelegate(QStyledItemDelegate):
    """选中行保持亮色文字；胜负颜色不被系统的深色选中文字覆盖。"""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        brush = index.data(Qt.ForegroundRole)
        color = brush.color() if isinstance(brush, QBrush) else QColor("#f4f4f5")
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            option.palette.setColor(group, QPalette.Text, color)
            option.palette.setColor(group, QPalette.HighlightedText, color)


class _HistoryPicker(ClickComboBox):
    """历史入口只显示时钟图标，展开后仍显示完整昵称和玩家编号。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MatchHistory")
        self.view().setMinimumWidth(250)

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = ""
        # 绘制边框与居中时钟，不绘制组合框额外的向下箭头。
        option.subControls = QStyle.SC_ComboBoxFrame
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        if not self.isEnabled():
            painter.setOpacity(.4)
        painter.drawPixmap((self.width() - 16) // 2, (self.height() - 16) // 2,
                           line_icon("history").pixmap(16, 16))


class AnalysisPage(QWidget):
    """独立页面；用户数据及缓存只写入传入的私人数据目录。"""

    # 仅传明确账号、对局编号和上下文；主窗口负责切换独立的对局分析页面。
    public_match_requested = pyqtSignal(object, object, object)

    def __init__(self, data_dir, parent=None):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.client = PublicMatchClient(self.data_dir)
        self.identity_client = PlayerIdentityClient(self.data_dir)
        self._identity_generation = 0
        self._identity_signals = _IdentitySignals(self)
        self._identity_signals.ready.connect(self._identities_ready)
        self._candidate_profiles = {}
        self._avatar_identity_requested = set()
        self.snapshot = None
        self.busy = False
        self._signals = None
        self.history = []
        self.setObjectName("AnalysisPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        outer.addWidget(_text("玩家查询", "PageTitle"))
        layout = outer
        # 宽窗一行、窄窗两行；历史不再拉伸占满一整行。
        self.query_toolbar = QWidget()
        self.query_toolbar.setObjectName("MatchQueryToolbar")
        self.query_toolbar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.query_toolbar.setStyleSheet("""
            QWidget#MatchQueryToolbar QLineEdit,
            QWidget#MatchQueryToolbar QComboBox {
                padding:2px 8px; min-height:0; border-radius:6px;
            }
            QWidget#MatchQueryToolbar QComboBox {padding-right:24px;}
            QWidget#MatchQueryToolbar QComboBox#MatchHistory {padding:2px 5px;}
            QWidget#MatchQueryToolbar QComboBox#MatchHistory::drop-down {width:14px;}
            QWidget#MatchQueryToolbar QPushButton {
                padding:2px 10px; min-height:0; border-radius:6px;
            }
            QWidget#MatchQueryToolbar QPushButton[compact_delete="true"] {padding:0; min-height:0;}
            QWidget#MatchQueryToolbar QPushButton[compact_icon="true"] {padding:0;}
        """)
        self.toolbar_layout = toolbar = QBoxLayout(QBoxLayout.TopToBottom, self.query_toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        self.query_row = QWidget()
        query_row = QHBoxLayout(self.query_row)
        query_row.setContentsMargins(0, 0, 0, 0)
        query_row.setSpacing(6)
        self.query = QLineEdit()
        self.query.setPlaceholderText("昵称、玩家编号或 AoE4 World 个人主页链接")
        self.query.setAccessibleName("玩家查询条件")
        self.query.setMinimumWidth(120)
        self.query.returnPressed.connect(self.lookup)
        query_row.addWidget(self.query, 1)
        self.query_button = _button("", self.lookup, True)
        self.query_button.setAccessibleName("查询玩家")
        self.query_button.setIcon(line_icon("user-search", "#171719"))
        self.query_button.setProperty("compact_icon", True)
        self.query_button.setToolTip("查询玩家")
        query_row.addWidget(self.query_button)
        self.refresh_button = _button("", self.refresh)
        self.refresh_button.setAccessibleName("刷新战绩")
        self.refresh_button.setIcon(line_icon("reset"))
        self.refresh_button.setProperty("compact_icon", True)
        self.refresh_button.setToolTip("刷新战绩")
        self.refresh_button.setEnabled(False)
        query_row.addWidget(self.refresh_button)
        toolbar.addWidget(self.query_row)
        self.query_options = QWidget()
        options = QHBoxLayout(self.query_options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(10)
        self.history_row = QWidget()
        recent = QHBoxLayout(self.history_row)
        recent.setContentsMargins(0, 0, 0, 0)
        recent.setSpacing(6)
        self.history_combo = _HistoryPicker()
        self.history_combo.setAccessibleName("最近搜索记录")
        self.history_combo.setSizeAdjustPolicy(ClickComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.history_combo.setMinimumContentsLength(8)
        self.history_combo.setMinimumWidth(0)
        self.history_combo.activated.connect(self._use_history)
        recent.addWidget(self.history_combo, 1)
        self.delete_history = _button("×", self._delete_history)
        self.delete_history.setProperty("compact_delete", True)
        self.delete_history.setAccessibleName("删除选中的搜索记录")
        self.delete_history.setToolTip("只删除这条搜索记录，已获取的战绩仍会保留。")
        recent.addWidget(self.delete_history)
        self.history_row.hide()
        options.addWidget(self.history_row)
        # 查询固定展示全部模式；此处只保留样本口径，不创建隐藏的模式限制。
        self.scope_row = QWidget()
        scope_layout = QHBoxLayout(self.scope_row)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.setSpacing(0)
        self.scope_label = _text("", "Muted")
        self.scope_label.setWordWrap(False)
        scope_layout.addWidget(self.scope_label)
        self.scope_row.hide()
        options.addWidget(self.scope_row)
        options.addStretch(1)
        toolbar.addWidget(self.query_options)
        layout.addWidget(self.query_toolbar)
        self.notice = _text("", "Muted")
        self.notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.notice)

        self.player_choices = _PlayerChoiceList()
        self.player_choices.setAccessibleName("昵称搜索结果")
        self.player_choices.setMinimumHeight(0)
        self.player_choices.setMaximumHeight(228)
        self.player_choices.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.player_choices.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.player_choices.setStyleSheet("""
            QListWidget {background:transparent;border:0;outline:0;}
            QListWidget::item {padding:0;border:1px solid #27272c;border-radius:10px;background:#161619;}
            QListWidget::item:hover {background:#202023;border-color:#44444c;}
            QListWidget::item:selected {background:#29292e;border-color:#8c8c96;}
            QListWidget::item:focus {border:2px solid #c4c4cb;}
        """)
        self.player_choices.itemActivated.connect(self._choose_player)
        self.player_choices.resized.connect(self._arrange_player_choices)
        self.player_choices.currentItemChanged.connect(self._candidate_selected)
        self.player_choices.verticalScrollBar().valueChanged.connect(self._refresh_visible_candidates)
        self.player_choices.hide()
        layout.addWidget(self.player_choices)
        self.choose_button = _button("查看选中玩家", self._choose_player, True)
        self.choose_button.hide()
        layout.addWidget(self.choose_button, 0, Qt.AlignRight)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        self.body_layout.setContentsMargins(0, 0, 4, 0)
        self.body_layout.setSpacing(14)
        self.scroll.setWidget(self.body)
        self.scroll_top_button = install_scroll_to_top(self.scroll)
        layout.addWidget(self.scroll, 1)
        self.empty = _text("输入玩家编号或昵称，开始查看对局。\n同名玩家可按头像、等级和唯一编号区分。", "Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.body_layout.addWidget(self.empty, 1)
        self._restore_history()
        self._restore_query()
        self._arrange_query_toolbar()

    def _arrange_query_toolbar(self):
        """按真实字号和所需宽度折行，保留文字、鼠标及键盘操作空间。"""
        if not hasattr(self, "scope_label"):
            return
        controls = (self.query, self.query_button, self.refresh_button,
                    self.history_combo, self.delete_history)
        height = max(28, max(widget.fontMetrics().height() for widget in controls) + 6)
        for widget in controls:
            widget.setFixedHeight(height)
        self.delete_history.setFixedWidth(height)
        self.query_button.setFixedWidth(height)
        self.refresh_button.setFixedWidth(height)
        self.history_combo.setFixedWidth(height)
        self.history_combo.setToolTip("最近搜索：" + self.history_combo.currentText())
        self.query_row.setFixedHeight(height)
        self.query_options.setFixedHeight(height)
        has_options = not self.history_row.isHidden() or not self.scope_row.isHidden()
        self.query_options.setVisible(has_options)
        options_width = self.query_options.layout().minimumSize().width() if has_options else 0
        query_width = max(180, self.query.fontMetrics().horizontalAdvance("输入玩家昵称或编号") + 20)
        minimum = (query_width + self.query_button.sizeHint().width() + self.refresh_button.sizeHint().width()
                   + 12 + options_width + (10 if has_options else 0))
        one_row = self.query_toolbar.width() >= minimum
        self.toolbar_layout.setDirection(QBoxLayout.LeftToRight if one_row else QBoxLayout.TopToBottom)
        self.toolbar_layout.setSpacing(10 if one_row else 4)
        self.toolbar_layout.setStretch(0, 1 if one_row else 0)
        self.toolbar_layout.setStretch(1, 0)
        self.query_options.setSizePolicy(QSizePolicy.Maximum if one_row else QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.query_toolbar.setProperty("singleRow", one_row)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange_query_toolbar()
        self._arrange_recent_columns()

    def _arrange_recent_columns(self):
        """窄窗合并时长到地图次行，给双方名单留下真正可读的宽度。"""
        table = getattr(self, "game_table", None)
        if table is None:
            return
        try:
            available = self.scroll.viewport().width()
            table.setColumnHidden(3, available < 980)
            table.setColumnHidden(4, True)
            # 依真实文本收紧地图列；长昵称获得剩余空间，不靠缩小文字换取整齐。
            maps = [table.item(row, 0).data(MATCH_METADATA_ROLE) or {} for row in range(table.rowCount())]
            map_font, sub_font = QFontMetrics(ui_font(12)), QFontMetrics(ui_font(10))
            map_width = max([112] + [max(map_font.horizontalAdvance(map_name(row.get("map"))),
                                        sub_font.horizontalAdvance(row.get("display_mode", "") + " · " + _duration(row.get("duration")))) + 16
                                     for row in maps])
            for column, width in ((0, 78), (1, min(176, map_width)), (3, 108),
                                  (5, MATCH_RESULT_COLUMN_WIDTH), (6, MATCH_ACTION_COLUMN_WIDTH)):
                table.setColumnWidth(column, width)
        except RuntimeError:
            pass  # 换模式时旧表已经交由Qt销毁，下一张表会重新排版。

    def _arrange_player_choices(self):
        """按候选区域的实际宽度分列，最多显示三完整行，其余仍可滚动。"""
        choices = self.player_choices
        count = choices.count()
        if not count:
            return
        gap, row_height = choices.spacing(), 76
        width = choices.viewport().width()
        columns = max(1, (width + gap) // (280 + gap))
        cell_width = max(1, (width - gap * 2) // columns)
        choices.setGridSize(QSize(cell_width, row_height))
        item_size = QSize(max(1, cell_width - gap * 2), row_height - gap * 2)
        for index in range(count):
            if choices.item(index).sizeHint() != item_size:
                choices.item(index).setSizeHint(item_size)
        rows = min(3, (count + columns - 1) // columns)
        target_height = rows * row_height
        if choices.height() != target_height:
            choices.setFixedHeight(target_height)
        choices.setAccessibleDescription(f"{count} 个候选，{columns} 列。方向键选择，回车查看选中玩家。")

    def _clear_player_choices(self):
        """新查询开始即清除旧候选，避免加载期间还能选到上一轮账号。"""
        self.player_choices.blockSignals(True)
        self.player_choices.clear()
        self.player_choices.blockSignals(False)
        self.player_choices.hide()
        self.choose_button.hide()
        self._candidate_profiles = {}
        self._avatar_identity_requested.clear()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange) and hasattr(self, "scope_label"):
            QTimer.singleShot(0, self._arrange_query_toolbar)

    def open_public_replay(self, row):
        """请求主窗口分析这一场，查询条件与全部记录保留在原页面。"""
        if not self.snapshot:
            return
        self.public_match_requested.emit(self.snapshot["profile_id"], row["game_id"], dict(row))

    def _restore_history(self):
        try:
            items = json.loads((self.data_dir / "战绩搜索记录.json").read_text(encoding="utf-8"))
            if isinstance(items, list):
                for entry in items[:20]:
                    if isinstance(entry, dict) and isinstance(entry.get("query"), str):
                        try:
                            parse_player_input(entry.get("profile_id") or entry["query"])
                        except MatchError:
                            continue
                        self.history.append({"query": entry["query"][:200], "profile_id": entry.get("profile_id"),
                                             "name": str(entry.get("name") or "")[:120]})
        except (OSError, ValueError, TypeError):
            pass
        self._render_history()

    def _render_history(self):
        self.history_combo.blockSignals(True)
        self.history_combo.clear()
        for entry in self.history:
            name = entry["name"] or entry["query"]
            number = entry.get("profile_id")
            self.history_combo.addItem(f"{name} · 玩家 {number}" if number else f"搜索：{name}")
        self.history_combo.blockSignals(False)
        self.history_row.setVisible(bool(self.history))
        self._arrange_query_toolbar()

    def _write_history(self):
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            path = self.data_dir / "战绩搜索记录.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass

    def _add_history(self, query, profile_id=None, name=""):
        query = str(query).strip()
        def same_entry(entry):
            # 两个同名账号均可保留；昵称搜索条件不能覆盖已选中的另一账号。
            existing = entry.get("profile_id")
            if profile_id and existing:
                return str(existing) == str(profile_id)
            return not existing and entry["query"].casefold() == query.casefold()
        self.history = [entry for entry in self.history
                        if not same_entry(entry)]
        self.history.insert(0, {"query": query, "profile_id": profile_id, "name": str(name)})
        self.history = self.history[:20]
        self._write_history()
        self._render_history()

    def _use_history(self, index):
        if self.busy or not 0 <= index < len(self.history):
            return
        entry = self.history[index]
        self.query.setText(entry["query"])
        number = entry.get("profile_id")
        if number:
            self.player_choices.hide()
            self.choose_button.hide()
            self.notice.setText("正在读取最近搜索的玩家战绩。")
            self._job(lambda: self.client.fetch_profile(number), "profile")
        else:
            self.lookup()

    def _delete_history(self):
        index = self.history_combo.currentIndex()
        if self.busy or not 0 <= index < len(self.history):
            return
        removed = self.history.pop(index)
        self._write_history()
        self._render_history()
        # 同时清除对应的自动恢复条件，避免下次启动又显示已删除的最近条目。
        try:
            path = self.data_dir / "战绩查询.json"
            saved = json.loads(path.read_text(encoding="utf-8"))
            selected_id, removed_id = saved.get("profile_id"), removed.get("profile_id")
            if ((selected_id and removed_id and str(selected_id) == str(removed_id))
                    or (not selected_id and not removed_id and saved.get("query") == removed["query"])):
                path.write_text("{}", encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _restore_query(self):
        try:
            saved = json.loads((self.data_dir / "战绩查询.json").read_text(encoding="utf-8"))
            self.query.setText(str(saved.get("query", ""))[:200])
            number = saved.get("profile_id")
            if number:
                cached = self.client.cached_profile(number)
                if cached:
                    self._show_snapshot(cached)
        except (OSError, ValueError, TypeError, MatchError):
            pass

    def _save_query(self, profile_id=None):
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            path = self.data_dir / "战绩查询.json"
            data = {"query": self.query.text().strip(), "profile_id": profile_id}
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            # 查询与分析仍可使用，不因为辅助偏好保存失败中断操作。
            pass

    def _set_busy(self, value):
        self.busy = value
        self.query.setEnabled(not value)
        self.history_combo.setEnabled(not value)
        self.delete_history.setEnabled(not value)
        if value:
            self.notice.setStyleSheet("")
            self.notice.show()
        self.query_button.setEnabled(not value)
        self.choose_button.setEnabled(not value)
        self.refresh_button.setEnabled(not value and self.snapshot is not None)
        self.query_button.setToolTip("正在查询玩家…" if value else "查询玩家")
        self._arrange_query_toolbar()

    def _job(self, function, phase):
        if self.busy:
            return
        self._identity_generation += 1
        self._clear_player_choices()
        self._set_busy(True)
        signals = _QuerySignals()
        self._signals = signals
        signals.result.connect(self._job_result)
        signals.error.connect(self._job_error)
        signals.finished.connect(self._job_finished)

        def work():
            try:
                result = function()
                signals.result.emit(result, phase)
            except MatchError as error:
                signals.error.emit(str(error))
            except Exception:
                signals.error.emit("战绩读取失败，请稍后重试；已显示的本地记录仍可查看。")
            finally:
                signals.finished.emit()

        # daemon（守护线程）不会阻止退出软件；工作线程不直接访问任何界面控件。
        threading.Thread(target=work, name="PublicMatchQuery", daemon=True).start()

    def lookup(self):
        if self.busy:
            return
        try:
            target = parse_player_input(self.query.text())
        except MatchError as error:
            self._job_error(str(error))
            return
        if isinstance(target, int):
            self.notice.setText("正在读取公开战绩；已有缓存会保留。")
            self._job(lambda: self.client.fetch_profile(target), "profile")
        else:
            self.notice.setText("正在搜索玩家，可按头像、游戏等级与编号区分同名账号。")
            self._job(lambda: self.client.search_players(target), "search")

    def _choose_player(self, item=None):
        if self.busy:
            return
        selected = item if isinstance(item, QListWidgetItem) else self.player_choices.currentItem()
        if selected is None:
            self._job_error("请先选择一个玩家。")
            return
        number = selected.data(Qt.UserRole)
        self.player_choices.hide()
        self.choose_button.hide()
        self.notice.setText("正在读取该玩家最近的公开战绩。")
        self._job(lambda: self.client.fetch_profile(number), "profile")

    def refresh(self):
        if self.busy or not self.snapshot:
            return
        number = self.snapshot["profile_id"]
        self.notice.setText("正在刷新战绩；同一时间段内的记录会合并。")
        self._job(lambda: self.client.fetch_profile(number, refresh=True), "profile")

    def _job_result(self, result, phase):
        if phase == "search":
            self._add_history(self.query.text())
            self._clear_player_choices()
            self.player_choices.blockSignals(True)
            for position, player in enumerate(result):
                player = _merge_identity(player, self.identity_client.load_cached(player["profile_id"]))
                self._candidate_profiles[int(player["profile_id"])] = player
                item = QListWidgetItem()
                item.setData(Qt.UserRole, player["profile_id"])
                item.setData(Qt.AccessibleTextRole, str(player["name"]) + "，" + _identity_text(player))
                item.setSizeHint(QSize(200, 56))
                self.player_choices.addItem(item)
                self.player_choices.setItemWidget(item, _PlayerChoice(player, self.data_dir,
                    allow_network=position < 10))
            if result:
                self.player_choices.setCurrentRow(0)
                self.player_choices.show()
                self._arrange_player_choices()
                self.choose_button.show()
                self.notice.setText(f"找到 {len(result)} 个玩家 · 方向键选择，回车或双击查看")
            else:
                self.notice.setText("没有找到该昵称。请核对名称，或粘贴个人主页链接。")
            if self.snapshot is None:
                self.empty.setVisible(not bool(result))
            self.player_choices.blockSignals(False)
            self._start_identities([player["profile_id"] for player in result[:10]])
        else:
            self._show_snapshot(result)
            self._save_query(result["profile_id"])
            self._add_history(self.query.text(), result["profile_id"], result["profile"].get("name", ""))
            self._start_identities([result["profile_id"]], include_avatar=True)

    def _candidate_selected(self, current, previous=None):
        if current is not None and current.data(Qt.UserRole) not in self._avatar_identity_requested:
            self._start_identities([current.data(Qt.UserRole)], include_avatar=True)

    def _refresh_visible_candidates(self, *args):
        """只为屏幕内候选补头像地址；滚动后再加载新出现的行。"""
        if self.player_choices.isHidden():
            return
        viewport = self.player_choices.viewport().rect()
        for index in range(self.player_choices.count()):
            item = self.player_choices.item(index)
            if not self.player_choices.visualItemRect(item).intersects(viewport):
                continue
            number = item.data(Qt.UserRole)
            profile = self._candidate_profiles.get(number, {})
            if profile.get("steam_id") and number not in self._avatar_identity_requested:
                self._start_identities([number], include_avatar=True)

    def _start_identities(self, profile_ids, *, include_avatar=False):
        """只补当前搜索前十项或明确选中项；启动恢复缓存不触发联网。"""
        if not profile_ids:
            return
        if include_avatar:
            self._avatar_identity_requested.add(profile_ids[0])
        generation, signals = self._identity_generation, self._identity_signals
        client = self.identity_client
        def work():
            try:
                values = ([client.fetch_identity(profile_ids[0], include_avatar=True)] if include_avatar else
                          client.fetch_identities(profile_ids))
            except (IdentityError, OSError, ValueError):
                return  # 基础搜索仍可选，未取得的等级明确显示未提供。
            if isinstance(values, dict):
                values = list(values.values())
            try:
                signals.ready.emit(values, generation)
            except RuntimeError:
                pass  # 页面销毁后不再回传；后台任务不会阻止程序退出。
        threading.Thread(target=work, name="PlayerIdentity", daemon=True).start()

    def _identities_ready(self, identities, generation):
        if generation != self._identity_generation:
            return
        for identity in identities:
            number = identity.get("profile_id")
            if number in self._candidate_profiles:
                profile = _merge_identity(self._candidate_profiles[number], identity)
                self._candidate_profiles[number] = profile
                for index in range(self.player_choices.count()):
                    item = self.player_choices.item(index)
                    if item.data(Qt.UserRole) == number:
                        self.player_choices.itemWidget(item).update_profile(profile)
                        item.setData(Qt.AccessibleTextRole, profile["name"] + "，" + _identity_text(profile))
            if self.snapshot and self.snapshot["profile_id"] == number and hasattr(self, "profile_avatar"):
                profile = _merge_identity(self.snapshot["profile"], identity)
                self.snapshot["profile"] = profile
                self.profile_avatar.set_profile(profile, self.data_dir)
                self.profile_header_name.setText(str(profile.get("name") or "未提供昵称"))
                self.profile_header_name.setToolTip(self.profile_header_name.text())
                self.profile_header_name.setAccessibleName(self.profile_header_name.text())
                self.profile_meta.setText(_identity_text(profile))
                self.profile_meta.setToolTip(self.profile_meta.text())
                self.profile_rank_summary.set_profile(profile)
        self._refresh_visible_candidates()

    def _job_error(self, message):
        self.notice.setText(message)
        self.notice.setStyleSheet("color:#e6b8ad;")
        self.notice.show()

    def _job_finished(self):
        self._set_busy(False)

    def _clear_body(self):
        _clear_layout(self.body_layout)

    def _show_snapshot(self, snapshot):
        self.snapshot = snapshot
        self.report = analyze_games(snapshot["games"], snapshot["profile_id"])
        self._clear_body()
        self.notice.setStyleSheet("")
        warning = snapshot.get("warning", "")
        self.notice.setText(warning)
        self.notice.setVisible(bool(warning))
        if warning:
            self.notice.setStyleSheet("color:#e6b8ad;")
        top = QHBoxLayout()
        top.setSpacing(10)
        profile = _merge_identity(snapshot["profile"], self.identity_client.load_cached(snapshot["profile_id"]))
        player_name = str(profile.get("name") or f"玩家 {snapshot['profile_id']}")
        self.profile_avatar = PlayerAvatar(size=40)
        self.profile_avatar.set_profile(profile, self.data_dir, allow_network=False)
        top.addWidget(self.profile_avatar, 0, Qt.AlignVCenter)
        identity = QVBoxLayout()
        identity.setSpacing(4)
        identity.setAlignment(Qt.AlignVCenter)
        # 两行按内容高度成组居中；右侧两行段位不再把姓名与资料各自拉高。
        title = _PlayerName(player_name)
        title.setObjectName("SectionTitle")
        title.setTextFormat(Qt.PlainText)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        title.setToolTip(player_name)
        title.setAccessibleName(player_name)
        self.profile_header_name = title
        identity.addWidget(title)
        self.profile_meta = _text(_identity_text(profile), "Muted")
        self.profile_meta.setWordWrap(False)
        self.profile_meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.profile_meta.setToolTip(self.profile_meta.text())
        identity.addWidget(self.profile_meta)
        top.addLayout(identity, 1)
        self.profile_rank_summary = ProfileRankSummary(profile)
        top.addWidget(self.profile_rank_summary, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.body_layout.addLayout(top)
        scope = f"最近 {len(snapshot['games'])} 场样本 · 非赛季累计"
        self.scope_label.setText(f"近 {len(snapshot['games'])} 场样本")
        self.scope_label.setToolTip(scope)
        self.scope_label.setAccessibleName(scope)
        self.scope_row.show()
        self._arrange_query_toolbar()
        fetched = _time(snapshot.get("fetched_at"), full=True)
        origin = "本地缓存" if snapshot.get("source") == "cache" else "网络记录"
        self.refresh_button.setToolTip(f"重新读取该玩家的公开战绩。\n当前{origin}获取于 {fetched}。")
        self.detail = QWidget()
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_layout.setSpacing(14)
        self.detail_layout.setAlignment(Qt.AlignTop)
        self.body_layout.addWidget(self.detail)
        self._render_mode()
        excluded = sum(self.report["skipped"].values())
        if excluded:
            reasons = "、".join(f"{key} {value} 场" for key, value in self.report["skipped"].items())
            self.body_layout.addWidget(_text("统计已排除：" + reasons + "。", "Muted"))
        source_link = _text(f'<a style="color:#b8bac2" href="{API_SOURCE}">数据来源与接口说明 ↗</a>', "Muted")
        source_link.setOpenExternalLinks(True)
        self.body_layout.addWidget(source_link)
        self.body_layout.addStretch()
        self.refresh_button.setEnabled(not self.busy)
        self.scroll.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def _render_mode(self, *args):
        if not hasattr(self, "detail_layout"):
            return
        self._arrange_query_toolbar()
        _clear_layout(self.detail_layout)
        code = None  # 界面固定全部模式，旧查询记录中的任何模式字段均不参与过滤。
        info = self.report["by_mode"].get(code)
        rows = [row for row in self.report["rows"] if code is None or row["mode"] == code]
        if not rows:
            self.detail_layout.addWidget(_text("暂无可分析的已完成公开对局。请稍后刷新，或确认玩家编号。", "Muted"))
            if code is None:
                self._render_official_overview()
            return
        if code is None:
            # 全部模式直接展示各模式卡片；场数已在卡片和固定筛选栏中显示。
            groups = []
            for current in self.report["modes"]:
                summary = self.report["by_mode"][current]
                groups.append({"code": current, "name": mode_name(current), **summary,
                               "rows": [row for row in self.report["rows"] if row["mode"] == current]})
            self.mode_cards = ModeSummaryGrid(groups)
            self.detail_layout.addWidget(self.mode_cards)
        else:
            self._render_metrics(info)
        self._render_recent_games(rows, code)
        if info is not None:
            self._render_groups(info)
        else:
            self._render_official_overview()
        self.scroll.verticalScrollBar().setValue(0)

    def _render_official_overview(self):
        """官网的赛季或模式累计放在最近记录之后，避免与近期样本混用。"""
        overview = profile_overview(self.snapshot["profile"])
        if not overview:
            return
        heading = _text("官方总览", "SectionTitle")
        heading.setToolTip("服务器提供的赛季或模式累计；与最近公开记录分别统计。")
        self.detail_layout.addWidget(heading)
        values = []
        for row in overview:
            count = "未提供场数" if row["games"] is None else f"{row['games']} 场"
            if row["wins"] is not None and row["losses"] is not None:
                count += f" · {row['wins']} 胜 {row['losses']} 负"
            rate = "未提供" if row["win_rate"] is None else f"{row['win_rate']:.1f}%"
            rating = "未提供" if row["rating"] is None else f"{row['rating']:g}\n{row['rating_label']}"
            values.append([row["name"], row["scope"], count, rate, rating])
        self.overview_table = self._table(["模式", "范围", "场数 / 胜负", "胜率", "评分"], values, 44)
        self.overview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in ((1, 104), (2, 178), (3, 72), (4, 100)):
            self.overview_table.setColumnWidth(column, width)
        self.detail_layout.addWidget(self.overview_table)

    def _render_metrics(self, info):
        """单个模式保留经济简表，明确统计范围。"""
        metrics = QWidget()
        metric_row = QHBoxLayout(metrics)
        metric_row.setContentsMargins(0, 0, 0, 0)
        metric_row.setSpacing(10)
        values = [("样本", f"{info['count']} 场", f"{info['wins']} 胜 · {info['losses']} 负"),
                  ("胜率", f"{info['win_rate']:.1f}%", "仅当前模式"),
                  ("中位时长", _duration(info["median_duration"]), f"{info['duration_count']} 场提供时长")]
        for heading, value, caption in values:
            card = QFrame()
            card.setObjectName("Card")
            col = QVBoxLayout(card)
            col.setContentsMargins(14, 12, 14, 12)
            col.setSpacing(4)
            col.addWidget(_text(heading, "Muted"))
            number = _text(value)
            number.setStyleSheet("font-size:21px;font-weight:600;")
            col.addWidget(number)
            col.addWidget(_text(caption, "Muted"))
            metric_row.addWidget(card, 1)
        self.detail_layout.addWidget(metrics)

    def _render_groups(self, info):
        self.detail_layout.addWidget(_text("文明与地图", "SectionTitle"))
        groups = []
        for key, heading, namer in (("civilizations", "文明", civilization_name), ("maps", "地图", map_name)):
            for group in info[key]:
                groups.append([heading, namer(group["key"]), f"{group['count']} 场",
                               f"{group['wins']} 胜 {group['losses']} 负", f"{group['win_rate']:.1f}%"])
        self.group_table = self._table(["分类", "名称", "样本", "胜负", "胜率"], groups, 36)
        self.group_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for column, width in ((0, 50), (2, 64), (3, 86), (4, 72)):
            self.group_table.setColumnWidth(column, width)
        self.detail_layout.addWidget(self.group_table)

    def _render_recent_games(self, rows, code):
        self.recent_panel = RoundedMatchPanel()
        self.recent_panel.setObjectName("RecentMatchesPanel")
        self.recent_panel.setStyleSheet("QFrame#RecentMatchesPanel{background:#161619;border:0;border-radius:12px;}")
        self.recent_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        recent_layout = QVBoxLayout(self.recent_panel)
        recent_layout.setContentsMargins(0, 0, 0, 0)
        recent_layout.setSpacing(0)
        self.recent_heading = _text("最近对局", "SectionTitle")
        self.recent_heading.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.recent_heading.setContentsMargins(14, 12, 14, 12)
        recent_layout.addWidget(self.recent_heading)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background:#252529;border:0;")
        recent_layout.addWidget(divider)
        game_values = []
        for row in rows:
            delta = row["strength_delta"]
            strength = "评分未提供" if delta is None else f"对手队伍均分{'高' if delta >= 0 else '低'} {abs(delta):.0f}"
            time_text = _time(row["started_at"])
            if code is None:
                time_text += "\n" + mode_name(row["mode"])
            game_values.append([time_text,
                                map_name(row["map"]) + "\n" + civilization_name(row["civilization"]),
                                ("、".join(row["opponents"]) or "未提供对手") + "\n" + strength,
                                ranked_result(row)["text"], _duration(row["duration"]),
                                "胜利" if row["result"] == "win" else "失利", ""])
        self.game_table = self._table(["日期", "地图 / 模式 / 时长", "ELO / 玩家 / ELO / 对手", "赛后段位", "时长", "结果", "分析"], game_values, 56, action_column=6)
        self.game_table.horizontalHeaderItem(2).setToolTip("ELO：公开匹配分，用于说明匹配强度；与赛后排位积分分开。未提供时显示—。")
        # 只通过对局编号读取同场原资料；玩家归属仅用profile_id，不用同名昵称。
        source_games = {str(game.get("game_id")): game for game in self.snapshot.get("games", []) if isinstance(game, dict)}
        display_rows = []
        for row in rows:
            view_row = {**row, "display_mode": mode_name(row["mode"]),
                        "focus_profile_id": self.snapshot["profile_id"]}
            raw_game = source_games.get(str(row["game_id"]), {})
            teams = [[dict(entry.get("player", entry)) for entry in team if isinstance(entry, dict)
                      and isinstance(entry.get("player", entry), dict)]
                     for team in (raw_game.get("teams") or []) if isinstance(team, list)]
            own_index = next((number for number, team in enumerate(teams)
                              if any(str(player.get("profile_id")) == str(self.snapshot["profile_id"]) for player in team)), None)
            if own_index is not None:
                people = [player for team in teams for player in team]
                for player, caption in zip(people, player_display_labels(people, id_key="profile_id")):
                    player["display_name"] = caption
                view_row["display_teams"] = [
                    {"label": "我方", "players": teams[own_index]},
                    {"label": "对手" if len(teams) == 2 else "其他玩家",
                     "players": [player for number, team in enumerate(teams) if number != own_index for player in team]},
                ]
            display_rows.append(view_row)
        # 每行保留对局编号，便于在模式切换与数据刷新测试中核对记录。
        for index, row in enumerate(rows):
            self.game_table.item(index, 0).setData(Qt.UserRole, row["game_id"])
        self.game_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for column, width in ((0, 94), (1, 154), (3, 112), (4, 50),
                              (5, MATCH_RESULT_COLUMN_WIDTH), (6, MATCH_ACTION_COLUMN_WIDTH)):
            self.game_table.setColumnWidth(column, width)
        for index, row in enumerate(rows):
            color = CARD_WIN if row["result"] == "win" else CARD_LOSS
            self.game_table.item(index, 5).setForeground(QColor(color))
            self.game_table.item(index, 3).setToolTip(ranked_result(row)["tooltip"])
            delta = row["strength_delta"]
            if delta is None:
                strength = "双方评分不完整，无法比较对手强弱。"
            else:
                method = "匹配分（MMR）" if row["strength_kind"] == "mmr" else "排位积分"
                strength = f"按双方队伍平均{method}比较：对手{'高' if delta >= 0 else '低'} {abs(delta):.0f} 分。"
            members = display_rows[index].get("display_teams", [])
            team_tooltip = "\n".join(team["label"] + "：" + "、".join(
                player["display_name"] + f"（账号{player.get('profile_id', '未提供')}）" for player in team["players"])
                                     for team in members)
            self.game_table.item(index, 2).setToolTip((team_tooltip or "、".join(row["opponents"])) + "\n" + strength)
            actions = QWidget()
            action_row = QHBoxLayout(actions)
            action_row.setContentsMargins(MATCH_ACTION_PADDING, 0, MATCH_ACTION_PADDING, 0)
            action_row.setSpacing(0)
            analyze = _button("↗", lambda checked=False, match=row: self.open_public_replay(match))
            analyze.setObjectName("GetMatchReplay")
            analyze.setFixedSize(MATCH_ACTION_SIZE)
            analyze.setAccessibleName("对局分析")
            analyze.setFocusPolicy(Qt.StrongFocus)
            analyze.setStyleSheet("QPushButton{padding:0;border:1px solid transparent;border-radius:6px;"
                                 "background:transparent;color:#a4a4ad;font-size:16px;}"
                                 "QPushButton:hover{background:#303036;color:white;}"
                                 "QPushButton:focus{border-color:#929299;color:white;}")
            analyze.setToolTip("对局分析\n自动获取这场公开回放并在软件内分析。")
            action_row.addWidget(analyze, 0, Qt.AlignCenter)
            self.game_table.setCellWidget(index, 6, actions)
        style_recent_matches(self.game_table, display_rows)
        self.game_table.setColumnWidth(6, MATCH_ACTION_COLUMN_WIDTH)
        self.game_table.horizontalHeader().moveSection(1, 0)
        recent_layout.addWidget(self.game_table)
        self.detail_layout.addWidget(self.recent_panel)
        self._arrange_recent_columns()
        QTimer.singleShot(0, self._arrange_recent_columns)

    def _table(self, headers, values, row_height, *, action_column=None):
        table_type = RoundedMatchTable if action_column is not None else QTableWidget
        table = table_type(len(values), len(headers))
        if action_column is not None:
            table.setHorizontalHeader(MatchActionHeader(action_column, table))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().hide()
        table.setFrameShape(QFrame.NoFrame)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setShowGrid(False)
        table.setItemDelegate(_ReadableTableDelegate(table))
        palette = table.palette()
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            palette.setColor(group, QPalette.Text, QColor("#f4f4f5"))
            palette.setColor(group, QPalette.HighlightedText, QColor("#f4f4f5"))
            palette.setColor(group, QPalette.Highlight, QColor("#232328"))
        table.setPalette(palette)
        table.setWordWrap(False)
        table.horizontalHeader().setHighlightSections(False)
        table.horizontalHeader().setMinimumSectionSize(44)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.horizontalHeader().setFixedHeight(30)
        table.setStyleSheet("QTableWidget {background:#161619;alternate-background-color:#161619;border:0;border-radius:10px;outline:0;}"
                            "QTableWidget::item {padding:6px 8px;border-bottom:1px solid #252529;}"
                            "QTableWidget::item:selected {background:#232328;}"
                            "QHeaderView::section {background:#161619;color:#73737d;border:0;padding:8px;}"
                            "QTableCornerButton::section {background:#161619;border:0;}")
        table.setAlternatingRowColors(False)
        for index, row in enumerate(values):
            table.setRowHeight(index, row_height)
            for column, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                item.setForeground(QColor("#f4f4f5"))
                item.setToolTip(str(value))
                table.setItem(index, column, item)
        table.setFixedHeight(32 + row_height * len(values))
        table.setMinimumWidth(0)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return table

