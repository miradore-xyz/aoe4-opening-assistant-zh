"""悬浮窗几何：首次位于助手所在屏幕右上角，其后保留手动位置。

available area（可用区）已经扣除任务栏；Qt 坐标为逻辑像素，不再乘缩放率。
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtWidgets import QApplication


def fit_overlay_geometry(areas, size, position=None, preferred_point=None, margin=16):
    """返回完整位于某个可用区内的矩形，支持左侧屏的负坐标。

    position（已有位置）为空时，以 preferred_point（助手中心）所在屏右上角
    摆放；否则优先使用窗口当前相交面积最大的屏幕并夹回可见区。
    """
    available = [QRect(area) for area in areas if area.isValid() and area.width() > 0 and area.height() > 0]
    if not available:
        return QRect(position or QPoint(), size)
    preferred = next((area for area in available if preferred_point is not None and area.contains(preferred_point)), available[0])
    if position is None:
        area = preferred
    else:
        current = QRect(position, size)
        overlaps = [(max(0, area.intersected(current).width()) * max(0, area.intersected(current).height()), area) for area in available]
        overlap, area = max(overlaps, key=lambda item: item[0])
        if overlap == 0:
            area = preferred
    # 极小可用区优先保证可见；通常使用16像素边距，手动位置传0保留边缘停靠。
    inset = max(0, min(int(margin), (area.width() - 1) // 2, (area.height() - 1) // 2))
    inner = area.adjusted(inset, inset, -inset, -inset)
    width, height = min(max(1, size.width()), inner.width()), min(max(1, size.height()), inner.height())
    if position is None:
        x, y = inner.right() - width + 1, inner.top()
    else:
        x = min(max(position.x(), inner.left()), inner.right() - width + 1)
        y = min(max(position.y(), inner.top()), inner.bottom() - height + 1)
    return QRect(x, y, width, height)


def fit_overlay_widget(overlay, manager, use_default=False):
    """应用经过验证的几何，更新设置输入框但不额外写盘。

    use_default（首次右上角）仅在本次应用会话第一次显示时传True。
    原有拖动保存、设置保存和尺寸保存继续负责写盘。
    """
    screens = QApplication.screens()
    primary = QApplication.primaryScreen()
    screens = ([primary] if primary is not None else []) + [screen for screen in screens if screen is not primary]
    geometry = fit_overlay_geometry([screen.availableGeometry() for screen in screens], overlay.size(),
        position=None if use_default else overlay.pos(), preferred_point=manager.frameGeometry().center(),
        margin=16 if use_default else 0)
    applying = getattr(overlay, "applying_size", False)
    overlay.applying_size = True
    try:
        # 屏幕小于常规最小尺寸时也不让窗口伸出可用区。
        overlay.setMinimumSize(min(280, geometry.width()), min(160, geometry.height()))
        overlay.setGeometry(geometry)
        manager.settings.update(x=geometry.x(), y=geometry.y(), width=geometry.width(), height=geometry.height())
        for field, value in (("x_input", geometry.x()), ("y_input", geometry.y()),
                             ("width_input", geometry.width()), ("height_input", geometry.height())):
            widget = getattr(manager, field, None)
            if widget is not None:
                widget.setValue(value)
    finally:
        overlay.applying_size = applying
    return geometry
