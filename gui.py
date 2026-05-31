#!/usr/bin/env python3
"""Small Tkinter GUI for plan_probe.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "plan_probe_gui_settings.json"
GUI_API_KEY_ENV = "PLAN_PROBE_GUI_API_KEY"


def toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


class PlanProbeGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("API Quota Tracker")
        self.geometry("1120x720")
        self.minsize(980, 640)

        self.process: subprocess.Popen[str] | None = None
        self.reader_thread: threading.Thread | None = None
        self.latest_output_dir: Path | None = None
        self.vars: dict[str, tk.StringVar] = {}
        self.bool_vars: dict[str, tk.BooleanVar] = {}

        self._build_ui()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _var(self, name: str, default: str = "") -> tk.StringVar:
        value = tk.StringVar(value=default)
        self.vars[name] = value
        return value

    def _bool_var(self, name: str, default: bool = False) -> tk.BooleanVar:
        value = tk.BooleanVar(value=default)
        self.bool_vars[name] = value
        return value

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=2)

        ttk.Label(
            root,
            text="Enter an official API endpoint, run a tiny smoke test, then probe the plan window.",
            font=("", 11),
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        form = ttk.LabelFrame(root, text="Provider")
        form.grid(row=1, column=0, sticky="ew")
        for col in (1, 3):
            form.columnconfigure(col, weight=1)

        self._entry(form, 0, 0, "Provider name", "provider_name", "")
        self._entry(form, 0, 2, "Model", "model", "")
        self._entry(form, 1, 0, "API base URL", "base_url", "", width=48)
        self._entry(form, 1, 2, "Path", "path", "/chat/completions")
        self._entry(form, 2, 0, "API key", "api_key", "", show="*", width=48)
        self._entry(form, 2, 2, "Auth scheme", "auth_scheme", "Bearer")
        self._entry(form, 3, 0, "Auth header", "auth_header", "Authorization")
        self._entry(form, 3, 2, "Output folder", "output_dir", "runs")
        ttk.Button(form, text="Browse", command=self._browse_output_dir).grid(row=3, column=4, sticky="ew", padx=(4, 8), pady=6)

        run_frame = ttk.LabelFrame(root, text="Probe settings")
        run_frame.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        run_frame.columnconfigure(0, weight=1)
        run_frame.columnconfigure(1, weight=1)

        settings = ttk.Frame(run_frame)
        settings.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=10)
        for col in range(8):
            settings.columnconfigure(col, weight=1)

        self._compact_entry(settings, 0, "Duration", "duration", "5h")
        self._compact_entry(settings, 2, "Max calls", "max_calls", "100000")
        self._compact_entry(settings, 4, "Concurrency", "concurrency", "1")
        self._compact_entry(settings, 6, "Timeout sec", "timeout", "60")
        self._compact_entry(settings, 0, "Min interval", "min_interval", "0", row=1)
        ttk.Label(settings, text="Mode").grid(row=1, column=2, sticky="e", padx=(0, 6), pady=(10, 0))
        self.pacing = tk.StringVar(value="fast")
        ttk.Combobox(settings, textvariable=self.pacing, values=("fast", "even"), width=8, state="readonly").grid(
            row=1, column=3, sticky="w", pady=(10, 0)
        )

        prompt_row = ttk.Frame(run_frame)
        prompt_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10)
        prompt_row.columnconfigure(1, weight=1)
        ttk.Label(prompt_row, text="Prompt").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(prompt_row, textvariable=self._var("prompt", "Reply with OK.")).grid(row=0, column=1, sticky="ew")
        ttk.Label(prompt_row, text="max_tokens").grid(row=0, column=2, sticky="e", padx=(12, 8))
        ttk.Entry(prompt_row, textvariable=self._var("max_tokens", "1"), width=8).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(
            prompt_row,
            text="Stop on quota/rate-limit signal",
            variable=self._bool_var("stop_on_limit", True),
        ).grid(row=0, column=4, sticky="e", padx=(12, 0))

        buttons = ttk.Frame(run_frame)
        buttons.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        for col in range(2):
            buttons.columnconfigure(col, weight=1)

        ttk.Button(buttons, text="Smoke test: 5 calls", command=self._preset_smoke).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=4)
        ttk.Button(buttons, text="Run until limit", command=self._preset_until_limit).grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=4)
        ttk.Button(buttons, text="Even window: 5h/1500", command=self._preset_1500).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=4)
        self.dry_run_button = ttk.Button(buttons, text="Check config only", command=lambda: self._start(dry_run=True))
        self.dry_run_button.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=4)
        self.start_button = ttk.Button(buttons, text="Start probe", command=lambda: self._start(dry_run=False))
        self.start_button.grid(row=2, column=0, sticky="ew", padx=(0, 6), pady=4)
        self.stop_button = ttk.Button(buttons, text="Stop", command=self._stop, state=tk.DISABLED)
        self.stop_button.grid(row=2, column=1, sticky="ew", padx=(6, 0), pady=4)
        ttk.Button(buttons, text="Open report", command=self._open_report).grid(row=3, column=0, sticky="ew", padx=(0, 6), pady=4)
        ttk.Button(buttons, text="Open output folder", command=self._open_output).grid(row=3, column=1, sticky="ew", padx=(6, 0), pady=4)

        tips = ttk.Label(
            run_frame,
            text=(
                "Recommended flow: run a 5-call smoke test first. Use 'Run until limit' "
                "to estimate total capacity, or 'Even window' to test a published quota window."
            ),
            wraplength=380,
            justify="left",
        )
        tips.grid(row=2, column=1, sticky="nsew", padx=10, pady=12)

        log_frame = ttk.LabelFrame(root, text="Log")
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=14, wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        self.status = tk.StringVar(value="Not started")
        ttk.Label(root, textvariable=self.status).grid(row=4, column=0, sticky="w", pady=(8, 0))

    def _entry(
        self,
        parent: ttk.Frame,
        row: int,
        col: int,
        label: str,
        name: str,
        default: str,
        *,
        show: str | None = None,
        width: int = 24,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="e", padx=(8, 6), pady=6)
        ttk.Entry(parent, textvariable=self._var(name, default), show=show, width=width).grid(
            row=row,
            column=col + 1,
            sticky="ew",
            padx=(0, 8),
            pady=6,
        )

    def _compact_entry(self, parent: ttk.Frame, col: int, label: str, name: str, default: str, *, row: int = 0) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="e", padx=(0, 6), pady=(0 if row == 0 else 10, 0))
        ttk.Entry(parent, textvariable=self._var(name, default), width=10).grid(
            row=row,
            column=col + 1,
            sticky="w",
            pady=(0 if row == 0 else 10, 0),
        )

    def _browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.vars["output_dir"].get() or str(APP_DIR))
        if selected:
            self.vars["output_dir"].set(selected)

    def _preset_smoke(self) -> None:
        self.pacing.set("fast")
        self.vars["duration"].set("10m")
        self.vars["max_calls"].set("5")
        self.vars["concurrency"].set("1")
        self.vars["min_interval"].set("1")
        self._append_log("Preset: 5-call smoke test.\n")

    def _preset_until_limit(self) -> None:
        self.pacing.set("fast")
        self.vars["duration"].set("5h")
        self.vars["max_calls"].set("100000")
        self.vars["concurrency"].set("1")
        self.vars["min_interval"].set("0")
        self._append_log("Preset: run until a limit signal appears.\n")

    def _preset_1500(self) -> None:
        self.pacing.set("even")
        self.vars["duration"].set("5h")
        self.vars["max_calls"].set("1500")
        self.vars["concurrency"].set("1")
        self.vars["min_interval"].set("0")
        self._append_log("Preset: 1500 calls spread over 5 hours.\n")

    def _validate_required(self) -> bool:
        required = {
            "Provider name": "provider_name",
            "API base URL": "base_url",
            "Model": "model",
            "API key": "api_key",
        }
        missing = [label for label, key in required.items() if not self.vars[key].get().strip()]
        if missing:
            messagebox.showerror("Missing fields", "Please fill: " + ", ".join(missing))
            return False
        api_key = self.vars["api_key"].get().strip()
        if api_key.lower().startswith("bearer "):
            api_key = api_key.split(None, 1)[1].strip()
            self.vars["api_key"].set(api_key)
        try:
            api_key.encode("latin-1")
        except UnicodeEncodeError:
            messagebox.showerror("Invalid API key", "The API key contains characters that cannot be sent in HTTP headers.")
            return False
        return True

    def _build_config(self) -> str:
        output_dir = self.vars["output_dir"].get().strip() or "runs"
        return f"""[provider]
name = {toml_quote(self.vars["provider_name"].get().strip())}
base_url = {toml_quote(self.vars["base_url"].get().strip().rstrip("/"))}
path = {toml_quote(self.vars["path"].get().strip() or "/chat/completions")}
model = {toml_quote(self.vars["model"].get().strip())}
api_key_env = {toml_quote(GUI_API_KEY_ENV)}
auth_header = {toml_quote(self.vars["auth_header"].get().strip() or "Authorization")}
auth_scheme = {toml_quote(self.vars["auth_scheme"].get().strip() or "Bearer")}

[run]
pacing = {toml_quote(self.pacing.get())}
duration = {toml_quote(self.vars["duration"].get().strip() or "5h")}
max_calls = {self.vars["max_calls"].get().strip() or "100000"}
concurrency = {self.vars["concurrency"].get().strip() or "1"}
min_interval_seconds = {self.vars["min_interval"].get().strip() or "0"}
request_timeout_seconds = {self.vars["timeout"].get().strip() or "60"}
output_dir = {toml_quote(output_dir)}
stop_on_limit = {str(self.bool_vars["stop_on_limit"].get()).lower()}

[request]
prompt = {toml_quote(self.vars["prompt"].get())}
max_tokens = {self.vars["max_tokens"].get().strip() or "1"}
temperature = 0

[limit_detection]
status_codes = [402, 403, 429]
body_keywords = ["rate limit", "too many requests", "quota", "insufficient", "limit exceeded"]
"""

    def _start(self, *, dry_run: bool) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showinfo("Already running", "A probe is already running.")
            return
        if not self._validate_required():
            return

        config_text = self._build_config()
        try:
            tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".toml", delete=False)
            tmp.write(config_text)
            tmp.close()
        except OSError as exc:
            messagebox.showerror("Could not write config", str(exc))
            return

        command = [sys.executable, str(APP_DIR / "plan_probe.py"), "--config", tmp.name]
        if dry_run:
            command.append("--dry-run")
        env = os.environ.copy()
        env[GUI_API_KEY_ENV] = self.vars["api_key"].get().strip()

        self.log.delete("1.0", tk.END)
        self.status.set("Checking config..." if dry_run else "Running...")
        self._append_log("Starting: " + ("dry run" if dry_run else "probe") + "\n")
        self._set_running(True)
        self._save_settings()

        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(APP_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except OSError as exc:
            self._set_running(False)
            messagebox.showerror("Could not start", str(exc))
            return

        self.reader_thread = threading.Thread(target=self._read_process_output, daemon=True)
        self.reader_thread.start()
        self.after(500, self._poll_process)

    def _read_process_output(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self.after(0, self._append_log, line)
            if line.startswith("Output directory:"):
                path = line.split(":", 1)[1].strip()
                output_path = Path(path)
                if not output_path.is_absolute():
                    output_path = APP_DIR / output_path
                self.latest_output_dir = output_path

    def _poll_process(self) -> None:
        if not self.process:
            return
        code = self.process.poll()
        if code is None:
            self.after(500, self._poll_process)
            return
        self._set_running(False)
        if code == 0:
            self.status.set("Done")
            self._append_log("Done. Open the report for details.\n")
        elif code == 2:
            self.status.set("Done with failures or limit signals")
            self._append_log("Done with failures or limit signals. Open the report for details.\n")
        else:
            self.status.set(f"Exited with code {code}")
            self._append_log(f"Exited with code {code}.\n")

    def _stop(self) -> None:
        if self.process and self.process.poll() is None:
            self._append_log("Stopping after the current request finishes...\n")
            self.status.set("Stopping...")
            self.process.terminate()

    def _set_running(self, running: bool) -> None:
        state = tk.DISABLED if running else tk.NORMAL
        self.start_button.configure(state=state)
        self.dry_run_button.configure(state=state)
        self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)

    def _append_log(self, text: str) -> None:
        self.log.insert(tk.END, text)
        self.log.see(tk.END)

    def _open_output(self) -> None:
        output_dir = Path(self.vars["output_dir"].get().strip() or "runs")
        if not output_dir.is_absolute():
            output_dir = APP_DIR / output_dir
        target = self.latest_output_dir if self.latest_output_dir and self.latest_output_dir.exists() else output_dir
        if target.exists():
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("No output yet", "Run a probe first.")

    def _open_report(self) -> None:
        if self.latest_output_dir:
            report = self.latest_output_dir / "report.txt"
            if report.exists():
                os.startfile(report)  # type: ignore[attr-defined]
                return
        messagebox.showinfo("No report yet", "Run a full probe first.")

    def _load_settings(self) -> None:
        if not SETTINGS_PATH.exists():
            return
        try:
            payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for name, value in payload.get("vars", {}).items():
            if name in self.vars and name != "api_key":
                self.vars[name].set(str(value))
        if "pacing" in payload:
            self.pacing.set(str(payload["pacing"]))
        for name, value in payload.get("bools", {}).items():
            if name in self.bool_vars:
                self.bool_vars[name].set(bool(value))

    def _save_settings(self) -> None:
        payload: dict[str, object] = {
            "vars": {name: var.get() for name, var in self.vars.items() if name != "api_key"},
            "bools": {name: var.get() for name, var in self.bool_vars.items()},
            "pacing": self.pacing.get(),
        }
        SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _on_close(self) -> None:
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("Probe still running", "Stop the running probe and close?"):
                return
            self._stop()
        self._save_settings()
        self.destroy()


if __name__ == "__main__":
    PlanProbeGui().mainloop()
