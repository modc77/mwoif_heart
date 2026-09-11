from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from mwoif import __app_name__, __version__
from mwoif.core.config import PROJECT_ROOT
from mwoif.ui.controller import UiController
from mwoif.ui.theme import APP_QSS, COLORS


JsonDict = dict[str, Any]
PREF_PATH = PROJECT_ROOT / "state" / "ui_preferences.json"


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def fmt_dt(value: Any) -> str:
    if value in (None, ""):
        return "—"
    text = str(value)
    return text.replace("T", " ")[:19]


def scrub_log(text: str) -> str:
    # Core already redacts secrets. This is a second presentation-layer guard.
    out = str(text)
    out = re.sub(r"(?i)(password\s*[=:]\s*)([^\s,}]+)", r"\1<REDACTED>", out)
    out = re.sub(r"(?i)(sessionKey\s*[=:]\s*)([^\s,}]+)", r"\1<REDACTED>", out)
    out = re.sub(r"(?i)(authorization\s*[=:]\s*)([^\s,}]+)", r"\1<REDACTED>", out)
    return out


def load_prefs() -> dict[str, Any]:
    defaults = {
        "template_sender_hs_id": 3,
        "template_receiver_hr_id": 3,
        "shared_credential_name": "default",
        "max_receiver_hearts": 3000,
        "friend_batch_slots": 50,
        "fast_batch_size": 50,
        "fast_sender_workers": 10,
        "fast_network_workers": 20,
        "warm_pool_target": 100,
        "warm_login_workers": 20,
        "auto_warm_pool": True,
        "performance_profile": "สมดุล",
        "developer_log": False,
    }
    try:
        if PREF_PATH.exists():
            raw = json.loads(PREF_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                defaults.update({k: v for k, v in raw.items() if k in defaults})
    except Exception:
        pass
    return defaults


def save_prefs(data: dict[str, Any]) -> None:
    PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREF_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    log = Signal(str)


class Task(QRunnable):
    def __init__(self, fn: Callable[[Callable[[str], None]], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(self.signals.log.emit)
            self.signals.finished.emit(result)
        except Exception as exc:
            self.signals.error.emit(f"{type(exc).__name__}: {exc}")


class MetricCard(QFrame):
    def __init__(self, label: str, value: str = "—", note: str = "") -> None:
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(1)
        self.value = QLabel(value)
        self.value.setObjectName("metricValue")
        self.label = QLabel(label)
        self.label.setObjectName("metricLabel")
        layout.addWidget(self.value)
        layout.addWidget(self.label)
        if note:
            note_label = QLabel(note)
            note_label.setObjectName("hint")
            layout.addWidget(note_label)

    def set_value(self, value: Any) -> None:
        self.value.setText(str(value))


class Section(QFrame):
    def __init__(self, title: str | None = None) -> None:
        super().__init__()
        self.setObjectName("section")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(15, 13, 15, 13)
        self.box.setSpacing(10)
        if title:
            lbl = QLabel(title)
            lbl.setObjectName("sectionTitle")
            self.box.addWidget(lbl)


class TableModel(QAbstractTableModel):
    def __init__(self, headers: list[str]) -> None:
        super().__init__()
        self.headers = headers
        self.rows: list[list[Any]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        value = self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return "—" if value in (None, "") else str(value)
        if role == Qt.ItemDataRole.UserRole:
            return value
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in {0, 3, 4, 5, 6}:
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None

    def set_rows(self, rows: list[list[Any]]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class DataTable(QTableView):
    def __init__(self, headers: list[str]) -> None:
        super().__init__()
        self.model_data = TableModel(headers)
        self.setModel(self.model_data)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(31)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.setSortingEnabled(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.setMinimumHeight(220)

    def fill(self, rows: list[list[Any]]) -> None:
        self.model_data.set_rows(rows)

    def value_at(self, row: int, column: int) -> Any:
        if 0 <= row < len(self.model_data.rows) and 0 <= column < len(self.model_data.headers):
            return self.model_data.rows[row][column]
        return None

    def current_row(self) -> int:
        return self.currentIndex().row()


class PasswordDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("ตั้งรหัสกลางของไอดีส่ง")
        self.setModal(True)
        self.setFixedWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("รหัสกลางของไอดีส่ง")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel("รหัสนี้ถูกเข้ารหัสใน Vault และใช้กับไอดีส่งที่ตั้งเป็น Shared Password")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(hint)
        self.name = QLineEdit("default")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        password_row = QWidget()
        password_box = QHBoxLayout(password_row)
        password_box.setContentsMargins(0, 0, 0, 0)
        password_box.setSpacing(6)
        password_box.addWidget(self.password, 1)
        self.password_toggle = QPushButton("แสดง")
        self.password_toggle.setCheckable(True)
        self.password_toggle.setFixedWidth(62)
        self.password_toggle.setToolTip("แสดง/ซ่อนรหัสผ่าน (Local เท่านั้น)")
        self.password_toggle.toggled.connect(self._toggle_password)
        password_box.addWidget(self.password_toggle)
        form = QFormLayout()
        form.addRow("ชื่อชุดรหัส", self.name)
        form.addRow("รหัสผ่าน", password_row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("บันทึก")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ยกเลิก")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _toggle_password(self, checked: bool) -> None:
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        self.password_toggle.setText("ซ่อน" if checked else "แสดง")


class SenderDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("เพิ่มไอดีส่งใจ")
        self.setModal(True)
        self.setFixedWidth(430)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("เพิ่มไอดีส่งใจ")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.email = QLineEdit()
        self.email.setPlaceholderText("demo00001@gmail.com")
        self.label = QLineEdit()
        self.label.setPlaceholderText("Demo00001")
        form = QFormLayout()
        form.addRow("อีเมล", self.email)
        form.addRow("ชื่อเรียก", self.label)
        layout.addLayout(form)
        hint = QLabel("ใช้รหัสกลางที่ตั้งไว้ในระบบ ไม่ต้องกรอกรหัสซ้ำทุกไอดี")
        hint.setObjectName("hint")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("เพิ่มไอดี")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ยกเลิก")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class BulkDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("เพิ่มไอดีส่งแบบชุด")
        self.setModal(True)
        self.setFixedWidth(450)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("เพิ่มไอดีส่งแบบชุด")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel("สำหรับบัญชีที่สร้างไว้แล้ว ระบบนี้เพิ่มเฉพาะข้อมูลเข้า Sender Pool")
        hint.setObjectName("hint")
        layout.addWidget(hint)

        self.prefix = QLineEdit("demo")
        self.domain = QLineEdit("gmail.com")
        self.start = QSpinBox(); self.start.setRange(1, 999999); self.start.setValue(1)
        self.end = QSpinBox(); self.end.setRange(1, 999999); self.end.setValue(20)
        self.width = QSpinBox(); self.width.setRange(1, 8); self.width.setValue(5)
        self.label_prefix = QLineEdit("Demo")
        form = QFormLayout()
        form.addRow("คำนำหน้า", self.prefix)
        form.addRow("โดเมน", self.domain)
        form.addRow("เริ่ม", self.start)
        form.addRow("ถึง", self.end)
        form.addRow("จำนวนหลัก", self.width)
        form.addRow("ชื่อเรียก", self.label_prefix)
        layout.addLayout(form)
        self.preview = QLabel("ตัวอย่าง: demo00001@gmail.com → demo00020@gmail.com")
        self.preview.setObjectName("hint")
        layout.addWidget(self.preview)
        for widget in (self.prefix, self.domain, self.start, self.end, self.width):
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._update_preview)
            else:
                widget.valueChanged.connect(self._update_preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("เพิ่มเข้าระบบ")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ยกเลิก")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_preview(self, *_args: Any) -> None:
        p = self.prefix.text().strip()
        d = self.domain.text().strip().lstrip("@")
        w = self.width.value()
        self.preview.setText(
            f"ตัวอย่าง: {p}{str(self.start.value()).zfill(w)}@{d} → {p}{str(self.end.value()).zfill(w)}@{d}"
        )


class MwoifHeartWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.controller = UiController()
        self.pool = QThreadPool.globalInstance()
        self.prefs = load_prefs()
        self._busy = 0
        self._tasks: set[Task] = set()
        self._sender_rows: list[dict[str, Any]] = []
        self._job_rows: list[dict[str, Any]] = []
        self._event_rows: list[dict[str, Any]] = []
        self._active_job_id: int | None = None
        self._last_cooldown_count = 0
        self._runtime_workers: dict[int, dict[str, Any]] = {}
        self._runtime_batch_no = 0
        self._runtime_requested = 0
        self._runtime_completed = 0
        self._runtime_failed = 0
        self._runtime_started_at: float | None = None
        self._developer_log_enabled = bool(self.prefs.get("developer_log", False))
        self._worker_refresh_pending = False
        self._warm_task_active = False

        self.setWindowTitle("M WOIF Heart • Local Control Center")
        self.resize(1180, 760)
        self.setMinimumSize(1030, 680)
        self.setStyleSheet(APP_QSS)
        self._build()
        self._navigate(0)
        # Let the Qt event loop start before touching DB/network-backed state.
        # This also prevents startup refresh work from racing window construction.
        QTimer.singleShot(150, self.refresh_all)
        QTimer.singleShot(900, self._auto_warm_pool)
        self._warm_maintenance_timer = QTimer(self)
        self._warm_maintenance_timer.setInterval(60000)
        self._warm_maintenance_timer.timeout.connect(self._warm_maintenance_tick)
        self._warm_maintenance_timer.start()

    # ---------- shell ----------
    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(186)
        side = QVBoxLayout(sidebar); side.setContentsMargins(14, 17, 14, 14); side.setSpacing(5)
        brand = QLabel("M WOIF HEART"); brand.setObjectName("brand")
        brand_sub = QLabel("LOCAL LAB • FINAL V1"); brand_sub.setObjectName("brandSub")
        side.addWidget(brand); side.addWidget(brand_sub); side.addSpacing(14)

        self.nav: list[QPushButton] = []
        nav_items = [
            ("♥  ปั้มใจ", 0),
            ("▦  ไอดีส่ง", 1),
            ("▤  งาน", 2),
            ("!  Error ล่าสุด", 3),
            ("≡  บันทึก", 4),
            ("⚙  ตั้งค่า", 5),
        ]
        for text, index in nav_items:
            btn = QPushButton(text)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, i=index: self._navigate(i))
            side.addWidget(btn)
            self.nav.append(btn)
        side.addStretch(1)
        version = QLabel(__version__); version.setObjectName("hint")
        side.addWidget(version)
        shell.addWidget(sidebar)

        right = QWidget(); right_box = QVBoxLayout(right); right_box.setContentsMargins(0, 0, 0, 0); right_box.setSpacing(0)
        topbar = QFrame(); topbar.setObjectName("topbar"); topbar.setFixedHeight(54)
        top = QHBoxLayout(topbar); top.setContentsMargins(20, 0, 20, 0)
        self.top_title = QLabel("ปั้มใจ"); self.top_title.setStyleSheet("font-weight:700;font-size:11pt;")
        top.addWidget(self.top_title)
        top.addStretch(1)
        self.mode_chip = QLabel("●  DIRECT HTTP")
        self.mode_chip.setStyleSheet(f"color:{COLORS['green']};font-weight:700;font-size:8.5pt;")
        top.addWidget(self.mode_chip)
        self.busy_chip = QLabel("พร้อม")
        self.busy_chip.setStyleSheet(f"color:{COLORS['muted']};padding-left:14px;")
        top.addWidget(self.busy_chip)
        refresh = QPushButton("รีเฟรช")
        refresh.clicked.connect(self.refresh_all)
        top.addWidget(refresh)
        right_box.addWidget(topbar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        self.pages = QStackedWidget()
        self.page_home = self._build_home_page()
        self.page_senders = self._build_senders_page()
        self.page_jobs = self._build_jobs_page()
        self.page_errors = self._build_errors_page()
        self.page_activity = self._build_activity_page()
        self.page_settings = self._build_settings_page()
        for page in (self.page_home, self.page_senders, self.page_jobs, self.page_errors, self.page_activity, self.page_settings):
            self.pages.addWidget(page)
        splitter.addWidget(self.pages)
        splitter.addWidget(self._build_console())
        splitter.setSizes([570, 145])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        right_box.addWidget(splitter, 1)
        shell.addWidget(right, 1)

    def _scroll_page(self) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget(); box = QVBoxLayout(body); box.setContentsMargins(20, 16, 20, 18); box.setSpacing(12)
        scroll.setWidget(body)
        return scroll, box

    def _page_heading(self, box: QVBoxLayout, title: str, subtitle: str) -> None:
        title_lbl = QLabel(title); title_lbl.setObjectName("pageTitle")
        sub_lbl = QLabel(subtitle); sub_lbl.setObjectName("pageSub"); sub_lbl.setWordWrap(True)
        box.addWidget(title_lbl); box.addWidget(sub_lbl)

    def _build_console(self) -> QWidget:
        wrap = QFrame(); wrap.setObjectName("console")
        box = QVBoxLayout(wrap); box.setContentsMargins(10, 7, 10, 9); box.setSpacing(5)
        bar = QHBoxLayout()
        title = QLabel("บันทึกการทำงาน"); title.setStyleSheet("font-weight:650;font-size:9pt;")
        bar.addWidget(title); bar.addStretch(1)
        self.dev_log_btn = QPushButton("Dev Log")
        self.dev_log_btn.setCheckable(True)
        self.dev_log_btn.setChecked(self._developer_log_enabled)
        self.dev_log_btn.setFixedHeight(27)
        self.dev_log_btn.setToolTip("แสดง Log เทคนิคทั้งหมดสำหรับแก้ไขระบบ")
        self.dev_log_btn.toggled.connect(self._toggle_developer_log)
        clear = QPushButton("ล้าง"); clear.setFixedHeight(27); clear.clicked.connect(lambda: self.console.clear())
        copy = QPushButton("คัดลอก"); copy.setFixedHeight(27); copy.clicked.connect(lambda: QApplication.clipboard().setText(self.console.toPlainText()))
        bar.addWidget(self.dev_log_btn); bar.addWidget(clear); bar.addWidget(copy)
        box.addLayout(bar)
        self.console = QPlainTextEdit(); self.console.setReadOnly(True); self.console.setMaximumBlockCount(2500)
        box.addWidget(self.console)
        return wrap

    # ---------- home ----------
    def _build_home_page(self) -> QWidget:
        page, box = self._scroll_page()
        self._page_heading(box, "ปั้มใจ", "สร้างงานใหม่และดูสถานะจาก Production Core โดยไม่เปิด Browser ระหว่าง Replay")

        metrics = QHBoxLayout(); metrics.setSpacing(9)
        self.m_ready = MetricCard("พร้อมใช้งาน", "—")
        self.m_cooldown = MetricCard("ติดคูลดาวน์", "—")
        self.m_attention = MetricCard("ต้องตรวจสอบ", "—")
        self.m_leased = MetricCard("กำลังใช้งาน", "—")
        self.m_warm = MetricCard("พร้อมรอ", "—")
        for card in (self.m_ready, self.m_warm, self.m_cooldown, self.m_attention, self.m_leased): metrics.addWidget(card)
        box.addLayout(metrics)

        content = QHBoxLayout(); content.setSpacing(12)
        create = Section("สร้างงานรับใจ")
        create.setMinimumWidth(390); create.setMaximumWidth(455)
        self.receiver_email = QLineEdit(); self.receiver_email.setPlaceholderText("อีเมลไอดีรับใจ")
        self.receiver_password = QLineEdit(); self.receiver_password.setEchoMode(QLineEdit.EchoMode.Password); self.receiver_password.setPlaceholderText("รหัสผ่าน")
        receiver_password_row = QWidget()
        receiver_password_box = QHBoxLayout(receiver_password_row)
        receiver_password_box.setContentsMargins(0, 0, 0, 0)
        receiver_password_box.setSpacing(6)
        receiver_password_box.addWidget(self.receiver_password, 1)
        self.receiver_password_toggle = QPushButton("แสดง")
        self.receiver_password_toggle.setCheckable(True)
        self.receiver_password_toggle.setFixedWidth(62)
        self.receiver_password_toggle.setToolTip("แสดง/ซ่อนรหัสผ่านไอดีรับใจ (Local เท่านั้น)")
        self.receiver_password_toggle.toggled.connect(self._toggle_receiver_password)
        receiver_password_box.addWidget(self.receiver_password_toggle)
        self.heart_amount = QSpinBox(); self.heart_amount.setRange(1, safe_int(self.prefs.get("max_receiver_hearts"), 3000)); self.heart_amount.setValue(1); self.heart_amount.setSuffix(" ใจ")
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.addRow("อีเมล", self.receiver_email)
        form.addRow("รหัสผ่าน", receiver_password_row)
        form.addRow("จำนวน", self.heart_amount)
        create.box.addLayout(form)
        note = QLabel("ก่อนเริ่มงานระบบจะตรวจพื้นที่เพื่อนของไอดีรับ ถ้าพื้นที่ไม่พอจะถามยืนยันก่อนลบเพื่อนเท่าที่จำเป็น แล้วจึงเริ่มงาน")
        note.setObjectName("hint"); note.setWordWrap(True); create.box.addWidget(note)
        self.friend_preflight_label = QLabel("พื้นที่เพื่อน: ยังไม่ได้ตรวจ")
        self.friend_preflight_label.setObjectName("hint")
        create.box.addWidget(self.friend_preflight_label)

        adv_btn = QPushButton("ตั้งค่าขั้นสูง")
        adv_btn.setCheckable(True)
        create.box.addWidget(adv_btn)
        self.advanced = QWidget(); adv = QFormLayout(self.advanced); adv.setContentsMargins(0, 0, 0, 0)
        self.template_sender = QSpinBox(); self.template_sender.setRange(1, 999999); self.template_sender.setValue(safe_int(self.prefs.get("template_sender_hs_id"), 3))
        self.template_receiver = QSpinBox(); self.template_receiver.setRange(0, 999999); self.template_receiver.setValue(safe_int(self.prefs.get("template_receiver_hr_id"), 3)); self.template_receiver.setSpecialValueText("อัตโนมัติ")
        adv.addRow("Template Sender", self.template_sender)
        adv.addRow("Template Receiver", self.template_receiver)
        self.advanced.setVisible(False); adv_btn.toggled.connect(self.advanced.setVisible)
        create.box.addWidget(self.advanced)

        buttons = QHBoxLayout()
        self.start_job_btn = QPushButton("เริ่มทำงาน"); self.start_job_btn.setObjectName("primary"); self.start_job_btn.clicked.connect(self._start_new_job)
        self.check_btn = QPushButton("ตรวจระบบ"); self.check_btn.clicked.connect(self._run_update_check)
        self.warm_btn = QPushButton("เตรียม Sender"); self.warm_btn.clicked.connect(self._warm_pool_now)
        buttons.addWidget(self.start_job_btn, 1); buttons.addWidget(self.warm_btn); buttons.addWidget(self.check_btn)
        create.box.addLayout(buttons)
        content.addWidget(create)

        live = Section("สถานะงานล่าสุด")
        status_row = QHBoxLayout()
        self.job_status = QLabel("ยังไม่มีงานที่กำลังทำ"); self.job_status.setStyleSheet("font-weight:700;")
        self.job_id_label = QLabel("JOB —"); self.job_id_label.setObjectName("hint")
        self.stop_job_btn = QPushButton("หยุดหลัง Batch นี้")
        self.stop_job_btn.setEnabled(False)
        self.stop_job_btn.setVisible(False)
        self.stop_job_btn.clicked.connect(self._stop_active_job)
        status_row.addWidget(self.job_status); status_row.addStretch(1); status_row.addWidget(self.stop_job_btn); status_row.addWidget(self.job_id_label)
        live.box.addLayout(status_row)
        self.job_progress = QProgressBar(); self.job_progress.setRange(0, 100); self.job_progress.setValue(0)
        live.box.addWidget(self.job_progress)
        stats = QGridLayout(); stats.setHorizontalSpacing(25); stats.setVerticalSpacing(4)
        self.run_done = QLabel("0"); self.run_failed = QLabel("0"); self.run_remaining = QLabel("0"); self.run_elapsed = QLabel("—")
        for i, (label, value) in enumerate((("สำเร็จ", self.run_done), ("ผิดพลาด", self.run_failed), ("เหลือ", self.run_remaining), ("เวลา", self.run_elapsed))):
            lab = QLabel(label); lab.setObjectName("fieldLabel"); value.setStyleSheet("font-weight:700;font-size:11pt;")
            stats.addWidget(lab, 0, i); stats.addWidget(value, 1, i)
        live.box.addLayout(stats)
        batch_stats = QHBoxLayout()
        self.run_batch = QLabel("Batch —"); self.run_batch.setObjectName("hint")
        self.run_speed = QLabel("ความเร็ว —"); self.run_speed.setObjectName("hint")
        self.run_eta = QLabel("ETA —"); self.run_eta.setObjectName("hint")
        batch_stats.addWidget(self.run_batch); batch_stats.addSpacing(12); batch_stats.addWidget(self.run_speed); batch_stats.addSpacing(12); batch_stats.addWidget(self.run_eta); batch_stats.addStretch(1)
        live.box.addLayout(batch_stats)
        self.worker_table = DataTable(["Sender", "ขั้นตอน", "สถานะ"])
        self.worker_table.setMaximumHeight(165)
        self.worker_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        live.box.addWidget(self.worker_table)
        recent_title = QLabel("งานล่าสุด"); recent_title.setObjectName("fieldLabel"); live.box.addWidget(recent_title)
        self.home_jobs = DataTable(["JOB", "สถานะ", "ความคืบหน้า", "ไอดีรับ", "เวลา"])
        self.home_jobs.setMaximumHeight(135)
        live.box.addWidget(self.home_jobs)
        content.addWidget(live, 1)
        box.addLayout(content)
        box.addStretch(1)
        return page

    # ---------- senders ----------
    def _build_senders_page(self) -> QWidget:
        page, box = self._scroll_page()
        self._page_heading(box, "ไอดีส่ง", "จัดการ Sender Pool, รหัสกลาง และบัญชีแบบชุดสำหรับงาน Local")
        row = QHBoxLayout(); row.setSpacing(9)
        self.s_total = MetricCard("ทั้งหมด", "—"); self.s_ready = MetricCard("พร้อม", "—"); self.s_cd = MetricCard("คูลดาวน์", "—"); self.s_err = MetricCard("มี Error ล่าสุด", "—")
        for c in (self.s_total, self.s_ready, self.s_cd, self.s_err): row.addWidget(c)
        box.addLayout(row)
        toolbar = QHBoxLayout()
        self.sender_search = QLineEdit(); self.sender_search.setPlaceholderText("ค้นหา ID / ชื่อ / อีเมล / สถานะ"); self.sender_search.textChanged.connect(self._render_senders)
        self.sender_filter = QComboBox(); self.sender_filter.addItems(["ทั้งหมด", "พร้อมใช้งาน", "มี Error ล่าสุด"]); self.sender_filter.currentIndexChanged.connect(self._render_senders)
        add = QPushButton("+ เพิ่มไอดี"); add.clicked.connect(self._add_sender_dialog)
        bulk = QPushButton("เพิ่มแบบชุด"); bulk.clicked.connect(self._bulk_dialog)
        shared = QPushButton("ตั้งรหัสกลาง"); shared.setObjectName("primary"); shared.clicked.connect(self._password_dialog)
        warm = QPushButton("เตรียม Sender"); warm.clicked.connect(self._warm_pool_now)
        refresh = QPushButton("รีเฟรช"); refresh.clicked.connect(self.refresh_senders)
        toolbar.addWidget(self.sender_search, 1); toolbar.addWidget(self.sender_filter); toolbar.addWidget(add); toolbar.addWidget(bulk); toolbar.addWidget(shared); toolbar.addWidget(warm); toolbar.addWidget(refresh)
        box.addLayout(toolbar)
        self.sender_table = DataTable(["ID", "ชื่อ", "อีเมล", "สถานะ", "ส่งล่าสุด", "ผ่าน", "ผิดพลาด", "Error"])
        self.sender_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        box.addWidget(self.sender_table, 1)
        return page

    # ---------- jobs ----------
    def _build_jobs_page(self) -> QWidget:
        page, box = self._scroll_page()
        self._page_heading(box, "งาน", "ดูคิวงาน ความคืบหน้า และสั่งรันงานเดิมต่อจาก Core")
        toolbar = QHBoxLayout(); toolbar.addStretch(1)
        run = QPushButton("รันงานที่เลือก"); run.setObjectName("primary"); run.clicked.connect(self._run_selected_job)
        refresh = QPushButton("รีเฟรช"); refresh.clicked.connect(self.refresh_jobs)
        toolbar.addWidget(run); toolbar.addWidget(refresh); box.addLayout(toolbar)
        self.jobs_table = DataTable(["JOB", "ไอดีรับ", "สถานะ", "ขอ", "สำเร็จ", "ผ่าน", "พลาด", "เริ่ม", "Error"])
        self.jobs_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        box.addWidget(self.jobs_table, 1)
        return page

    # ---------- errors ----------
    def _build_errors_page(self) -> QWidget:
        page, box = self._scroll_page()
        self._page_heading(box, "Error ล่าสุด", "Local ไม่ปิด Sender อัตโนมัติ หน้านี้เป็นข้อมูลวินิจฉัยจาก Error ล่าสุดเท่านั้น")
        bar = QHBoxLayout(); self.error_count = QLabel("0 รายการ"); self.error_count.setObjectName("hint"); bar.addWidget(self.error_count); bar.addStretch(1)
        refresh = QPushButton("รีเฟรช"); refresh.clicked.connect(self.refresh_senders); bar.addWidget(refresh); box.addLayout(bar)
        self.error_table = DataTable(["ID", "ชื่อ", "อีเมล", "สถานะ", "ขั้นตอน", "Error", "พลาดทั้งหมด", "ล่าสุด"])
        self.error_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        box.addWidget(self.error_table, 1)
        return page

    # ---------- activity ----------
    def _build_activity_page(self) -> QWidget:
        page, box = self._scroll_page()
        self._page_heading(box, "บันทึก", "เหตุการณ์ที่บันทึกในฐานข้อมูล ใช้ไล่สถานะ Job / Round / Sender")
        bar = QHBoxLayout(); bar.addStretch(1); refresh = QPushButton("รีเฟรช"); refresh.clicked.connect(self.refresh_events); bar.addWidget(refresh); box.addLayout(bar)
        self.events_table = DataTable(["เวลา", "ประเภท", "JOB", "Sender", "ขั้นตอน", "ผล", "Error", "รายละเอียด"])
        self.events_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        box.addWidget(self.events_table, 1)
        return page

    # ---------- settings ----------
    def _build_settings_page(self) -> QWidget:
        page, box = self._scroll_page()
        self._page_heading(box, "ตั้งค่า", "ค่าของ Local UI เท่านั้น ไม่แก้โครงสร้างฐานข้อมูลและไม่เก็บรหัสผ่านไว้ในไฟล์นี้")
        grid = QHBoxLayout(); grid.setSpacing(12)
        core = Section("ระบบที่ใช้งาน")
        for title, value in [
            ("Login Provider", "Direct HTTP Exact Template"),
            ("Browser ระหว่าง Replay", "ไม่เปิด"),
            ("Pair Cooldown", "60 นาที"),
            ("Sender Selection", "สุ่ม + ไม่ซ้ำใน Job"),
            ("Fast Engine", "Batch + Real-time"),
            ("Friend Preflight", "ตรวจทุกครั้งก่อนเริ่ม"),
        ]:
            row = QHBoxLayout(); a = QLabel(title); a.setObjectName("fieldLabel"); b = QLabel(value); b.setStyleSheet("font-weight:650;"); row.addWidget(a); row.addStretch(1); row.addWidget(b); core.box.addLayout(row)
        grid.addWidget(core, 1)

        local = Section("ค่าเริ่มต้นของ UI")
        self.pref_performance = QComboBox()
        self.pref_performance.addItems(["เสถียร", "สมดุล", "เร็ว", "สูงสุด", "เทอร์โบ 100", "กำหนดเอง"])
        saved_profile = str(self.prefs.get("performance_profile") or "สมดุล")
        self.pref_performance.setCurrentText(saved_profile if saved_profile in {"เสถียร", "สมดุล", "เร็ว", "สูงสุด", "เทอร์โบ 100", "กำหนดเอง"} else "สมดุล")
        self.pref_template_sender = QSpinBox(); self.pref_template_sender.setRange(1, 999999); self.pref_template_sender.setValue(safe_int(self.prefs.get("template_sender_hs_id"), 3))
        self.pref_template_receiver = QSpinBox(); self.pref_template_receiver.setRange(0, 999999); self.pref_template_receiver.setSpecialValueText("อัตโนมัติ"); self.pref_template_receiver.setValue(safe_int(self.prefs.get("template_receiver_hr_id"), 3))
        self.pref_max_hearts = QSpinBox(); self.pref_max_hearts.setRange(1, 10000); self.pref_max_hearts.setValue(safe_int(self.prefs.get("max_receiver_hearts"), 3000))
        self.pref_friend_batch = QSpinBox(); self.pref_friend_batch.setRange(5, 100); self.pref_friend_batch.setValue(safe_int(self.prefs.get("friend_batch_slots"), 50)); self.pref_friend_batch.setSuffix(" ช่อง")
        self.pref_fast_batch = QSpinBox(); self.pref_fast_batch.setRange(5, 100); self.pref_fast_batch.setValue(safe_int(self.prefs.get("fast_batch_size"), 50)); self.pref_fast_batch.setSuffix(" ไอดี")
        self.pref_fast_workers = QSpinBox(); self.pref_fast_workers.setRange(1, 50); self.pref_fast_workers.setValue(safe_int(self.prefs.get("fast_sender_workers"), 10)); self.pref_fast_workers.setSuffix(" workers")
        self.pref_network_workers = QSpinBox(); self.pref_network_workers.setRange(1, 50); self.pref_network_workers.setValue(safe_int(self.prefs.get("fast_network_workers"), 20)); self.pref_network_workers.setSuffix(" connections")
        self.pref_warm_target = QSpinBox(); self.pref_warm_target.setRange(10, 1000); self.pref_warm_target.setValue(safe_int(self.prefs.get("warm_pool_target"), 100)); self.pref_warm_target.setSuffix(" ไอดี")
        self.pref_warm_workers = QSpinBox(); self.pref_warm_workers.setRange(1, 50); self.pref_warm_workers.setValue(safe_int(self.prefs.get("warm_login_workers"), 20)); self.pref_warm_workers.setSuffix(" workers")
        self.pref_auto_warm = QCheckBox("เตรียมอัตโนมัติเมื่อเปิดโปรแกรม"); self.pref_auto_warm.setChecked(bool(self.prefs.get("auto_warm_pool", True)))
        form = QFormLayout(); form.addRow("โหมดประสิทธิภาพ", self.pref_performance); form.addRow("Template Sender", self.pref_template_sender); form.addRow("Template Receiver", self.pref_template_receiver); form.addRow("จำนวนใจสูงสุดต่อ Job", self.pref_max_hearts); form.addRow("พื้นที่เพื่อนที่เตรียม", self.pref_friend_batch); form.addRow("ขนาด Batch", self.pref_fast_batch); form.addRow("Sender Workers", self.pref_fast_workers); form.addRow("Network Workers", self.pref_network_workers); form.addRow("Warm Pool เป้าหมาย", self.pref_warm_target); form.addRow("Warm Login Workers", self.pref_warm_workers); form.addRow("", self.pref_auto_warm)
        local.box.addLayout(form)
        self.pref_performance.currentTextChanged.connect(self._apply_performance_profile)
        save = QPushButton("บันทึกค่า"); save.setObjectName("primary"); save.clicked.connect(self._save_ui_settings); local.box.addWidget(save)
        grid.addWidget(local, 1)
        box.addLayout(grid)
        info = Section("หมายเหตุ")
        hint = QLabel("รหัสผ่าน Receiver อยู่ใน Vault ของ Job และ Shared Sender Password อยู่ใน encrypted Vault เท่านั้น หน้านี้เก็บแค่ Template ID และค่า UI ที่ไม่ใช่ความลับ")
        hint.setObjectName("hint"); hint.setWordWrap(True); info.box.addWidget(hint); box.addWidget(info)
        box.addStretch(1)
        return page

    # ---------- navigation ----------
    def _navigate(self, index: int) -> None:
        names = ["ปั้มใจ", "ไอดีส่ง", "งาน", "Error ล่าสุด", "บันทึก", "ตั้งค่า"]
        self.pages.setCurrentIndex(index)
        self.top_title.setText(names[index])
        for i, btn in enumerate(self.nav): btn.setChecked(i == index)
        if index == 1: self.refresh_senders()
        elif index == 2: self.refresh_jobs()
        elif index == 3: self.refresh_senders()
        elif index == 4: self.refresh_events()

    # ---------- tasks ----------
    def _task(
        self,
        fn: Callable[[Callable[[str], None]], Any],
        on_done: Callable[[Any], None] | None = None,
        label: str = "กำลังทำงาน",
        *,
        refresh_after: bool = False,
    ) -> None:
        task = Task(fn)
        # Keep a Python reference while QThreadPool owns/runs the QRunnable.
        # Without this, PySide wrappers can be collected before signal delivery.
        self._tasks.add(task)
        task.signals.log.connect(self.append_log)
        task.signals.error.connect(lambda e, t=task: self._task_error(e, label, t))
        task.signals.finished.connect(
            lambda result, t=task: self._task_done(result, on_done, label, refresh_after, t)
        )
        self._set_busy(True, label)
        self.pool.start(task)

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy += 1 if busy else -1
        self._busy = max(0, self._busy)
        active = self._busy > 0
        self.busy_chip.setText(label if active else "พร้อม")
        self.busy_chip.setStyleSheet(f"color:{COLORS['amber'] if active else COLORS['muted']};padding-left:14px;font-weight:{'700' if active else '500'};")

    def _task_done(
        self,
        result: Any,
        cb: Callable[[Any], None] | None,
        label: str,
        refresh_after: bool,
        task: Task,
    ) -> None:
        self._tasks.discard(task)
        self._set_busy(False)
        if cb:
            cb(result)
        # IMPORTANT: refresh/read tasks must never schedule more refresh tasks on completion.
        # The previous UI did that unconditionally, causing an exponential refresh storm:
        # refresh -> finished -> 3 refreshes -> each finished -> 3 more ... until the UI exited.
        if refresh_after:
            self.refresh_all()

    def _toggle_developer_log(self, checked: bool) -> None:
        self._developer_log_enabled = bool(checked)
        self.prefs["developer_log"] = bool(checked)
        try:
            save_prefs(self.prefs)
        except Exception:
            pass
        if checked:
            self.append_log("เปิด Dev Log แล้ว")

    def _stop_active_job(self) -> None:
        hj_id = int(self._active_job_id or 0)
        if hj_id <= 0:
            return
        self.stop_job_btn.setEnabled(False)
        self.stop_job_btn.setText("กำลังหยุด…")
        self.job_status.setText("จะหยุดหลัง Batch ที่กำลังทำ")
        self._task(
            lambda _log: self.controller.request_stop_job(hj_id=hj_id),
            lambda _r: self.append_log(f"ส่งคำสั่งหยุด JOB #{hj_id} แล้ว • ระบบจะจบ Batch ที่กำลังทำก่อน"),
            "กำลังส่งคำสั่งหยุด",
        )

    def _runtime_worker_rows(self) -> list[list[Any]]:
        stage_map = {
            "LEASE": "เลือกไอดี", "LOGIN": "เข้าสู่ระบบ", "WARM": "พร้อมรอ", "READY": "พร้อม",
            "ADD": "เพิ่มเพื่อน", "ACCEPT": "รับเพื่อน", "SEND": "ส่งใจ",
            "MAILBOX": "รอจดหมาย", "RECEIVE": "รับใจ", "REMOVE": "ลบเพื่อน",
        }
        status_map = {
            "running": "กำลังทำ", "ok": "ผ่าน", "error": "ผิดพลาด",
            "pending-recovery": "รอตรวจซ้ำ",
        }
        rows: list[list[Any]] = []
        local_email_map = getattr(self, "_sender_email_local", {})
        for hs_id, item in list(self._runtime_workers.items())[-50:]:
            sender = local_email_map.get(int(hs_id)) or item.get("email_mask") or f"Sender #{hs_id}"
            rows.append([sender, stage_map.get(str(item.get("stage")), str(item.get("stage") or "—")), status_map.get(str(item.get("status")), str(item.get("status") or "—"))])
        return rows

    def _refresh_worker_table_live(self) -> None:
        self._worker_refresh_pending = False
        if hasattr(self, "worker_table"):
            self.worker_table.fill(self._runtime_worker_rows())

    def _schedule_worker_table_refresh(self) -> None:
        if self._worker_refresh_pending:
            return
        self._worker_refresh_pending = True
        QTimer.singleShot(45, self._refresh_worker_table_live)

    def _apply_runtime_event(self, event: JsonDict) -> None:
        kind = str(event.get("event") or "")
        if kind == "FRIEND_PREFLIGHT":
            if event.get("parser_valid") is False:
                self.friend_preflight_label.setText("พื้นที่เพื่อน: อ่านข้อมูลไม่สำเร็จ • หยุดก่อนเริ่ม")
                return
            current = safe_int(event.get("friend_count"), 0); cap = safe_int(event.get("capacity"), 300); free = safe_int(event.get("free_slots"), max(0, cap-current)); need = safe_int(event.get("need_remove"), 0)
            self.friend_preflight_label.setText(f"พื้นที่เพื่อน: {current}/{cap} • ว่าง {free}" + (f" • ต้องลบ {need}" if need > 0 else " • พร้อม"))
            return
        if kind == "FRIEND_CLEANUP":
            count = safe_int(event.get("count"), 0); status = str(event.get("status") or "")
            self.friend_preflight_label.setText(f"กำลังลบเพื่อน {count} คน…" if status == "running" else f"ลบเพื่อน {count} คนแล้ว • กำลังตรวจซ้ำ")
            return
        if kind == "WARM_POOL":
            ready = safe_int(event.get("ready"), 0); target = safe_int(event.get("target"), safe_int(self.prefs.get("warm_pool_target"), 100)); warming = safe_int(event.get("warming"), 0)
            if hasattr(self, "m_warm"):
                self.m_warm.set_value(f"{ready}/{target}")
            if str(event.get("status") or "") == "running" and hasattr(self, "busy_chip"):
                self.busy_chip.setText(f"เตรียม Sender {ready}/{target}")
            elif hasattr(self, "busy_chip") and self._busy <= 0:
                self.busy_chip.setText("พร้อม")
            return
        if kind == "JOB_START":
            self._active_job_id = safe_int(event.get("hj_id"), self._active_job_id or 0) or self._active_job_id
            self._runtime_requested = safe_int(event.get("requested"), 0)
            self._runtime_completed = safe_int(event.get("completed"), 0)
            self._runtime_failed = 0
            self._runtime_workers.clear()
            self.job_status.setText("กำลังเริ่ม Fast Batch")
            self.stop_job_btn.setVisible(True); self.stop_job_btn.setEnabled(True); self.stop_job_btn.setText("หยุดหลัง Batch นี้")
            self.job_progress.setRange(0, 100); self.job_progress.setValue(0)
            self.run_done.setText(str(self._runtime_completed)); self.run_failed.setText("0"); self.run_remaining.setText(str(max(0, self._runtime_requested-self._runtime_completed)))
            self.worker_table.fill([])
            return
        if kind == "RECEIVER_READY":
            self.job_status.setText("ไอดีรับพร้อม • กำลังเตรียม Sender")
            return
        if kind == "BATCH_START":
            self._runtime_batch_no = safe_int(event.get("batch_no"), 0)
            target = safe_int(event.get("target"), 0)
            self.run_batch.setText(f"Batch #{self._runtime_batch_no} • {target} ไอดี")
            self.job_status.setText(f"กำลังทำ Batch #{self._runtime_batch_no}")
            self._runtime_workers.clear(); self.worker_table.fill([])
            return
        if kind == "SENDER_STAGE":
            hs_id = safe_int(event.get("hs_id"), 0)
            if hs_id > 0:
                self._runtime_workers[hs_id] = {
                    "email_mask": event.get("email_mask") or self._runtime_workers.get(hs_id, {}).get("email_mask"),
                    "stage": event.get("stage"),
                    "status": event.get("status"),
                }
                self._schedule_worker_table_refresh()
            return
        if kind == "BATCH_STAGE":
            stage_map = {"ADD":"เพิ่มเพื่อน", "ACCEPT":"รับเพื่อน", "SEND":"ส่งใจ", "MAILBOX":"ค้นหาใจ", "RECEIVE":"รับใจ", "REMOVE":"ลบเพื่อน"}
            stage = str(event.get("stage") or "")
            done = safe_int(event.get("done"), 0); total = safe_int(event.get("total"), 0)
            if total > 0:
                self.job_status.setText(f"Batch #{self._runtime_batch_no} • {stage_map.get(stage, stage)} {done}/{total}")
            return
        if kind == "JOB_PROGRESS":
            requested = safe_int(event.get("requested"), self._runtime_requested)
            completed = safe_int(event.get("completed"), self._runtime_completed)
            failed = safe_int(event.get("failed"), self._runtime_failed)
            remaining = safe_int(event.get("remaining"), max(0, requested-completed))
            self._runtime_requested = requested; self._runtime_completed = completed; self._runtime_failed = failed
            self.run_done.setText(str(completed)); self.run_failed.setText(str(failed)); self.run_remaining.setText(str(remaining))
            self.job_progress.setRange(0,100); self.job_progress.setValue(100 if requested <= 0 else min(100, int(completed*100/requested)))
            elapsed = float(event.get("elapsed_seconds") or 0); speed = float(event.get("hearts_per_second") or 0); eta = event.get("eta_seconds")
            self.run_elapsed.setText(f"{elapsed:.1f} วินาที")
            self.run_speed.setText(f"ความเร็ว {speed:.2f} ใจ/วิ" if speed > 0 else "ความเร็ว —")
            self.run_eta.setText(f"ETA {float(eta):.0f} วิ" if eta not in (None, "") else "ETA —")
            return
        if kind == "BATCH_DONE":
            passed = safe_int(event.get("passed"), 0); target = safe_int(event.get("target"), 0); secs = float(event.get("elapsed_seconds") or 0)
            self.job_status.setText(f"Batch #{safe_int(event.get('batch_no'),0)} ผ่าน {passed}/{target} • {secs:.1f} วิ")
            return
        if kind == "JOB_STOPPED":
            self.job_status.setText("หยุดแล้วหลังจบ Batch")
            self.stop_job_btn.setEnabled(False)
            return
        if kind == "JOB_DONE":
            status = str(event.get("status") or "")
            completed = safe_int(event.get("completed"), 0); requested = safe_int(event.get("requested"), 0); failed = safe_int(event.get("failed"), 0)
            self.run_done.setText(str(completed)); self.run_failed.setText(str(failed)); self.run_remaining.setText(str(max(0, requested-completed)))
            self.job_progress.setRange(0,100); self.job_progress.setValue(100 if requested and completed >= requested else (int(completed*100/requested) if requested else 0))
            self.job_status.setText("เสร็จสมบูรณ์" if status == "completed" else self._thai_status(status))
            self.stop_job_btn.setEnabled(False); self.stop_job_btn.setVisible(False)
            return

    def _task_error(self, error: str, label: str, task: Task | None = None) -> None:
        if task is not None:
            self._tasks.discard(task)
        self._set_busy(False)
        # A failed preflight/runner task must never leave the Local UI locked.
        if hasattr(self, "start_job_btn"):
            self.start_job_btn.setEnabled(True)
        if hasattr(self, "warm_btn"):
            self.warm_btn.setEnabled(True)
            self._warm_task_active = False
        if hasattr(self, "stop_job_btn"):
            self.stop_job_btn.setEnabled(False)
            self.stop_job_btn.setVisible(False)
            self.stop_job_btn.setText("หยุดหลัง Batch นี้")
        if hasattr(self, "job_progress") and self.job_progress.maximum() == 0:
            self.job_progress.setRange(0, 100)
        self.append_log(f"ERROR [{label}] {error}")
        QMessageBox.critical(self, "ทำงานไม่สำเร็จ", error)

    def append_log(self, text: str) -> None:
        clean = scrub_log(text).strip()
        if not clean:
            return
        if clean.startswith("@RT "):
            try:
                event = json.loads(clean[4:])
                if isinstance(event, dict):
                    self._apply_runtime_event(event)
            except Exception:
                if self._developer_log_enabled:
                    self.console.appendPlainText(clean)
            return
        technical_prefixes = (
            "HTTP ", "SESSION ", "V3 HTTP", "LOGIN WEB", "NETWORK ",
            "P51 ", "P53 RECEIVER CONTEXT", "P53 SELECT", "P54 WARM ",
        )
        if not self._developer_log_enabled and clean.startswith(technical_prefixes):
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.console.appendPlainText(f"[{stamp}] {clean}")

    # ---------- refresh ----------
    def refresh_all(self) -> None:
        self.refresh_dashboard(); self.refresh_senders(); self.refresh_jobs(); self.refresh_events()

    def refresh_dashboard(self) -> None:
        self._task(lambda _log: self.controller.dashboard(), self._apply_dashboard, "กำลังตรวจระบบ")

    def refresh_senders(self) -> None:
        self._task(lambda _log: self.controller.list_senders(), self._apply_senders, "กำลังโหลดไอดีส่ง")

    def refresh_jobs(self) -> None:
        self._task(lambda _log: self.controller.list_jobs(), self._apply_jobs, "กำลังโหลดงาน")

    def refresh_events(self) -> None:
        self._task(lambda _log: self.controller.list_events(), self._apply_events, "กำลังโหลดบันทึก")

    def _apply_dashboard(self, data: JsonDict) -> None:
        prod = data.get("prod", {}) or {}; senders = prod.get("senders", {}) or {}
        self._last_cooldown_count = safe_int(senders.get("cooldown_pairs_active"), 0)
        self.m_ready.set_value(senders.get("selectable", 0)); self.m_cooldown.set_value(self._last_cooldown_count); self.m_attention.set_value(senders.get("needs_attention", 0)); self.m_leased.set_value(senders.get("leased_active", 0))
        warm = data.get("warm_pool", {}) or {}; self.m_warm.set_value(f"{safe_int(warm.get('ready'),0)}/{safe_int(self.prefs.get('warm_pool_target'),100)}")
        self.mode_chip.setText("●  DIRECT HTTP" if data.get("ok") else "●  SYSTEM CHECK")
        self.mode_chip.setStyleSheet(f"color:{COLORS['green'] if data.get('ok') else COLORS['amber']};font-weight:700;font-size:8.5pt;")
        jobs = data.get("jobs", []) or []
        self.home_jobs.fill([[r.get("hj_id"), self._thai_status(r.get("status")), f"{r.get('completed_hearts',0)}/{r.get('requested_hearts',0)}", r.get("hr_id"), fmt_dt(r.get("started_at") or r.get("queued_at"))] for r in jobs])

    def _apply_senders(self, rows: list[dict[str, Any]]) -> None:
        self._sender_rows = rows
        # Local policy: every stored Sender remains enabled/selectable. Error
        # fields are diagnostics only and never create a quarantine state.
        self._sender_email_local = {
            int(r.get("hs_id") or 0): str(r.get("email_local") or r.get("email_mask") or "")
            for r in rows if int(r.get("hs_id") or 0) > 0
        }
        diagnostic_errors = [r for r in rows if str(r.get("last_error_code") or "").strip()]
        self.s_total.set_value(len(rows)); self.s_ready.set_value(len(rows)); self.s_err.set_value(len(diagnostic_errors))
        self.s_cd.set_value(self._last_cooldown_count)
        self._render_senders()
        self.error_count.setText(f"{len(diagnostic_errors)} รายการ • ไม่ได้ปิดไอดี")
        self.error_table.fill([[r.get("hs_id"), r.get("label"), r.get("email_local") or r.get("email_mask"), "พร้อม", r.get("last_error_stage"), r.get("last_error_code"), r.get("total_error",0), fmt_dt(r.get("last_login_at") or r.get("last_send_at"))] for r in diagnostic_errors])

    def _render_senders(self) -> None:
        if not hasattr(self, "sender_table"):
            return
        query = self.sender_search.text().strip().lower() if hasattr(self, "sender_search") else ""
        mode = self.sender_filter.currentText() if hasattr(self, "sender_filter") else "ทั้งหมด"
        rows = []
        for r in self._sender_rows:
            status = self._thai_sender_status(r)
            blob = " ".join(str(r.get(k) or "") for k in ("hs_id","label","email_local","email_mask","health_status","last_error_code")).lower()
            if query and query not in blob: continue
            if mode == "พร้อมใช้งาน" and status != "พร้อม": continue
            if mode == "มี Error ล่าสุด" and not str(r.get("last_error_code") or "").strip(): continue
            rows.append([r.get("hs_id"), r.get("label"), r.get("email_local") or r.get("email_mask"), status, fmt_dt(r.get("last_send_at")), r.get("total_pass",0), r.get("total_error",0), r.get("last_error_code") or "—"])
        self.sender_table.fill(rows)

    def _apply_jobs(self, rows: list[dict[str, Any]]) -> None:
        self._job_rows = rows
        self.jobs_table.fill([[r.get("hj_id"), r.get("hr_id"), self._thai_status(r.get("status")), r.get("requested_hearts",0), r.get("completed_hearts",0), r.get("successful_rounds",0), r.get("failed_rounds",0), fmt_dt(r.get("started_at") or r.get("queued_at")), r.get("last_error_code") or "—"] for r in rows])

    def _apply_events(self, rows: list[dict[str, Any]]) -> None:
        self._event_rows = rows
        self.events_table.fill([[fmt_dt(r.get("created_at")), r.get("event_type"), r.get("hj_id"), r.get("hs_id"), r.get("step"), "ผ่าน" if r.get("success") else "ผิดพลาด", r.get("error_code") or "—", r.get("detail") or "—"] for r in rows])

    # ---------- actions ----------
    def _toggle_receiver_password(self, checked: bool) -> None:
        self.receiver_password.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        self.receiver_password_toggle.setText("ซ่อน" if checked else "แสดง")

    def _start_new_job(self) -> None:
        email = self.receiver_email.text().strip()
        password = self.receiver_password.text()
        amount = self.heart_amount.value()
        template_hs = self.template_sender.value()
        template_hr = self.template_receiver.value() or None
        fast_batch = safe_int(self.prefs.get("fast_batch_size"), 50)
        batch_slots = min(100, max(safe_int(self.prefs.get("friend_batch_slots"), 50), fast_batch))
        if not email or "@" not in email or not password:
            QMessageBox.warning(self, "ข้อมูลไม่ครบ", "กรอกอีเมลและรหัสผ่านของไอดีรับใจให้ครบ")
            return
        answer = QMessageBox.question(
            self,
            "ยืนยันตรวจและเริ่มงาน",
            f"งานนี้ต้องการ {amount} ใจ\nระบบจะตรวจรายชื่อเพื่อนก่อนเริ่ม และเตรียมพื้นที่สูงสุด {min(amount, batch_slots)} ช่องสำหรับ Batch\n\nถ้าพื้นที่ไม่พอ ระบบจะถามยืนยันก่อนลบเพื่อน",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._runtime_workers.clear()
        self._runtime_batch_no = 0
        self._runtime_requested = amount
        self._runtime_completed = 0
        self._runtime_failed = 0
        self.worker_table.fill([])
        self.run_batch.setText("Batch —")
        self.run_speed.setText("ความเร็ว —")
        self.run_eta.setText("ETA —")
        self.job_status.setText("กำลังตรวจพื้นที่เพื่อน…")
        self.job_progress.setRange(0, 0)
        self.run_done.setText("0")
        self.run_failed.setText("0")
        self.run_remaining.setText(str(amount))
        self.run_elapsed.setText("กำลังตรวจสอบ")
        self.start_job_btn.setEnabled(False)
        self.receiver_password.clear()

        def work(log: Callable[[str], None]) -> Any:
            return self.controller.prepare_new_job_preflight(
                email=email,
                password=password,
                amount=amount,
                template_hr_id=template_hr,
                batch_slots=batch_slots,
                friend_capacity=300,
                event_cb=log,
            )

        self._task(work, lambda r: self._preflight_ready(r, template_hs), "กำลังตรวจพื้นที่เพื่อน")

    def _preflight_ready(self, result: JsonDict, template_hs: int) -> None:
        job = result.get("created_job", {}) or {}
        pre = result.get("preflight", {}) or {}
        hj_id = safe_int(job.get("hj_id"), 0)
        if hj_id <= 0:
            self.start_job_btn.setEnabled(True)
            QMessageBox.critical(self, "ตรวจระบบไม่สำเร็จ", "ไม่พบ JOB ที่สร้างสำหรับ Preflight")
            return
        self._active_job_id = hj_id
        self.job_id_label.setText(f"JOB #{hj_id}")

        friend_count = safe_int(pre.get("friend_count"), 0)
        capacity = safe_int(pre.get("friend_capacity"), 300)
        free_slots = safe_int(pre.get("free_slots"), max(0, capacity-friend_count))
        required = safe_int(pre.get("required_slots"), 1)
        need_remove = safe_int(pre.get("need_remove"), 0)
        confidence = str(pre.get("parser_confidence") or "none")
        self.append_log(
            f"ตรวจเพื่อน JOB #{hj_id}: {friend_count}/{capacity} ว่าง={free_slots} ต้องการ={required} ต้องลบ={need_remove}"
        )

        if not bool(pre.get("parser_valid", True)):
            self.start_job_btn.setEnabled(True)
            self.job_status.setText("อ่านรายชื่อเพื่อนไม่สำเร็จ")
            QMessageBox.critical(
                self,
                "ยังตรวจพื้นที่เพื่อนไม่ได้",
                "เซิร์ฟเวอร์ตอบรายชื่อเพื่อนมา แต่ระบบยังอ่านจำนวน/Player ID ได้ไม่มั่นใจ\n\n"
                "งานถูกหยุดก่อนเริ่ม Sender เพื่อไม่ให้ชนเพื่อนเต็มหรือลบผิดคน",
            )
            self._task(lambda _log: self.controller.cancel_prepared_job(hj_id=hj_id, reason="friend list parser could not verify capacity"), None, "กำลังยกเลิกงาน", refresh_after=True)
            return

        if need_remove <= 0:
            self.job_status.setText("พื้นที่เพียงพอ • กำลังเริ่มงาน")
            self._run_prepared_job_ui(hj_id=hj_id, template_hs=template_hs, allow_cleanup=False)
            return

        if not bool(pre.get("cleanup_supported")):
            self.start_job_btn.setEnabled(True)
            self.job_status.setText("ตรวจรายชื่อเพื่อนไม่ครบ")
            QMessageBox.critical(
                self,
                "ยังลบเพื่อนอัตโนมัติไม่ได้",
                f"พบเพื่อน {friend_count}/{capacity} แต่ตัวอ่านรายชื่อยังไม่ยืนยัน Player ID ได้ครบ (confidence={confidence})\n\nระบบหยุดก่อนเพื่อไม่ให้ลบผิดคน",
            )
            self._task(lambda _log: self.controller.cancel_prepared_job(hj_id=hj_id, reason="friend list parser not safe for cleanup"), None, "กำลังยกเลิกงาน", refresh_after=True)
            return

        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle("พื้นที่เพื่อนไม่พอ")
        confirm.setText(
            f"เพื่อนปัจจุบัน: {friend_count}/{capacity}\n"
            f"พื้นที่ว่าง: {free_slots}\n"
            f"ต้องการสำหรับ Batch: {required}\n\n"
            f"ระบบต้องลบเพื่อน {need_remove} คนก่อนเริ่มงาน"
        )
        confirm.setInformativeText("ลบเฉพาะจำนวนที่จำเป็น จากนั้นระบบจะตรวจพื้นที่ซ้ำก่อนเริ่ม Sender")
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        confirm.button(QMessageBox.StandardButton.Yes).setText(f"ลบ {need_remove} คนและเริ่มงาน")
        confirm.button(QMessageBox.StandardButton.No).setText("ยกเลิก ไม่ลบ")
        answer = confirm.exec()
        if answer != QMessageBox.StandardButton.Yes:
            self.start_job_btn.setEnabled(True)
            self.job_status.setText("ยกเลิกก่อนเริ่ม • ยังไม่ได้ลบเพื่อน")
            self.job_progress.setRange(0, 100); self.job_progress.setValue(0)
            self.run_elapsed.setText("ยกเลิก")
            self._task(lambda _log: self.controller.cancel_prepared_job(hj_id=hj_id), None, "กำลังยกเลิกงาน", refresh_after=True)
            return

        self.job_status.setText(f"กำลังลบเพื่อน {need_remove} คนและเริ่มงาน…")
        self._run_prepared_job_ui(hj_id=hj_id, template_hs=template_hs, allow_cleanup=True)

    def _run_prepared_job_ui(self, *, hj_id: int, template_hs: int, allow_cleanup: bool) -> None:
        self.job_progress.setRange(0, 100)
        self.job_progress.setValue(0)
        self.stop_job_btn.setVisible(True)
        self.stop_job_btn.setEnabled(True)
        sender_workers = safe_int(self.prefs.get("fast_sender_workers"), 10)
        network_workers = safe_int(self.prefs.get("fast_network_workers"), 20)
        batch_size = safe_int(self.prefs.get("fast_batch_size"), 50)
        warm_target = safe_int(self.prefs.get("warm_pool_target"), 100)
        warm_workers = safe_int(self.prefs.get("warm_login_workers"), 20)
        def work(log: Callable[[str], None]) -> Any:
            return self.controller.run_prepared_job(
                hj_id=hj_id,
                template_hs_id=template_hs,
                allow_cleanup=allow_cleanup,
                sender_workers=sender_workers,
                network_workers=network_workers,
                batch_size=batch_size,
                warm_target=warm_target,
                warm_workers=warm_workers,
                event_cb=log,
            )
        self._task(work, self._job_finished, "Fast Batch กำลังทำงาน", refresh_after=True)

    def _job_finished(self, result: JsonDict) -> None:
        self.start_job_btn.setEnabled(True)
        self.stop_job_btn.setEnabled(False)
        self.stop_job_btn.setVisible(False)
        runner = result.get("runner", {}) or {}
        job = result.get("created_job", {}) or {}
        requested = safe_int(runner.get("requested_hearts"), safe_int(job.get("requested_hearts"), 0))
        completed = safe_int(runner.get("completed_hearts"), 0)
        failed = safe_int(runner.get("failed_this_run"), 0)
        remaining = safe_int(runner.get("remaining_hearts"), max(0, requested-completed))
        self._active_job_id = safe_int(job.get("hj_id"), 0) or None
        self.job_id_label.setText(f"JOB #{self._active_job_id}" if self._active_job_id else "JOB —")
        self.job_status.setText("เสร็จสมบูรณ์" if runner.get("status") == "completed" else self._thai_status(runner.get("status")))
        self.job_progress.setRange(0, 100); self.job_progress.setValue(100 if requested <= 0 else min(100, int(completed * 100 / requested)))
        elapsed_s = float(runner.get("elapsed_ms") or 0) / 1000.0
        self.run_done.setText(str(completed)); self.run_failed.setText(str(failed)); self.run_remaining.setText(str(remaining)); self.run_elapsed.setText(f"{elapsed_s:.1f} วินาที")
        speed = float(runner.get("hearts_per_second") or 0)
        self.run_speed.setText(f"ความเร็ว {speed:.2f} ใจ/วิ" if speed > 0 else "ความเร็ว —")
        self.run_eta.setText("ETA 0 วิ" if remaining <= 0 else self.run_eta.text())
        self.append_log(f"JOB #{self._active_job_id} จบสถานะ={runner.get('status')} สำเร็จ={completed}/{requested} ผิดพลาด={failed}")
        if runner.get("status") == "completed":
            QMessageBox.information(self, "งานเสร็จแล้ว", f"JOB #{self._active_job_id}\nสำเร็จ {completed}/{requested} ใจ")
        elif runner.get("no_eligible_sender"):
            counts = runner.get("eligible_sender_counts_end", {}) or {}
            eligible = safe_int(counts.get("eligible"), 0)
            cooldown = safe_int(counts.get("cooldown"), 0)
            attempted = safe_int(counts.get("already_attempted"), 0)
            leased = safe_int(counts.get("leased"), 0)
            QMessageBox.warning(
                self,
                "ไอดีส่งไม่พอ",
                "ไม่มี Sender ที่มีสิทธิ์ส่งให้ไอดีรับนี้ต่อใน JOB นี้แล้ว\n\n"
                f"พร้อมจริงสำหรับไอดีรับนี้: {eligible}\n"
                f"ติดคูลดาวน์คู่ Sender→Receiver: {cooldown}\n"
                f"ถูกใช้/ลองแล้วใน JOB นี้: {attempted}\n"
                f"กำลังถูก lease: {leased}\n\n"
                "หมายเหตุ: Local จะไม่ปิด Sender อัตโนมัติแม้เกิด Error\n"
                "Warm Pool คือ Session ที่ล็อกอินพร้อม ส่วนคูลดาวน์คู่คือข้อจำกัดหลังส่งสำเร็จจริง"
            )

    def _run_update_check(self) -> None:
        template_hs = safe_int(self.prefs.get("template_sender_hs_id"), 3)

        def work(log: Callable[[str], None]) -> Any:
            return self.controller.local_update_check(
                template_hs_id=template_hs,
                sender_hs_id=template_hs,
                event_cb=log,
            )

        def done(result: JsonDict) -> None:
            ok = bool(result.get("ok"))
            stages = result.get("stages") or []
            passed = sum(1 for item in stages if isinstance(item, dict) and item.get("ok"))
            failed = [str(item.get("stage")) for item in stages if isinstance(item, dict) and not item.get("ok")]
            elapsed = float(result.get("elapsed_ms") or 0.0) / 1000.0
            if ok:
                text = f"ระบบหลักผ่าน {passed}/{len(stages)} ขั้น • {elapsed:.1f} วินาที\nพร้อมใช้เป็น Local ตรวจหลังเกมอัปเดต"
                self.append_log(f"UPDATE CHECK PASS {passed}/{len(stages)} • {elapsed:.1f}s")
                QMessageBox.information(self, "ตรวจระบบผ่าน", text)
            else:
                names = ", ".join(failed) if failed else "UNKNOWN"
                text = f"พบจุดที่ต้องตรวจ: {names}\nผ่าน {passed}/{len(stages)} ขั้น • {elapsed:.1f} วินาที\nดู Dev Log เพื่อหา layer ที่เปลี่ยน"
                self.append_log(f"UPDATE CHECK FAIL stage={names} • {elapsed:.1f}s")
                QMessageBox.warning(self, "ตรวจระบบไม่ผ่าน", text)

        self._task(work, done, "กำลังตรวจระบบหลังอัปเดต", refresh_after=False)

    def _auto_warm_pool(self) -> None:
        if bool(self.prefs.get("auto_warm_pool", True)):
            self._warm_pool_now(auto=True)

    def _warm_maintenance_tick(self) -> None:
        if not bool(self.prefs.get("auto_warm_pool", True)) or self._warm_task_active:
            return
        try:
            status = self.controller.warm_pool_status()
        except Exception:
            return
        target = safe_int(self.prefs.get("warm_pool_target"), 100)
        ready = safe_int(status.get("ready"), 0)
        self.m_warm.set_value(f"{ready}/{target}")
        if ready < target:
            self._warm_pool_now(auto=True)

    def _warm_pool_now(self, _checked: bool = False, *, auto: bool = False) -> None:
        if self._warm_task_active:
            return
        self._warm_task_active = True
        target = safe_int(self.prefs.get("warm_pool_target"), 100)
        workers = safe_int(self.prefs.get("warm_login_workers"), 20)
        template_hs = safe_int(self.prefs.get("template_sender_hs_id"), 3)
        if hasattr(self, "warm_btn"):
            self.warm_btn.setEnabled(False)
        def work(log: Callable[[str], None]) -> Any:
            return self.controller.warm_sender_pool(target=target, workers=workers, template_hs_id=template_hs, event_cb=log)
        def done(result: JsonDict) -> None:
            self._warm_task_active = False
            if hasattr(self, "warm_btn"):
                self.warm_btn.setEnabled(True)
            ready = safe_int(result.get("ready"), 0)
            self.m_warm.set_value(f"{ready}/{target}")
            if not auto:
                self.append_log(f"Warm Pool พร้อม {ready}/{target} ไอดี")
        self._task(work, done, "กำลังเตรียม Sender", refresh_after=False)

    def _password_dialog(self) -> None:
        dlg = PasswordDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        password = dlg.password.text(); name = dlg.name.text().strip() or "default"
        if not password:
            QMessageBox.warning(self, "ข้อมูลไม่ครบ", "กรอกรหัสผ่านก่อน")
            return
        dlg.password.clear()
        self._task(lambda _log: self.controller.set_shared_password(password, name), lambda _r: QMessageBox.information(self, "บันทึกแล้ว", "ตั้งรหัสกลางของไอดีส่งเรียบร้อย"), "กำลังบันทึกรหัสกลาง", refresh_after=True)

    def _add_sender_dialog(self) -> None:
        dlg = SenderDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        email = dlg.email.text().strip(); label = dlg.label.text().strip() or email.split("@",1)[0]
        if not email or "@" not in email:
            QMessageBox.warning(self, "ข้อมูลไม่ถูกต้อง", "กรอกอีเมลให้ถูกต้อง")
            return
        shared = str(self.prefs.get("shared_credential_name") or "default")
        self._task(lambda _log: self.controller.add_sender(label=label, email=email, shared_credential=shared), lambda _r: QMessageBox.information(self, "เพิ่มแล้ว", "เพิ่มไอดีส่งเข้า Sender Pool เรียบร้อย"), "กำลังเพิ่มไอดี", refresh_after=True)

    def _bulk_dialog(self) -> None:
        dlg = BulkDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        count = dlg.end.value() - dlg.start.value() + 1
        if count <= 0 or count > 20000:
            QMessageBox.warning(self, "ช่วงไม่ถูกต้อง", "จำนวนไอดีต้องอยู่ระหว่าง 1 ถึง 20,000")
            return
        answer = QMessageBox.question(self, "ยืนยันเพิ่มไอดี", f"เพิ่มบัญชีที่มีอยู่แล้วจำนวน {count:,} ไอดีเข้า Sender Pool?")
        if answer != QMessageBox.StandardButton.Yes: return
        shared = str(self.prefs.get("shared_credential_name") or "default")
        self._task(lambda _log: self.controller.import_sender_pattern(prefix=dlg.prefix.text().strip(), domain=dlg.domain.text().strip(), start=dlg.start.value(), end=dlg.end.value(), width=dlg.width.value(), label_prefix=dlg.label_prefix.text().strip() or "Sender", shared_credential=shared), lambda r: QMessageBox.information(self, "เพิ่มเรียบร้อย", f"เพิ่ม/อัปเดต {safe_int(r.get('count'),0):,} ไอดี"), "กำลังเพิ่มไอดีแบบชุด", refresh_after=True)

    def _run_selected_job(self) -> None:
        row = self.jobs_table.current_row()
        if row < 0:
            QMessageBox.warning(self, "ยังไม่ได้เลือกงาน", "เลือก JOB จากตารางก่อน")
            return
        hj_id = safe_int(self.jobs_table.value_at(row, 0), 0)
        if hj_id <= 0:
            return
        answer = QMessageBox.question(self, "ยืนยันตรวจและรันงาน", f"ตรวจพื้นที่เพื่อนแล้วรัน JOB #{hj_id} ต่อ?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        template_hs = safe_int(self.prefs.get("template_sender_hs_id"), 3)
        template_hr = safe_int(self.prefs.get("template_receiver_hr_id"), 0) or None
        fast_batch = safe_int(self.prefs.get("fast_batch_size"), 50)
        batch_slots = min(100, max(safe_int(self.prefs.get("friend_batch_slots"), 50), fast_batch))
        self._active_job_id = hj_id
        self.job_id_label.setText(f"JOB #{hj_id}")
        self.job_status.setText("กำลังตรวจพื้นที่เพื่อน…")
        self._navigate(0)
        self._task(
            lambda log: self.controller.prepare_existing_job_preflight(
                hj_id=hj_id, template_hr_id=template_hr, batch_slots=batch_slots, friend_capacity=300, event_cb=log
            ),
            lambda r: self._preflight_ready(r, template_hs),
            "กำลังตรวจพื้นที่เพื่อน",
        )

    def _apply_performance_profile(self, profile: str) -> None:
        if not all(hasattr(self, name) for name in ("pref_friend_batch", "pref_fast_batch", "pref_fast_workers", "pref_network_workers")):
            return
        profiles = {
            # friend slots, batch, sender workers, network workers, warm target, warm-login workers
            "เสถียร": (20, 20, 5, 10, 50, 10),
            "สมดุล": (50, 50, 10, 20, 100, 20),
            "เร็ว": (50, 50, 20, 40, 200, 30),
            "สูงสุด": (50, 50, 50, 50, 300, 50),
            "เทอร์โบ 100": (100, 100, 50, 50, 150, 50),
        }
        values = profiles.get(str(profile))
        if not values:
            return
        friend_slots, batch, sender_workers, network_workers, warm_target, warm_workers = values
        self.pref_friend_batch.setValue(friend_slots)
        self.pref_fast_batch.setValue(batch)
        self.pref_fast_workers.setValue(sender_workers)
        self.pref_network_workers.setValue(network_workers)
        if hasattr(self, "pref_warm_target"):
            self.pref_warm_target.setValue(warm_target)
        if hasattr(self, "pref_warm_workers"):
            self.pref_warm_workers.setValue(warm_workers)

    def _infer_performance_profile(self) -> str:
        current = (
            self.pref_friend_batch.value(),
            self.pref_fast_batch.value(),
            self.pref_fast_workers.value(),
            self.pref_network_workers.value(),
        )
        profiles = {
            (20, 20, 5, 10): "เสถียร",
            (50, 50, 10, 20): "สมดุล",
            (50, 50, 20, 40): "เร็ว",
            (50, 50, 50, 50): "สูงสุด",
            (100, 100, 50, 50): "เทอร์โบ 100",
        }
        return profiles.get(current, "กำหนดเอง")

    def _save_ui_settings(self) -> None:
        self.prefs["template_sender_hs_id"] = self.pref_template_sender.value()
        self.prefs["template_receiver_hr_id"] = self.pref_template_receiver.value()
        self.prefs["max_receiver_hearts"] = self.pref_max_hearts.value()
        self.prefs["friend_batch_slots"] = self.pref_friend_batch.value()
        self.prefs["fast_batch_size"] = self.pref_fast_batch.value()
        self.prefs["fast_sender_workers"] = self.pref_fast_workers.value()
        self.prefs["fast_network_workers"] = self.pref_network_workers.value()
        self.prefs["warm_pool_target"] = self.pref_warm_target.value()
        self.prefs["warm_login_workers"] = self.pref_warm_workers.value()
        self.prefs["auto_warm_pool"] = self.pref_auto_warm.isChecked()
        self.prefs["performance_profile"] = self._infer_performance_profile()
        self.prefs["developer_log"] = bool(self._developer_log_enabled)
        save_prefs(self.prefs)
        self.template_sender.setValue(self.pref_template_sender.value()); self.template_receiver.setValue(self.pref_template_receiver.value()); self.heart_amount.setMaximum(self.pref_max_hearts.value())
        QMessageBox.information(self, "บันทึกแล้ว", "บันทึกค่าเริ่มต้นของ Local UI แล้ว")

    def closeEvent(self, event) -> None:  # Qt lifecycle
        try:
            self.controller.close()
        except Exception:
            pass
        event.accept()

    # ---------- text helpers ----------
    @staticmethod
    def _thai_status(status: Any) -> str:
        mapping = {
            "queued": "รอทำงาน", "running": "กำลังทำงาน", "completed": "เสร็จแล้ว", "paused": "หยุดชั่วคราว",
            "stopping": "กำลังหยุด", "failed": "ผิดพลาด", "already-completed": "เสร็จแล้ว", "cancelled": "ยกเลิก",
        }
        return mapping.get(str(status or "").lower(), str(status or "—"))

    @staticmethod
    def _thai_sender_status(row: dict[str, Any]) -> str:
        # Operator-only Local: Error/health history is diagnostic only.
        return "พร้อม"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setFont(QFont("Leelawadee UI", 10))
    window = MwoifHeartWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
