#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQ = ROOT / "requirements.txt"
VENV = ROOT / ".venv"


def _hidden_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _deps_ready() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("requests", "grpc"))


def _in_project_venv() -> bool:
    try:
        return VENV.resolve() in Path(sys.executable).resolve().parents
    except Exception:
        return False


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def _venv_pythonw() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "pythonw.exe"
    return _venv_python()


def _launch_venv() -> None:
    exe = _venv_pythonw()
    if not exe.exists():
        raise RuntimeError("ไม่พบ Python virtual environment ของโปรเจกต์")
    subprocess.Popen(
        [str(exe), str(ROOT / "main.py")],
        cwd=ROOT,
        creationflags=_hidden_flags(),
    )


def _setup_gui(create_venv: bool) -> bool:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("M WOIF • ติดตั้งครั้งแรก")
    root.geometry("520x230")
    root.resizable(False, False)
    root.configure(bg="#0b1020")

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TFrame", background="#0b1020")
    style.configure("TLabel", background="#0b1020", foreground="#e7edf7", font=("Segoe UI", 10))
    style.configure("Title.TLabel", background="#0b1020", foreground="#ffffff", font=("Segoe UI Semibold", 17))
    style.configure("Horizontal.TProgressbar", troughcolor="#172033", background="#6ea8fe")

    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="M WOIF • Heart Worker V1", style="Title.TLabel").pack(anchor="w")
    ttk.Label(frame, text="กำลังเตรียม Python environment สำหรับเปิด UI ครั้งแรก").pack(anchor="w", pady=(7, 16))
    status = tk.StringVar(value="เตรียมระบบ...")
    ttk.Label(frame, textvariable=status).pack(anchor="w")
    bar = ttk.Progressbar(frame, mode="indeterminate")
    bar.pack(fill="x", pady=(12, 8))
    bar.start(10)
    ttk.Label(frame, text="ครั้งถัดไปดับเบิลคลิก run.bat แล้ว UI จะเปิดทันที", foreground="#8fa1b8").pack(anchor="w")

    q: queue.Queue[tuple[str, str]] = queue.Queue()
    result = {"ok": False}

    def worker():
        try:
            if create_venv:
                q.put(("status", "กำลังสร้าง .venv ..."))
                subprocess.run(
                    [sys.executable, "-m", "venv", str(VENV)],
                    cwd=ROOT,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=_hidden_flags(),
                )
                pip_python = _venv_python()
            else:
                pip_python = Path(sys.executable)

            q.put(("status", "กำลังติดตั้ง requirements ..."))
            subprocess.run(
                [str(pip_python), "-m", "pip", "install", "-r", str(REQ)],
                cwd=ROOT,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_hidden_flags(),
            )
            q.put(("done", "PASS"))
        except Exception as exc:
            q.put(("fail", f"{type(exc).__name__}: {exc}"))

    threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            while True:
                kind, text = q.get_nowait()
                if kind == "status":
                    status.set(text)
                elif kind == "done":
                    result["ok"] = True
                    root.destroy()
                    return
                elif kind == "fail":
                    bar.stop()
                    messagebox.showerror("ติดตั้งไม่สำเร็จ", text, parent=root)
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(100, poll)

    root.after(100, poll)
    root.mainloop()
    return bool(result["ok"])


def _bootstrap() -> None:
    # On Windows always prefer the project's .venv and pythonw so no console
    # is needed after the BAT launcher closes.
    if os.name == "nt" and not _in_project_venv():
        if _venv_pythonw().exists():
            _launch_venv()
            raise SystemExit(0)
        if not _setup_gui(create_venv=True):
            raise SystemExit(1)
        _launch_venv()
        raise SystemExit(0)

    if not _deps_ready():
        if not _setup_gui(create_venv=False):
            raise SystemExit(1)


_bootstrap()

from ui import main as ui_main  # noqa: E402

if __name__ == "__main__":
    ui_main()
