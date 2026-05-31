#!/usr/bin/env python3
"""Probe how many API calls a plan allows in a time window.

This tool sends minimal OpenAI-compatible chat completion requests and writes a
human-readable report plus JSON logs. It never writes API keys to output files.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import json
import os
import re
import signal
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


STOP_REQUESTED = False


def _handle_stop(signum: int, frame: object) -> None:
    del signum, frame
    global STOP_REQUESTED
    STOP_REQUESTED = True


signal.signal(signal.SIGINT, _handle_stop)
signal.signal(signal.SIGTERM, _handle_stop)


@dataclasses.dataclass(frozen=True)
class ProbeConfig:
    provider_name: str
    base_url: str
    path: str
    model: str
    api_key: str
    auth_header: str
    auth_scheme: str
    pacing: str
    duration_seconds: float
    max_calls: int
    concurrency: int
    min_interval_seconds: float
    request_timeout_seconds: float
    output_dir: Path
    stop_on_limit: bool
    prompt: str
    max_tokens: int
    temperature: float | None
    extra_headers: dict[str, str]
    limit_status_codes: set[int]
    limit_keywords: list[str]


def parse_duration(raw: str | int | float) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)?", text)
    if not match:
        raise ValueError(f"Invalid duration {raw!r}. Examples: 30s, 10m, 5h")
    value = float(match.group(1))
    unit = match.group(2) or "s"
    return value * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def load_config(path: Path) -> ProbeConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    provider = raw.get("provider", {})
    run = raw.get("run", {})
    request = raw.get("request", {})
    limit_detection = raw.get("limit_detection", {})

    api_key = provider.get("api_key") or os.getenv(provider.get("api_key_env", ""))
    if not api_key:
        env_name = provider.get("api_key_env", "PROVIDER_API_KEY")
        raise SystemExit(f"Missing API key. Set the {env_name} environment variable.")
    try:
        str(api_key).encode("latin-1")
    except UnicodeEncodeError as exc:
        raise SystemExit("API key contains non-header-safe characters.") from exc

    base_url = str(provider.get("base_url", "")).rstrip("/")
    if not base_url:
        raise SystemExit("provider.base_url is required.")
    path_part = str(provider.get("path", "/chat/completions"))
    if not path_part.startswith("/"):
        path_part = "/" + path_part

    extra_headers = {
        str(k): str(v)
        for k, v in dict(provider.get("headers", {})).items()
        if str(k).lower() not in {"authorization", "x-api-key", "api-key"}
    }

    return ProbeConfig(
        provider_name=str(provider.get("name", "provider")).strip() or "provider",
        base_url=base_url,
        path=path_part,
        model=str(provider.get("model", "")).strip(),
        api_key=str(api_key),
        auth_header=str(provider.get("auth_header", "Authorization")),
        auth_scheme=str(provider.get("auth_scheme", "Bearer")),
        pacing=str(run.get("pacing", "fast")).strip().lower(),
        duration_seconds=parse_duration(run.get("duration", "5h")),
        max_calls=int(run.get("max_calls", 100000)),
        concurrency=max(1, int(run.get("concurrency", 1))),
        min_interval_seconds=max(0.0, float(run.get("min_interval_seconds", 0))),
        request_timeout_seconds=max(1.0, float(run.get("request_timeout_seconds", 60))),
        output_dir=Path(run.get("output_dir", "runs")),
        stop_on_limit=bool(run.get("stop_on_limit", True)),
        prompt=str(request.get("prompt", "Reply with OK.")),
        max_tokens=max(1, int(request.get("max_tokens", 1))),
        temperature=request.get("temperature", 0),
        extra_headers=extra_headers,
        limit_status_codes={int(x) for x in limit_detection.get("status_codes", [402, 403, 429])},
        limit_keywords=[str(x).lower() for x in limit_detection.get("body_keywords", [])],
    )


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", text.strip()).strip("-")
    return slug or "provider"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_request_body(config: ProbeConfig) -> bytes:
    body = {
        "model": config.model,
        "messages": [{"role": "user", "content": config.prompt}],
        "max_tokens": config.max_tokens,
    }
    if config.temperature is not None:
        body["temperature"] = config.temperature
    return json.dumps(body).encode("utf-8")


def is_limit_hit(config: ProbeConfig, status: int | None, body: str, error_type: str) -> bool:
    if status in config.limit_status_codes:
        return True
    text = f"{error_type}\n{body}".lower()
    return any(keyword and keyword in text for keyword in config.limit_keywords)


def perform_call(config: ProbeConfig, index: int) -> dict[str, Any]:
    started = time.perf_counter()
    status: int | None = None
    body = ""
    headers: dict[str, str] = {}
    error_type = ""

    auth_value = config.api_key if not config.auth_scheme else f"{config.auth_scheme} {config.api_key}"
    request_headers = {
        "Content-Type": "application/json",
        config.auth_header: auth_value,
        **config.extra_headers,
    }

    req = urllib.request.Request(
        config.base_url + config.path,
        data=build_request_body(config),
        headers=request_headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=config.request_timeout_seconds) as resp:
            status = int(resp.status)
            headers = dict(resp.headers.items())
            body = resp.read(4096).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        headers = dict(exc.headers.items())
        body = exc.read(4096).decode("utf-8", errors="replace")
        error_type = f"http_{status}"
    except urllib.error.URLError as exc:
        error_type = type(exc.reason).__name__ if getattr(exc, "reason", None) else "url_error"
        body = str(exc)
    except TimeoutError:
        error_type = "timeout"
    except Exception as exc:  # noqa: BLE001
        error_type = type(exc).__name__
        body = str(exc)

    latency_ms = int((time.perf_counter() - started) * 1000)
    limit_hit = is_limit_hit(config, status, body, error_type)
    ok = bool(status is not None and 200 <= status < 300 and not limit_hit)

    return {
        "index": index,
        "time": now_iso(),
        "ok": ok,
        "limit_hit": limit_hit,
        "status": status,
        "latency_ms": latency_ms,
        "error_type": error_type,
        "response_headers": {
            key: value
            for key, value in headers.items()
            if key.lower() in {"x-request-id", "x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"}
        },
        "body_excerpt": body[:800],
    }


def describe_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))


def create_report(config: ProbeConfig, summary: dict[str, Any], out_dir: Path) -> str:
    ok = int(summary.get("ok", 0))
    total = int(summary.get("total", 0))
    failed = int(summary.get("failed", 0))
    submitted = int(summary.get("submitted", 0))
    first_limit = summary.get("first_limit_call_index")
    status_counts = summary.get("status_counts", {})
    error_counts = summary.get("error_counts", {})
    mode = "run until a limit signal appears" if config.pacing == "fast" else "spread calls evenly over the window"

    lines = [
        "API Quota Tracker Report",
        "",
        f"Provider / model: {config.provider_name} / {config.model}",
        f"Mode: {mode}",
        f"Started: {summary.get('started_at')}",
        f"Ended: {summary.get('ended_at')}",
        "",
        "Summary",
        f"- Submitted calls: {submitted}",
        f"- Completed results: {total}",
        f"- Successful calls: {ok}",
        f"- Failed or limited calls: {failed}",
    ]

    if status_counts.get("401"):
        lines.extend(
            [
                "- The run saw HTTP 401 authentication failures.",
                "",
                "Interpretation",
                "This usually means the API key was missing, invalid, expired, or sent with the wrong auth format.",
            ]
        )
    elif status_counts.get("403"):
        lines.extend(
            [
                "- The run saw HTTP 403 permission failures.",
                "",
                "Interpretation",
                "This usually means the account, model, endpoint, or plan does not have permission for the requested route.",
            ]
        )
    elif first_limit is None:
        lines.extend(
            [
                "- No clear quota or rate-limit signal was detected.",
                "",
                "Interpretation",
                "The configured test window ended before the plan limit was reached. Increase max_calls or duration if you want to keep probing.",
            ]
        )
    else:
        estimated_capacity = max(0, int(first_limit) - 1)
        lines.extend(
            [
                f"- First suspected quota/rate-limit signal appeared at call {first_limit}.",
                f"- Rough successful-call estimate under these settings: about {estimated_capacity}.",
                "",
                "Interpretation",
                "If this happened very quickly with HTTP 429, it may be a short rate-limit window rather than the total plan quota.",
                "Try setting min_interval_seconds to 0.5 or 1 and compare another run.",
            ]
        )

    lines.extend(
        [
            "",
            "Evidence",
            f"- HTTP status counts: {describe_counts(status_counts)}",
            f"- Client/network error counts: {describe_counts(error_counts)}",
        ]
    )

    if summary.get("first_limit_status") is not None:
        lines.append(f"- First limit status: {summary.get('first_limit_status')}")
    limit_headers = summary.get("first_limit_headers") or {}
    if limit_headers:
        lines.append("- First limit response headers: " + ", ".join(f"{k}: {v}" for k, v in limit_headers.items()))
    limit_excerpt = str(summary.get("first_limit_body_excerpt") or "").strip()
    if limit_excerpt:
        lines.extend(["", "First limit response excerpt", limit_excerpt[:800]])

    lines.extend(
        [
            "",
            "Output files",
            f"- Report: {out_dir / 'report.txt'}",
            f"- Summary JSON: {out_dir / 'summary.json'}",
            f"- Per-call log: {out_dir / 'results.jsonl'}",
            "",
            "Reminder",
            "Do not run the same account in several quota tests at once, or the results will include calls from other tools.",
        ]
    )
    return "\n".join(lines) + "\n"


def new_summary(config: ProbeConfig) -> dict[str, Any]:
    return {
        "provider": config.provider_name,
        "model": config.model,
        "started_at": now_iso(),
        "ended_at": None,
        "submitted": 0,
        "total": 0,
        "ok": 0,
        "failed": 0,
        "limit_hits": 0,
        "first_limit_call_index": None,
        "first_limit_status": None,
        "first_limit_headers": None,
        "first_limit_body_excerpt": None,
        "status_counts": {},
        "error_counts": {},
    }


def update_summary(summary: dict[str, Any], result: dict[str, Any]) -> None:
    summary["total"] += 1
    if result["ok"]:
        summary["ok"] += 1
    else:
        summary["failed"] += 1

    status = result.get("status")
    if status is not None:
        key = str(status)
        summary["status_counts"][key] = summary["status_counts"].get(key, 0) + 1
    if result.get("error_type"):
        key = str(result["error_type"])
        summary["error_counts"][key] = summary["error_counts"].get(key, 0) + 1
    if result.get("limit_hit"):
        summary["limit_hits"] += 1
        if summary["first_limit_call_index"] is None:
            summary["first_limit_call_index"] = result["index"]
            summary["first_limit_status"] = result.get("status")
            summary["first_limit_headers"] = result.get("response_headers")
            summary["first_limit_body_excerpt"] = result.get("body_excerpt")


def run_probe(config: ProbeConfig, dry_run: bool = False) -> int:
    if config.pacing not in {"fast", "even"}:
        raise SystemExit("run.pacing must be 'fast' or 'even'.")

    interval = config.min_interval_seconds
    if config.pacing == "even" and config.max_calls > 1:
        interval = max(interval, config.duration_seconds / max(1, config.max_calls - 1))

    if dry_run:
        print("Dry run OK. No calls were sent.")
        print(f"Provider: {config.provider_name}")
        print(f"Endpoint: {config.base_url + config.path}")
        print(f"Model: {config.model}")
        print(f"Mode: {config.pacing}, duration={config.duration_seconds:.0f}s, max_calls={config.max_calls}")
        print(f"Concurrency: {config.concurrency}, interval={interval:.3f}s")
        return 0

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = config.output_dir / f"{run_id}-{safe_slug(config.provider_name)}"
    out_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        out_dir / "metadata.json",
        {
            "provider": config.provider_name,
            "base_url": config.base_url,
            "path": config.path,
            "model": config.model,
            "pacing": config.pacing,
            "duration_seconds": config.duration_seconds,
            "max_calls": config.max_calls,
            "concurrency": config.concurrency,
            "min_interval_seconds": interval,
            "created_at": now_iso(),
        },
    )

    print(f"Output directory: {out_dir}")
    summary = new_summary(config)
    start = time.monotonic()
    deadline = start + config.duration_seconds
    next_index = 1
    next_submit_time = start
    active: dict[concurrent.futures.Future[dict[str, Any]], int] = {}

    with (out_dir / "results.jsonl").open("a", encoding="utf-8") as results_file:
        with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as pool:
            while (next_index <= config.max_calls or active) and not STOP_REQUESTED:
                now = time.monotonic()
                while (
                    next_index <= config.max_calls
                    and len(active) < config.concurrency
                    and now < deadline
                    and now >= next_submit_time
                    and not STOP_REQUESTED
                ):
                    index = next_index
                    active[pool.submit(perform_call, config, index)] = index
                    summary["submitted"] += 1
                    next_index += 1
                    next_submit_time = max(next_submit_time + interval, time.monotonic() + config.min_interval_seconds)
                    now = time.monotonic()

                if not active:
                    if next_index > config.max_calls or time.monotonic() >= deadline:
                        break
                    time.sleep(min(0.25, max(0.01, next_submit_time - time.monotonic())))
                    continue

                done, _pending = concurrent.futures.wait(
                    active.keys(),
                    timeout=0.25,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    active.pop(future, None)
                    result = future.result()
                    update_summary(summary, result)
                    results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
                    results_file.flush()
                    print(
                        f"#{result['index']} status={result.get('status')} "
                        f"ok={result['ok']} limit={result['limit_hit']} "
                        f"latency_ms={result['latency_ms']}",
                        flush=True,
                    )
                    if result.get("limit_hit") and config.stop_on_limit:
                        next_index = config.max_calls + 1
                        break

                if next_index > config.max_calls and not active:
                    break
                if time.monotonic() >= deadline and not active:
                    break

    summary["ended_at"] = now_iso()
    write_json(out_dir / "summary.json", summary)
    (out_dir / "report.txt").write_text(create_report(config, summary, out_dir), encoding="utf-8")

    print("Done.")
    print(f"Successful calls: {summary['ok']}")
    print(f"Failed/limited calls: {summary['failed']}")
    print(f"Report: {out_dir / 'report.txt'}")
    return 0 if summary["failed"] == 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Track API call capacity for an OpenAI-compatible endpoint.")
    parser.add_argument("--config", required=True, help="Path to TOML config.")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without sending API calls.")
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    return run_probe(config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
