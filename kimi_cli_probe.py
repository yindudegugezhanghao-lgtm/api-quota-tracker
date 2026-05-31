#!/usr/bin/env python3
"""Probe Kimi Code Plan usage through the official Kimi CLI.

This optional helper is useful when a plan is only available through the Kimi
coding agent CLI rather than a generic HTTP API endpoint.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk


APP_DIR = Path(__file__).resolve().parent
RUNS_DIR = APP_DIR / "kimi_cli_runs"


@dataclass
class KimiCliConfig:
    duration_seconds: int
    max_calls: int
    min_interval_seconds: float
    timeout_seconds: int
    model: str
    prompt: str


def parse_duration(raw: str) -> int:
    text = raw.strip().lower()
    match = re.fullmatch(r"(\d+)(s|m|h)?", text)
    if not match:
        raise ValueError("Invalid duration. Examples: 10m, 1h, 5h")
    value = int(match.group(1))
    unit = match.group(2) or "s"
    return value * {"s": 1, "m": 60, "h": 3600}[unit]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def run_kimi_call(config: KimiCliConfig, index: int, out_dir: Path) -> dict[str, object]:
    started = time.perf_counter()
    command = [
        "kimi",
        "--quiet",
        "--model",
        config.model,
        "--max-steps-per-turn",
        "1",
        "--prompt",
        config.prompt,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(out_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.timeout_seconds,
        )
        output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        ok = completed.returncode == 0
        error_type = "" if ok else f"exit_{completed.returncode}"
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + "\nCall timed out."
        ok = False
        error_type = "timeout"
    except Exception as exc:  # noqa: BLE001
        output = str(exc)
        ok = False
        error_type = type(exc).__name__

    limit_patterns = [
        r"rate limit",
        r"too many requests",
        r"quota",
        r"insufficient",
        r"limit exceeded",
        r"only available",
    ]
    limit_hit = any(re.search(pattern, output, re.IGNORECASE) for pattern in limit_patterns)
    return {
        "index": index,
        "time": now_iso(),
        "ok": ok and not limit_hit,
        "limit_hit": limit_hit,
        "error_type": error_type,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "output_excerpt": output[-2000:],
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_report(config: KimiCliConfig, summary: dict[str, object], out_dir: Path) -> str:
    first_limit = summary.get("first_limit_call_index")
    lines = [
        "Kimi CLI Probe Report",
        "",
        f"Model: {config.model}",
        f"Started: {summary.get('started_at')}",
        f"Ended: {summary.get('ended_at')}",
        "",
        "Summary",
        f"- Submitted calls: {summary.get('submitted')}",
        f"- Successful calls: {summary.get('ok')}",
        f"- Failed calls: {summary.get('failed')}",
    ]
    if first_limit:
        lines.extend(
            [
                f"- First suspected quota/rate-limit signal: call {first_limit}",
                f"- Rough successful-call estimate: about {max(0, int(first_limit) - 1)}",
            ]
        )
    else:
        lines.append("- No clear quota/rate-limit signal was detected.")
    lines.extend(
        [
            "",
            "Notes",
            "This helper invokes the official Kimi CLI, so it is better suited for Kimi Code Plan than a generic HTTP probe.",
            "",
            "Output files",
            f"- Report: {out_dir / 'report.txt'}",
            f"- Per-call log: {out_dir / 'results.jsonl'}",
            f"- Summary JSON: {out_dir / 'summary.json'}",
        ]
    )
    return "\n".join(lines) + "\n"


class KimiCliProbe(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Kimi CLI Probe")
        self.geometry("900x620")
        self.minsize(820, 560)
        self.stop_requested = threading.Event()
        self.worker: threading.Thread | None = None
        self.messages: queue.Queue[str] = queue.Queue()
        self.latest_out_dir: Path | None = None
        self.scheduled_timer: threading.Timer | None = None

        self.model = tk.StringVar(value="kimi-for-coding")
        self.duration = tk.StringVar(value="5h")
        self.max_calls = tk.StringVar(value="100000")
        self.interval = tk.StringVar(value="3")
        self.timeout = tk.StringVar(value="120")
        self.prompt = tk.StringVar(value="Reply with OK.")
        self.start_later = tk.StringVar(value="")

        self._build_ui()
        self.after(200, self._drain_messages)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        ttk.Label(root, text="Probe Kimi Code Plan with the official Kimi CLI", font=("", 13, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 12),
        )

        form = ttk.LabelFrame(root, text="Settings")
        form.grid(row=1, column=0, sticky="ew")
        for col in (1, 3):
            form.columnconfigure(col, weight=1)
        fields = [
            ("Model", self.model),
            ("Duration", self.duration),
            ("Max calls", self.max_calls),
            ("Min interval", self.interval),
            ("Timeout sec", self.timeout),
            ("Prompt", self.prompt),
            ("Start later", self.start_later),
        ]
        for index, (label, var) in enumerate(fields):
            row, pair = divmod(index, 2)
            ttk.Label(form, text=label).grid(row=row, column=pair * 2, sticky="e", padx=(8, 6), pady=6)
            ttk.Entry(form, textvariable=var).grid(row=row, column=pair * 2 + 1, sticky="ew", padx=(0, 8), pady=6)

        buttons = ttk.Frame(root)
        buttons.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        for col in range(6):
            buttons.columnconfigure(col, weight=1)
        ttk.Button(buttons, text="1-call smoke test", command=self._preset_one).grid(row=0, column=0, sticky="ew", padx=4)
        ttk.Button(buttons, text="Full probe", command=self._preset_full).grid(row=0, column=1, sticky="ew", padx=4)
        self.start_button = ttk.Button(buttons, text="Start", command=self.start)
        self.start_button.grid(row=0, column=2, sticky="ew", padx=4)
        self.schedule_button = ttk.Button(buttons, text="Schedule", command=self.schedule_start)
        self.schedule_button.grid(row=0, column=3, sticky="ew", padx=4)
        self.stop_button = ttk.Button(buttons, text="Stop/cancel", command=self.stop, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=4, sticky="ew", padx=4)
        ttk.Button(buttons, text="Open output", command=self.open_results).grid(row=0, column=5, sticky="ew", padx=4)

        log_frame = ttk.LabelFrame(root, text="Log")
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    def _preset_one(self) -> None:
        self.duration.set("5m")
        self.max_calls.set("1")
        self.interval.set("0")
        self._append("Preset: 1-call smoke test.\n")

    def _preset_full(self) -> None:
        self.duration.set("5h")
        self.max_calls.set("100000")
        self.interval.set("3")
        self._append("Preset: full probe, 5 hours, 3-second interval.\n")

    def _parse_scheduled_time(self) -> dt.datetime:
        text = self.start_later.get().strip().lower()
        if not text:
            raise ValueError("Enter a relative delay like 60m or a clock time like 02:30.")
        relative = re.fullmatch(r"(\d+)(s|m|h)", text)
        now = dt.datetime.now()
        if relative:
            value = int(relative.group(1))
            unit = relative.group(2)
            return now + dt.timedelta(seconds=value * {"s": 1, "m": 60, "h": 3600}[unit])
        clock = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
        if clock:
            hour, minute = int(clock.group(1)), int(clock.group(2))
            if hour > 23 or minute > 59:
                raise ValueError("Clock time must look like 02:30 or 14:05.")
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += dt.timedelta(days=1)
            return target
        raise ValueError("Invalid scheduled time. Use 60m, 1h, or 02:30.")

    def _make_config(self) -> KimiCliConfig:
        return KimiCliConfig(
            duration_seconds=parse_duration(self.duration.get()),
            max_calls=max(1, int(self.max_calls.get())),
            min_interval_seconds=max(0.0, float(self.interval.get())),
            timeout_seconds=max(1, int(self.timeout.get())),
            model=self.model.get().strip() or "kimi-for-coding",
            prompt=self.prompt.get(),
        )

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Already running", "A probe is already running.")
            return
        try:
            config = self._make_config()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Invalid settings", str(exc))
            return
        self.stop_requested.clear()
        self.start_button.configure(state=tk.DISABLED)
        self.schedule_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.worker = threading.Thread(target=self._run_probe, args=(config,), daemon=True)
        self.worker.start()

    def schedule_start(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Already running", "A probe is already running.")
            return
        if self.scheduled_timer and self.scheduled_timer.is_alive():
            messagebox.showinfo("Already scheduled", "A scheduled probe is already waiting.")
            return
        try:
            target = self._parse_scheduled_time()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Invalid schedule", str(exc))
            return
        delay = max(0.0, (target - dt.datetime.now()).total_seconds())
        self.scheduled_timer = threading.Timer(delay, self.start)
        self.scheduled_timer.daemon = True
        self.scheduled_timer.start()
        self.stop_button.configure(state=tk.NORMAL)
        self._append(f"Scheduled for {target.strftime('%Y-%m-%d %H:%M:%S')}.\n")

    def stop(self) -> None:
        if self.scheduled_timer and self.scheduled_timer.is_alive():
            self.scheduled_timer.cancel()
            self._append("Scheduled start cancelled.\n")
        self.stop_requested.set()
        self._append("Stop requested. The current call may need to finish first.\n")

    def _run_probe(self, config: KimiCliConfig) -> None:
        run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = RUNS_DIR / f"{run_id}-kimi-cli"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.latest_out_dir = out_dir
        self.messages.put(f"Output directory: {out_dir}\n")
        write_json(out_dir / "metadata.json", {"model": config.model, "created_at": now_iso()})

        summary: dict[str, object] = {
            "started_at": now_iso(),
            "ended_at": None,
            "submitted": 0,
            "ok": 0,
            "failed": 0,
            "first_limit_call_index": None,
        }
        deadline = time.monotonic() + config.duration_seconds
        with (out_dir / "results.jsonl").open("a", encoding="utf-8") as results_file:
            for index in range(1, config.max_calls + 1):
                if self.stop_requested.is_set() or time.monotonic() >= deadline:
                    break
                summary["submitted"] = int(summary["submitted"]) + 1
                result = run_kimi_call(config, index, out_dir)
                results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
                results_file.flush()
                if result["ok"]:
                    summary["ok"] = int(summary["ok"]) + 1
                else:
                    summary["failed"] = int(summary["failed"]) + 1
                if result["limit_hit"] and summary["first_limit_call_index"] is None:
                    summary["first_limit_call_index"] = index
                    self.stop_requested.set()
                self.messages.put(
                    f"#{index} ok={result['ok']} limit={result['limit_hit']} latency_ms={result['latency_ms']}\n"
                )
                if self.stop_requested.is_set():
                    break
                time.sleep(config.min_interval_seconds)

        summary["ended_at"] = now_iso()
        write_json(out_dir / "summary.json", summary)
        (out_dir / "report.txt").write_text(create_report(config, summary, out_dir), encoding="utf-8")
        self.messages.put("Done. Open the output folder and read report.txt.\n")
        self.after(0, self._set_idle)

    def _set_idle(self) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.schedule_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)

    def _drain_messages(self) -> None:
        try:
            while True:
                self._append(self.messages.get_nowait())
        except queue.Empty:
            pass
        self.after(200, self._drain_messages)

    def _append(self, text: str) -> None:
        self.log.insert(tk.END, text)
        self.log.see(tk.END)

    def open_results(self) -> None:
        target = self.latest_out_dir if self.latest_out_dir and self.latest_out_dir.exists() else RUNS_DIR
        if target.exists():
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("No output yet", "Run a probe first.")


if __name__ == "__main__":
    KimiCliProbe().mainloop()
