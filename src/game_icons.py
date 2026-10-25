"""游戏原始图案的彩色图标按钮。

不改变游戏图案本身；为透明素材补上按单位、建筑、科技区分的底色。
tile=图标底板，token=开局文件中的图标标记。彩色底板由界面绘制，并非游戏截图。
"""
from functools import lru_cache
from pathlib import Path

from PyQt5.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PyQt5.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QPixmap


def icon_category(token, kind=None):
    """类别来自资料路径；保留资源各自的辨识色。"""
    name = str(token).replace("\\", "/").lower()
    if kind == "technology" or "/technologies/" in name or "technology_" in name:
        return "technology"
    if "landmark" in name:
        return "landmark"
    if kind == "building" or "/buildings/" in name or "building_" in name:
        return "building"
    if "unit_" in name or "/units/" in name:
        return "unit"
    for word in ("food", "wood", "gold", "stone"):
        if word in name and "resource" in name:
            return word
    return "other"


COLORS = {
    "unit": ("#8a5636", "#52351f"),
    "building": ("#5a6676", "#333e4d"),
    "landmark": ("#536073", "#27394d"),
    "technology": ("#358471", "#1c4d43"),
    "food": ("#8a4c49", "#542f30"),
    "wood": ("#5d7650", "#36472d"),
    "gold": ("#8a7845", "#514626"),
    "stone": ("#767985", "#464b58"),
    "other": ("#666a74", "#3d414b"),
}


@lru_cache(maxsize=2048)
def tile_image(path, token, kind=None):
    """以统一128像素画布合成，显示时再按屏幕缩放率平滑采样。"""
    source = QImage(str(path))
    if source.isNull():
        return QImage()
    result = QImage(128, 128, QImage.Format_ARGB32_Premultiplied)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    top, bottom = COLORS[icon_category(token, kind)]
    fill = QLinearGradient(0, 0, 110, 128)
    fill.setColorAt(0, QColor(top))
    fill.setColorAt(1, QColor(bottom))
    painter.setBrush(fill)
    painter.setPen(QPen(QColor("#79909b"), 1))
    painter.drawRoundedRect(QRectF(1, 1, 126, 126), 8, 8)
    scaled = source.scaled(112, 112, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    painter.drawImage((128 - scaled.width()) // 2, (128 - scaled.height()) // 2, scaled)
    painter.end()
    return result


def tile_pixmap(path, token, size, ratio=1.0, kind=None):
    """返回带正确设备像素比例的彩色游戏图标。"""
    original = tile_image(str(Path(path)), str(token), kind)
    pixels = max(1, round(size * ratio))
    result = QPixmap.fromImage(original.scaled(pixels, pixels, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    result.setDevicePixelRatio(ratio)
    return result


@lru_cache(maxsize=2048)
def icon_data_url(path, token):
    """富文本使用同一图标，不向程序或用户目录写入衍生图片。"""
    picture = tile_image(str(path), str(token))
    if picture.isNull():
        return Path(path).as_uri()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    picture.save(buffer, "PNG")
    return "data:image/png;base64," + bytes(data.toBase64()).decode("ascii")
