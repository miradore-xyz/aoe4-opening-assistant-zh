"""帝国开局助手视觉组件：近黑界面、灰色面板与清晰的文明旗帜。英文控件名均为程序接口。

crest=徽记，card=卡片，switch=开关，delegate=列表绘制器。
"""
import ctypes
import sys
import re
import json
from string import Template
from pathlib import Path
from functools import lru_cache
from PyQt5.QtCore import Qt, QSize, QRect, QRectF, QPointF, QEvent, QByteArray, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QIcon,
                        QLinearGradient, QPolygonF, QFontMetrics)
from PyQt5.QtWidgets import (QWidget, QFrame, QLabel, QCheckBox, QStyle, QStyledItemDelegate,
                            QHBoxLayout, QVBoxLayout, QGridLayout, QSizeGrip, QApplication, QPushButton, QToolTip)
from PyQt5.QtSvg import QSvgRenderer
from core import FIELDS
from app_fonts import FONT_CSS, make_ui_font

ACCENT = "#C4C4CB"  # 中性选中态；胜负绿红只在战绩组件表达比赛结果。
GOLD = "#b8bac2"  # 旧接口名保留兼容；现为中性图标色，不再使用金色。
MUTED = "#929299"
CARD_GAP = 16  # 同级卡片横纵间隔共用逻辑像素值，内部文字/按钮间距另计。

# 视觉令牌：base=背景，sidebar=侧栏，surface=面板，raised=浮层，accent=状态强调，ink=正文。
PALETTE = dict(base="#0F0F11", sidebar="#111113", surface="#161619", raised="#202023",
               gold=GOLD, accent=ACCENT, ink="#F4F4F5", muted=MUTED, edge="#27272C")
STYLE = Template("""
QWidget {background:transparent;color:$ink;font-family:$fonts;font-size:14px;}
QWidget#Manager {background:transparent;}
QDialog, QMessageBox, QFileDialog {background:$base;}
QWidget#Sidebar {background:$sidebar;border-right:1px solid $edge;}
QFrame#Card {background:$surface;border:0;border-radius:16px;}
QFrame#Hero {background:$surface;border:0;border-radius:12px;}
QFrame#CollectionPanel {background:$base;border:0;border-radius:0;}
QFrame#SessionPanel, QFrame#FocusPanel {background:$surface;border:0;border-radius:14px;}
QFrame#StepRail {background:transparent;border:0;border-radius:0;}
QFrame#Console {background:#19191c;border:0;border-radius:12px;}
QFrame#PreviewCard {background:$surface;border:0;border-radius:12px;}
QLabel {background:transparent;border:0;}
QLabel#PageTitle, QLabel#LibraryHeading {color:$ink;font-size:24px;font-weight:600;}
QLabel#DetailHeading, QLabel#SessionTitle {color:$ink;font-size:22px;font-weight:600;}
QLabel#SectionTitle {color:$ink;font-size:16px;font-weight:600;}
QLabel#CommandText, QLabel#PreviewText {color:$ink;font-size:19px;font-weight:400;}
QLabel#Muted, QLabel#Eyebrow {color:$muted;font-size:12px;}
QLabel#FieldLabel {color:#A4A4AB;font-size:13px;}
QLabel#StatusPill {color:$muted;background:transparent;border:0;padding:0;font-size:12px;}
QPushButton {background:#202023;border:1px solid #2D2D32;border-radius:8px;padding:8px 14px;color:#D6D6DB;}
QPushButton:hover {background:#2a2a2e;border-color:#44444c;}
QPushButton:pressed {background:#18181b;}
QPushButton:focus {border:1px solid $gold;}
QPushButton:disabled {color:#606068;border-color:#252529;background:#19191c;}
QPushButton#Primary {color:#151517;background:#e4e4e7;border:1px solid transparent;font-weight:600;}
QPushButton#Primary:hover {background:#fafafa;}
QPushButton#Primary:pressed {background:#c4c4cb;}
QPushButton#Primary:disabled {color:#606068;background:#252529;border-color:#303035;}
QPushButton#Quiet, QPushButton#StartButton {background:transparent;border:1px solid #2D2D32;color:#B2B2BA;}
QPushButton#Quiet:hover, QPushButton#StartButton:hover {background:$raised;border-color:#55555e;color:$ink;}
QPushButton#Tapestry {background:$raised;border:1px solid #38383e;border-radius:8px;color:$ink;padding:7px 10px;}
QPushButton#Tapestry:focus {border:1px solid $gold;}
QPushButton#Nav {text-align:left;padding:10px 12px;border:0;border-left:2px solid transparent;border-radius:8px;background:transparent;color:$muted;}
QPushButton#Nav:hover {color:$ink;background:$raised;}
QPushButton#Nav:checked {color:$ink;background:$surface;border-left-color:$accent;}
QPushButton#NavTab, QPushButton#QuietNav {background:transparent;border:1px solid transparent;border-radius:7px;padding:7px 13px;color:$muted;}
QPushButton#NavTab:hover, QPushButton#QuietNav:hover {color:$ink;background:$surface;}
QPushButton#NavTab:checked, QPushButton#QuietNav:checked {color:$ink;background:$raised;}
QPushButton#NavTab:focus, QPushButton#QuietNav:focus {border-color:$gold;}
QLineEdit, QTextEdit, QSpinBox, QComboBox, QKeySequenceEdit {background:#1E1E21;border:1px solid #303035;border-radius:8px;padding:8px 11px;selection-background-color:#45454D;selection-color:$ink;}
QLineEdit#HomeSearch {background:$surface;border:1px solid transparent;border-radius:9px;padding:9px 13px;}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus, QKeySequenceEdit:focus {border-color:$gold;}
QLineEdit#HomeSearch:focus {border-color:$gold;}
QSpinBox {min-height:20px;}
QSpinBox::up-button, QSpinBox::down-button {width:17px;background:$raised;border:0;border-radius:3px;margin:2px;}
QComboBox {min-height:20px;padding-right:24px;}
QComboBox::drop-down {width:24px;border:0;}
QComboBox QAbstractItemView {background:$raised;color:$ink;selection-background-color:#303035;border:1px solid #4c4c55;}
QListWidget {border:0;background:transparent;outline:0;padding:0;}
QListWidget::item {border:0;background:transparent;padding:0;}
QListWidget#Timeline, QListWidget#CoverLibrary {background:transparent;border:0;padding:0;}
QSplitter::handle {background:transparent;width:12px;}
QTabWidget::pane {border:0;background:transparent;}
QGroupBox {border:0;background:$surface;border-radius:16px;margin-top:0;padding:54px 22px 22px 22px;font-size:16px;font-weight:600;}
QGroupBox::title {subcontrol-origin:padding;subcontrol-position:top left;left:22px;top:20px;padding:0;color:$ink;font-size:16px;font-weight:600;}
QGroupBox QLabel {color:#A4A4AB;font-size:13px;font-weight:400;}
QGroupBox QLineEdit, QGroupBox QSpinBox, QGroupBox QComboBox, QGroupBox QKeySequenceEdit, QGroupBox QCheckBox, QGroupBox QPushButton {font-weight:400;}
QScrollArea {border:0;background:transparent;}
QScrollBar:vertical {border:0;background:transparent;width:7px;margin:2px;}
QScrollBar::handle:vertical {background:#414147;border-radius:3px;min-height:26px;}
QScrollBar::handle:vertical:hover {background:#666670;}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {background:transparent;}
QSlider::groove:horizontal {height:4px;border-radius:2px;background:#3e3e44;}
QSlider::sub-page:horizontal {background:#b8bac2;border-radius:2px;}
QSlider::handle:horizontal {background:#f4f4f5;border:3px solid #707078;width:12px;height:12px;margin:-7px 0;border-radius:9px;}
QToolTip {color:$ink;background:#252529;border:1px solid #4d4d55;padding:6px;}
QListWidget#EditorSteps {background:$surface;border:0;border-radius:8px;padding:6px;}
QListWidget#EditorSteps::item {padding:9px 11px;margin:2px;border-radius:4px;}
QListWidget#EditorSteps::item:selected {color:$ink;background:#303035;}
""").substitute(PALETTE, fonts=FONT_CSS)
_asset_root = Path(sys._MEIPASS) / 'assets' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent / 'upstream/docs/assets'
_hd_root = _asset_root / 'hd' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent / '高清图标'


def game_pixmap(path, size):
    """优先加载高清原图，按屏幕实际缩放率保留物理像素，避免先缩小再被系统放大。"""
    hd = {'landmark_hre/burgrave-palace.webp': 'burgrave-palace-2.png',
          'building_economy/town-center.webp': 'town-center.png',
          'building_economy/house.webp': 'house.png'}
    source = _hd_root / hd[path] if path in hd else _asset_root / 'aoe4' / path
    if not source.exists():
        source = _asset_root / 'aoe4' / path
    screen = QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    from game_icons import tile_pixmap
    return tile_pixmap(source, path, size, ratio)
_design_root = _asset_root / 'design' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent / '界面素材'
# QSS无法继承SVG的currentColor，使用只替换颜色的派生文件，原路径与线宽保持Lucide官方值。
_combo_arrow = (_design_root / '图标/Lucide派生/chevron-down-normal.svg').as_posix()
_combo_arrow_disabled = (_design_root / '图标/Lucide派生/chevron-down-disabled.svg').as_posix()
STYLE += ('QComboBox {padding-right:28px;}'
          'QComboBox::drop-down {width:24px;border:0;}'
          'QComboBox::down-arrow {image:url("' + _combo_arrow + '");width:16px;height:16px;}'
          'QComboBox::down-arrow:disabled {image:url("' + _combo_arrow_disabled + '");}'
          'QComboBox#MatchHistory::drop-down {width:0;}'
          'QComboBox#MatchHistory::down-arrow {image:none;width:0;height:0;}')
_tapestries = {}
_flag_root = _asset_root / 'flags' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent / '文明旗帜'
_flag_files = json.loads((_flag_root / 'manifest.json').read_text(encoding='utf-8'))
_flag_cache = {}


def civilization_flag(civilization):
    """读取455×256的文明旗原图；civilization为原开局格式的文明标识。"""
    name = civilization[0] if isinstance(civilization, list) and civilization else civilization
    name = name or 'Generic'
    if name not in _flag_cache:
        _flag_cache[name] = QPixmap(str(_flag_root / _flag_files.get(name, _flag_files['Generic'])))
    return _flag_cache[name]


def tapestry(variant='knight'):
    """缓存原创织锦背景，variant=骑士、盘龙或城堡样式。"""
    if variant not in _tapestries:
        _tapestries[variant] = QPixmap(str(_design_root / f'tapestry-{variant}.png'))
    return _tapestries[variant]


def paint_tapestry(painter, rect, variant='knight', strength=1.0):
    """旧版纹样接口保留兼容；现在统一绘制克制的中性面板。"""
    painter.fillRect(rect, QColor(PALETTE['surface']))


class TapestryButton(QPushButton):
    """兼容原入口按钮接口；采用中性色与标准键盘焦点。"""
    def __init__(self, caption, callback, variant='castle', parent=None):
        super().__init__(caption, parent)
        self.variant = variant
        self.setObjectName('Tapestry')
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(callback)
        self.setToolTip(caption)

    def paintEvent(self, event):
        # 沿用标准按钮绘制，焦点、悬停与按下状态由统一样式表显示。
        super().paintEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


def ui_font(size=14, weight=QFont.Normal, numeric=False):
    # numeric（数字用途）保留调用兼容；数字与正文共享得意黑，列对齐由布局负责。
    return make_ui_font(size, weight)

def paint_game_surface(painter, rect, patterned=True):
    """正文采用中性石墨底色；有主题的游戏装饰留给书架封面。"""
    painter.fillRect(rect, QColor(PALETTE['base']))


class GameCard(QFrame):
    def paintEvent(self, event):
        # 面板底色与圆角由统一样式表绘制，文明识别交给旗帜本身。
        super().paintEvent(event)


def text(value, role=None):
    item = QLabel(value)
    item.setTextFormat(Qt.PlainText)
    item.setWordWrap(True)
    if role:
        item.setObjectName(role)
    return item


def card(title=None, subtitle=None, name="Card"):
    frame = GameCard()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    if title:
        layout.addWidget(text(title, "SectionTitle"))
    if subtitle:
        layout.addWidget(text(subtitle, "Muted"))
    return frame, layout


def crest(size=64):
    """绘制原创盾形龙首徽记，不使用外部位图素材。"""
    image = QPixmap(size * 2, size * 2)
    image.fill(Qt.transparent)
    image.setDevicePixelRatio(2)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 64, size / 64)
    shield = QPainterPath()
    shield.moveTo(32, 3)
    shield.lineTo(55, 12)
    shield.lineTo(53, 36)
    shield.quadTo(49, 51, 32, 60)
    shield.quadTo(15, 51, 11, 36)
    shield.lineTo(9, 12)
    shield.closeSubpath()
    gradient = QLinearGradient(8, 3, 50, 60)
    gradient.setColorAt(0, QColor("#29292e"))
    gradient.setColorAt(1, QColor("#18181b"))
    p.setBrush(gradient)
    p.setPen(QPen(QColor(GOLD), 1.4))
    p.drawPath(shield)
    dragon = QPainterPath()
    dragon.moveTo(22, 43)
    dragon.lineTo(27, 34)
    dragon.lineTo(23, 29)
    dragon.lineTo(31, 17)
    dragon.lineTo(39, 16)
    dragon.lineTo(43, 23)
    dragon.lineTo(37, 22)
    dragon.lineTo(34, 27)
    dragon.lineTo(40, 32)
    dragon.lineTo(35, 43)
    dragon.lineTo(28, 48)
    p.setPen(QPen(QColor("#e4e4e7"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(dragon)
    p.drawLine(26, 17, 26, 12)
    p.drawLine(32, 14, 34, 10)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#f4f4f5"))
    p.drawEllipse(QPointF(35.5, 20.5), 1, 1)
    p.end()
    return image


class Switch(QCheckBox):
    """保留复选框的键盘和辅助功能语义，画成清晰的滑动开关。"""
    def sizeHint(self):
        base = super().sizeHint()
        return QSize(base.width() + 28, max(30, base.height()))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = (self.height() - 18) / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT if self.isChecked() else "#38383f"))
        p.drawRoundedRect(QRectF(0, y, 34, 18), 9, 9)
        p.setBrush(QColor("#202023" if self.isChecked() else "#a1a1aa"))
        p.drawEllipse(QRectF(18 if self.isChecked() else 3, y + 3, 12, 12))
        p.setPen(QColor(PALETTE["ink"] if self.isEnabled() else "#62626c"))
        p.setFont(self.font())
        p.drawText(self.rect().adjusted(46, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        if self.hasFocus():
            p.setPen(QPen(QColor(GOLD), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self.rect().adjusted(0, 1, -1, -1), 7, 7)

    def hitButton(self, point):
        return self.rect().contains(point)


class StepDelegate(QStyledItemDelegate):
    """演练轨道：56 像素两层列表，时间小标签和完整悬停说明。"""
    def paint(self, painter, option, index):
        p = painter
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(0, 1, -3, -1)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        data = index.data(Qt.UserRole + 1) or {}
        if selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#26262C'))
            p.drawRoundedRect(QRectF(rect), 8, 8)
        elif hovered:
            p.fillRect(rect, QColor('#202023'))
        # 序号轨道只承担进度顺序，不再给每个步骤套厚边卡片。
        center = QPointF(rect.left() + 20, rect.center().y())
        p.setPen(QPen(QColor('#36363d'), 1))
        line_top = rect.top() if index.row() > 0 else round(center.y())
        line_bottom = rect.bottom() if index.row() < index.model().rowCount() - 1 else round(center.y())
        p.drawLine(round(center.x()), line_top, round(center.x()), line_bottom)
        p.setBrush(QColor(ACCENT if selected else PALETTE['surface']))
        p.setPen(QPen(QColor(ACCENT if selected else '#55555f'), 1))
        p.drawEllipse(center, 10, 10)
        p.setFont(ui_font(10, QFont.DemiBold, True))
        p.setPen(QColor('#091b10' if selected else MUTED))
        p.drawText(QRectF(center.x() - 10, center.y() - 10, 20, 20), Qt.AlignCenter, str(data.get('number', '')))
        time_rect = QRect(rect.left() + 42, rect.top() + 6, max(0, rect.width() - 54), 18)
        p.setFont(ui_font(11, numeric=bool(data.get('time'))))
        p.setPen(QColor('#F4F4F5' if selected else MUTED))
        p.drawText(time_rect, Qt.AlignLeft | Qt.AlignVCenter, str(data.get('time') or '手动'))
        body_rect = QRect(rect.left() + 42, rect.top() + 25, max(0, rect.width() - 54), 22)
        p.setFont(ui_font(13, QFont.Medium if selected else QFont.Normal))
        p.setPen(QColor(PALETTE['ink'] if selected else '#d1d1d6'))
        summary = str(data.get('summary') or index.data() or '')
        p.drawText(body_rect, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(summary, Qt.ElideRight, max(0, body_rect.width())))
        if option.state & QStyle.State_HasFocus:
            p.setPen(QPen(QColor(GOLD), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect.adjusted(3, 3, -3, -3))
        p.restore()

    def helpEvent(self, event, view, option, index):
        if event.type() == QEvent.ToolTip and index.isValid():
            data = index.data(Qt.UserRole + 1) or {}
            summary = str(data.get('summary') or index.data() or '')
            timing = str(data.get('time') or '手动切换')
            QToolTip.showText(event.globalPos(), f"{timing}　{summary}", view)
            return True
        return super().helpEvent(event, view, option, index)

    def sizeHint(self, option, index):
        return QSize(100, 56)


class LibraryDelegate(QStyledItemDelegate):
    """平整开局库；24 像素移除按钮与绘制区域使用同一坐标。"""
    remove_requested = pyqtSignal(str)

    @staticmethod
    def remove_rect(item_rect):
        return QRect(item_rect.right() - 31, item_rect.top() + 10, 24, 24)

    def editorEvent(self, event, model, option, index):
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease) and event.button() == Qt.LeftButton:
            if self.remove_rect(option.rect).contains(event.pos()):
                if event.type() == QEvent.MouseButtonRelease:
                    self.remove_requested.emit(str(index.data(Qt.UserRole)))
                return True
        return super().editorEvent(event, model, option, index)

    def helpEvent(self, event, view, option, index):
        if event.type() == QEvent.ToolTip and index.isValid():
            if self.remove_rect(option.rect).contains(event.pos()):
                tip = '移除此开局（移除后可撤销）'
            else:
                tip = str(index.data() or '')
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        return super().helpEvent(event, view, option, index)

    def paint(self, painter, option, index):
        p = painter
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(0, 0, -1, 0)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected or hovered:
            p.fillRect(rect, QColor('#29292F' if selected else '#1c1c1f'))
        if selected:
            p.fillRect(rect.left(), rect.top() + 11, 2, rect.height() - 22, QColor(ACCENT))
        icon = index.data(Qt.DecorationRole)
        text_x = rect.left() + 14
        if isinstance(icon, QIcon) and not icon.isNull():
            # 文明小旗保留原始宽高比，不放大至封面图尺寸。
            picture = icon.pixmap(28, 22)
            p.drawPixmap(rect.left() + 14, rect.top() + 15, picture)
            text_x = rect.left() + 52
        lines = str(index.data() or '').split('\n')
        title = lines[0].replace('龙之骑士团 · ', '')
        subtitle = lines[-1] if len(lines) > 1 else ''
        p.setFont(ui_font(13, QFont.Medium))
        p.setPen(QColor(PALETTE['ink'] if selected else '#d1d1d6'))
        title_rect = QRect(text_x, rect.top() + 10, max(0, rect.right() - 35 - text_x), 36)
        # 短标题占一行；常见开局名称允许两行，文明说明始终留在下方。
        metrics = p.fontMetrics()
        if ' · ' in title:
            first, second = title.split(' · ', 1)
            p.drawText(title_rect, Qt.AlignLeft | Qt.AlignTop,
                       metrics.elidedText(first, Qt.ElideRight, title_rect.width()) + '\n' +
                       metrics.elidedText(second, Qt.ElideRight, title_rect.width()))
        elif metrics.horizontalAdvance(title) <= title_rect.width():
            p.drawText(title_rect.adjusted(0, 1, 0, 0), Qt.AlignLeft | Qt.AlignTop, title)
        else:
            first, second = '', title
            for letter in title:
                if metrics.horizontalAdvance(first + letter) > title_rect.width():
                    break
                first += letter
            second = title[len(first):].lstrip()
            first = first.rstrip()
            if second and second[0] in '）】》、，。；：！？' and first:
                second = first[-1] + second
                first = first[:-1]
            p.drawText(title_rect, Qt.AlignLeft | Qt.AlignTop,
                       first + '\n' + metrics.elidedText(second, Qt.ElideRight, title_rect.width()))
        p.setFont(ui_font(12))
        p.setPen(QColor('#b5beb8' if selected else MUTED))
        subtitle_rect = QRect(text_x, rect.top() + 51, max(0, rect.right() - 12 - text_x), 19)
        p.drawText(subtitle_rect, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(subtitle, Qt.ElideRight, subtitle_rect.width()))
        close = self.remove_rect(option.rect)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#34343b' if hovered else '#252529'))
        p.drawRoundedRect(QRectF(close), 4, 4)
        # 移除标记和进入箭头共用Lucide，不再手画不同粗细的几何符号。
        icon_rect = QRect(close.center().x() - 7, close.center().y() - 7, 16, 16)
        line_icon("x", '#ffffff' if hovered else '#b5b5be').paint(p, icon_rect)
        p.setPen(QPen(QColor('#28282e'), 1))
        p.drawLine(rect.left() + 14, rect.bottom(), rect.right() - 12, rect.bottom())
        if option.state & QStyle.State_HasFocus:
            p.setPen(QPen(QColor(GOLD), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect.adjusted(4, 4, -4, -4))
        p.restore()

    def sizeHint(self, option, index):
        return QSize(100, 76)


class CoverDelegate(LibraryDelegate):
    """开局书架封面。Cover=上半部插画，整张卡片可进入演练。"""
    open_requested = pyqtSignal(str)
    CELL_GAP_ROLE = Qt.UserRole + 4
    CARD_HEIGHT = 180  # 文明缩略图、两行标题、独立操作行共用的逻辑高度。

    @classmethod
    def card_rect(cls, item_rect, index):
        """绘制、点击与提示共用卡片边界，列间留白不算进卡片内容。"""
        gap = index.data(cls.CELL_GAP_ROLE)
        gap = gap if isinstance(gap, int) and 0 <= gap <= 32 else 0
        return item_rect.adjusted(0, 0, -gap, -16)

    def editorEvent(self, event, model, option, index):
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease) and event.button() == Qt.LeftButton:
            card = self.card_rect(option.rect, index)
            if self.remove_rect(card).contains(event.pos()):
                if event.type() == QEvent.MouseButtonRelease:
                    self.remove_requested.emit(str(index.data(Qt.UserRole)))
                return True
            if event.type() == QEvent.MouseButtonRelease and card.contains(event.pos()):
                self.open_requested.emit(str(index.data(Qt.UserRole)))
                return True
        return QStyledItemDelegate.editorEvent(self, event, model, option, index)

    def helpEvent(self, event, view, option, index):
        if event.type() == QEvent.ToolTip and index.isValid():
            if self.remove_rect(self.card_rect(option.rect, index)).contains(event.pos()):
                tip = '移除此开局（移除后可撤销）'
            else:
                details = index.data(Qt.UserRole + 3) or {}
                lines = [str(index.data() or '')]
                if details.get('author'):
                    lines.append('作者：' + str(details['author']))
                if details.get('steps') is not None:
                    lines.append(f"共 {details['steps']} 步，点击进入演练")
                tip = '\n'.join(lines)
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        return super().helpEvent(event, view, option, index)

    @staticmethod
    def _title_lines(title, metrics, width):
        """按实际字宽排成两行，避免中文标题被机械截断成半个词。"""
        if metrics.horizontalAdvance(title) <= width:
            return [title]
        first = ''
        for letter in title:
            if metrics.horizontalAdvance(first + letter) > width:
                break
            first += letter
        second = title[len(first):].lstrip()
        if second and second[0] in '）】》、，。；：！？' and first:
            second = first[-1] + second
            first = first[:-1]
        return [first.rstrip(), metrics.elidedText(second, Qt.ElideRight, width)]

    def paint(self, painter, option, index):
        p = painter
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.card_rect(option.rect, index))
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        shape = QPainterPath()
        shape.addRoundedRect(rect, 13, 13)
        p.setClipPath(shape)
        p.fillRect(rect, QColor('#1f1f23' if hovered else PALETTE['surface']))
        cover = QRectF(rect.left() + 16, rect.top() + 17, 58, 33)
        details = index.data(Qt.UserRole + 3) or {}
        # 封面展示完整文明旗帜，保留旗上龙首、鹰冠等图案，不裁掉上下边缘。
        picture = civilization_flag(details.get('civilization', 'Generic'))
        p.fillRect(cover, QColor('#202023'))
        if not picture.isNull():
            scale = min(cover.width() / picture.width(), cover.height() / picture.height())
            target = QRectF(0, 0, picture.width() * scale, picture.height() * scale)
            target.moveCenter(cover.center())
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(target, picture, QRectF(picture.rect()))
        p.setClipping(False)
        lines = str(index.data() or '').split('\n')
        title = str(details.get('name') or lines[0]).replace('龙之骑士团 · ', '')
        civilization = lines[-1] if len(lines) > 1 else ''
        inset = 16
        text_width = max(0, round(rect.width() - inset * 2))
        p.setFont(ui_font(14, QFont.DemiBold))
        p.setPen(QColor(PALETTE['ink']))
        title_lines = self._title_lines(title, p.fontMetrics(), text_width)
        title_top = round(rect.top() + 67)
        for number, line in enumerate(title_lines):
            p.drawText(QRectF(rect.left() + inset, title_top + number * 20,
                             text_width, 20), Qt.AlignLeft | Qt.AlignVCenter, line)
        p.setFont(ui_font(11))
        p.setPen(QColor(MUTED))
        footer_y = round(rect.bottom() - 29)
        metadata_width = max(0, round(rect.width() - 116))
        p.drawText(QRectF(cover.right() + 11, cover.top(), max(0, rect.width() - 116), cover.height()),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(civilization, Qt.ElideRight, metadata_width))
        step_count = details.get('steps')
        if step_count is not None:
            p.drawText(QRectF(rect.left() + inset, footer_y, text_width, 19),
                       Qt.AlignLeft | Qt.AlignVCenter, f'{step_count} 个步骤')
        # 辅助信息和操作共用一条独立底栏，避免与两行长标题拥挤。
        p.setPen(QPen(QColor('#27272C'), 1))
        p.drawLine(QPointF(rect.left() + inset, footer_y - 9), QPointF(rect.right() - inset, footer_y - 9))
        # 卡片内的进入动作只保留文字和箭头，整张卡片仍可点击。
        action = QRectF(rect.right() - 116, rect.bottom() - 30, 102, 24)
        action_color = QColor('#f4f4f5' if hovered else '#b9b9c1')
        p.setPen(action_color)
        p.drawText(action.adjusted(7, 0, -18, 0), Qt.AlignCenter, '开始演练')
        arrow = QRect(round(action.right() - 21), round(action.center().y() - 8), 16, 16)
        line_icon("chevron", action_color).paint(p, arrow)
        # × 始终可见，绘制和点击坐标沿用移除接口，避免误入开局。
        close = self.remove_rect(self.card_rect(option.rect, index))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(14, 14, 16, 225 if hovered else 205))
        p.drawRoundedRect(QRectF(close), 7, 7)
        icon_rect = QRect(close.center().x() - 7, close.center().y() - 7, 16, 16)
        line_icon("x", '#ffffff' if hovered else '#d4d4d8').paint(p, icon_rect)
        if selected or option.state & QStyle.State_HasFocus:
            p.setPen(QPen(QColor('#85858F' if selected else GOLD), 1.2 if selected else 1, Qt.SolidLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(0.7, 0.7, -0.7, -0.7), 13, 13)
        p.restore()

    def sizeHint(self, option, index):
        assigned = index.data(Qt.SizeHintRole)
        return assigned if isinstance(assigned, QSize) else QSize(200, self.CARD_HEIGHT + 16)


_LINE_ICONS = {
    "search": "search", "user-search": "user-search", "arrow-up": "arrow-up", "back": "arrow-left", "grid": "layout-grid",
    "sound": "volume-2", "window": "app-window", "folder": "folder",
    "settings": "settings", "help": "circle-question-mark", "history": "clock",
    "filter": "list-filter", "reset": "rotate-cw", "chevron": "chevron-right",
    "online": "globe", "new": "plus", "edit": "pencil", "import": "folder-input",
    "export": "upload", "x": "x", "close": "x", "chevron-down": "chevron-down", "maximize": "maximize", "play": "play", "pause": "pause",
    "lock-keyhole": "lock-keyhole", "lock-keyhole-open": "lock-keyhole-open",
}


@lru_cache(maxsize=64)
def _lucide_icon(name, rgba):
    """Lucide（线条图标库）原SVG只替换颜色，20逻辑像素以双倍密度绘制。"""
    color = QColor.fromRgba(rgba)
    source = (_design_root / "图标/Lucide" / f"{name}.svg").read_bytes()
    renderer = QSvgRenderer(QByteArray(source.replace(b"currentColor", color.name().encode("ascii"))))
    image = QPixmap(40, 40)
    image.fill(Qt.transparent)
    image.setDevicePixelRatio(2)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setOpacity(color.alphaF())
    renderer.render(painter, QRectF(0, 0, 20, 20))
    painter.end()
    return QIcon(image)


def line_icon(kind, color=GOLD):
    """通用操作统一使用Lucide；reset为顺时针单箭头刷新，history只保留时钟。"""
    return _lucide_icon(_LINE_ICONS.get(kind, "upload"), QColor(color).rgba())


class Grip(QSizeGrip):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#676770"), 1.5, Qt.SolidLine, Qt.RoundCap))
        for offset in (4, 8, 12):
            p.drawLine(self.width() - offset, self.height() - 2, self.width() - 2, self.height() - offset)


class Resources(QWidget):
    """游戏图标与人数配对；各项中文说明保留在悬停提示中。"""
    definitions = {
        "food": (FIELDS["food"], "resource/resource_food.webp"),
        "wood": (FIELDS["wood"], "resource/resource_wood.webp"),
        "gold": (FIELDS["gold"], "resource/resource_gold.webp"),
        "stone": (FIELDS["stone"], "resource/resource_stone.webp"),
        "builder": (FIELDS["builder"], "resource/repair.webp"),
        "villager_count": (FIELDS["villager_count"], "unit_worker/villager.webp"),
        "population_count": (FIELDS["population_count"], "building_economy/house.webp"),
        "age": (FIELDS["age"], "age/age_1.webp"),
    }

    def __init__(self, assets, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.columns = 4
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.cells = {}
        for key, (name, path) in self.definitions.items():
            # 即使未显示、尚未加入布局，也由资源栏持有并随其销毁。
            frame = QFrame(self)
            frame.hide()  # 仅由set_step按已有目标显隐，父级show不会带出未指定项。
            frame.setStyleSheet("QFrame{background:#202023;border:0;border-radius:6px;} QLabel{border:0;background:transparent;}")
            row = QHBoxLayout(frame)
            row.setContentsMargins(5, 3, 5, 3)
            row.setSpacing(5)
            picture = QLabel()
            picture.setPixmap(game_pixmap(path, 18))
            value = text("—")
            value.setFont(ui_font(12, QFont.DemiBold, True))
            value.setStyleSheet("color:#e4e4e7;font-size:12px;")
            row.addWidget(picture)
            row.addWidget(value)
            row.addStretch()
            self.cells[key] = (frame, picture, value)
        self.setMinimumHeight(0)

    def set_step(self, step):
        position = 0
        for key, (frame, picture, value) in self.cells.items():
            number = step.get("resources", {}).get(key, -1) if key in ("food", "wood", "gold", "stone", "builder") else step.get(key, -1)
            shown = number >= 0
            frame.setVisible(shown)
            if shown:
                name, path = self.definitions[key]
                if key == "age":
                    picture.setPixmap(game_pixmap(f"age/age_{number}.webp", 18))
                value.setText(str(number))
                frame.setToolTip(f"{name}：{number}（开局作者填写的建议值）")
                self.grid.addWidget(frame, position // self.columns, position % self.columns)
                position += 1
        self.setVisible(position > 0)


def dark_titlebar(window):
    """仅设置本程序的系统标题栏为深色，保留系统原有移动、缩放和关闭操作。"""
    try:
        enabled = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(int(window.winId())), 20,
                                                ctypes.byref(enabled), ctypes.sizeof(enabled))
    except (AttributeError, OSError):
        pass
