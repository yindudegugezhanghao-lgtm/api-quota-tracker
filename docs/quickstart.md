# Quickstart

This guide shows the safest way to run API Quota Tracker for the first time.

## 1. Download

Download the latest release:

https://github.com/yindudegugezhanghao-lgtm/api-quota-tracker/releases/latest

Or download the repository ZIP from GitHub and unzip it.

## 2. Install Python

Install Python 3.11 or newer from:

https://www.python.org/downloads/

During installation on Windows, enable `Add python.exe to PATH`.

## 3. Start the GUI

Double-click:

```text
open_gui.bat
```

If Windows asks which app should open it, choose Windows Command Processor.

## 4. Fill The Form

Use the values from your API provider:

- `Provider name`: a short local name, such as `my-provider`
- `API base URL`: the official API base URL
- `Path`: usually `/chat/completions`
- `Model`: the exact model name to test
- `API key`: paste the key for this run
- `Output folder`: keep the default `runs` unless you want a custom folder

The GUI does not save the API key in its settings file.

## 5. Smoke Test First

Click `Smoke test: 5 calls`.

Read the log and then open `report.txt`. Continue only if the smoke test succeeds.

## 6. Choose A Probe Mode

Use `Run until limit` when you want to learn where the provider starts rejecting calls.

Use `Even window: 5h/1500` when a provider advertises a fixed quota window and you want to test that pacing.

## 7. Read The Output

Each run creates a folder under `runs`, with:

- `report.txt`: human-readable summary
- `summary.json`: machine-readable summary
- `results.jsonl`: one JSON record per call
- `metadata.json`: config metadata without the API key

Start with `report.txt`.

## CLI Alternative

Copy the example config:

```powershell
Copy-Item config.example.toml config.toml
```

Edit `config.toml`, then set your key only in the current terminal session:

```powershell
$env:PROVIDER_API_KEY = Read-Host "Paste provider API key"
```

Check the config without sending API calls:

```powershell
python plan_probe.py --config config.toml --dry-run
```

Run:

```powershell
python plan_probe.py --config config.toml
```
