#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from mwoif.accounts import (
    existing_slots,
    import_pair,
    import_pair_raw,
    next_sender_slot,
    pair_status,
    update_pair_raw,
)
from mwoif.auth_store import AuthStore
from mwoif.account_health import (
    auth_token_info,
    health_label,
    load_health,
    save_health,
    test_account_health,
)
from mwoif.config import load_config
from mwoif.friend_grpc import list_friends
from mwoif.heart_mailbox import preview_mail_list
from mwoif.session_store import SessionStore
from mwoif.slots import normalize_slot
from mwoif.version import VERSION
from mwoif.workflow import run_cycle

ROOT = Path(__file__).resolve().parent


class AddAccountDialog(tk.Toplevel):
    """เพิ่มไอดีแบบ V1: Copy จาก LAB แล้ววาง Session/Auth ในหน้าต่างเดียว."""

    def __init__(self, parent: "HeartWorkerUI", *, fixed_slot: str | None = None, partial: bool = False):
        super().__init__(parent)
        self.parent = parent
        self.fixed_slot = normalize_slot(fixed_slot) if fixed_slot else None
        self.partial = bool(partial)
        self.result = None
        self.title("แก้ไข Session / Auth" if self.partial else "เพิ่มไอดี")
        self.configure(bg=parent.BG)
        self.transient(parent)
        self.grab_set()

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        width = min(760, max(620, int(sw * 0.72)))
        height = min(650, max(520, int(sh * 0.78)))
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(620, sw - 40), min(500, sh - 60))

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

        header = tk.Frame(self, bg=parent.PANEL, highlightthickness=1, highlightbackground=parent.BORDER)
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        header.columnconfigure(1, weight=1)
        tk.Label(header, text=("แก้ไข Session / Auth" if self.partial else "เพิ่มไอดี"), bg=parent.PANEL, fg=parent.TEXT,
                 font=("Segoe UI Semibold", 14)).grid(row=0, column=0, sticky="w", padx=(14, 8), pady=12)
        tk.Label(header, text=(
                 "วางเฉพาะค่าที่ต้องการเปลี่ยน • ช่องที่ว่างจะเก็บค่าเดิมไว้"
                 if self.partial else
                 "วางเฉพาะค่าที่เปลี่ยนจาก LAB — ค่าเดิมที่คงที่ Python เติมให้อัตโนมัติ"
                 ), bg=parent.PANEL, fg=parent.MUTED, font=("Segoe UI", 9)).grid(row=1, column=0, columnspan=3,
                 sticky="w", padx=14, pady=(0, 12))

        tk.Label(header, text="ไอดี", bg=parent.PANEL, fg=parent.MUTED,
                 font=("Segoe UI", 9)).grid(row=0, column=1, sticky="e", padx=(4, 6))
        self.slot_var = tk.StringVar(value=self.fixed_slot or next_sender_slot(parent.cfg.root, parent.receiver_var.get()))
        self.slot_entry = ttk.Entry(header, textvariable=self.slot_var, width=11, justify="center")
        self.slot_entry.grid(row=0, column=2, sticky="e", padx=(0, 14))
        if self.fixed_slot:
            self.slot_entry.configure(state="disabled")

        self.session_text = self._section(
            row=1,
            title="1. Session (3 ค่า)",
            hint="member_seq • current_lv • session_key",
            paste_cmd=lambda: self._paste_to(self.session_text),
            file_cmd=lambda: self._file_to(self.session_text, "เลือก Session JSON"),
        )
        self.auth_text = self._section(
            row=3,
            title="2. Auth (4 ค่า)",
            hint="mid • token • fgs_id • process_ms",
            paste_cmd=lambda: self._paste_to(self.auth_text),
            file_cmd=lambda: self._file_to(self.auth_text, "เลือก Auth JSON"),
        )

        actions = tk.Frame(self, bg=parent.BG)
        actions.grid(row=5, column=0, sticky="ew", padx=14, pady=(10, 14))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="ยกเลิก", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(actions, text=("บันทึกการแก้ไข" if self.partial else "บันทึก"), style="Primary.TButton", command=lambda: self._save(False)).pack(side="right")
        if not self.fixed_slot and not self.partial:
            ttk.Button(actions, text="บันทึก + ไอดีถัดไป", style="Success.TButton",
                       command=lambda: self._save(True)).pack(side="right", padx=(0, 6))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(80, self._focus_first)

    def _section(self, *, row: int, title: str, hint: str, paste_cmd, file_cmd):
        box = tk.Frame(self, bg=self.parent.PANEL, highlightthickness=1, highlightbackground=self.parent.BORDER)
        box.grid(row=row, column=0, sticky="nsew", padx=14, pady=(0, 8))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)

        bar = tk.Frame(box, bg=self.parent.PANEL)
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 5))
        tk.Label(bar, text=title, bg=self.parent.PANEL, fg=self.parent.TEXT,
                 font=("Segoe UI Semibold", 10)).pack(side="left")
        tk.Label(bar, text=hint, bg=self.parent.PANEL, fg=self.parent.MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="วางจากคลิปบอร์ด", command=paste_cmd).pack(side="right")
        ttk.Button(bar, text="เลือกไฟล์", command=file_cmd).pack(side="right", padx=(0, 6))

        text = tk.Text(box, height=7, wrap="word", bg=self.parent.EDITOR,
                       fg=self.parent.TEXT, insertbackground=self.parent.TEXT,
                       relief="flat", borderwidth=0, padx=9, pady=7,
                       font=("Cascadia Mono", 8))
        text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        return text

    def _focus_first(self):
        if not self.fixed_slot:
            self.slot_entry.focus_set()
        else:
            self.session_text.focus_set()

    def _paste_to(self, widget: tk.Text):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("คลิปบอร์ดว่าง", "ยังไม่มีข้อความในคลิปบอร์ด", parent=self)
            return
        widget.delete("1.0", "end")
        widget.insert("1.0", text.strip())
        widget.focus_set()

    def _file_to(self, widget: tk.Text, title: str):
        filename = filedialog.askopenfilename(
            parent=self,
            title=title,
            filetypes=[("JSON", "*.json"), ("ไฟล์ทั้งหมด", "*.*")],
        )
        if not filename:
            return
        try:
            data = Path(filename).read_text(encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("อ่านไฟล์ไม่ได้", str(exc), parent=self)
            return
        widget.delete("1.0", "end")
        widget.insert("1.0", data)

    def _save(self, continue_next: bool):
        try:
            slot = normalize_slot(self.fixed_slot or self.slot_var.get())
            session_raw = self.session_text.get("1.0", "end-1c").strip()
            auth_raw = self.auth_text.get("1.0", "end-1c").strip()
            if self.partial:
                if not session_raw and not auth_raw:
                    raise ValueError("วาง Session ใหม่ หรือ Auth ใหม่ อย่างน้อย 1 รายการ")
                result = update_pair_raw(
                    slot=slot,
                    session_raw=session_raw,
                    auth_raw=auth_raw,
                    sessions=self.parent.sessions,
                    auths=self.parent.auths,
                )
                changed = []
                if session_raw:
                    changed.append("Session")
                if auth_raw:
                    changed.append("Auth")
                self.parent._log(f"แก้ไขไอดี {slot} • {' + '.join(changed)} สำเร็จ", "pass")
            else:
                if not session_raw:
                    raise ValueError("ยังไม่ได้วาง Session จาก LAB")
                if not auth_raw:
                    raise ValueError("ยังไม่ได้วาง Auth จาก LAB")
                result = import_pair_raw(
                    slot=slot,
                    session_raw=session_raw,
                    auth_raw=auth_raw,
                    sessions=self.parent.sessions,
                    auths=self.parent.auths,
                )
                self.parent._log(f"เพิ่มไอดี {slot} สำเร็จ", "pass")
            self.parent.refresh_accounts()
            self.result = result

            if continue_next:
                self.slot_var.set(next_sender_slot(self.parent.cfg.root, self.parent.receiver_var.get()))
                self.session_text.delete("1.0", "end")
                self.auth_text.delete("1.0", "end")
                self.session_text.focus_set()
                self.parent._toast("บันทึกแล้ว • วาง Session ของไอดีถัดไปได้เลย", "pass")
            else:
                self.destroy()
        except Exception as exc:
            messagebox.showerror("เพิ่มไอดีไม่สำเร็จ", f"{type(exc).__name__}: {exc}", parent=self)


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: "HeartWorkerUI"):
        super().__init__(parent)
        self.parent = parent
        self.title("ตั้งค่า")
        self.configure(bg=parent.BG)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = 460, 390
        self.geometry(f"{width}x{height}+{max(0,(sw-width)//2)}+{max(0,(sh-height)//2)}")

        wf = parent.cfg.workflow
        frame = tk.Frame(self, bg=parent.PANEL, highlightthickness=1, highlightbackground=parent.BORDER)
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(frame, text="ตั้งค่าการทำงาน", bg=parent.PANEL, fg=parent.TEXT,
                 font=("Segoe UI Semibold", 13)).grid(row=0, column=0, columnspan=2,
                 sticky="w", padx=14, pady=(14, 12))

        fields = [
            ("receiver_slot", "ตัวรับหลัก", str(wf.get("receiver_slot", "A"))),
            ("mailbox_retries", "ลองอ่านกล่องใจ (ครั้ง)", str(wf.get("mailbox_retries", 6))),
            ("mailbox_delay_seconds", "หน่วงอ่านกล่องใจ (วินาที)", str(wf.get("mailbox_delay_seconds", 1.0))),
            ("step_delay_seconds", "หน่วงระหว่างขั้น (วินาที)", str(wf.get("step_delay_seconds", 0.35))),
            ("batch_delay_seconds", "หน่วงระหว่างไอดี (วินาที)", str(wf.get("batch_delay_seconds", 0.5))),
            ("grpc_timeout_seconds", "Timeout gRPC", str(wf.get("grpc_timeout_seconds", 12))),
            ("ds_timeout_seconds", "Timeout DS", str(wf.get("ds_timeout_seconds", 20))),
        ]
        self.vars = {}
        for i, (key, label, value) in enumerate(fields, 1):
            tk.Label(frame, text=label, bg=parent.PANEL, fg=parent.MUTED,
                     font=("Segoe UI", 9)).grid(row=i, column=0, sticky="w", padx=14, pady=5)
            var = tk.StringVar(value=value)
            self.vars[key] = var
            ttk.Entry(frame, textvariable=var, width=18).grid(row=i, column=1, sticky="e", padx=14, pady=5)

        bar = tk.Frame(frame, bg=parent.PANEL)
        bar.grid(row=len(fields)+1, column=0, columnspan=2, sticky="ew", padx=14, pady=(14, 14))
        ttk.Button(bar, text="ยกเลิก", command=self.destroy).pack(side="right", padx=(6,0))
        ttk.Button(bar, text="บันทึก", style="Primary.TButton", command=self._save).pack(side="right")

    def _save(self):
        try:
            wf = self.parent.cfg.raw.setdefault("workflow", {})
            wf["receiver_slot"] = normalize_slot(self.vars["receiver_slot"].get())
            wf["mailbox_retries"] = max(1, int(self.vars["mailbox_retries"].get()))
            for key in (
                "mailbox_delay_seconds", "step_delay_seconds", "batch_delay_seconds",
                "grpc_timeout_seconds", "ds_timeout_seconds",
            ):
                value = float(self.vars[key].get())
                if value < 0:
                    raise ValueError(f"{key} ต้องไม่น้อยกว่า 0")
                wf[key] = value
            self.parent._write_config()
            self.parent.receiver_var.set(wf["receiver_slot"])
            self.parent.refresh_accounts()
            self.parent._toast("บันทึกการตั้งค่าแล้ว", "pass")
            self.destroy()
        except Exception as exc:
            messagebox.showerror("ค่าที่กรอกไม่ถูกต้อง", str(exc), parent=self)


class HeartWorkerUI(tk.Tk):
    BG = "#08111f"
    PANEL = "#101b2d"
    PANEL2 = "#15233a"
    EDITOR = "#091522"
    BORDER = "#243753"
    TEXT = "#eef5ff"
    MUTED = "#8fa3bd"
    ACCENT = "#65a6ff"
    ACCENT_HOVER = "#83b8ff"
    GREEN = "#55d6a2"
    RED = "#ff7185"
    AMBER = "#ffc761"

    STEP_LABELS = {
        "friend-add": "เพิ่มเพื่อน",
        "friend-accept": "รับเพื่อน",
        "heart-send": "ส่งใจ",
        "heart-mail-list": "อ่านกล่องใจ",
        "heart-receive": "รับใจ",
        "friend-remove": "ลบเพื่อน",
    }

    STATUS_LABELS = {
        "READY": "พร้อม",
        "WAIT": "รอข้อมูล",
        "QUEUED": "รอคิว",
        "RUNNING": "กำลังทำ",
        "DONE": "สำเร็จ",
        "FAILED": "ผิดพลาด",
    }

    def __init__(self):
        super().__init__()
        self.title(f"M WOIF • ส่งใจ V1 • {VERSION}")
        self.configure(bg=self.BG)

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(1080, max(760, int(sw * 0.92)))
        height = min(760, max(560, int(sh * 0.88)))
        width = min(width, max(680, sw - 24))
        height = min(height, max(500, sh - 50))
        self.geometry(f"{width}x{height}+{max(0,(sw-width)//2)}+{max(0,(sh-height)//2)}")
        self.minsize(min(760, max(680, sw-40)), min(560, max(500, sh-70)))

        self.cfg = load_config(ROOT)
        self.sessions = SessionStore(self.cfg.root)
        self.auths = AuthStore(self.cfg.root)

        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.checked_senders: set[str] = set()
        self.row_state: dict[str, dict[str, str]] = {}
        self.running = False
        self.health_testing = False
        self.stop_after_current = False
        self.run_total = 0
        self.run_done = 0
        self.run_pass = 0
        self.run_fail = 0
        self.current_sender = ""

        self._setup_style()
        self._build_ui()
        self.refresh_accounts()
        self._load_update_map()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- style ----------------
    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=self.BG)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT, font=("Segoe UI", 9))
        style.configure("Panel.TLabel", background=self.PANEL, foreground=self.TEXT, font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED, font=("Segoe UI", 8))
        style.configure("Muted.Panel.TLabel", background=self.PANEL, foreground=self.MUTED, font=("Segoe UI", 8))
        style.configure("Title.TLabel", background=self.BG, foreground="#ffffff", font=("Segoe UI Semibold", 16))
        style.configure("Section.TLabel", background=self.PANEL, foreground="#ffffff", font=("Segoe UI Semibold", 10))
        style.configure("Primary.TButton", background=self.ACCENT, foreground="#06101c", padding=(12, 7), font=("Segoe UI Semibold", 9))
        style.map("Primary.TButton", background=[("active", self.ACCENT_HOVER), ("disabled", "#33445c")])
        style.configure("Success.TButton", background="#1e6b53", foreground="#effff9", padding=(11, 7), font=("Segoe UI Semibold", 9))
        style.map("Success.TButton", background=[("active", "#278468")])
        style.configure("Danger.TButton", background="#572a35", foreground="#ffe9ee", padding=(10, 7), font=("Segoe UI", 9))
        style.map("Danger.TButton", background=[("active", "#703440")])
        style.configure("TButton", background=self.PANEL2, foreground=self.TEXT, padding=(9, 6), borderwidth=0, font=("Segoe UI", 8))
        style.map("TButton", background=[("active", "#223754")])
        style.configure("TEntry", fieldbackground=self.PANEL2, foreground=self.TEXT, insertcolor=self.TEXT, borderwidth=1)
        style.configure("TCombobox", fieldbackground=self.PANEL2, background=self.PANEL2, foreground=self.TEXT, arrowcolor=self.TEXT)
        style.configure("Treeview", background=self.EDITOR, fieldbackground=self.EDITOR, foreground=self.TEXT,
                        rowheight=26, borderwidth=0, font=("Segoe UI", 8))
        style.configure("Treeview.Heading", background=self.PANEL2, foreground=self.MUTED,
                        font=("Segoe UI Semibold", 8), relief="flat")
        style.map("Treeview", background=[("selected", "#254f7f")], foreground=[("selected", "#ffffff")])
        style.configure("Horizontal.TProgressbar", troughcolor=self.PANEL2, background=self.ACCENT, borderwidth=0)
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.PANEL2, foreground=self.MUTED, padding=(13, 6), font=("Segoe UI", 8))
        style.map("TNotebook.Tab", background=[("selected", self.PANEL)], foreground=[("selected", self.TEXT)])

    # ---------------- layout ----------------
    def _build_ui(self):
        header = tk.Frame(self, bg=self.BG)
        header.pack(fill="x", padx=14, pady=(11, 7))

        left = tk.Frame(header, bg=self.BG)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="M WOIF • ส่งใจ V1", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text="ตัวรับ 1 ไอดี • ไอดีส่ง 1–100++ • ทำทีละไอดีเพื่อความเสถียร",
                  style="Muted.TLabel").pack(anchor="w", pady=(1, 0))

        actions = tk.Frame(header, bg=self.BG)
        actions.pack(side="right")
        ttk.Button(actions, text="ตั้งค่า", command=self.open_settings).pack(side="right", padx=(5,0))
        ttk.Button(actions, text="รีเฟรช", command=self.refresh_accounts).pack(side="right")

        # Compact status strip
        strip = tk.Frame(self, bg=self.PANEL, highlightthickness=1, highlightbackground=self.BORDER)
        strip.pack(fill="x", padx=14, pady=(0, 8))
        self.stat_accounts = self._mini_stat(strip, "ไอดีทั้งหมด")
        self.stat_ready = self._mini_stat(strip, "พร้อม")
        self.stat_selected = self._mini_stat(strip, "เลือกแล้ว")
        self.stat_pass = self._mini_stat(strip, "สำเร็จ")
        self.stat_fail = self._mini_stat(strip, "ผิดพลาด")
        self.toast_var = tk.StringVar(value="พร้อมทำงาน")
        self.toast_label = tk.Label(strip, textvariable=self.toast_var, bg=self.PANEL, fg=self.MUTED,
                                    font=("Segoe UI", 8), anchor="e")
        self.toast_label.pack(side="right", fill="x", expand=True, padx=12)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.runner_tab = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.accounts_tab = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.log_tab = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.update_tab = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.notebook.add(self.runner_tab, text="  ส่งใจ  ")
        self.notebook.add(self.accounts_tab, text="  จัดการไอดี  ")
        self.notebook.add(self.log_tab, text="  บันทึก  ")
        self.notebook.add(self.update_tab, text="  อัปเดตเกม  ")

        self._build_runner_tab()
        self._build_accounts_tab()
        self._build_log_tab()
        self._build_update_tab()

    def _mini_stat(self, parent, title):
        box = tk.Frame(parent, bg=self.PANEL)
        box.pack(side="left", padx=(11, 7), pady=7)
        tk.Label(box, text=title, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 7)).pack(side="left")
        var = tk.StringVar(value="0")
        tk.Label(box, textvariable=var, bg=self.PANEL, fg=self.TEXT,
                 font=("Segoe UI Semibold", 10)).pack(side="left", padx=(5,0))
        return var

    def _build_runner_tab(self):
        root = self.runner_tab
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # Receiver compact card
        receiver = tk.Frame(root, bg=self.PANEL2, highlightthickness=1, highlightbackground=self.BORDER)
        receiver.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 7))
        tk.Label(receiver, text="ตัวรับหลัก", bg=self.PANEL2, fg=self.TEXT,
                 font=("Segoe UI Semibold", 10)).pack(side="left", padx=(12, 8), pady=9)
        self.receiver_var = tk.StringVar(value=str(self.cfg.workflow.get("receiver_slot", "A")))
        self.receiver_combo = ttk.Combobox(receiver, textvariable=self.receiver_var, state="readonly", width=9)
        self.receiver_combo.pack(side="left", pady=7)
        self.receiver_combo.bind("<<ComboboxSelected>>", self._receiver_changed)
        self.receiver_ready_var = tk.StringVar(value="ยังไม่พร้อม")
        self.receiver_ready_label = tk.Label(receiver, textvariable=self.receiver_ready_var, bg=self.PANEL2,
                                             fg=self.AMBER, font=("Segoe UI Semibold", 8))
        self.receiver_ready_label.pack(side="left", padx=(9, 6))
        self.receiver_mid_var = tk.StringVar(value="MID: -")
        tk.Label(receiver, textvariable=self.receiver_mid_var, bg=self.PANEL2, fg=self.MUTED,
                 font=("Segoe UI", 8)).pack(side="left", fill="x", expand=True)
        ttk.Button(receiver, text="ตั้งค่าตัวรับ", command=self.import_receiver_dialog).pack(side="right", padx=(5, 10), pady=6)

        # Sender toolbar
        toolbar = tk.Frame(root, bg=self.PANEL)
        toolbar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        ttk.Button(toolbar, text="+ เพิ่มไอดีส่ง", style="Primary.TButton", command=self.import_sender_dialog).pack(side="left")
        ttk.Button(toolbar, text="เลือกพร้อมทั้งหมด", command=self.check_all_ready).pack(side="left", padx=(6,0))
        ttk.Button(toolbar, text="ยกเลิกเลือก", command=self.clear_checked).pack(side="left", padx=(6,0))
        ttk.Button(toolbar, text="ลบไอดี", style="Danger.TButton", command=self.delete_focused_local).pack(side="right")

        # Sender table; horizontal scrollbar prevents break on small screens.
        wrap = tk.Frame(root, bg=self.PANEL)
        wrap.grid(row=2, column=0, sticky="nsew", padx=10)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        cols = ("use", "slot", "ready", "mid", "status", "step")
        self.sender_tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        defs = [
            ("use", "เลือก", 48, "center", False),
            ("slot", "ไอดี", 64, "center", False),
            ("ready", "พร้อม", 64, "center", False),
            ("mid", "MID", 150, "w", True),
            ("status", "สถานะ", 88, "center", False),
            ("step", "ขั้นตอน", 140, "w", True),
        ]
        for col, title, width, anchor, stretch in defs:
            self.sender_tree.heading(col, text=title)
            self.sender_tree.column(col, width=width, minwidth=45, anchor=anchor, stretch=stretch)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.sender_tree.yview)
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.sender_tree.xview)
        self.sender_tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.sender_tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        self.sender_tree.bind("<ButtonRelease-1>", self._tree_click)
        self.sender_tree.bind("<Double-1>", self._tree_double_click)
        self.sender_tree.tag_configure("ready", foreground=self.TEXT)
        self.sender_tree.tag_configure("wait", foreground=self.MUTED)
        self.sender_tree.tag_configure("running", foreground=self.ACCENT)
        self.sender_tree.tag_configure("done", foreground=self.GREEN)
        self.sender_tree.tag_configure("failed", foreground=self.RED)

        # Bottom run bar
        run = tk.Frame(root, bg=self.PANEL2, highlightthickness=1, highlightbackground=self.BORDER)
        run.grid(row=3, column=0, sticky="ew", padx=10, pady=(7, 10))
        run.columnconfigure(0, weight=1)

        top = tk.Frame(run, bg=self.PANEL2)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 5))
        self.run_summary_var = tk.StringVar(value="เลือกไอดีส่งจากตาราง")
        tk.Label(top, textvariable=self.run_summary_var, bg=self.PANEL2, fg=self.MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x", expand=True)
        self.current_sender_var = tk.StringVar(value="-")
        self.current_step_var = tk.StringVar(value="-")
        tk.Label(top, textvariable=self.current_sender_var, bg=self.PANEL2, fg=self.TEXT,
                 font=("Segoe UI Semibold", 9)).pack(side="right")
        tk.Label(top, text="กำลังทำ: ", bg=self.PANEL2, fg=self.MUTED,
                 font=("Segoe UI", 8)).pack(side="right")

        bar = tk.Frame(run, bg=self.PANEL2)
        bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.run_selected_btn = ttk.Button(bar, text="เริ่มที่เลือก", style="Primary.TButton", command=self.run_checked)
        self.run_selected_btn.pack(side="left")
        self.run_all_btn = ttk.Button(bar, text="เริ่มทั้งหมด", style="Success.TButton", command=self.run_all_ready)
        self.run_all_btn.pack(side="left", padx=(6,0))
        self.stop_btn = ttk.Button(bar, text="หยุดหลังไอดีนี้", style="Danger.TButton", command=self.request_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(6,0))
        self.progress = ttk.Progressbar(bar, maximum=100, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 8))
        self.progress_var = tk.StringVar(value="0 / 0")
        tk.Label(bar, textvariable=self.progress_var, bg=self.PANEL2, fg=self.MUTED,
                 font=("Segoe UI", 8)).pack(side="right")

    def _build_accounts_tab(self):
        root = self.accounts_tab
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        bar1 = tk.Frame(root, bg=self.PANEL)
        bar1.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        ttk.Button(bar1, text="+ เพิ่มไอดีส่ง", style="Primary.TButton", command=self.import_sender_dialog).pack(side="left")
        ttk.Button(bar1, text="ตั้งค่าตัวรับ", command=self.import_receiver_dialog).pack(side="left", padx=(6,0))
        ttk.Button(bar1, text="นำเข้าโฟลเดอร์", command=self.import_folder_dialog).pack(side="left", padx=(6,0))
        ttk.Button(bar1, text="รีเฟรช", command=self.refresh_accounts).pack(side="right")

        bar2 = tk.Frame(root, bg=self.PANEL)
        bar2.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        ttk.Button(bar2, text="ทดสอบทั้งหมด", style="Success.TButton", command=self.test_all_accounts).pack(side="left")
        ttk.Button(bar2, text="ทดสอบที่เลือก", command=self.test_selected_account).pack(side="left", padx=(6,0))
        ttk.Button(bar2, text="แก้ไข Session / Auth", command=self.edit_account_tab_selected).pack(side="left", padx=(6,0))
        ttk.Button(bar2, text="ลบ", style="Danger.TButton", command=self.delete_account_tab_selected).pack(side="right")
        self.health_status_var = tk.StringVar(value="Auth ดูเวลา exp ได้ • Session ต้องทดสอบออนไลน์")
        tk.Label(bar2, textvariable=self.health_status_var, bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 8), anchor="e").pack(side="right", fill="x", expand=True, padx=(8,8))

        wrap = tk.Frame(root, bg=self.PANEL)
        wrap.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0,5))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        cols = ("slot", "role", "session", "auth", "auth_left", "online", "mid", "member")
        self.accounts_tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        defs = [
            ("slot", "ไอดี", 64, "center", False),
            ("role", "หน้าที่", 76, "center", False),
            ("session", "Session", 72, "center", False),
            ("auth", "Auth", 66, "center", False),
            ("auth_left", "Auth เหลือ", 112, "center", False),
            ("online", "ทดสอบออนไลน์", 138, "center", False),
            ("mid", "MID", 150, "w", True),
            ("member", "memberSeq", 118, "e", True),
        ]
        for col, title, width, anchor, stretch in defs:
            self.accounts_tree.heading(col, text=title)
            self.accounts_tree.column(col, width=width, minwidth=48, anchor=anchor, stretch=stretch)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.accounts_tree.yview)
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.accounts_tree.xview)
        self.accounts_tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.accounts_tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        self.accounts_tree.tag_configure("health_pass", foreground=self.GREEN)
        self.accounts_tree.tag_configure("health_warn", foreground=self.AMBER)
        self.accounts_tree.tag_configure("health_fail", foreground=self.RED)
        self.accounts_tree.tag_configure("health_muted", foreground=self.TEXT)
        self.accounts_tree.bind("<Double-1>", lambda _e: self.edit_account_tab_selected())

        note = tk.Label(root,
            text="ทดสอบทั้งหมดเป็น READ ONLY: Auth = Friend List gRPC • Session = myMailList.ds • ไม่มีการส่ง/รับ/ลบเพื่อน",
            bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 7), anchor="w")
        note.grid(row=3, column=0, sticky="ew", padx=12, pady=(0,7))

    def _build_log_tab(self):
        root = self.log_tab
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        tools = tk.Frame(root, bg=self.PANEL)
        tools.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 7))
        tk.Label(tools, text="ทดสอบ", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 8)).pack(side="left")
        self.tool_slot_var = tk.StringVar()
        self.tool_slot_combo = ttk.Combobox(tools, textvariable=self.tool_slot_var, state="readonly", width=9)
        self.tool_slot_combo.pack(side="left", padx=(6,4))
        ttk.Button(tools, text="รายชื่อเพื่อน", command=self.tool_test_friend_list).pack(side="left")
        self.tool_sender_var = tk.StringVar()
        self.tool_sender_combo = ttk.Combobox(tools, textvariable=self.tool_sender_var, state="readonly", width=9)
        self.tool_sender_combo.pack(side="left", padx=(10,4))
        ttk.Button(tools, text="อ่านกล่องใจ", command=self.tool_read_mailbox).pack(side="left")
        ttk.Button(tools, text="คัดลอก", command=self.copy_log).pack(side="right")
        ttk.Button(tools, text="ล้าง", command=self.clear_log).pack(side="right", padx=(0,6))

        self.log = tk.Text(root, bg=self.EDITOR, fg=self.TEXT, insertbackground=self.TEXT,
                           relief="flat", font=("Cascadia Mono", 8), wrap="word",
                           padx=10, pady=8)
        self.log.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0,10))
        self.log.tag_configure("muted", foreground=self.MUTED)
        self.log.tag_configure("info", foreground=self.ACCENT)
        self.log.tag_configure("pass", foreground=self.GREEN)
        self.log.tag_configure("fail", foreground=self.RED)
        self.log.tag_configure("warn", foreground=self.AMBER)
        self.log.insert("end", "พร้อมทำงาน\n", "muted")
        self.log.configure(state="disabled")

    def _build_update_tab(self):
        root = self.update_tab
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        bar = tk.Frame(root, bg=self.PANEL)
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,7))
        self.build_text = tk.StringVar(value="กำลังอ่านข้อมูล build ...")
        tk.Label(bar, textvariable=self.build_text, bg=self.PANEL, fg=self.TEXT,
                 font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Button(bar, text="คู่มือเกมอัปเดต", command=lambda: self.open_doc("GAME_UPDATE_GUIDE.md")).pack(side="right")
        ttk.Button(bar, text="จุดค้นหา Ghidra", command=lambda: self.open_doc("GHIDRA_SEARCH_ANCHORS.md")).pack(side="right", padx=(0,6))
        ttk.Button(bar, text="เปิดโฟลเดอร์ docs", command=self.open_docs).pack(side="right", padx=(0,6))

        wrap = tk.Frame(root, bg=self.PANEL)
        wrap.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0,10))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        cols = ("group", "name", "anchor", "current", "status")
        self.update_tree = ttk.Treeview(wrap, columns=cols, show="headings")
        defs = [
            ("group", "กลุ่ม", 90),
            ("name", "รายการ", 130),
            ("anchor", "จุดค้นหา", 220),
            ("current", "ค่าปัจจุบัน", 170),
            ("status", "สถานะ", 110),
        ]
        for col, title, width in defs:
            self.update_tree.heading(col, text=title)
            self.update_tree.column(col, width=width, anchor="w", stretch=True)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.update_tree.yview)
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.update_tree.xview)
        self.update_tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.update_tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")

    # ---------------- account helpers ----------------
    def _all_status(self) -> list[dict[str, Any]]:
        return [pair_status(slot, self.sessions, self.auths) for slot in existing_slots(self.cfg.root)]

    def refresh_accounts(self):
        rows = self._all_status()
        slots = [x["slot"] for x in rows]
        ready = [x["slot"] for x in rows if x["ready"]]

        receiver = self.receiver_var.get().strip().upper() if hasattr(self, "receiver_var") else str(self.cfg.workflow.get("receiver_slot", "A")).upper()
        base_choices = ready or slots
        choices = [receiver] + [x for x in base_choices if x != receiver]
        if not choices:
            choices = [receiver or "A"]

        self.checked_senders.discard(receiver)
        if hasattr(self, "receiver_combo"):
            self.receiver_combo["values"] = choices

        rst = next((x for x in rows if x["slot"] == receiver), None)
        if hasattr(self, "receiver_ready_var"):
            ready_now = bool(rst and rst["ready"])
            self.receiver_ready_var.set("พร้อม" if ready_now else "ยังไม่พร้อม")
            self.receiver_ready_label.configure(fg=self.GREEN if ready_now else self.AMBER)
            self.receiver_mid_var.set(f"MID: {rst['mid']} • memberSeq: {rst['member_seq']}" if rst else "MID: -")

        if hasattr(self, "sender_tree"):
            self.sender_tree.delete(*self.sender_tree.get_children())
            valid = set(slots)
            self.checked_senders.intersection_update(valid)
            for st in rows:
                if st["slot"] == receiver:
                    continue
                state = self.row_state.setdefault(st["slot"], {
                    "status": "READY" if st["ready"] else "WAIT", "step": "-", "result": ""
                })
                status_code = state["status"]
                tag = {
                    "READY": "ready", "WAIT": "wait", "QUEUED": "wait",
                    "RUNNING": "running", "DONE": "done", "FAILED": "failed",
                }.get(status_code, "ready")
                self.sender_tree.insert("", "end", iid=st["slot"], tags=(tag,), values=(
                    "✓" if st["slot"] in self.checked_senders else "○",
                    st["slot"],
                    "ใช่" if st["ready"] else "ไม่",
                    st["mid"],
                    self.STATUS_LABELS.get(status_code, status_code),
                    state["step"],
                ))

        if hasattr(self, "accounts_tree"):
            self.accounts_tree.delete(*self.accounts_tree.get_children())
            warn_minutes = int(self.cfg.workflow.get("auth_expiry_warning_minutes", 30))
            for st in rows:
                auth = self.auths.load(st["slot"])
                token = auth_token_info(auth, warn_seconds=warn_minutes * 60)
                cached = load_health(self.cfg.root, st["slot"])
                online_text, health_tag = health_label(token_info=token, cached=cached)
                auth_left = token.get("remaining_text") or "ไม่ทราบ"
                if token.get("state") == "EXPIRING_SOON":
                    auth_left = f"⚠ {auth_left}"
                elif token.get("state") == "EXPIRED":
                    auth_left = "หมดแล้ว"
                self.accounts_tree.insert("", "end", iid=f"acct:{st['slot']}", tags=(f"health_{health_tag}",), values=(
                    st["slot"],
                    "ตัวรับ" if st["slot"] == receiver else "ตัวส่ง",
                    "มี" if st["session"] else "ไม่มี",
                    "มี" if st["auth"] else "ไม่มี",
                    auth_left,
                    online_text,
                    st["mid"],
                    st["member_seq"] or "",
                ))

        if hasattr(self, "tool_slot_combo"):
            self.tool_slot_combo["values"] = slots
            if slots and self.tool_slot_var.get() not in slots:
                self.tool_slot_var.set(slots[0])
            senders = [x for x in ready if x != receiver]
            self.tool_sender_combo["values"] = senders
            if senders and self.tool_sender_var.get() not in senders:
                self.tool_sender_var.set(senders[0])

        self.stat_accounts.set(str(len(rows)))
        self.stat_ready.set(str(sum(1 for x in rows if x["ready"])))
        self._update_run_summary()

    def _receiver_changed(self, _event=None):
        if self.running:
            messagebox.showwarning("กำลังทำงาน", "เปลี่ยนตัวรับได้หลังงานปัจจุบันจบ", parent=self)
            return
        receiver = normalize_slot(self.receiver_var.get())
        self.cfg.raw.setdefault("workflow", {})["receiver_slot"] = receiver
        self._write_config()
        self.refresh_accounts()
        self._toast(f"ตั้งตัวรับหลักเป็น {receiver}", "info")

    def _tree_click(self, event):
        row = self.sender_tree.identify_row(event.y)
        col = self.sender_tree.identify_column(event.x)
        if row and col == "#1":
            self.toggle_sender(row)

    def _tree_double_click(self, event):
        row = self.sender_tree.identify_row(event.y)
        if row:
            self.toggle_sender(row)

    def toggle_sender(self, slot: str):
        if self.running:
            return
        st = pair_status(slot, self.sessions, self.auths)
        if not st["ready"]:
            messagebox.showwarning("ไอดียังไม่พร้อม", f"{slot} ยังไม่มี Session/Auth ที่พร้อม", parent=self)
            return
        if slot in self.checked_senders:
            self.checked_senders.remove(slot)
        else:
            self.checked_senders.add(slot)
        self._update_sender_row(slot)
        self._update_run_summary()

    def check_all_ready(self):
        if self.running:
            return
        receiver = normalize_slot(self.receiver_var.get())
        self.checked_senders = {st["slot"] for st in self._all_status() if st["ready"] and st["slot"] != receiver}
        self.refresh_accounts()

    def clear_checked(self):
        if self.running:
            return
        self.checked_senders.clear()
        self.refresh_accounts()

    def _update_sender_row(self, slot: str):
        if not self.sender_tree.exists(slot):
            return
        vals = list(self.sender_tree.item(slot, "values"))
        vals[0] = "✓" if slot in self.checked_senders else "○"
        state = self.row_state.setdefault(slot, {"status": "READY", "step": "-", "result": ""})
        vals[4] = self.STATUS_LABELS.get(state["status"], state["status"])
        vals[5] = state["step"]
        tag = {
            "READY": "ready", "WAIT": "wait", "QUEUED": "wait",
            "RUNNING": "running", "DONE": "done", "FAILED": "failed",
        }.get(state["status"], "ready")
        self.sender_tree.item(slot, values=vals, tags=(tag,))

    def _set_row_state(self, slot: str, *, status=None, step=None, result=None):
        state = self.row_state.setdefault(slot, {"status": "READY", "step": "-", "result": ""})
        if status is not None:
            state["status"] = status
        if step is not None:
            state["step"] = step
        if result is not None:
            state["result"] = result
        self._update_sender_row(slot)

    def _ordered_checked(self) -> list[str]:
        return [slot for slot in existing_slots(self.cfg.root) if slot in self.checked_senders]

    def _update_run_summary(self):
        if not hasattr(self, "run_summary_var"):
            return
        selected = self._ordered_checked()
        self.stat_selected.set(str(len(selected)))
        receiver = self.receiver_var.get() if hasattr(self, "receiver_var") else "A"
        if selected:
            preview = ", ".join(selected[:10])
            if len(selected) > 10:
                preview += f" +{len(selected)-10}"
            self.run_summary_var.set(f"{len(selected)} ไอดี → ตัวรับ {receiver} • {preview}")
        else:
            self.run_summary_var.set(f"ตัวรับ {receiver} • เลือกไอดีส่งจากตาราง")

    # ---------------- imports ----------------
    def import_receiver_dialog(self):
        if self.running:
            return
        AddAccountDialog(self, fixed_slot=normalize_slot(self.receiver_var.get()))

    def import_sender_dialog(self):
        if self.running:
            return
        AddAccountDialog(self)

    def import_folder_dialog(self):
        if self.running:
            return
        directory = filedialog.askdirectory(parent=self, title="เลือกโฟลเดอร์ไอดี")
        if not directory:
            return
        base = Path(directory)
        good = bad = 0
        for child in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name):
            sf, af = child / "session.json", child / "auth.json"
            if not sf.is_file() or not af.is_file():
                continue
            try:
                import_pair(slot=child.name, session_file=sf, auth_file=af,
                            sessions=self.sessions, auths=self.auths)
                good += 1
                self._log(f"นำเข้า {child.name} สำเร็จ", "pass")
            except Exception as exc:
                bad += 1
                self._log(f"นำเข้า {child.name} ไม่สำเร็จ • {exc}", "fail")
        self.refresh_accounts()
        messagebox.showinfo("นำเข้าโฟลเดอร์", f"สำเร็จ {good}\nผิดพลาด {bad}", parent=self)

    def _delete_slot(self, slot: str):
        if self.running:
            return
        slot = normalize_slot(slot)
        if slot == normalize_slot(self.receiver_var.get()):
            text = f"ลบข้อมูล Session/Auth ของตัวรับ {slot} จากเครื่องนี้หรือไม่?\nไม่ลบบัญชีในเกม"
        else:
            text = f"ลบข้อมูล Session/Auth ของไอดี {slot} จากเครื่องนี้หรือไม่?\nไม่ลบบัญชีในเกม"
        if not messagebox.askyesno("ยืนยันการลบ", text, parent=self):
            return
        for prefix in ("session", "auth", "health"):
            (ROOT / ".state" / f"{prefix}_{slot}.json").unlink(missing_ok=True)
        self.checked_senders.discard(slot)
        self.row_state.pop(slot, None)
        self.refresh_accounts()
        self._toast(f"ลบข้อมูลไอดี {slot} แล้ว", "warn")

    def delete_focused_local(self):
        slot = self.sender_tree.focus()
        if not slot:
            messagebox.showwarning("ยังไม่ได้เลือก", "คลิกไอดีที่ต้องการลบก่อน", parent=self)
            return
        self._delete_slot(slot)

    def delete_account_tab_selected(self):
        item = self.accounts_tree.focus()
        if not item:
            messagebox.showwarning("ยังไม่ได้เลือก", "คลิกไอดีที่ต้องการลบก่อน", parent=self)
            return
        slot = item.split(":", 1)[-1]
        self._delete_slot(slot)

    # ---------------- account health / edit ----------------
    def _focused_account_slot(self) -> str:
        item = self.accounts_tree.focus()
        if not item:
            return ""
        return normalize_slot(item.split(":", 1)[-1])

    def edit_account_tab_selected(self):
        if self.running or self.health_testing:
            return
        slot = self._focused_account_slot()
        if not slot:
            messagebox.showwarning("ยังไม่ได้เลือก", "คลิกไอดีที่ต้องการแก้ไขก่อน", parent=self)
            return
        AddAccountDialog(self, fixed_slot=slot, partial=True)

    def test_selected_account(self):
        slot = self._focused_account_slot()
        if not slot:
            messagebox.showwarning("ยังไม่ได้เลือก", "คลิกไอดีที่ต้องการทดสอบก่อน", parent=self)
            return
        self._start_account_test([slot])

    def test_all_accounts(self):
        self._start_account_test(existing_slots(self.cfg.root))

    def _start_account_test(self, slots: list[str]):
        if self.running:
            messagebox.showwarning("กำลังส่งใจ", "รอให้งานส่งใจจบก่อนค่อยทดสอบไอดี", parent=self)
            return
        if self.health_testing:
            return
        slots = [normalize_slot(x) for x in slots]
        if not slots:
            messagebox.showinfo("ไม่มีไอดี", "ยังไม่มีไอดีให้ทดสอบ", parent=self)
            return
        self.health_testing = True
        self.health_status_var.set(f"กำลังทดสอบ 0 / {len(slots)}")
        self._log(f"เริ่มทดสอบ Session/Auth แบบ READ ONLY • {len(slots)} ไอดี", "info")
        threading.Thread(target=self._account_test_worker, args=(slots,), daemon=True).start()

    def _account_test_worker(self, slots: list[str]):
        total = len(slots)
        grpc_timeout = float(self.cfg.workflow.get("account_test_grpc_timeout_seconds", 6))
        ds_timeout = float(self.cfg.workflow.get("account_test_ds_timeout_seconds", 8))
        delay = float(self.cfg.workflow.get("account_test_delay_seconds", 0.15))
        passed = failed = 0
        for index, slot in enumerate(slots, 1):
            try:
                result = test_account_health(
                    cfg=self.cfg,
                    slot=slot,
                    session=self.sessions.load(slot),
                    auth=self.auths.load(slot),
                    grpc_timeout=grpc_timeout,
                    ds_timeout=ds_timeout,
                )
                save_health(self.cfg.root, slot, result)
                if result.get("status") == "PASS":
                    passed += 1
                else:
                    failed += 1
                self.events.put({
                    "event": "account_health_result",
                    "slot": slot,
                    "index": index,
                    "total": total,
                    "result": result,
                })
            except Exception as exc:
                failed += 1
                self.events.put({
                    "event": "account_health_exception",
                    "slot": slot,
                    "index": index,
                    "total": total,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            if index < total and delay > 0:
                time.sleep(delay)
        self.events.put({
            "event": "account_health_done",
            "total": total,
            "passed": passed,
            "failed": failed,
        })

    # ---------------- run ----------------
    def run_checked(self):
        self._start_run(self._ordered_checked())

    def run_all_ready(self):
        receiver = normalize_slot(self.receiver_var.get())
        senders = [st["slot"] for st in self._all_status() if st["ready"] and st["slot"] != receiver]
        self.checked_senders.update(senders)
        self.refresh_accounts()
        self._start_run(senders)

    def _start_run(self, senders: list[str]):
        if self.running:
            return
        receiver = normalize_slot(self.receiver_var.get())
        rst = pair_status(receiver, self.sessions, self.auths)
        if not rst["ready"]:
            messagebox.showerror("ตัวรับยังไม่พร้อม", f"ตัวรับ {receiver} ยังไม่มี Session/Auth ที่พร้อม", parent=self)
            return

        senders = [s for s in senders if s != receiver and pair_status(s, self.sessions, self.auths)["ready"]]
        if not senders:
            messagebox.showwarning("ไม่มีไอดีส่ง", "ยังไม่ได้เลือกไอดีส่งที่พร้อม", parent=self)
            return

        if not messagebox.askyesno(
            "เริ่มส่งใจ",
            f"ตัวรับ: {receiver}\nไอดีส่ง: {len(senders)} ไอดี\n\n"
            "แต่ละไอดีจะทำ: เพิ่มเพื่อน → รับเพื่อน → ส่งใจ → รับใจ → ลบเพื่อน\n\n"
            "เริ่มทำงานจริงหรือไม่?",
            parent=self,
        ):
            return

        self.running = True
        self.stop_after_current = False
        self.run_total = len(senders)
        self.run_done = self.run_pass = self.run_fail = 0
        self.stat_pass.set("0")
        self.stat_fail.set("0")
        self.progress["value"] = 0
        self.progress_var.set(f"0 / {self.run_total}")
        self._set_running_controls(True)
        for s in senders:
            self._set_row_state(s, status="QUEUED", step="-")
        self._log(f"เริ่มงาน • {len(senders)} ไอดี → {receiver}", "info")
        self._toast("กำลังทำงาน...", "info")
        threading.Thread(target=self._run_worker, args=(senders, receiver), daemon=True).start()

    def _run_worker(self, senders: list[str], receiver: str):
        wf = self.cfg.workflow
        for index, sender in enumerate(senders, 1):
            self.events.put({"event": "ui_sender_begin", "sender": sender, "index": index, "total": len(senders)})
            try:
                result = run_cycle(
                    cfg=self.cfg,
                    sessions=self.sessions,
                    auths=self.auths,
                    sender=sender,
                    receiver=receiver,
                    live=True,
                    source_type=int(wf.get("friend_source_type", 2)),
                    grpc_timeout=float(wf.get("grpc_timeout_seconds", 12)),
                    ds_timeout=float(wf.get("ds_timeout_seconds", 20)),
                    mailbox_retries=int(wf.get("mailbox_retries", 6)),
                    mailbox_delay=float(wf.get("mailbox_delay_seconds", 1.0)),
                    step_delay=float(wf.get("step_delay_seconds", 0.35)),
                    event_cb=self.events.put,
                )
            except Exception as exc:
                result = {"ok": False, "failed_step": "internal", "failure": {"error": type(exc).__name__, "message": str(exc)}}
                self.events.put({
                    "event": "cycle_failed", "sender": sender, "receiver": receiver,
                    "failed_step": "internal", "failure": result["failure"],
                })
            self.events.put({"event": "ui_sender_result", "sender": sender, "result": result, "index": index, "total": len(senders)})
            if self.stop_after_current:
                break
            if index < len(senders):
                time.sleep(float(wf.get("batch_delay_seconds", 0.5)))
        self.events.put({"event": "ui_run_done"})

    def request_stop(self):
        if self.running:
            self.stop_after_current = True
            self.stop_btn.configure(state="disabled")
            self._toast("จะหยุดหลังไอดีปัจจุบันจบ", "warn")
            self._log("สั่งหยุด • จะหยุดหลังไอดีปัจจุบันจบ", "warn")

    def _set_running_controls(self, running: bool):
        self.run_selected_btn.configure(state="disabled" if running else "normal")
        self.run_all_btn.configure(state="disabled" if running else "normal")
        self.receiver_combo.configure(state="disabled" if running else "readonly")
        self.stop_btn.configure(state="normal" if running else "disabled")

    # ---------------- tools ----------------
    def tool_test_friend_list(self):
        if self.running:
            return
        slot = self.tool_slot_var.get().strip()
        if not slot:
            messagebox.showwarning("ยังไม่ได้เลือก", "เลือกไอดีก่อน", parent=self)
            return
        self._log(f"ทดสอบรายชื่อเพื่อน {slot} ...", "info")
        threading.Thread(target=self._friend_list_worker, args=(slot,), daemon=True).start()

    def _friend_list_worker(self, slot: str):
        try:
            auth = self.auths.load(slot)
            if not auth or not auth.ready:
                raise ValueError("Auth ยังไม่พร้อม")
            result = list_friends(
                cfg=self.cfg, slot=slot, auth=auth, request_body=b"", timeout=float(self.cfg.workflow.get("grpc_timeout_seconds", 12))
            )
            self.events.put({"event": "tool_result", "tool": "friend-list", "slot": slot, "result": result})
        except Exception as exc:
            self.events.put({"event": "tool_exception", "tool": "friend-list", "slot": slot, "error": f"{type(exc).__name__}: {exc}"})

    def tool_read_mailbox(self):
        if self.running:
            return
        receiver = normalize_slot(self.receiver_var.get())
        sender = self.tool_sender_var.get().strip()
        if not sender:
            messagebox.showwarning("ยังไม่ได้เลือก", "เลือกไอดีส่งก่อน", parent=self)
            return
        self._log(f"อ่านกล่องใจ {receiver} ← {sender} ...", "info")
        threading.Thread(target=self._mailbox_worker, args=(receiver, sender), daemon=True).start()

    def _mailbox_worker(self, receiver: str, sender: str):
        try:
            rs = self.sessions.load(receiver)
            ra = self.auths.load(receiver)
            ss = self.sessions.load(sender)
            if not rs or not rs.established or not ra or not ra.ready or not ss or not ss.established:
                raise ValueError("Session/Auth ของตัวรับหรือตัวส่งยังไม่พร้อม")
            result = preview_mail_list(
                cfg=self.cfg, slot=receiver, session=rs, auth=ra,
                from_slot=sender, from_member_seq=int(ss.member_seq),
                live=True, timeout=float(self.cfg.workflow.get("ds_timeout_seconds", 20)),
            )
            self.events.put({"event": "tool_result", "tool": "mailbox", "slot": receiver, "sender": sender, "result": result})
        except Exception as exc:
            self.events.put({"event": "tool_exception", "tool": "mailbox", "slot": receiver, "error": f"{type(exc).__name__}: {exc}"})

    # ---------------- config/events ----------------
    def _write_config(self):
        (ROOT / "config.json").write_text(json.dumps(self.cfg.raw, ensure_ascii=False, indent=2), encoding="utf-8")
        self.cfg = load_config(ROOT)

    def open_settings(self):
        if not self.running:
            SettingsDialog(self)

    def _drain_events(self):
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _handle_event(self, e: dict[str, Any]):
        kind = e.get("event")
        sender = str(e.get("sender") or "")

        if kind == "ui_sender_begin":
            self.current_sender = sender
            self.current_sender_var.set(sender)
            self.current_step_var.set("เริ่ม")
            self._set_row_state(sender, status="RUNNING", step="เริ่ม")
            self._log(f"[{e['index']}/{e['total']}] {sender} เริ่ม", "info")
            return

        if kind == "step_start":
            step = str(e.get("step") or "")
            label = self.STEP_LABELS.get(step, step)
            if sender:
                self._set_row_state(sender, status="RUNNING", step=label)
            self.current_step_var.set(label)
            self._toast(f"{sender} • {label}", "info")
            self._log(f"{sender} • {label} ...", "muted")
            return

        if kind == "mailbox_attempt":
            self._log(f"{sender} • อ่านกล่องใจครั้ง {e.get('attempt')}/{e.get('max_attempts')}", "muted")
            return

        if kind == "step_done":
            step = str(e.get("step") or "")
            label = self.STEP_LABELS.get(step, step)
            extra = f" • seq={e.get('life_mail_seq')}" if e.get("life_mail_seq") else ""
            if sender:
                self._set_row_state(sender, status="RUNNING", step=label)
            self._log(f"{sender} • {label} สำเร็จ{extra}", "pass")
            return

        if kind == "cycle_failed":
            fail = e.get("failure") or {}
            detail = fail.get("error") or fail.get("grpc_details") or fail.get("message") or ""
            label = self.STEP_LABELS.get(str(e.get("failed_step") or ""), str(e.get("failed_step") or ""))
            self._set_row_state(sender, status="FAILED", step=label)
            self._log(f"{sender} • ผิดพลาดที่ {label} • {detail}", "fail")
            return

        if kind == "ui_sender_result":
            result = e.get("result") or {}
            self.run_done += 1
            if result.get("ok"):
                self.run_pass += 1
                self._set_row_state(sender, status="DONE", step="ครบทุกขั้น")
                self._log(f"[{self.run_done}/{self.run_total}] {sender} เสร็จสมบูรณ์", "pass")
            else:
                self.run_fail += 1
                self._set_row_state(sender, status="FAILED")
                self._log(f"[{self.run_done}/{self.run_total}] {sender} ไม่สำเร็จ", "fail")
            self.stat_pass.set(str(self.run_pass))
            self.stat_fail.set(str(self.run_fail))
            self.progress["value"] = 100 * self.run_done / max(1, self.run_total)
            self.progress_var.set(f"{self.run_done} / {self.run_total}")
            return

        if kind == "ui_run_done":
            self.running = False
            self._set_running_controls(False)
            stopped = self.stop_after_current and self.run_done < self.run_total
            self.current_sender_var.set("-")
            self.current_step_var.set("-")
            self.refresh_accounts()
            self._toast(f"เสร็จ • สำเร็จ {self.run_pass} • ผิดพลาด {self.run_fail}", "pass" if not self.run_fail else "warn")
            messagebox.showinfo("งานเสร็จ", f"สำเร็จ {self.run_pass}\nผิดพลาด {self.run_fail}" + ("\nหยุดตามคำสั่ง" if stopped else ""), parent=self)
            return

        if kind == "account_health_result":
            slot = str(e.get("slot") or "")
            result = e.get("result") or {}
            status = str(result.get("status") or "UNKNOWN")
            token = result.get("token") or {}
            self.health_status_var.set(f"กำลังทดสอบ {e.get('index')} / {e.get('total')} • {slot}")
            self.refresh_accounts()
            if status == "PASS":
                self._log(
                    f"{slot} • Auth ผ่าน • Session ผ่าน • Auth เหลือ {token.get('remaining_text', 'ไม่ทราบ')}",
                    "pass",
                )
            elif status == "AUTH_EXPIRED":
                self._log(f"{slot} • Auth หมดเวลาแล้ว • วาง Auth ใหม่ได้โดยไม่ต้องเปลี่ยน Session", "fail")
            elif status == "AUTH_FAIL":
                self._log(f"{slot} • Auth ไม่ผ่าน • {result.get('auth_detail', '')}", "fail")
            elif status == "SESSION_FAIL":
                self._log(f"{slot} • Auth ผ่าน แต่ Session ไม่ผ่าน • {result.get('session_detail', '')}", "fail")
            else:
                self._log(f"{slot} • ข้อมูล Session/Auth ไม่ครบ", "fail")
            return

        if kind == "account_health_exception":
            self.health_status_var.set(f"กำลังทดสอบ {e.get('index')} / {e.get('total')} • {e.get('slot')}")
            self._log(f"{e.get('slot')} • ทดสอบไม่ได้ • {e.get('error')}", "fail")
            return

        if kind == "account_health_done":
            self.health_testing = False
            self.refresh_accounts()
            self.health_status_var.set(
                f"ทดสอบเสร็จ • ผ่าน {e.get('passed')} • ต้องแก้ {e.get('failed')}"
            )
            self._toast(
                f"ทดสอบไอดีเสร็จ • ผ่าน {e.get('passed')} / {e.get('total')}",
                "pass" if not e.get("failed") else "warn",
            )
            return

        if kind == "tool_result":
            result = e.get("result") or {}
            if e.get("tool") == "friend-list":
                if result.get("ok"):
                    self._log(f"ทดสอบ {e.get('slot')} สำเร็จ • gRPC {result.get('grpc_code')}", "pass")
                else:
                    self._log(f"ทดสอบ {e.get('slot')} ไม่สำเร็จ • {result.get('grpc_details') or result.get('error')}", "fail")
            elif e.get("tool") == "mailbox":
                candidates = result.get("life_mail_candidates") or []
                if result.get("ok"):
                    self._log(f"กล่องใจ {e.get('slot')} ← {e.get('sender')} • ค้าง {len(candidates)} • seq={result.get('suggested_life_mail_seq')}", "pass")
                else:
                    self._log(f"อ่านกล่องใจไม่สำเร็จ • {result.get('error') or result.get('message')}", "fail")
            return

        if kind == "tool_exception":
            self._log(f"ทดสอบไม่สำเร็จ • {e.get('error')}", "fail")

    # ---------------- logs/docs ----------------
    def _toast(self, text: str, tag="info"):
        self.toast_var.set(text)
        color = {"pass": self.GREEN, "fail": self.RED, "warn": self.AMBER, "info": self.ACCENT}.get(tag, self.MUTED)
        self.toast_label.configure(fg=color)

    def _log(self, text: str, tag="muted"):
        if not hasattr(self, "log"):
            return
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def copy_log(self):
        text = self.log.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self._toast("คัดลอกบันทึกแล้ว", "pass")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _load_update_map(self):
        path = ROOT / "docs" / "CURRENT_BUILD_MAP.json"
        if not path.is_file():
            self.build_text.set("ไม่พบ CURRENT_BUILD_MAP.json")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.build_text.set(str(exc))
            return
        game = data.get("game", {})
        self.build_text.set(f"เกม {game.get('version')} • build {game.get('build_version')} • {data.get('status')}")
        self.update_tree.delete(*self.update_tree.get_children())
        for row in data.get("items", []):
            self.update_tree.insert("", "end", values=(
                row.get("group", ""), row.get("name", ""), row.get("search_anchor", ""),
                row.get("current", ""), row.get("status", "")
            ))

    def open_docs(self):
        self._open_path(ROOT / "docs")

    def open_doc(self, name: str):
        self._open_path(ROOT / "docs" / name)

    def _open_path(self, path: Path):
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("เปิดไม่ได้", str(exc), parent=self)

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno("กำลังทำงาน", "ยังมีไอดีกำลังทำงานอยู่\nปิดโปรแกรมตอนนี้หรือไม่?", parent=self):
                return
        self.destroy()


def main():
    HeartWorkerUI().mainloop()


if __name__ == "__main__":
    main()
