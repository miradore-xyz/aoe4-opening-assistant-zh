"""公开战绩的紧凑展示组件。

ModeSummaryGrid=分模式样本卡片，RecentMatchDelegate=对局行绘制器。
所有数值由调用方提供；这里只排版，不合并模式、不补造评分。
"""
from __future__ import annotations

import math
import re
from datetime import datetime

from PyQt5.QtCore import QEvent, Qt, QPointF, QRectF, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QBrush, QFont, QFontMetrics, QFontMetricsF, QPainter, QPainterPath, QPen, QPalette
from PyQt5.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy, QStyle,
    QStyleOptionHeader, QStyledItemDelegate, QTableWidget, QToolTip, QVBoxLayout, QWidget,
)

from visual import CARD_GAP, civilization_flag, ui_font
from rank_display import ranked_result, rank_badge
from rounded_corners import make_corner_covers, position_corner_covers


MATCH_METADATA_ROLE = Qt.UserRole + 1
CARD_HEIGHT = 132
MATCH_ROW_HEIGHT = 56
# 结果槽和单一箭头入口各自保持明确命中区，列宽随入口收紧。
MATCH_CONTROL_SIZE = QSize(44, 26)
MATCH_ACTION_SIZE = QSize(28, 28)
MATCH_ACTION_PADDING = 8
MATCH_RESULT_PADDING = 6
MATCH_RESULT_COLUMN_WIDTH = MATCH_CONTROL_SIZE.width() + MATCH_RESULT_PADDING * 2
MATCH_ACTION_COLUMN_WIDTH = MATCH_ACTION_PADDING * 2 + MATCH_ACTION_SIZE.width()
# 视觉首列仅增加左侧留白；地图表头、地图名与模式/时长共用16像素起点。
MATCH_MAP_LEFT_PADDING = 16
MATCH_RANK_COLUMN_WIDTH = 80  # 原色段位徽章与本局有符号积分变化共用紧凑槽。
# 双方名单与表头共用这些槽位，避免各自计算产生横向偏移。
MATCH_TEAM_MAX_WIDTH = 205  # 每队保留135像素名字槽，长名省略，宽窗不继续拉开两队。
MATCH_GROUP_GAP = 16  # 七个阅读组使用同一最小间距，宽窗余量均分到六处组间隙。
MATCH_PLAYER_HEIGHT = 19
MATCH_PLAYER_PITCH = 21
MATCH_SCORE_WIDTH = 32
MATCH_FLAG_OFFSET = 40
MATCH_NAME_OFFSET = 70
INK = "#f4f4f5"
MUTED = "#9da6b0"
WIN = "#51c283"
LOSS = "#e3958b"
# 模式卡局部色阶，不改真实胜负值，也不改变其他页面的业务颜色。
CARD_SURFACE = "#161619"
CARD_AUX = "#73737d"
CARD_MODE = "#a4a4ad"
CARD_WIN = "#17b85b"
CARD_LOSS = "#ef4d4d"
CHART_WIN = "#45ad78"  # 小图保留低饱和度；名单与胜负数字使用明确语义色。
CHART_LOSS = "#cf7771"

# 网站使用小写文明代码；旗帜资源沿用原开局格式的英文全名。
_CIVILIZATION_FLAGS = {
    "abbasid_dynasty": "Abbasid Dynasty", "ayyubids": "Ayyubids",
    "byzantines": "Byzantines", "chinese": "Chinese",
    "delhi_sultanate": "Delhi Sultanate", "english": "English",
    "french": "French", "golden_horde": "Golden Horde",
    "holy_roman_empire": "Holy Roman Empire", "house_of_lancaster": "House of Lancaster",
    "japanese": "Japanese", "jeanne_darc": "Jeanne d'Arc", "jin_dynasty": "Jin Dynasty",
    "knights_templar": "Knights Templar", "macedonian_dynasty": "Macedonian Dynasty",
    "malians": "Malians", "mongols": "Mongols", "order_of_the_dragon": "Order of the Dragon",
    "ottomans": "Ottomans", "rus": "Rus", "sengoku_daimyo": "Sengoku Daimyo",
    "tughlaq_dynasty": "Tughlaq Dynasty", "zhu_xis_legacy": "Zhu Xi's Legacy",
}


def match_team_columns(rect):
    """名单使用有限名字槽；宽窗中的两队间隙与整表其他组一致。"""
    rect = QRectF(rect)
    gap = max(MATCH_GROUP_GAP, rect.width() - MATCH_TEAM_MAX_WIDTH * 2)
    width = max(0, min(MATCH_TEAM_MAX_WIDTH, (rect.width() - gap) / 2))
    return tuple(QRectF(rect.left() + number * (width + gap),
                        rect.top(), width, rect.height()) for number in range(2))


def arrange_recent_match_columns(table, map_preferred_width):
    """按七个内容组统一分配整表宽度；不存在独吞剩余宽度的伸展列。"""
    available = table.viewport().width()
    if available <= 0:
        return
    map_width = max(88, min(108 if available < 980 else 152, map_preferred_width))
    fixed_width = map_width + 52 + 64 + 44 + 28
    inner_width = max(0, available - MATCH_MAP_LEFT_PADDING * 2)
    gap = max(MATCH_GROUP_GAP, (inner_width - fixed_width - MATCH_TEAM_MAX_WIDTH * 2) / 6)
    team_width = max(0, min(MATCH_TEAM_MAX_WIDTH, (inner_width - fixed_width - gap * 6) / 2))
    names = ("map", "date", "players", "opponents", "rank", "result", "action")
    widths = (map_width, 52, team_width, team_width, 64, 44, 28)
    groups = {}
    cursor = float(MATCH_MAP_LEFT_PADDING)
    for name, width in zip(names, widths):
        groups[name] = (cursor, width)
        cursor += width + gap
    # 物理列边界放在相邻组之间；双方名单仍在同一模型列中，保留原编号和信号。
    borders = [0] + [round(groups[name][0] + groups[name][1] + gap / 2)
                     for name in ("map", "date", "opponents", "rank", "result")] + [available]
    columns = (1, 0, 2, 3, 5, 6)
    content = {}
    for number, (column, name) in enumerate(zip(columns, ("map", "date", "players", "rank", "result", "action"))):
        cell_width = borders[number + 1] - borders[number]
        start, width = groups[name]
        if column == 2:
            width = team_width * 2 + gap
        inset = start - borders[number]
        if column in (0, 3, 5):
            inset = (cell_width - width) / 2
        elif column == 6:
            inset = round(inset)
        content[column] = (inset, width)
        table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Fixed)
        table.setColumnWidth(column, cell_width)
    table.match_column_content = content
    table.match_group_gap = gap
    table.match_map_preferred_width = map_preferred_width
    # 实际按钮与分析表头共用最后一个内容槽，跟随列宽变化而非固定左内距。
    for row in range(table.rowCount()):
        action = table.cellWidget(row, 6)
        if action is not None and action.layout() is not None:
            left = round(content[6][0])
            action.layout().setContentsMargins(left, 0, table.columnWidth(6) - left - MATCH_ACTION_SIZE.width(), 0)
    table.viewport().update()
    table.horizontalHeader().viewport().update()


def match_cell_content_rect(table, column, rect):
    """表头、正文和名字命中读取同一份实际内容槽，列宽取整不会产生各自偏移。"""
    rect = QRectF(rect)
    content = getattr(table, "match_column_content", {})
    if column in content:
        inset, width = content[column]
        return QRectF(rect.left() + inset, rect.top(), width, rect.height())
    return match_map_content_rect(rect) if column == 1 else rect.adjusted(8, 0, -8, 0)


def match_map_content_rect(rect):
    """地图列的统一内容槽；不改变双方名单及名字双击的几何基准。"""
    return QRectF(rect).adjusted(MATCH_MAP_LEFT_PADDING, 0, -8, 0)


def match_player_rect(area, index, count):
    """整组玩家以行中心为轴；单人不会因为队伍标题而向下偏移。"""
    height = MATCH_PLAYER_HEIGHT + max(0, count - 1) * MATCH_PLAYER_PITCH
    return QRectF(area.left(), area.center().y() - height / 2 + index * MATCH_PLAYER_PITCH,
                  area.width(), MATCH_PLAYER_HEIGHT)


def _label(text, size=12, color=INK, weight=QFont.Normal):
    label = QLabel(str(text))
    label.setTextFormat(Qt.PlainText)
    label.setFont(ui_font(size, weight))
    css_weight = 600 if weight >= QFont.DemiBold else 400
    label.setStyleSheet(f"color:{color};background:transparent;border:0;padding:0;"
                       f"font-size:{size}px;font-weight:{css_weight};")
    return label


def _valid_duration(value):
    """公开资料以秒为单位；缺失、非数或非正时长不转换成零分钟。"""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else None


def _duration_label(value):
    seconds = _valid_duration(value)
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds is not None else "时长未提供"


class RecentDurationChart(QWidget):
    """逐场时长小柱图：左旧右新、绿胜红负，所有模式使用相同纵向尺度。"""

    def __init__(self, rows, duration_max=None, sample_count=None, parent=None):
        super().__init__(parent)
        # 输入沿用 normalize_games（对局整理函数）的新→旧顺序，不重新抽样。
        self.rows = [dict(row) for row in reversed(rows)]
        self.sample_count = len(self.rows) if sample_count is None else sample_count
        self.duration_max = _valid_duration(duration_max)
        if self.duration_max is None:
            self.duration_max = max((_valid_duration(row.get("duration")) or 0 for row in self.rows), default=0) or None
        self.hovered_index = None
        self.setFixedHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setAccessibleName(f"最近{self.sample_count}场对局时长，左旧右新，绿色胜利、红色失利")
        scale = _duration_label(self.duration_max)
        self.setToolTip(f"每柱一场，左旧右新；各模式共用时长上限 {scale}。空心灰框表示时长未提供。")

    def plot_rect(self):
        # 按真实中文字体高度预留底栏，并给横向基线与场数标签留出9逻辑像素。
        caption_height = math.ceil(QFontMetricsF(ui_font(9)).height())
        return QRectF(0, 0, max(0, self.width()), max(0, self.height() - caption_height - 9))

    def caption_rect(self):
        """场数标签的完整绘制区域；不占用柱形高度或贴住横向基线。"""
        plot = self.plot_rect()
        return QRectF(0, plot.bottom() + 9, self.width(), max(0, self.height() - plot.bottom() - 9))

    def bar_rect(self, index):
        """已知时长按比例绘制；未知值没有柱高，保留其横向位置。"""
        if not 0 <= index < len(self.rows) or self.duration_max is None:
            return None
        duration = _valid_duration(self.rows[index].get("duration"))
        if duration is None:
            return None
        plot = self.plot_rect()
        slot = plot.width() / len(self.rows)
        width = min(8.0, slot * .75)
        height = plot.height() * min(duration / self.duration_max, 1)
        return QRectF(plot.left() + slot * (index + .5) - width / 2, plot.bottom() - height, width, height)

    def index_at(self, position):
        """按最近横向槽位命中，窄卡的柱间空隙也能查看，不舍弃任何场次。"""
        plot = self.plot_rect()
        if not self.rows or plot.width() <= 0 or not plot.contains(position):
            return None
        return min(len(self.rows) - 1, max(0, int((position.x() - plot.left()) * len(self.rows) / plot.width())))

    def match_tooltip(self, index):
        row = self.rows[index]
        result = "胜利" if row.get("result") == "win" else "失利" if row.get("result") == "loss" else "结果未提供"
        started = str(row.get("started_at") or "")
        try:
            date = datetime.fromisoformat(started.replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
        except (ValueError, TypeError):
            date = started or "时间未提供"
        identity = f"对局 {row['game_id']} · " if row.get("game_id") is not None else ""
        return f"{identity}{date}\n{result} · {_duration_label(row.get('duration'))}"

    def _hover(self, position, global_position):
        index = self.index_at(position)
        if index != self.hovered_index:
            self.hovered_index = index
            self.update()
        if index is not None:
            QToolTip.showText(global_position, self.match_tooltip(index), self)
        else:
            QToolTip.hideText()

    def mouseMoveEvent(self, event):
        self._hover(event.pos(), event.globalPos())
        super().mouseMoveEvent(event)

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            self._hover(event.pos(), event.globalPos())
            return True
        return super().event(event)

    def leaveEvent(self, event):
        self.hovered_index = None
        self.update()
        QToolTip.hideText()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        plot = self.plot_rect()
        painter.setPen(QPen(QColor("#2b2b30"), 1))
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        for index, row in enumerate(self.rows):
            bar = self.bar_rect(index)
            if bar is not None:
                color = QColor(CHART_WIN if row.get("result") == "win" else CHART_LOSS)
                # 小图只交代逐场分布，悬停时再强调当前场，避免彩柱抢过数字。
                color.setAlpha(235 if self.hovered_index == index else 150)
                painter.fillRect(bar, color)
                if self.hovered_index == index and bar.width() >= 1 and bar.height() >= 1:
                    painter.setPen(QPen(QColor("#edf2f5"), 1))
                    painter.drawRect(bar.adjusted(.5, .5, -.5, -.5))
            else:
                slot = plot.width() / len(self.rows)
                center = plot.left() + slot * (index + .5)
                side = min(4.0, slot * .7)
                painter.setPen(QPen(QColor(CARD_AUX), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(QRectF(center - side / 2, plot.center().y() - side / 2, side, side))
        painter.setFont(ui_font(9))
        painter.setPen(QColor(CARD_AUX))
        caption = f"最近{self.sample_count}场" + (" · 时长未提供" if self.sample_count and self.duration_max is None else "")
        footer = self.caption_rect()
        painter.drawText(footer, Qt.AlignLeft | Qt.AlignVCenter, caption)
        painter.end()


class ModeSummaryCard(QFrame):
    """一个模式、一套统计口径；summary 保存调用方的完整原数据。"""

    def __init__(self, summary, parent=None, *, duration_max=None):
        super().__init__(parent)
        self.summary = dict(summary)
        name = str(summary.get("name") or "未提供模式")
        count = summary.get("count", 0)
        wins, losses = summary.get("wins", 0), summary.get("losses", 0)
        rate = summary.get("win_rate")
        rate_text = f"{rate:.1f}%" if isinstance(rate, (int, float)) and math.isfinite(rate) else "—"
        parts = re.match(r"^(快速|排位)\s+(.+)$", name)
        heading, mode = (parts[1] + "匹配", parts[2]) if parts else ("近期战绩", name)
        self.setObjectName("ModeSummaryCard")
        self.setFixedHeight(CARD_HEIGHT)
        self.setMinimumWidth(154)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("QFrame#ModeSummaryCard{background:#161619;border:1px solid #1c1c20;border-radius:12px;}")
        self.setAccessibleName(f"{name}，最近样本 {count} 场，{wins} 胜 {losses} 负，胜率 {rate_text}")
        self.setToolTip(self.accessibleName())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 11, 13, 9)
        layout.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.heading_label = _label(heading, 12, INK, QFont.DemiBold)
        top.addWidget(self.heading_label, 1)
        self.wins_label = _label(f"{wins}胜", 11, CARD_WIN)
        self.losses_label = _label(f"{losses}负", 11, CARD_LOSS)
        top.addWidget(self.wins_label)
        top.addWidget(self.losses_label)
        layout.addLayout(top)
        middle = QGridLayout()
        middle.setHorizontalSpacing(15)
        middle.setVerticalSpacing(1)
        self.mode_caption = _label("模式", 9, CARD_AUX)
        self.rate_caption = _label("胜率", 9, CARD_AUX)
        self.name_label = _label(mode, 11, CARD_MODE)
        self.name_label.setToolTip(name)
        self.rate_label = _label(rate_text, 16, INK, QFont.DemiBold)
        self.rate_label.setFont(ui_font(16, QFont.DemiBold, numeric=True))
        middle.addWidget(self.mode_caption, 0, 0)
        middle.addWidget(self.rate_caption, 0, 1)
        middle.addWidget(self.name_label, 1, 0, Qt.AlignLeft | Qt.AlignVCenter)
        middle.addWidget(self.rate_label, 1, 1, Qt.AlignLeft | Qt.AlignVCenter)
        middle.setColumnStretch(2, 1)
        layout.addLayout(middle)
        self.duration_chart = RecentDurationChart(summary.get("rows", []), duration_max, count, self)
        layout.addWidget(self.duration_chart)

    def minimumSizeHint(self):
        # 长模式名（如主机模式）保留完整文本，自适应网格不会压扁卡片。
        return QSize(max(154, self.layout().minimumSize().width()), CARD_HEIGHT)


class ModeSummaryGrid(QWidget):
    """分模式卡片网格；主窗口四列，更窄的空间按文字尺寸减少列数。

    items=输入的模式字典列表，cards=对应的可见卡片，set_modes=更新展示。
    """

    def __init__(self, items=None, parent=None):
        super().__init__(parent)
        self.items = []
        self.cards = []
        self.column_count = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(CARD_GAP)
        self._grid.setVerticalSpacing(CARD_GAP)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_modes(items or [])

    def set_modes(self, items):
        self.items = [dict(item) for item in items]
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        duration_max = max((_valid_duration(row.get("duration")) or 0
                            for item in self.items for row in item.get("rows", [])), default=0) or None
        self.cards = [ModeSummaryCard(item, self, duration_max=duration_max) for item in self.items]
        self.column_count = 0
        self._reflow()

    def _reflow(self):
        width = max(0, self.width())
        preferred = 4 if width >= 646 else 3 if width >= 482 else 2 if width >= 318 else 1
        columns = min(preferred, len(self.cards)) or 1
        # 以真实中文文本尺寸校验列数，避免主机等较长模式名被挤掉。
        minimum = max((card.minimumSizeHint().width() for card in self.cards), default=154)
        while columns > 1 and columns * minimum + (columns - 1) * CARD_GAP > width:
            columns -= 1
        if columns != self.column_count:
            while self._grid.count():
                self._grid.takeAt(0)
            for number, card in enumerate(self.cards):
                self._grid.addWidget(card, number // columns, number % columns)
            for column in range(4):
                self._grid.setColumnStretch(column, 1 if column < columns else 0)
            self.column_count = columns
        rows = math.ceil(len(self.cards) / columns)
        height = rows * CARD_HEIGHT + max(0, rows - 1) * CARD_GAP
        self.setFixedHeight(height)
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def minimumSizeHint(self):
        return QSize(max((card.minimumSizeHint().width() for card in self.cards), default=0), self.height())

class RoundedMatchPanel(QFrame):
    """标题、表头、正文共用平滑外缘，不用硬多边形裁剪曲线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._corners = make_corner_covers(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        position_corner_covers(self, self._corners)

    def showEvent(self, event):
        super().showEvent(event)
        position_corner_covers(self, self._corners)


class RoundedMatchTable(QTableWidget):
    """独立表格使用平滑圆角；一体卡片内的表格不再额外挖去四角。"""

    matchActivated = pyqtSignal(object)  # 对局记录：保留原查询焦点、game_id与同场上下文。
    playerActivated = pyqtSignal(object)  # 玩家记录：只由名字文字命中触发，编号可缺失但不猜测。

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._corners = make_corner_covers(self)

    def _update_corners(self):
        nested = isinstance(self.parentWidget(), RoundedMatchPanel)
        for corner in self._corners:
            corner.setVisible(not nested)
        position_corner_covers(self, self._corners)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_corners"):
            self._update_corners()
        if hasattr(self, "match_map_preferred_width"):
            arrange_recent_match_columns(self, self.match_map_preferred_width)

    def showEvent(self, event):
        super().showEvent(event)
        self._update_corners()

    def mouseDoubleClickEvent(self, event):
        index = self.indexAt(event.pos())
        if event.button() == Qt.LeftButton and index.isValid():
            metadata = index.sibling(index.row(), 0).data(MATCH_METADATA_ROLE) or {}
            if metadata.get("game_id") is not None:
                self.setCurrentCell(index.row(), index.column())
                self.selectRow(index.row())
                if index.column() == 2:
                    delegate = self.itemDelegate(index)
                    if isinstance(delegate, RecentMatchDelegate):
                        player = delegate.player_at(self.visualRect(index), metadata, event.pos())
                        if player is not None:
                            self.playerActivated.emit(dict(player))
                            event.accept()
                            return
                self.matchActivated.emit(dict(metadata))
                # 一次双击只派发一个目标；不再让基类额外发出一轮双击/编辑信号。
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class RecentMatchDelegate(QStyledItemDelegate):
    """保留表格模型与键盘选择，绘制有层次的双行对局记录。"""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        foreground = index.data(Qt.ForegroundRole)
        color = foreground.color() if isinstance(foreground, QBrush) else QColor(INK)
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            option.palette.setColor(group, QPalette.Text, color)
            option.palette.setColor(group, QPalette.HighlightedText, color)

    @staticmethod
    def result_badge_rect(cell):
        """返回实际绘制边界，与行内分析按钮保持同宽同高、垂直居中。"""
        rect = QRectF(cell)
        return QRectF(rect.center().x() - MATCH_CONTROL_SIZE.width() / 2,
                      rect.center().y() - MATCH_CONTROL_SIZE.height() / 2,
                      MATCH_CONTROL_SIZE.width(), MATCH_CONTROL_SIZE.height())

    @staticmethod
    def _text(painter, rect, text, size=12, color=INK, weight=QFont.Normal,
              alignment=Qt.AlignLeft | Qt.AlignVCenter, numeric=False):
        painter.setFont(ui_font(size, weight, numeric))
        painter.setPen(QColor(color))
        clipped = painter.fontMetrics().elidedText(str(text), Qt.ElideRight, max(0, int(rect.width())))
        painter.drawText(rect, alignment, clipped)

    @staticmethod
    def player_name_layout(content, metadata):
        """绘制和点击共用玩家行、名字槽、相同字体及省略后字宽，空白不算名字。"""
        result = []
        for area, team in zip(match_team_columns(content), metadata.get("display_teams", [])[:2]):
            players = team.get("players", [])
            for index, player in enumerate(players):
                line = match_player_rect(area, index, len(players))
                name_rect = QRectF(area.left() + MATCH_NAME_OFFSET, line.top(),
                                   max(0, area.width() - MATCH_NAME_OFFSET), MATCH_PLAYER_HEIGHT)
                focused = str(player.get("profile_id")) == str(metadata.get("focus_profile_id"))
                weight = QFont.DemiBold if focused else QFont.Normal
                metrics = QFontMetrics(ui_font(11, weight))
                text = str(player.get("display_name") or player.get("name") or "")
                clipped = metrics.elidedText(text, Qt.ElideRight, max(0, int(name_rect.width())))
                text_width = min(name_rect.width(), metrics.horizontalAdvance(clipped))
                text_height = min(name_rect.height(), metrics.height())
                hit_rect = QRectF(name_rect.left(), name_rect.center().y() - text_height / 2,
                                  text_width, text_height)
                result.append({"player": player, "area": area, "line": line, "name_rect": name_rect,
                               "text": text, "visible_text": clipped, "hit_rect": hit_rect, "weight": weight})
        return result

    def content_rect(self, cell, column):
        return match_cell_content_rect(self.parent(), column, cell)

    def player_layout_at(self, cell, metadata, position):
        """名字绘制、省略提示及双击共用相同的实际文字命中矩形。"""
        content = self.content_rect(cell, 2)
        for layout in self.player_name_layout(content, metadata):
            if layout["visible_text"] and layout["hit_rect"].contains(QPointF(position)):
                return layout
        return None

    def player_at(self, cell, metadata, position):
        layout = self.player_layout_at(cell, metadata, position)
        return layout["player"] if layout is not None else None

    def helpEvent(self, event, view, option, index):
        """双方整格不弹队伍说明；仅省略名字的实际文字上提示该一个完整昵称。"""
        if event.type() == QEvent.ToolTip and index.column() == 2:
            metadata = index.sibling(index.row(), 0).data(MATCH_METADATA_ROLE) or {}
            layout = self.player_layout_at(option.rect, metadata, event.pos())
            if layout is not None and layout["visible_text"] != layout["text"]:
                name = str(layout["player"].get("name") or layout["text"])
                QToolTip.showText(event.globalPos(), name, view.viewport(), layout["hit_rect"].toAlignedRect())
            else:
                QToolTip.hideText()
            return True
        return super().helpEvent(event, view, option, index)

    def _paint_teams(self, painter, rect, metadata):
        """明确ID关联后的双方列表；姓名命中区域直接复用此处的实际绘制布局。"""
        if len(metadata.get("display_teams", [])) >= 2:
            left, right = match_team_columns(rect)
            # VS（对阵）只占双方原有留白，不挤占昵称槽，也不成为玩家名字命中区域。
            versus = QRectF(left.right(), rect.top(), max(0, right.left() - left.right()), rect.height())
            self._text(painter, versus, "VS", 13, INK, QFont.DemiBold, Qt.AlignCenter)
        for layout in self.player_name_layout(rect, metadata):
            player, area, line = layout["player"], layout["area"], layout["line"]
            y = line.top()
            rating = player.get("mmr")
            if not isinstance(rating, (int, float)) or isinstance(rating, bool) or not math.isfinite(rating):
                rating = None
            rating_text = f"{rating:g}" if rating is not None else "—"
            self._text(painter, QRectF(area.left(), y, MATCH_SCORE_WIDTH, 19), rating_text,
                       10, "#c4c4ca" if rating is not None else CARD_AUX,
                       alignment=Qt.AlignRight | Qt.AlignVCenter, numeric=True)
            flag_name = _CIVILIZATION_FLAGS.get(str(player.get("civilization")))
            flag = civilization_flag(flag_name) if flag_name else None
            if flag is not None and not flag.isNull():
                target = QRectF(area.left() + MATCH_FLAG_OFFSET, line.center().y() - 7, 23, 14)
                clip = QPainterPath()
                clip.addRoundedRect(target, 2, 2)
                painter.save()
                painter.setClipPath(clip, Qt.IntersectClip)
                painter.drawPixmap(target, flag, QRectF(flag.rect()))
                painter.restore()
            else:
                self._text(painter, QRectF(area.left() + MATCH_FLAG_OFFSET, y, 23, 19), "—", 10, CARD_AUX,
                           alignment=Qt.AlignCenter)
            result = player.get("result")
            color = CARD_WIN if result == "win" else CARD_LOSS if result == "loss" else CARD_MODE
            self._text(painter, layout["name_rect"], layout["text"], 11, color, layout["weight"])

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipRect(option.rect)
        rect = QRectF(option.rect)
        metadata = index.sibling(index.row(), 0).data(MATCH_METADATA_ROLE) or {}
        result = metadata.get("result")
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        background = QColor("#232328" if selected else "#1c1c20" if hover else CARD_SURFACE)
        column = index.column()
        painter.fillRect(rect, background)
        painter.setPen(QPen(QColor("#252529"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        content = self.content_rect(rect, column)
        expanded = index.model().columnCount() >= 7
        rank_column, duration_column, result_column = (3, 4, 5) if expanded else (-1, 4, 3)
        action_column = 6 if expanded else 5
        lines = str(index.data(Qt.DisplayRole) or "").split("\n", 1)
        primary = lines[0]
        secondary = lines[1] if len(lines) > 1 else ""
        has_teams = bool(metadata.get("display_teams"))
        if column == 2 and has_teams:
            self._paint_teams(painter, content, metadata)
        elif column == 1 and has_teams:
            top = QRectF(content.left(), rect.center().y() - 19, content.width(), 20)
            bottom = QRectF(content.left(), rect.center().y() + 3, content.width(), 17)
            self._text(painter, top, primary, 12, "#d5d5db")
            self._text(painter, bottom, metadata.get("display_mode", "") + " · " + _duration_label(metadata.get("duration")), 10, CARD_AUX)
        elif column == 0:
            # 仅拆分已格式化的月日/时间；原单元格文字、排序及对局编号保持原值。
            date_time = re.fullmatch(r"(\d{2}-\d{2})\s+(\d{2}:\d{2})", primary.strip())
            if date_time:
                line_height = QFontMetrics(ui_font(10, numeric=True)).height()
                gap = 4
                top = rect.center().y() - line_height - gap / 2
                for number, text in enumerate(date_time.groups()):
                    line = QRectF(content.left(), top + number * (line_height + gap), content.width(), line_height)
                    self._text(painter, line, text, 10, CARD_MODE, alignment=Qt.AlignCenter, numeric=True)
            else:
                self._text(painter, content, primary or "未提供时间", 10, CARD_MODE,
                           alignment=Qt.AlignCenter, numeric=True)
        elif column == 1:
            flag = civilization_flag(_CIVILIZATION_FLAGS.get(str(metadata.get("civilization")), "Generic"))
            if not flag.isNull():
                target = QRectF(content.left(), rect.center().y() - 9, 30, 18)
                clip = QPainterPath()
                clip.addRoundedRect(target, 3, 3)
                painter.save()
                painter.setClipPath(clip, Qt.IntersectClip)
                painter.drawPixmap(target, flag, QRectF(flag.rect()))
                painter.restore()
                content.adjust(38, 0, 0, 0)
        if column == 0 or (has_teams and column in (1, 2)):
            pass  # 双方/地图/日期已按专用槽绘制，避免默认文字覆盖。
        elif column == rank_column:
            rank = ranked_result(metadata)
            if rank["points"] is None or rank["change"] is None:
                self._text(painter, content, "—", 11, MUTED, alignment=Qt.AlignCenter)
            else:
                badge = rank_badge(rank["icon_name"])
                if not badge.isNull():
                    # 徽章直接用原图原色；只给本局积分增减赋予红绿语义色。
                    image = QRectF(content.left(), rect.center().y() - 14, 28, 28)
                    painter.drawPixmap(image, badge, QRectF(badge.rect()))
                    content.adjust(32, 0, 0, 0)
                change = rank["change"]
                color = CARD_WIN if change > 0 else CARD_LOSS if change < 0 else CARD_MODE
                self._text(painter, content, rank["change_text"].replace("-", "−"), 11,
                           color, QFont.DemiBold, Qt.AlignCenter, True)
        elif column == result_column:
            winning = result == "win" or primary == "胜利"
            pill = self.result_badge_rect(rect)
            self._text(painter, pill, primary, 11, CARD_WIN if winning else CARD_LOSS,
                       QFont.Normal, Qt.AlignCenter)
        elif column == duration_column:
            self._text(painter, content, primary, 11, INK, QFont.DemiBold, Qt.AlignCenter, True)
        elif column == action_column:
            # 操作按钮仍由 QTableWidget 的单元格控件处理，保留鼠标与键盘行为。
            if primary:
                self._text(painter, content, primary, 11, MUTED, alignment=Qt.AlignCenter)
        elif secondary:
            top = QRectF(content.left(), rect.center().y() - 18, content.width(), 19)
            bottom = QRectF(content.left(), rect.center().y() + 1, content.width(), 17)
            self._text(painter, top, primary, 12, INK, QFont.DemiBold if column == 1 else QFont.Normal)
            self._text(painter, bottom, secondary, 10, "#c1ccd3" if selected else MUTED)
        else:
            self._text(painter, content, primary)
        if option.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor("#7c958a"), 1, Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(2, 2, -2, -2))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(80, MATCH_ROW_HEIGHT)


class MatchActionHeader(QHeaderView):
    """分析表头与唯一箭头入口共用同一中心，不再预留外链槽。"""

    def __init__(self, action_column, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.action_column = action_column

    def action_text_rect(self):
        cell = QRectF(self.sectionViewportPosition(self.action_column), 0,
                      self.sectionSize(self.action_column), self.height())
        if self.action_column in getattr(self.parent(), "match_column_content", {}):
            return match_cell_content_rect(self.parent(), self.action_column, cell)
        return QRectF(cell.left() + MATCH_ACTION_PADDING, 0, MATCH_ACTION_SIZE.width(), self.height())

    def team_header_rects(self):
        """真实表头槽与名单的分数、旗帜/昵称组保持同一列基准。"""
        cell = QRectF(self.sectionViewportPosition(2), 0, self.sectionSize(2), self.height())
        content = match_cell_content_rect(self.parent(), 2, cell)
        return tuple((QRectF(area.left(), 0, MATCH_SCORE_WIDTH, self.height()),
                      QRectF(area.left() + MATCH_FLAG_OFFSET, 0,
                             max(0, area.width() - MATCH_FLAG_OFFSET), self.height()))
                     for area in match_team_columns(content))

    def paintSection(self, painter, rect, logical_index):
        painter.save()
        # 明确绘制整条表头，避免平台原生渐变/矩形底盖过统一面板色。
        painter.fillRect(rect, QColor(CARD_SURFACE))
        painter.setPen(QPen(QColor("#252529"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.setFont(ui_font(10))
        painter.setPen(QColor(CARD_AUX))
        if logical_index == 2:
            for number, (score_rect, name_rect) in enumerate(self.team_header_rects()):
                painter.setFont(ui_font(10))
                painter.drawText(score_rect, Qt.AlignRight | Qt.AlignVCenter, "ELO")
                painter.setFont(ui_font(10))
                label = "玩家" if number == 0 else "对手"
                painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, label)
            painter.restore()
            return
        caption = self.model().headerData(logical_index, Qt.Horizontal, Qt.DisplayRole) or ""
        text_rect = (self.action_text_rect() if logical_index == self.action_column else
                     match_cell_content_rect(self.parent(), logical_index, rect))
        alignment = (Qt.AlignCenter if logical_index == self.action_column else
                     self.model().headerData(logical_index, Qt.Horizontal, Qt.TextAlignmentRole) or
                     (Qt.AlignLeft | Qt.AlignVCenter))
        caption = painter.fontMetrics().elidedText(str(caption), Qt.ElideRight, max(0, int(text_rect.width())))
        painter.drawText(text_rect, int(alignment), caption)
        painter.restore()


def style_recent_matches(table, rows):
    """应用行样式；保留原文字、提示、游戏编号和现有打开按钮。

    rows=原 normalize_games（对局整理函数）的结果；仅在第零列增加绘制元数据。
    """
    table.setItemDelegate(RecentMatchDelegate(table))
    table.setStyleSheet(
        "QTableWidget{background:#161619;alternate-background-color:#161619;border:0;outline:0;}"
        "QTableWidget::item{border:0;padding:0;}"
        "QHeaderView::section{background:#161619;color:#73737d;border:0;border-bottom:1px solid #252529;"
        "padding:4px 8px;font-size:10px;font-weight:400;}"
        "QTableCornerButton::section{background:transparent;border:0;}"
    )
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    header.setFixedHeight(30)
    table.setAlternatingRowColors(False)
    expanded = table.columnCount() >= 7
    widths = ((0, 94), (1, 154), (3, MATCH_RANK_COLUMN_WIDTH), (4, 50),
              (5, MATCH_RESULT_COLUMN_WIDTH), (6, MATCH_ACTION_COLUMN_WIDTH)) if expanded else (
        (0, 94), (1, 154), (3, MATCH_RESULT_COLUMN_WIDTH), (4, 50), (5, MATCH_ACTION_COLUMN_WIDTH))
    action_column = 6 if expanded else 5
    for column, width in widths:
        table.setColumnWidth(column, width)
    for number, row in enumerate(rows):
        item = table.item(number, 0)
        if item is not None:
            item.setData(MATCH_METADATA_ROLE, dict(row))
        if expanded and table.item(number, 3) is not None:
            rank = ranked_result(row)
            table.item(number, 3).setToolTip(rank["tooltip"])
            table.item(number, 3).setData(Qt.AccessibleTextRole, rank["text"])
        team_size = max((len(team["players"]) for team in row.get("display_teams", [])), default=0)
        table.setRowHeight(number, max(MATCH_ROW_HEIGHT, 20 + team_size * MATCH_PLAYER_PITCH))
        action = table.cellWidget(number, action_column)
        if isinstance(action, QPushButton):
            action.setStyleSheet(
                "QPushButton{margin:0;padding:3px 5px;border:1px solid transparent;border-radius:6px;"
                "background:transparent;color:#c0cbd2;font-size:11px;}"
                "QPushButton:hover{background:#2b333a;border-color:#4b5960;color:#ffffff;}"
                "QPushButton:focus{border-color:#7c958a;}"
            )
            action.setMinimumHeight(28)
            action.setMaximumHeight(16777215)
    for column in ((0, 3, 4, 5, 6) if expanded else (0, 3, 4, 5)):
        header_item = table.horizontalHeaderItem(column)
        if header_item is not None:
            header_item.setTextAlignment(Qt.AlignCenter)
    table.setFixedHeight(30 + sum(table.rowHeight(number) for number in range(table.rowCount())))
    return table.itemDelegate()
