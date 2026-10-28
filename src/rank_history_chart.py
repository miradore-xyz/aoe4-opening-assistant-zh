"""排位积分历史：仅绘制已获取的单排/组排评分快照，不推算赛前赛后分数。

series=独立评分序列，timestamp=来源Unix秒，plot=日期与积分共用绘图区。
"""
from __future__ import annotations

import math
import re
from datetime import datetime, time, timedelta, timezone

from PyQt5.QtCore import QEvent, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget

from profile_rank_summary import ProfileRankSummary
from visual import CARD_GAP, ui_font


SERIES = (("rm_solo", "单排", "#8bb8ee"), ("rm_team", "组排", "#c7a7e8"))


def _stamp(value):
    """只接收完整Unix秒；日期越界、布尔、小数或非数字不进入图表。"""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    if not re.fullmatch(r"[0-9]+", str(value)):
        return None
    try:
        number = int(value)
        datetime.fromtimestamp(number, timezone.utc).astimezone()
        return number
    except (ValueError, OverflowError, OSError):
        return None


def ranking_history(profile):
    """原样读取明确的排位历史，rm_1v1旧别名、ELO及其他模式均不混入。"""
    modes = profile.get("modes") if isinstance(profile, dict) else None
    modes = modes if isinstance(modes, dict) else {}
    output = {}
    for code, _, _ in SERIES:
        mode = modes.get(code)
        history = mode.get("rating_history") if isinstance(mode, dict) else None
        points = {}
        if isinstance(history, dict):
            for raw_time, value in history.items():
                stamp = _stamp(raw_time)
                rating = value.get("rating") if isinstance(value, dict) else None
                if (stamp is not None and isinstance(rating, (int, float)) and not isinstance(rating, bool)
                        and math.isfinite(rating) and rating >= 0):
                    points[stamp] = {"timestamp": stamp, "rating": rating}
        output[code] = [points[key] for key in sorted(points)]
    return output


def local_time(stamp, pattern="%Y-%m-%d %H:%M:%S"):
    """按用户系统本地时区显示来源时间，不把资料更新时间改写成开赛时刻。"""
    return datetime.fromtimestamp(stamp, timezone.utc).astimezone().strftime(pattern)


def recent_rank_history(profile, now=None):
    """近15个本地自然日（含今天），只筛真实快照，不补点、不前填分数。

    每个日期单独转本地午夜，避免跨夏令时时把一天硬写成86400秒。
    """
    now = now or datetime.now().astimezone()
    today = now.astimezone().date()
    first = today - timedelta(days=14)
    midnight = lambda day: int(datetime.combine(day, time.min).astimezone().timestamp())
    bounds = (midnight(first), midnight(today + timedelta(days=1)))
    series = {code: [point for point in points if bounds[0] <= point["timestamp"] <= now.timestamp()]
              for code, points in ranking_history(profile).items()}
    ticks = [(midnight(first + timedelta(days=offset)),
              (first + timedelta(days=offset)).strftime("%m-%d" if first.year == today.year else "%Y-%m-%d"))
             for offset in (0, 7, 14)]
    return series, bounds, ticks


class RankHistoryChart(QWidget):
    """两条实线共用真实时间轴；放大圆点便于识别，不显示浮动读数框。"""
    pointInspected = pyqtSignal(str)
    POINT_HIT_RADIUS = 8

    def __init__(self, series, parent=None, *, time_bounds=None, date_ticks=None):
        super().__init__(parent)
        self.series = series
        self.time_bounds = time_bounds
        self.window_ticks = date_ticks
        self.timestamps = sorted({point["timestamp"] for points in series.values() for point in points})
        self.selected_timestamp = None
        self.setFixedHeight(160)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("单排与组排排位积分历史图")
        self.guide = "横轴为本地日期，纵轴为排位积分。方向键切换时间，Home和End跳至首末记录。"
        self.setAccessibleDescription(self.guide)

    def plot_rect(self):
        return QRectF(44, 12, max(1, self.width() - 60), self.height() - 48)

    def _date_rect(self, stamp, label):
        """文字盒受视口边界约束；日期位置仍来自真实时间比例。"""
        area = self.plot_rect()
        first, last = self.time_bounds or (self.timestamps[0], self.timestamps[-1])
        fraction = (stamp - first) / (last - first) if last != first else .5
        x = area.left() + fraction * area.width()
        width = QFontMetricsF(ui_font(10)).horizontalAdvance(label) + 4
        left = max(area.left(), min(x - width / 2, self.width() - 12 - width))
        return QRectF(left, area.bottom() + 10, width, 20)

    def date_ticks(self):
        """横轴只标日期；同一天只标一次，跨年时显示年份以免混淆。"""
        if self.window_ticks is not None:
            ticks = self.window_ticks
            selected = [ticks[0]]
            for tick in ticks[1:]:
                if self._date_rect(*tick).left() >= self._date_rect(*selected[-1]).right() + 16:
                    selected.append(tick)
            return selected
        if not self.timestamps:
            return []
        first, last = self.time_bounds or (self.timestamps[0], self.timestamps[-1])
        first_date, last_date = local_time(first, "%Y-%m-%d"), local_time(last, "%Y-%m-%d")
        middle = (first + last) // 2
        if first_date == last_date:
            return [(middle, first_date)]
        pattern = "%Y-%m-%d" if first_date[:4] != last_date[:4] else "%m-%d"
        stamps = [first]
        if local_time(middle, "%Y-%m-%d") not in (first_date, last_date):
            stamps.append(middle)
        stamps.append(last)
        ticks = [(stamp, local_time(stamp, pattern)) for stamp in stamps]
        # 首末日期优先，中间日期只有留足字间距时才显示，不均分或合并原始数据点。
        selected = [ticks[0]]
        if len(ticks) > 1 and self._date_rect(*ticks[-1]).left() >= self._date_rect(*ticks[0]).right() + 16:
            selected.append(ticks[-1])
        if len(ticks) == 3 and len(selected) == 2:
            middle_rect = self._date_rect(*ticks[1])
            if (middle_rect.left() >= self._date_rect(*selected[0]).right() + 16
                    and middle_rect.right() + 16 <= self._date_rect(*selected[-1]).left()):
                selected.insert(1, ticks[1])
        return selected

    def score_bounds(self):
        values = [point["rating"] for points in self.series.values() for point in points]
        if not values:
            return 0, 1, 1
        low, high = min(values), max(values)
        # 极大但有限的来源数值也不能令取整溢出；不改变或截断原始记录。
        if high > 1e12:
            bottom = low * .95 if low == high else low
            return bottom, high, (high - bottom) / 4
        span = max(high - low, 40)
        rough = span / 4
        power = 10 ** math.floor(math.log10(rough))
        step = next(part * power for part in (1, 2, 5, 10) if part * power >= rough)
        bottom = max(0, math.floor((low - span * .08) / step) * step)
        top = math.ceil((high + span * .08) / step) * step
        return bottom, max(bottom + step, top), step

    def point_position(self, point):
        area = self.plot_rect()
        first, last = self.time_bounds or (self.timestamps[0], self.timestamps[-1])
        fraction = (point["timestamp"] - first) / (last - first) if last != first else .5
        low, high, _ = self.score_bounds()
        return QPointF(area.left() + fraction * area.width(),
                       area.bottom() - (point["rating"] - low) / (high - low) * area.height())

    def inspect_text(self, stamp):
        details = []
        for code, name, _ in SERIES:
            point = next((point for point in self.series.get(code, []) if point["timestamp"] == stamp), None)
            if point is not None:
                details.append(f"{name} {point['rating']:g} 分")
        return f"{local_time(stamp)}（本地时间） · " + " / ".join(details) if details else ""

    def _inspect(self, stamp):
        if stamp == self.selected_timestamp:
            return
        self.selected_timestamp = stamp
        text = self.inspect_text(stamp) if stamp is not None else ""
        self.setAccessibleDescription(text or self.guide)
        self.pointInspected.emit(text)
        self.update()

    def event(self, event):
        # 日期/分数只供辅助功能读取，不再弹悬停框，也不从父控件继承提示。
        if event.type() == QEvent.ToolTip:
            event.accept()
            return True
        return super().event(event)

    def mouseMoveEvent(self, event):
        candidates = [(math.hypot(event.x() - self.point_position(point).x(),
                                  event.y() - self.point_position(point).y()), point["timestamp"])
                      for points in self.series.values() for point in points]
        nearest = min(candidates, default=None)
        self._inspect(nearest[1] if nearest is not None and nearest[0] <= self.POINT_HIT_RADIUS else None)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if not self.hasFocus():
            self._inspect(None)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self.timestamps:
            self._inspect(self.selected_timestamp if self.selected_timestamp is not None else self.timestamps[0])

    def focusOutEvent(self, event):
        self._inspect(None)
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if self.timestamps and event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Home, Qt.Key_End):
            index = self.timestamps.index(self.selected_timestamp) if self.selected_timestamp in self.timestamps else 0
            if event.key() == Qt.Key_Home:
                index = 0
            elif event.key() == Qt.Key_End:
                index = len(self.timestamps) - 1
            else:
                index = max(0, min(len(self.timestamps) - 1, index + (1 if event.key() == Qt.Key_Right else -1)))
            self._inspect(self.timestamps[index])
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(ui_font(10))
        if not self.timestamps:
            painter.setPen(QColor("#929299"))
            painter.drawText(self.plot_rect(), Qt.AlignCenter, "近15天暂无排位积分记录")
            for stamp, label in self.date_ticks():
                painter.drawText(self._date_rect(stamp, label), Qt.AlignHCenter | Qt.AlignTop, label)
            return
        area = self.plot_rect()
        low, high, step = self.score_bounds()
        value = low
        while value <= high + step * .01:
            y = area.bottom() - (value - low) / (high - low) * area.height()
            painter.setPen(QPen(QColor("#27272c"), 1))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(QColor("#81818b"))
            painter.drawText(QRectF(0, y - 8, area.left() - 12, 16), Qt.AlignRight | Qt.AlignVCenter, f"{value:g}")
            value += step
        painter.setPen(QColor("#81818b"))
        for stamp, label in self.date_ticks():
            painter.drawText(self._date_rect(stamp, label), Qt.AlignHCenter | Qt.AlignTop, label)
        for code, _, color in SERIES:
            points = self.series.get(code, [])
            if not points:
                continue
            painter.setPen(QPen(QColor(color), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            path = QPainterPath()
            for index, point in enumerate(points):
                position = self.point_position(point)
                path.moveTo(position) if index == 0 else path.lineTo(position)
            # 一个来源点只画标记，不扩展成一条不存在的走势。
            if len(points) > 1:
                painter.drawPath(path)
            for point in points:
                position = self.point_position(point)
                selected = point["timestamp"] == self.selected_timestamp
                radius = 5 if selected else 3.6
                painter.setPen(QPen(QColor(color), 1.8) if selected else QPen(QColor("#161619"), 1))
                painter.setBrush(QColor("#161619") if selected else QColor(color))
                painter.drawEllipse(position, radius, radius)


class _SeriesLegend(QWidget):
    """图例使用与曲线一致的细线和小圆点，不靠字形菱形增加视觉重量。"""
    def __init__(self, name, color, active, parent=None):
        super().__init__(parent)
        self.name = name if active else name + " · 暂无记录"
        self.color, self.active = color, active
        # 绘制和测量都锁定同一字体，防止全局QSS把实际字变大后截掉末字。
        self._legend_font = ui_font(11)
        self.setFont(self._legend_font)
        self.setObjectName("RankSeriesLegend")
        self.setStyleSheet("QWidget#RankSeriesLegend{font-size:11px;background:transparent;border:0;}")
        self.setFixedSize(self.sizeHint())
        self.setAccessibleName(self.name)

    def sizeHint(self):
        metrics = QFontMetricsF(self._legend_font)
        width = math.ceil(max(metrics.horizontalAdvance(self.name), metrics.boundingRect(self.name).right()+1))+4
        return QSize(22+width,max(20,math.ceil(metrics.height())+4))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(self.color if self.active else "#55555f")
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(1, 10), QPointF(14, 10))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(7.5, 10), 1.8, 1.8)
        painter.setFont(self._legend_font)
        painter.setPen(QColor("#aaaab3" if self.active else "#686873"))
        painter.drawText(QRectF(22,0,self.width()-22,self.height()),Qt.AlignLeft|Qt.AlignVCenter,self.name)


class RankHistoryCard(QFrame):
    """模式摘要下的一张独立卡片；图表没有修改全模式对局列表的能力。"""
    def __init__(self, profile, parent=None, *, now=None):
        super().__init__(parent)
        self.setObjectName("RankHistoryCard")
        self.setStyleSheet("QFrame#RankHistoryCard{background:#161619;border:0;border-radius:12px;}")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        series, bounds, ticks = recent_rank_history(profile, now)
        layout = QVBoxLayout(self)
        # 宽度由外部分栏决定；不能让上一排图例的最小宽度阻止窄卡触发换行。
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.header = QGridLayout()
        self.header.setContentsMargins(0,0,0,0)
        self.header.setHorizontalSpacing(16)
        self.header.setVerticalSpacing(8)
        self.heading = self._label("排位积分走势 · 近15天",14,"#f4f4f5",QFont.DemiBold)
        self.legends = []
        for code, name, color in SERIES:
            self.legends.append(_SeriesLegend(name,color,bool(series[code])))
        self._header_rows = None
        self._arrange_header()
        layout.addLayout(self.header)
        self.chart = RankHistoryChart(series, time_bounds=bounds, date_ticks=ticks)
        layout.addWidget(self.chart)
        stamps = self.chart.timestamps
        counts = " / ".join(f"{name} {len(series[code])} 点" if series[code] else f"{name}暂无记录"
                            for code, name, _ in SERIES)
        dates = list(dict.fromkeys(local_time(stamp, "%Y-%m-%d") for stamp in (stamps[0], stamps[-1]))) if stamps else []
        date_range = "—".join(dates) + " · " if dates else ""
        self.summary = date_range + counts + " · 已获取的历史快照"
        # 统计口径仅供无障碍读取；底部不再绘制说明或当前读数，也不保留占位。
        self.setAccessibleName("排位积分走势")
        self.setAccessibleDescription(self.summary)

    def _arrange_header(self):
        """窄卡先把图例放到标题下一排，再逐行显示；完整文字始终保留。"""
        available = max(1,self.width()-32)
        legend_width = sum(legend.sizeHint().width()for legend in self.legends)+16
        required = self.heading.sizeHint().width()+16+legend_width
        # 两排时保留左侧伸缩列到首个图例的16px间距，也计入可用宽度。
        rows = 1 if available>=required else 2 if available>=legend_width+16 else 3
        if rows==self._header_rows:
            return False
        self._header_rows = rows
        for control in (self.heading,*self.legends):
            self.header.removeWidget(control)
        for column in range(3):
            self.header.setColumnStretch(column,0)
        if rows==1:
            self.header.setColumnStretch(0,1)
            self.header.addWidget(self.heading,0,0)
            for column,legend in enumerate(self.legends,1):
                self.header.addWidget(legend,0,column,Qt.AlignRight)
        elif rows==2:
            self.header.setColumnStretch(0,1)
            self.header.addWidget(self.heading,0,0,1,3)
            for column,legend in enumerate(self.legends,1):
                self.header.addWidget(legend,1,column,Qt.AlignRight)
        else:
            self.header.addWidget(self.heading,0,0,1,3)
            for row,legend in enumerate(self.legends,1):
                self.header.addWidget(legend,row,0,1,3,Qt.AlignRight)
        self.updateGeometry()
        return True

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self._arrange_header():
            self.layout().activate()
            self.updateGeometry()

    @staticmethod
    def _label(text, size, color, weight=QFont.Normal):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setFont(ui_font(size, weight))
        label.setStyleSheet(f"background:transparent;border:0;padding:0;color:{color};font-size:{size}px;"
                           f"font-weight:{600 if weight >= QFont.DemiBold else 400};")
        return label


class RankOverviewRow(QWidget):
    """走势与双段位宽屏2:1、窄屏叠排；同级卡片中缝固定16。"""
    GAP = CARD_GAP
    STACK_WIDTH = 840

    def __init__(self, profile, parent=None, *, now=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(self.GAP)
        self.history_card = RankHistoryCard(profile, now=now)
        # 右栏仅作透明布局容器；单排、组排各自拥有背景和圆角，不再套同色大卡。
        self.current_card = QWidget()
        self.current_card.setObjectName("CurrentRankColumn")
        self.current_card.setStyleSheet("QWidget#CurrentRankColumn{background:transparent;border:0;}")
        self.current_card.setMinimumWidth(0)
        self.current_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        current = QVBoxLayout(self.current_card)
        current.setContentsMargins(0, 0, 0, 0)
        current.setSpacing(0)
        self.rank_summary = ProfileRankSummary(profile, expanded=True)
        current.addWidget(self.rank_summary, 1)
        self._stacked = None
        self._arrange()

    def _arrange(self):
        stacked = self.width() < self.STACK_WIDTH
        if stacked != self._stacked:
            self._stacked = stacked
            for widget in (self.history_card, self.current_card):
                self.grid.removeWidget(widget)
            self.grid.setColumnStretch(0, 1 if stacked else 2)
            self.grid.setColumnStretch(1, 0 if stacked else 1)
            self.grid.addWidget(self.history_card, 0, 0)
            self.grid.addWidget(self.current_card, 1 if stacked else 0, 0 if stacked else 1)
        # 左侧整图与右侧两张小卡总高相同，不能让伸缩项把中缝撑大。
        height = max(256, self.history_card.sizeHint().height())
        self.history_card.setFixedHeight(height)
        self.current_card.setFixedHeight(height)
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()
