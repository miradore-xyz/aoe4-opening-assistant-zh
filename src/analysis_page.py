"""战绩分析页面：手动查询公开记录，不在后台轮询。

AnalysisPage=分析页面，signals=线程完成信号；网络工作使用守护线程，
界面更新回到 Qt 主线程。外部只需 AnalysisPage(data_dir, parent=None)。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from PyQt5.QtGui import QDesktopServices

from choice_controls import ClickComboBox
from match_analysis import (
    API_SOURCE, BASE_URL, MatchError, PublicMatchClient, analyze_games, civilization_name,
    map_name, mode_name, parse_player_input,
)


def _text(value, role=None):
    widget = QLabel(str(value))
    widget.setWordWrap(True)
    if role:
        widget.setObjectName(role)
    return widget


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


class AnalysisPage(QWidget):
    """独立页面；用户数据及缓存只写入传入的私人数据目录。"""

    def __init__(self, data_dir, parent=None):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.client = PublicMatchClient(self.data_dir)
        self.snapshot = None
        self.busy = False
        self._signals = None
        self.history = []
        self.setObjectName("AnalysisPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(_text("战绩分析", "PageTitle"))
        heading.addStretch()
        layout.addLayout(heading)
        query_row = QHBoxLayout()
        query_row.setSpacing(8)
        self.query = QLineEdit()
        self.query.setPlaceholderText("昵称、玩家编号或 AoE4 World 个人主页链接")
        self.query.setAccessibleName("玩家查询条件")
        self.query.setMinimumWidth(0)
        self.query.returnPressed.connect(self.lookup)
        query_row.addWidget(self.query, 1)
        self.query_button = _button("查询玩家", self.lookup, True)
        query_row.addWidget(self.query_button)
        self.refresh_button = _button("刷新战绩", self.refresh)
        self.refresh_button.setEnabled(False)
        query_row.addWidget(self.refresh_button)
        layout.addLayout(query_row)
        self.history_row = QWidget()
        recent = QHBoxLayout(self.history_row)
        recent.setContentsMargins(0, 0, 0, 0)
        recent.setSpacing(8)
        recent.addWidget(_text("最近搜索", "Muted"))
        self.history_combo = ClickComboBox()
        self.history_combo.setAccessibleName("最近搜索记录")
        self.history_combo.setSizeAdjustPolicy(ClickComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.history_combo.setMinimumContentsLength(8)
        self.history_combo.setMinimumWidth(0)
        self.history_combo.activated.connect(self._use_history)
        recent.addWidget(self.history_combo, 1)
        self.delete_history = _button("×", self._delete_history)
        self.delete_history.setFixedWidth(32)
        self.delete_history.setAccessibleName("删除选中的搜索记录")
        self.delete_history.setToolTip("只删除这条搜索记录，已获取的战绩仍会保留。")
        recent.addWidget(self.delete_history)
        self.history_row.hide()
        layout.addWidget(self.history_row)
        self.notice = _text("", "Muted")
        self.notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.notice)

        self.player_choices = QListWidget()
        self.player_choices.setAccessibleName("昵称搜索结果")
        self.player_choices.setMinimumHeight(0)
        self.player_choices.setMaximumHeight(150)
        self.player_choices.setStyleSheet("QListWidget::item {padding:8px 10px;border-radius:5px;} "
                                         "QListWidget::item:selected {background:#29382f;}")
        self.player_choices.itemDoubleClicked.connect(self._choose_player)
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
        layout.addWidget(self.scroll, 1)
        self.empty = _text("输入你的玩家编号或昵称，开始查看战绩。\n同名玩家会列出编号供你确认。", "Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.body_layout.addWidget(self.empty, 1)
        self._restore_history()
        self._restore_query()

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
        self.history = [entry for entry in self.history
                        if entry["query"].casefold() != query.casefold()
                        and not (profile_id and entry.get("profile_id") == profile_id)]
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
            if (saved.get("profile_id") == removed.get("profile_id") and removed.get("profile_id")) or saved.get("query") == removed["query"]:
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
        self.query_button.setText("正在查询…" if value else "查询玩家")

    def _job(self, function, phase):
        if self.busy:
            return
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
        self.player_choices.hide()
        self.choose_button.hide()
        if isinstance(target, int):
            self.notice.setText("正在读取公开战绩；已有缓存会保留。")
            self._job(lambda: self.client.fetch_profile(target), "profile")
        else:
            self.notice.setText("正在搜索玩家，请在结果中核对昵称与编号。")
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
            self.player_choices.clear()
            for player in result:
                item = QListWidgetItem(f"{player['name']}    ·    玩家编号 {player['profile_id']}")
                item.setData(Qt.UserRole, player["profile_id"])
                self.player_choices.addItem(item)
            if result:
                self.player_choices.setCurrentRow(0)
                self.player_choices.show()
                self.choose_button.show()
                self.notice.setText(f"找到 {len(result)} 个玩家；请选择你的账号，双击也可查看。")
            else:
                self.notice.setText("没有找到该昵称。请核对名称，或粘贴个人主页链接。")
        else:
            self._show_snapshot(result)
            self._save_query(result["profile_id"])
            self._add_history(self.query.text(), result["profile_id"], result["profile"].get("name", ""))

    def _job_error(self, message):
        self.notice.setText(message)
        self.notice.setStyleSheet("color:#e6b8ad;")
        self.notice.show()

    def _job_finished(self):
        self._set_busy(False)

    def _clear_body(self):
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()

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
        profile = snapshot["profile"]
        title = _text(str(profile.get("name") or f"玩家 {snapshot['profile_id']}"), "SectionTitle")
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(title, 1)
        link = _button("个人主页 ↗", lambda: QDesktopServices.openUrl(QUrl(f"{BASE_URL}/players/{snapshot['profile_id']}")))
        top.addWidget(link)
        self.body_layout.addLayout(top)
        self.mode = ClickComboBox()
        self.mode.setAccessibleName("战绩统计模式")
        modes = self.report["modes"]
        ordered = sorted(modes, key=lambda code: (-self.report["by_mode"][code]["count"], code))
        for code in ordered:
            count = self.report["by_mode"][code]["count"]
            self.mode.addItem(f"{mode_name(code)} · {count} 场", code)
        self.mode.setEnabled(bool(ordered))
        if not ordered:
            self.mode.addItem("没有已确认胜负的公开记录", None)
        self.body_layout.addWidget(self.mode)
        self.detail = QWidget()
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_layout.setSpacing(14)
        self.body_layout.addWidget(self.detail)
        self.mode.currentIndexChanged.connect(self._render_mode)
        self._render_mode()
        excluded = sum(self.report["skipped"].values())
        note = f"本地保留最近 {len(snapshot['games'])} 场公开记录。"
        if excluded:
            reasons = "、".join(f"{key} {value} 场" for key, value in self.report["skipped"].items())
            note += "统计已排除：" + reasons + "。"
        note += "各模式分别统计；未公开或尚未同步的对局不会计入。"
        self.body_layout.addWidget(_text(note, "Muted"))
        source_link = _text(f'<a style="color:#b8bac2" href="{API_SOURCE}">数据来源与接口说明 ↗</a>', "Muted")
        source_link.setOpenExternalLinks(True)
        self.body_layout.addWidget(source_link)
        self.body_layout.addStretch()
        self.refresh_button.setEnabled(not self.busy)

    def _render_mode(self, *args):
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        code = self.mode.currentData()
        info = self.report["by_mode"].get(code)
        if info is None:
            self.detail_layout.addWidget(_text("暂无可分析的已完成公开对局。请稍后刷新，或确认玩家编号。", "Muted"))
            return
        rows = [row for row in self.report["rows"] if row["mode"] == code]
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
        self.detail_layout.addWidget(_text("最近对局", "SectionTitle"))
        game_values = []
        for row in rows:
            delta = row["strength_delta"]
            strength = "评分未提供" if delta is None else f"对手队伍均分{'高' if delta >= 0 else '低'} {abs(delta):.0f}"
            game_values.append([_time(row["started_at"]),
                                map_name(row["map"]) + "\n" + civilization_name(row["civilization"]),
                                ("、".join(row["opponents"]) or "未提供对手") + "\n" + strength,
                                "胜利" if row["result"] == "win" else "失利", _duration(row["duration"]), ""])
        self.game_table = self._table(["时间", "地图 / 文明", "对手", "结果", "时长", "复盘"], game_values, 54)
        self.game_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for column, width in ((0, 100), (1, 145), (3, 48), (4, 56), (5, 62)):
            self.game_table.setColumnWidth(column, width)
        for index, row in enumerate(rows):
            color = "#51c283" if row["result"] == "win" else "#e3958b"
            self.game_table.item(index, 3).setForeground(QColor(color))
            delta = row["strength_delta"]
            if delta is None:
                strength = "双方评分不完整，无法比较对手强弱。"
            else:
                method = "匹配分（MMR）" if row["strength_kind"] == "mmr" else "排位积分"
                strength = f"按双方队伍平均{method}比较：对手{'高' if delta >= 0 else '低'} {abs(delta):.0f} 分。"
            self.game_table.item(index, 2).setToolTip("、".join(row["opponents"]) + "\n" + strength)
            review = _button("打开 ↗", lambda checked=False, url=row["url"]: QDesktopServices.openUrl(QUrl(url)))
            review.setStyleSheet("padding:4px 6px;border:0;background:transparent;color:#d4d4d8;")
            review.setToolTip("在默认浏览器打开该场对局的 AoE4 World 复盘网页；网页可能没有详细资料。")
            self.game_table.setCellWidget(index, 5, review)
        self.detail_layout.addWidget(self.game_table)

    def _table(self, headers, values, row_height):
        table = QTableWidget(len(values), len(headers))
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
        table.horizontalHeader().setMinimumSectionSize(44)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.horizontalHeader().setFixedHeight(30)
        table.setStyleSheet("QTableWidget {background:#161618;border:0;border-radius:8px;outline:0;}"
                            "QTableWidget::item {padding:4px 6px;border-bottom:1px solid #252529;}"
                            "QTableWidget::item:selected {background:#29382f;}"
                            "QHeaderView::section {background:#1b1b1e;color:#a4a4ab;border:0;padding:5px 6px;}"
                            "QTableCornerButton::section {background:#1b1b1e;border:0;}")
        for index, row in enumerate(values):
            table.setRowHeight(index, row_height)
            for column, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(index, column, item)
        table.setFixedHeight(32 + row_height * len(values))
        table.setMinimumWidth(0)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return table

