"""APM（每分钟操作次数）：显示本场公开摘要的原值，不由部分录像指令推算。"""
import math

from PyQt5.QtCore import QEvent, QLineF, QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QFontMetricsF, QPainter, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from app_fonts import make_ui_font


BLUE, GRAY, RED, ORANGE = "#7cb4f4", "#62626d", "#ef4d4d", "#e9a24b"
# 每100一档，600橙线、700红线；只按数值标色，不推断操作来源或原因。
REFERENCE_LEVELS = (100, 200, 300, 400, 500, 600, 700)


def _profile_id(value):
    """只按明确的账号编号关联，不用昵称猜测玩家身份。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def apm_players(detail, game_id):
    """必须核对同一对局；缺失或异常值保留为空，不伪装成零。"""
    expected = _profile_id(game_id)
    if (not expected or not isinstance(detail, dict)
            or _profile_id(detail.get("game_id")) != expected):
        return []
    source = detail.get("players")
    if not isinstance(source, list):
        return []
    players, seen = [], set()
    for raw in source:
        if not isinstance(raw, dict):
            continue
        profile_id = _profile_id(raw.get("profile_id"))
        if profile_id is None or profile_id in seen:
            continue
        seen.add(profile_id)
        value = raw.get("apm")
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value < 0):
            value = None
        players.append({"profile_id": profile_id,
                        "name": str(raw.get("name") or f"玩家 {profile_id}"), "apm": value})
    return players


class APMChart(QWidget):
    """一名玩家一根柱；长名字显示省略号，不弹出悬停提示。"""
    # object（Python对象）保留完整整数账号，不经过Qt的32位整型截断。
    playerActivated = pyqtSignal(object, str)

    def __init__(self, players, focus_id=None, parent=None):
        super().__init__(parent)
        self.players = players
        self.focus_id = _profile_id(focus_id)
        self.setFixedHeight(248)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("各玩家每分钟操作次数")
        self.setAccessibleDescription("纵轴从0起每100一档，600为橙色参考线，700为红色参考线；超过700的红柱标为异常操作量。" + "；".join(
            f"{player['name']}：{player['apm']:g}" if player["apm"] is not None
            else f"{player['name']}：暂无数据" for player in players))

    def geometry_rows(self):
        """返回真实柱体及姓名区域；纵轴从零起，不压缩数值差异。"""
        plot = QRectF(44, 28, max(1, self.width() - 56), self.height() - 72)
        values = [player["apm"] for player in self.players if player["apm"] is not None]
        # 所有分界始终可见；保留零起点和线性比例，超过700时继续扩展纵轴。
        maximum = max(REFERENCE_LEVELS[-1], math.ceil(max(values, default=0) / 100) * 100)
        slot = plot.width() / max(1, len(self.players))
        name_font = make_ui_font(12)
        metrics = QFontMetrics(name_font)
        precise_metrics = QFontMetricsF(name_font)
        rows = []
        for index, player in enumerate(self.players):
            center = plot.left() + (index + .5) * slot
            height = plot.height() * player["apm"] / maximum if player["apm"] is not None else 0
            # 柱体占槽位的72%，缩短柱间空隙；少人对局保留宽度上限。
            bar_width = min(88, slot * .72)
            name_rect = QRectF(center - slot / 2 + 6, plot.bottom() + 12, max(1, slot - 12), 20)
            visible_name = metrics.elidedText(player["name"], Qt.ElideRight, int(name_rect.width()))
            rows.append({"player": player,
                         "bar": QRectF(center - bar_width / 2, plot.bottom() - height, bar_width, height),
                         "name_rect": name_rect,
                         "visible_name": visible_name,
                         # 命中区与同一字体、居中省略文本共用几何，不把姓名旁的空白当作名字。
                         "name_hit_rect": precise_metrics.boundingRect(name_rect, Qt.AlignCenter, visible_name).intersected(name_rect),
                         "hit_rect": QRectF(center - slot / 2, plot.top(), slot, plot.height() + 36)})
        return plot, maximum, rows

    def mouseDoubleClickEvent(self, event):
        """只有左键双击实际绘制的姓名才请求唯一账号；柱体、空白和右键不触发。"""
        if event.button() == Qt.LeftButton and any(player["apm"] is not None for player in self.players):
            for row in self.geometry_rows()[2]:
                player = row["player"]
                number = _profile_id(player.get("profile_id"))
                if (number is not None and row["visible_name"].strip()
                        and row["name_hit_rect"].contains(event.localPos())):
                    self.playerActivated.emit(number, player["name"])
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not any(player["apm"] is not None for player in self.players):
            painter.setFont(make_ui_font(12))
            painter.setPen(QColor("#929299"))
            painter.drawText(self.rect(), Qt.AlignCenter, "本场暂无每分钟操作次数数据")
            return
        plot, maximum, rows = self.geometry_rows()
        painter.setFont(make_ui_font(11))
        ticks = range(0, maximum + 1, 100)
        for value in ticks:
            y = plot.bottom() - plot.height() * value / maximum
            reference = value > 0
            line_color = RED if value == 700 else ORANGE if value == 600 else "#55555e" if reference else "#303035"
            painter.setPen(QPen(QColor(line_color), 1,
                                Qt.DashLine if reference else Qt.SolidLine))
            painter.drawLine(QLineF(plot.left(), y, plot.right(), y))
            painter.setPen(QColor(RED if value == 700 else ORANGE if value == 600 else "#929299"))
            self._draw_tick(painter, QRectF(0, y - 9, 34, 18), f"{value:g}")
        for row in rows:
            player, bar = row["player"], row["bar"]
            focused = player["profile_id"] == self.focus_id
            high = player["apm"] is not None and player["apm"] > 700
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(RED if high else BLUE if focused else GRAY))
            if player["apm"] is not None:
                painter.drawRoundedRect(bar, 3, 3)
            painter.setFont(make_ui_font(13, QFont.DemiBold))
            painter.setPen(QColor(RED if high else "#d6d6db"))
            label = f"{player['apm']:g}" if player["apm"] is not None else "—"
            label_rect = QRectF(bar.center().x() - 50, bar.top() - 26, 100, 20)
            # 参考线可能经过柱顶数值；只在数字周围留出卡片底色，不让虚线穿过字形。
            label_width = QFontMetrics(painter.font()).horizontalAdvance(label) + 6
            painter.fillRect(QRectF(bar.center().x() - label_width / 2, label_rect.top(),
                                    label_width, label_rect.height()), QColor("#161619"))
            painter.drawText(label_rect, Qt.AlignCenter, label)
            painter.setFont(make_ui_font(12))
            painter.setPen(QColor("#c4d9f2" if focused else "#a4a4ad"))
            painter.drawText(row["name_rect"], Qt.AlignCenter, row["visible_name"])

    @staticmethod
    def _draw_tick(painter, rect, text):
        """按字形可见右边界对齐刻度，排除字体侧边留白差异。"""
        metrics = QFontMetrics(painter.font())
        ink = metrics.tightBoundingRect(text)
        x = rect.right() - 1 - ink.right()
        baseline = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
        painter.drawText(QPointF(x, baseline), text)

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            # 姓名和数值已直接绘制；拦截系统提示事件，避免遮挡图表。
            QToolTip.hideText()
            return True
        return super().event(event)


class APMCard(QFrame):
    """嵌入数据区的首项，外层已有内边距，避免重复添加边框和空白。"""
    playerActivated = pyqtSignal(object, str)

    def __init__(self, detail, game_id=None, focus_id=None, parent=None):
        super().__init__(parent)
        self.setObjectName("ReplayAPMCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("QFrame#ReplayAPMCard{background:#161619;border:0;border-radius:12px;}")
        players = apm_players(detail, game_id)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(12)
        self.title = QLabel("每分钟操作次数")
        self.title.setFont(make_ui_font(16, QFont.DemiBold))
        self.title.setStyleSheet("font-size:16px;font-weight:600;color:#f4f4f5;background:transparent;border:0;")
        self.title.setAccessibleDescription("每分钟操作次数，来自本场公开摘要的平均值。")
        header.addWidget(self.title)
        header.addStretch()
        self.legend = QLabel(f'<span style="color:{BLUE};">●</span> 当前查询玩家')
        self.legend.setFont(make_ui_font(12))
        self.legend.setStyleSheet("font-size:12px;color:#929299;background:transparent;border:0;")
        self.legend.setVisible(any(player["profile_id"] == _profile_id(focus_id) for player in players))
        header.addWidget(self.legend)
        self.high_legend = QLabel(f'<span style="color:{RED};">●</span> 异常操作量')
        self.high_legend.setFont(make_ui_font(12))
        self.high_legend.setStyleSheet("font-size:12px;color:#929299;background:transparent;border:0;")
        self.high_legend.setAccessibleDescription("红色柱体和数值表示每分钟操作次数超过700。")
        self.high_legend.setVisible(any(player["apm"] is not None for player in players))
        header.addWidget(self.high_legend)
        layout.addLayout(header)
        self.chart = APMChart(players, focus_id, self)
        self.chart.playerActivated.connect(self._activate_player)
        layout.addWidget(self.chart)

    def _activate_player(self, profile_id, name):
        """由卡片重新发出信号，使页面能核对当前卡片而非已卸载的图表。"""
        self.playerActivated.emit(profile_id, name)
