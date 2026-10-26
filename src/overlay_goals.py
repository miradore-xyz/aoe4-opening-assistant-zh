"""顶部居中的开局目标条；显示作者建议值，不读取游戏中的实际资源或人口。"""
from __future__ import annotations

import math
import re

from app_fonts import make_ui_font, register_ui_fonts
from PyQt5 import sip
from PyQt5.QtCore import Qt, QRect, QRectF
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap, QPen
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

from overlay_effects import configure_hud_shadow, install_hud_outline, overlay_style, style_backdrop
from visual import Resources


def fading_backdrop(width, height, ratio=1.0, *, edge=12, top=12, bottom=20, opacity=198):
    """按实际内容范围生成连贯深蓝暗衬；渐隐只落在文字外的留白中。"""
    picture = QPixmap(max(1, round(width * ratio)), max(1, round(height * ratio)))
    picture.setDevicePixelRatio(ratio)
    picture.fill(Qt.transparent)
    painter = QPainter(picture)
    painter.setClipRect(QRectF(2, 2, max(0, width - 4), max(0, height - 4)))
    horizontal = QLinearGradient(0, 0, width, 0)
    edge_fraction = min(.49, edge / max(1, width))
    for position, alpha in ((0, 0), (edge_fraction, opacity), (1 - edge_fraction, opacity), (1, 0)):
        horizontal.setColorAt(position, QColor(10, 25, 37, alpha))
    painter.fillRect(QRectF(0, 0, width, height), horizontal)
    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    vertical = QLinearGradient(0, 0, 0, height)
    for position, alpha in ((0, 0), (min(.49, top / max(1, height)), 255),
                            (max(.51, 1 - bottom / max(1, height)), 255), (1, 0)):
        vertical.setColorAt(position, QColor(0, 0, 0, alpha))
    painter.fillRect(QRectF(0, 0, width, height), vertical)
    painter.end()
    return picture


class GoalHUD(QWidget):
    """GoalHUD（目标提示层）：始终鼠标穿透，显隐由宿主明确控制。"""

    def __init__(self, renderer, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("开局目标")
        self.setObjectName("OpeningGoalHUD")
        self.setStyleSheet("QWidget#OpeningGoalHUD{background:transparent;border:0;}")
        self.renderer = renderer
        self._cleaned = False
        self._screen = None
        self._font_size = 14
        self.visual_style = "a"
        self._background_color = "#1b1b1e"
        self._background_opacity = 65
        self._shadow_enabled = True
        self._shadow_strength = 75
        self._text_color = ""
        self._content_key = None
        self.values = {}
        self.rows = []
        self.setAccessibleName("当前阶段目标：开局作者的建议值，并非游戏实际数值")
        self.cells = {}
        for key, (name, _path) in Resources.definitions.items():
            icon = QLabel(self)
            value = QLabel(self)
            icon.setStyleSheet("background:transparent;border:0;color:#e7d4a2;")
            value.setStyleSheet("background:transparent;border:0;color:#f4f0e4;")
            icon.setAttribute(Qt.WA_TransparentForMouseEvents)
            value.setAttribute(Qt.WA_TransparentForMouseEvents)
            install_hud_outline(icon)
            install_hud_outline(value)
            icon.hide()
            value.hide()
            self.cells[key] = (icon, value)
        self.hide()

    @property
    def has_targets(self):
        return bool(self.values) and not self._cleaned

    def set_style(self, value):
        """与正文共享样式，保持独立窗口、居中位置及永远穿透。"""
        value = overlay_style(value)
        if value == self.visual_style:
            return
        self.visual_style = value
        self._backdrop_key = None
        if self.values:
            self.sync_screen(self._screen or QApplication.primaryScreen())
        self.update()

    def set_appearance(self, settings):
        """即时同步正文外观；不重新选择步骤、显隐或穿透。"""
        if self._cleaned:
            return
        settings = settings if isinstance(settings, dict) else {}
        color = QColor(settings.get("overlay_color", "#1b1b1e"))
        background = color.name() if color.isValid() else "#1b1b1e"
        def percentage(key, default):
            value = settings.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return default
            return max(0, min(100, value))
        opacity = percentage("opacity", 65)
        enabled = settings.get("text_shadow", True)
        enabled = enabled if isinstance(enabled, bool) else True
        strength = percentage("text_shadow_strength", 75)
        text_color = settings.get("overlay_text_color", "")
        text_color = text_color.lower() if isinstance(text_color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", text_color) else ""
        before = (self._background_color, self._background_opacity, self._shadow_enabled,
                  self._shadow_strength, self._text_color)
        after = (background, opacity, enabled, strength, text_color)
        if before == after:
            return
        (self._background_color, self._background_opacity, self._shadow_enabled,
         self._shadow_strength, self._text_color) = after
        self._backdrop_key = None
        self._apply_text_colors()
        for icon, value in self.cells.values():
            configure_hud_shadow(icon, enabled, strength)
            configure_hud_shadow(value, enabled, strength)
        self.update()

    def _apply_text_colors(self):
        """文字颜色统一从当前外观取值；图片保持原始像素，不使用染色特效。"""
        for icon, value in self.cells.values():
            icon.setStyleSheet(f"background:transparent;border:0;color:{self._text_color or '#e7d4a2'};")
            value.setStyleSheet(f"background:transparent;border:0;padding:0;"
                               f"color:{self._text_color or '#f4f0e4'};font-family:'Sen','Source Han Sans SC','Microsoft YaHei UI';font-size:{self._font_size}px;")
            icon.update()
            value.update()

    def set_step(self, step, font_size=14):
        """更新当前阶段目标；未知值不补零，返回是否有有效目标。"""
        if self._cleaned:
            return False
        self._font_size = max(8, min(80, int(font_size)))
        step = step if isinstance(step, dict) else {}
        resources = step.get("resources", {})
        resources = resources if isinstance(resources, dict) else {}
        known = {}
        for key in Resources.definitions:
            number = resources.get(key, -1) if key in ("food", "wood", "gold", "stone", "builder") else step.get(key, -1)
            if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
                known[key] = number
        content_key = (self._font_size, tuple(known.items()), self.devicePixelRatioF(), self._text_color)
        if content_key == self._content_key:
            return bool(known)
        self._content_key = content_key
        font = make_ui_font()
        font.setPixelSize(self._font_size)
        prefix_font = make_ui_font()
        prefix_font.setPixelSize(max(9, self._font_size - 2))
        self._apply_text_colors()
        self.values = known
        accessible = []
        for key, (name, path) in Resources.definitions.items():
            number = known.get(key, -1)
            icon, value = self.cells[key]
            shown = isinstance(number, int) and not isinstance(number, bool) and number >= 0
            icon.setVisible(shown)
            value.setVisible(shown)
            if not shown:
                continue
            value.setFont(font)
            value.setText(str(number))
            value.setAccessibleName(f"{name}目标：{number}")
            value.setToolTip(f"{name}：{number}（开局作者填写的目标值）")
            path = f"age/age_{number}.webp" if key == "age" else path
            source = self.renderer.image_path(path)
            picture = QPixmap(source) if source else QPixmap()
            icon_edge = self._font_size + 5
            if not picture.isNull():
                ratio = max(1.0, self.devicePixelRatioF())
                picture = picture.scaled(round(icon_edge * ratio), round(icon_edge * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                picture.setDevicePixelRatio(ratio)
                icon.setText("")
                icon.setPixmap(picture)
                if key == "age":
                    # 游戏时代图标本身已经包含罗马数字，不再追加阿拉伯数字。
                    value.setText("")
                    value.hide()
            else:
                icon.setPixmap(QPixmap())
                icon.setText(name)
                fallback = QFont(prefix_font)
                fallback.setPixelSize(max(9, self._font_size - 1))
                icon.setFont(fallback)
                if key == "age":
                    value.setText({1: "I", 2: "II", 3: "III", 4: "IV"}.get(number, str(number)))
            description = f"{name}目标：{number}"
            accessible.append(description)
            icon.setAccessibleName(description)
        self.setAccessibleName("当前阶段目标：" + "，".join(accessible) + "（开局作者建议值）")
        if not self.values:
            self.hide()
            return False
        self.sync_screen(self._screen or QApplication.primaryScreen())
        return True

    def _arrange(self, area):
        """只用逻辑像素测量；窄屏按目标项换行，不重复乘显示倍率。"""
        margin, gap, internal_gap = (10, 16, 4) if self.visual_style == "b" else (8, 18, 4) if self.visual_style == "c" else (6, 14, 4)
        row_height = self._font_size + 10
        sizes = {}
        for key in self.values:
            icon, value = self.cells[key]
            icon_width = self._font_size + 5 if icon.pixmap() and not icon.pixmap().isNull() else icon.sizeHint().width()
            sizes[key] = (icon_width, value.sizeHint().width() if not value.isHidden() else 0)
        natural = margin * 2 + sum(first + (internal_gap + second if second else 0) + gap for first, second in sizes.values()) - gap
        available_width = max(1, area.width() - min(24, max(0, area.width() - 1)))
        width = min(max(1, natural), available_width)
        left, top = margin, 5 if self.visual_style == "b" else 3
        row = []
        self.rows = []
        for key, (icon_width, value_width) in sizes.items():
            cell_width = icon_width + (internal_gap + value_width if value_width else 0)
            if left + cell_width > width - margin and left > margin:
                self.rows.append(row)
                row = []
                left, top = margin, top + row_height + 3
            icon, value = self.cells[key]
            icon.setGeometry(left, top + (row_height - (self._font_size + 5)) // 2, icon_width, self._font_size + 5)
            if value_width:
                value.setGeometry(left + icon_width + internal_gap, top, min(value_width, max(1, width - margin - left - icon_width - internal_gap)), row_height)
            left += cell_width + gap
            row.append(key)
        self.rows.append(row)
        height = min(area.height(), top + row_height + (5 if self.visual_style == "b" else 3))
        self.resize(width, max(1, height))

    def sync_screen(self, screen):
        """同步宿主所在屏幕的位置；隐藏状态保持隐藏。"""
        if self._cleaned or not self.values:
            return
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        self._screen = screen
        area = QRect(screen.availableGeometry())
        self._arrange(area)
        x = area.left() + (area.width() - self.width()) // 2
        y = area.top() + max(72, round(area.height() * .12))
        self.move(max(area.left(), min(x, area.right() - self.width() + 1)),
                  max(area.top(), min(y, area.bottom() - self.height() + 1)))
        self.update()

    def show_for_screen(self, screen):
        """仅在宿主要求显示且存在目标时打开，不抢占游戏焦点。"""
        if not self.has_targets:
            return
        self.sync_screen(screen)
        self.show()

    def cleanup(self):
        """幂等清理，不创建定时器，也不在清理后重新显示。"""
        if self._cleaned:
            return
        self._cleaned = True
        if not sip.isdeleted(self):
            self.hide()
            self.close()

    def paintEvent(self, _event):
        """仅用实际一行或两行目标的窄暗衬，不再拆成多个小卡片。"""
        key = (self.width(), self.height(), self.devicePixelRatioF(), self.visual_style,
               self._background_color, self._background_opacity)
        if getattr(self, "_backdrop_key", None) != key:
            self._backdrop_key = key
            self._backdrop = style_backdrop(self.width(), self.height(), self.devicePixelRatioF(), self.visual_style,
                                            goal=True, background_color=self._background_color,
                                            opacity=self._background_opacity)
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._backdrop)
        if self.visual_style == "c" and self._background_opacity > 0:
            # 目标项之间的小侧轨沿用正文时间列节奏，不围成一组独立卡片。
            painter.setPen(QPen(QColor(229, 200, 142, round(120 * self._background_opacity / 100)), 1))
            for row in self.rows:
                for key in row[:-1]:
                    icon, value = self.cells[key]
                    right = value.geometry().right() if not value.isHidden() else icon.geometry().right()
                    x = right + 9
                    painter.drawLine(x, icon.y() + 3, x, icon.y() + icon.height() - 3)

