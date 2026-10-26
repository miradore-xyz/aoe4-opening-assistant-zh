"""更新入口与自绘弹窗；后台联网，安装始终由玩家明确点击。

controller=更新控制器，release=发行版，progress=下载进度，installer=安装器。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading

from PyQt5.QtCore import QObject, Qt, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QTextDocument
from PyQt5.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from update_client import UpdateCancelled, UpdateClient, UpdateError
from version import APP_VERSION
from windowing import DialogBar, FramelessDialog


def config_path():
    """开发时读取源码旁配置，便携发行包读取内置资源配置。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets" / "update_config.json"
    return Path(__file__).resolve().with_name("update_config.json")


def installer_args(manager):
    """Inno Setup（安装器）等待当前进程退出，并保留现有数据目录。"""
    root = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent.parent)
    return [
        "/SILENT", "/NORESTART", "/SUPPRESSMSGBOXES", "/CURRENTUSER",
        f"/DIR={root}", f"/DATA_DIR={Path(manager.data_dir).resolve()}",
        f"/WAITPID={os.getpid()}", "/RESTART_APP=1",
    ]


def _download_icon():
    """绘制向下下载箭头，避免与本地导入的文件夹图标混淆。"""
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(2)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#e5e5e8"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(10, 3, 10, 12)
    painter.drawLine(6, 8, 10, 12)
    painter.drawLine(10, 12, 14, 8)
    painter.drawLine(4, 13, 4, 17)
    painter.drawLine(4, 17, 16, 17)
    painter.drawLine(16, 17, 16, 13)
    painter.end()
    return QIcon(pixmap)


def _bytes(value):
    """把字节数转换为便于阅读的兆字节进度。"""
    return f"{max(0, int(value)) / (1024 * 1024):.1f} MB"


class _Signals(QObject):
    """工作线程只发送信号，不访问任何界面控件。"""

    checked = pyqtSignal(object, object, bool)
    progress = pyqtSignal(object, object)
    downloaded = pyqtSignal(object, object, bool)
    verified = pyqtSignal(object, object)


class _StatusLabel(QLabel):
    """按真实字体与换行测量错误正文，避免负行间距把双行提示挤在一起。"""

    def _text_height(self, width=None):
        document = QTextDocument()
        document.setDefaultFont(self.font())
        document.setDocumentMargin(0)
        document.setPlainText(self.text())
        margins = self.contentsMargins()
        if width is not None:
            document.setTextWidth(max(1, width - margins.left() - margins.right()))
        return math.ceil(document.size().height()) + margins.top() + margins.bottom()

    def heightForWidth(self, width):
        return max(super().heightForWidth(width), self._text_height(width))

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(max(hint.height(), self._text_height(hint.width())))
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setHeight(max(hint.height(), self._text_height()))
        return hint


class _UpdateDialog(FramelessDialog):
    """与主界面一致的更新说明、进度和操作区。"""

    closing = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("软件更新")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.resize(520, 200)
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)
        self.bar = DialogBar(self, "软件更新")
        root.addWidget(self.bar)
        body = QWidget(self)
        layout = QVBoxLayout(body)
        self._body_layout = layout
        layout.setContentsMargins(24, 18, 24, 24)
        layout.setSpacing(12)
        self.heading = QLabel("正在检查更新…", body)
        self.heading.setWordWrap(True)
        self.heading.setStyleSheet("font-size:18px; font-weight:600;")
        layout.addWidget(self.heading)
        self.current = QLabel(f"当前版本 {APP_VERSION}", body)
        self.current.setStyleSheet("color:#a4a4ab;font-size:12px;")
        layout.addWidget(self.current)
        self.notes = QPlainTextEdit(body)
        self.notes.setReadOnly(True)
        self.notes.setAccessibleName("更新内容")
        self.notes.setFrameShape(QPlainTextEdit.NoFrame)
        self.notes.setStyleSheet(
            "QPlainTextEdit {background:#161619;border:0;border-radius:8px;"
            "padding:10px;color:#e4e4e7;font-size:12px;}"
        )
        self.notes.hide()
        layout.addWidget(self.notes, 1)
        self.source = QLabel(body)
        self.source.setStyleSheet("color:#a4a4ab;font-size:11px;")
        self.source.setWordWrap(True)
        self.source.hide()
        layout.addWidget(self.source)
        self.progress = QProgressBar(body)
        self.progress.setAccessibleName("更新下载进度")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setStyleSheet(
            "QProgressBar {background:#29292e;border:0;border-radius:2px;}"
            "QProgressBar::chunk {background:#B8B8C0;border-radius:2px;}"
        )
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = _StatusLabel(body)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#b8bac2;font-size:12px;")
        self.status.hide()
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(10)
        actions.addStretch(1)
        self.cancel = QPushButton("关闭", body)
        self.cancel.setObjectName("Quiet")
        self.cancel.setMinimumHeight(34)
        self.cancel.clicked.connect(self.reject)
        actions.addWidget(self.cancel)
        self.primary = QPushButton("下载更新", body)
        self.primary.setObjectName("Primary")
        self.primary.setMinimumHeight(34)
        self.primary.setEnabled(False)
        self.primary.hide()
        actions.addWidget(self.primary)
        layout.addLayout(actions)
        root.addWidget(body, 1)

    def refresh_layout(self):
        """只给真实内容留位置；状态切换后撤销旧说明面板占用的高度。"""
        self.notes.setVisible(bool(self.notes.toPlainText().strip()))
        self.source.setVisible(bool(self.source.text().strip()))
        self.status.setVisible(bool(self.status.text().strip()))
        self._body_layout.invalidate()
        self._body_layout.activate()
        self.layout().invalidate()
        self.layout().activate()
        # 按当前窗口宽度计算换行高度；首选宽度下的 sizeHint 会低估窄窗口错误正文。
        height = self.layout().totalHeightForWidth(self.width())
        self.resize(self.width(), height if height >= 0 else self.layout().sizeHint().height())

    def set_status(self, message):
        """错误与下载状态保留可见；空状态不占一行。"""
        self.status.setText(message)
        self.refresh_layout()

    def show_summary(self, heading, message=""):
        """检查中、已最新和检查失败均不沿用上一次的新版本说明。"""
        self.heading.setText(heading)
        self.notes.clear()
        self.source.clear()
        self.progress.hide()
        self.primary.hide()
        self.cancel.setText("关闭")
        self.set_status(message)

    def done(self, result):
        self.closing.emit()
        super().done(result)


class UpdateController(QObject):
    """管理一次启动检查、手动检查、可取消下载和明确确认后的安装。

    集成方式：保存为 manager.update_controller，并可把 check 连接到帮助页按钮。
    """

    def __init__(self, manager, parent=None):
        super().__init__(parent or manager)
        self.manager = manager
        self.release = None
        self.verified_path = None
        self.dialog = None
        self._checking = False
        self._downloading = False
        self._verifying = False
        self._install_requested = False
        self._cancel_download = threading.Event()
        self._stopped = threading.Event()
        manager.destroyed.connect(self._manager_destroyed)
        self._signals = _Signals(self)
        self._signals.checked.connect(self._checked)
        self._signals.progress.connect(self._progress)
        self._signals.downloaded.connect(self._downloaded)
        self._signals.verified.connect(self._verified)
        self.client = None
        self._configuration_error = ""
        try:
            configuration = json.loads(config_path().read_text(encoding="utf-8-sig"))
            self.client = UpdateClient(configuration, current_version=APP_VERSION)
        except (OSError, ValueError, UpdateError) as error:
            self._configuration_error = f"更新配置无法读取：{error}"
        self.button = QPushButton("", manager.window_bar)
        self.button.setObjectName("UpdateAvailable")
        self.button.setIcon(_download_icon())
        self.button.setIconSize(QSize(18, 18))
        self.button.setFixedSize(34, 34)
        self.button.setAccessibleName("下载并安装软件更新")
        self.button.setToolTip("有新版本可下载")
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setStyleSheet(
            "QPushButton#UpdateAvailable {background:#24382c;border:1px solid #35523f;"
            "border-radius:8px;padding:0;color:#f4f4f5;}"
            "QPushButton#UpdateAvailable:hover {background:#2d4636;}"
            "QPushButton#UpdateAvailable:focus {border:1px solid #b8bac2;}"
        )
        self.button.clicked.connect(self.show_update)
        self.button.hide()
        manager.window_bar.status_layout.addWidget(self.button)
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(self._startup_check)
        self._startup_timer.start(2500)

    def _startup_check(self):
        if not self._stopped.is_set():
            self.check(manual=False)

    def _manager_destroyed(self):
        """父窗口销毁后停止下载，工作线程不再向已销毁的界面发送结果。"""
        self._stopped.set()
        self._cancel_download.set()

    def _emit(self, signal_name, *values):
        """下载或检查期间退出时，Qt 信号对象可能已随父窗口销毁。"""
        if self._stopped.is_set():
            return False
        try:
            getattr(self._signals, signal_name).emit(*values)
            return True
        except RuntimeError:
            return False

    def _ensure_dialog(self):
        if self.dialog is None:
            self.dialog = _UpdateDialog(self.manager)
            self.dialog.primary.clicked.connect(self._primary_clicked)
            self.dialog.closing.connect(self.cancel_download)
        return self.dialog

    def _show_dialog(self):
        dialog = self._ensure_dialog()
        dialog.refresh_layout()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def check(self, manual=True):
        """检查最新发行版；自动检查不显示成功、失败或无更新的提示。"""
        # clicked(bool) 会传 False；帮助页请用 lambda: check() 保持手动语义。
        manual = bool(manual)
        if self._downloading or self._verifying:
            if manual:
                self.show_update()
            return
        if manual:
            dialog = self._ensure_dialog()
            dialog.show_summary("正在检查更新…")
            self._show_dialog()
        if self._checking:
            return
        if self.client is None:
            if manual:
                self.dialog.show_summary("暂时无法检查更新", self._configuration_error)
            return
        self._checking = True

        def work():
            try:
                release, error = self.client.check(), None
            except Exception as problem:
                release, error = None, str(problem)
            self._emit("checked", release, error, manual)

        threading.Thread(target=work, name="更新检查", daemon=True).start()

    def _checked(self, release, error, manual):
        self._checking = False
        if error:
            if manual or (self.dialog is not None and self.dialog.isVisible()):
                dialog = self._ensure_dialog()
                dialog.show_summary("暂时无法检查更新", str(error))
            return
        self.release = release
        self.verified_path = None
        self.button.setVisible(release is not None)
        if release is not None:
            self.button.setToolTip(f"发现新版本 {release.version}，点击下载")
            if manual or (self.dialog is not None and self.dialog.isVisible()):
                self._populate_release()
        elif manual or (self.dialog is not None and self.dialog.isVisible()):
            dialog = self._ensure_dialog()
            dialog.show_summary("已是最新版本")

    def _populate_release(self):
        dialog = self._ensure_dialog()
        release = self.release
        dialog.heading.setText(f"发现新版本 {release.version}")
        # 未提供发行说明时不编造改动，也不显示空面板。
        dialog.notes.setPlainText((release.notes or "").strip())
        source = "GitHub 发行版" if release.source == "GitHub" else str(release.source)
        size = f" · {_bytes(release.size)}" if release.size else ""
        dialog.source.setText(f"更新来源：{source}{size}")
        dialog.primary.show()
        dialog.primary.setEnabled(not self._downloading and not self._verifying)
        dialog.primary.setText("安装并重启" if self.verified_path else "下载更新")
        dialog.cancel.setText("取消下载" if self._downloading else "关闭")
        if not self._downloading and not self._verifying:
            dialog.progress.setVisible(bool(self.verified_path))
            dialog.set_status("下载完成，点击后安装并重启。" if self.verified_path else "")
        else:
            dialog.refresh_layout()

    def show_update(self):
        """显示新版本说明；不会因打开弹窗而自动开始下载。"""
        if self.release is None:
            self.check(manual=True)
            return
        self._populate_release()
        self._show_dialog()

    def _primary_clicked(self):
        if self.verified_path is not None:
            self.install()
        else:
            self.download()

    def download(self):
        """保存到独立数据目录；校验完成前不给出安装入口。"""
        if self._downloading or self._verifying or self.release is None or self.client is None:
            return
        self._downloading = True
        self.verified_path = None
        self._cancel_download = threading.Event()
        cancel = self._cancel_download
        release = self.release
        dialog = self._ensure_dialog()
        dialog.primary.setEnabled(False)
        dialog.cancel.setText("取消下载")
        dialog.progress.setRange(0, 0)
        dialog.progress.show()
        dialog.set_status("正在连接下载来源…")

        def progress(received, total):
            if cancel.is_set():
                raise UpdateCancelled("下载已取消")
            if not self._emit("progress", received, total):
                raise UpdateCancelled("下载已取消")

        def work():
            try:
                path = self.client.download(release, Path(self.manager.data_dir) / "更新下载", progress)
                self._emit("downloaded", path, None, cancel.is_set())
            except UpdateCancelled:
                self._emit("downloaded", None, None, True)
            except Exception as problem:
                self._emit("downloaded", None, str(problem), cancel.is_set())

        threading.Thread(target=work, name="更新下载", daemon=True).start()

    def cancel_download(self):
        """关闭更新弹窗仅取消当前下载，不影响游戏计时或现有开局。"""
        self._install_requested = False
        if self._downloading:
            self._cancel_download.set()

    def _progress(self, received, total):
        if self.dialog is None or not self._downloading:
            return
        if total:
            self.dialog.progress.setRange(0, 1000)
            self.dialog.progress.setValue(min(1000, int(received * 1000 / total)))
            self.dialog.set_status(f"已下载 {_bytes(received)} / {_bytes(total)}")
        else:
            self.dialog.progress.setRange(0, 0)
            self.dialog.set_status(f"已下载 {_bytes(received)}")

    def _downloaded(self, path, error, cancelled):
        self._downloading = False
        dialog = self._ensure_dialog()
        dialog.cancel.setText("关闭")
        dialog.primary.setEnabled(True)
        dialog.progress.setRange(0, 1000)
        if cancelled:
            dialog.progress.hide()
            dialog.set_status("下载已取消。")
            dialog.primary.setText("重新下载")
        elif error:
            dialog.progress.hide()
            dialog.set_status(str(error))
            dialog.primary.setText("重新下载")
        else:
            self.verified_path = Path(path)
            dialog.progress.setValue(1000)
            dialog.set_status("下载完成，点击后安装并重启。")
            dialog.primary.setText("安装并重启")

    def _other_modal_is_open(self):
        return any(widget is not self.dialog and widget.isVisible() and widget.isModal()
                   for widget in QApplication.topLevelWidgets())

    def install(self):
        """只有明确点击后再次校验文件，并在正常保存退出后启动新版本。"""
        if self._verifying or self._downloading or self.verified_path is None:
            return
        dialog = self._ensure_dialog()
        if self._other_modal_is_open():
            dialog.set_status("请先保存并关闭正在编辑的窗口，再安装更新。")
            return
        self._verifying = True
        self._install_requested = True
        dialog.primary.setEnabled(False)
        dialog.cancel.setEnabled(False)
        dialog.set_status("正在检查安装文件…")
        path, release = self.verified_path, self.release

        def work():
            try:
                valid = self.client.verify_download(path, release)
                self._emit("verified", path if valid else None, None if valid else "安装文件校验失败，请重新下载。")
            except Exception as problem:
                self._emit("verified", None, str(problem))

        threading.Thread(target=work, name="安装文件校验", daemon=True).start()

    def _verified(self, path, error):
        self._verifying = False
        dialog = self._ensure_dialog()
        dialog.cancel.setEnabled(True)
        dialog.primary.setEnabled(True)
        if not self._install_requested:
            dialog.set_status("安装已取消。")
            return
        self._install_requested = False
        if error or path is None:
            self.verified_path = None
            dialog.set_status(str(error or "安装文件无法读取，请重新下载。"))
            dialog.primary.setText("重新下载")
            return
        if self._other_modal_is_open():
            dialog.set_status("请先保存并关闭正在编辑的窗口，再安装更新。")
            return
        try:
            self.manager.save_settings()
            subprocess.Popen([str(path), *installer_args(self.manager)], cwd=str(Path(path).parent))
        except (OSError, ValueError) as problem:
            dialog.set_status(f"安装程序未能启动：{problem}")
            return
        # 安装器仅等待 /WAITPID 指定的当前进程自然结束，绝不强杀编辑窗口。
        self.manager.close()
