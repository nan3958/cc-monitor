#!/usr/bin/env python3
"""cc-monitor: 监控 Claude Code 会话状态，检测空闲/等待并推送通知。

跨平台支持：Linux / macOS / Windows
"""

import json, os, re, subprocess, sys, time, platform, queue, threading
from datetime import datetime
from pathlib import Path

# ---- 通知脚本 ----
_script_dir = os.path.dirname(os.path.abspath(__file__))
_default_notify = os.path.join(_script_dir, "notify.sh")
# Windows 上优先用 notify.py（避免 CMD 弹窗）
if platform.system() == "Windows":
    _py_notify = os.path.join(_script_dir, "notify.py")
    if os.path.exists(_py_notify):
        _default_notify = _py_notify
NOTIFY_SCRIPT = os.environ.get("CC_MONITOR_NOTIFY", _default_notify)

# ---- 跨平台 VSCode 日志路径 ----
def _find_vscode_dirs() -> list[Path]:
    home = Path.home()
    candidates = []
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / "Code" / "logs")
        candidates.append(home / ".vscode-server" / "data" / "logs")
    elif system == "Darwin":
        candidates.append(home / "Library" / "Application Support" / "Code" / "logs")
        candidates.append(home / ".vscode-server" / "data" / "logs")
    else:
        candidates.append(home / ".vscode-server" / "data" / "logs")
        candidates.append(home / ".config" / "Code" / "logs")
    return [d for d in candidates if d.exists()]

def find_all_logs() -> list[Path]:
    """返回当前活跃的 Claude VSCode.log 文件（只看最近的日期目录）"""
    all_logs = []
    for base in _find_vscode_dirs():
        date_dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
        for log_dir in date_dirs[:2]:  # 只看最近 2 个日期目录
            for match in log_dir.glob("**/exthost*/Anthropic.claude-code/Claude VSCode.log"):
                all_logs.append(match)
    return sorted(all_logs, key=lambda p: p.stat().st_mtime, reverse=True)

# ---- tail -F（跨平台） ----
def follow_file(path: Path):
    f = open(path, 'r', encoding='utf-8', errors='replace')
    f.seek(0, 2)
    ino = os.fstat(f.fileno()).st_ino
    while True:
        try:
            cur_ino = os.stat(str(path)).st_ino
        except FileNotFoundError:
            cur_ino = None
        if cur_ino != ino:
            f.close()
            f = open(path, 'r', encoding='utf-8', errors='replace')
            ino = os.fstat(f.fileno()).st_ino
        line = f.readline()
        if line:
            yield line
        else:
            if os.stat(str(path)).st_size < f.tell():
                f.seek(0, 2)
            time.sleep(0.5)

# ---- 状态监控核心 ----
IDLE_CONFIRM, DEBOUNCE_IDLE = 5, 120
WAITING_CONFIRM, DEBOUNCE_WAITING = 8, 60
FAIL_RETRY_DEBOUNCE = 60  # 通知失败后的重试间隔
SESSION_TTL = 7200

sessions: dict = {}
_log_file = None

def _log(msg: str):
    ts = datetime.now()
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)
    if _log_file:
        print(f"[{ts}] {msg}", file=_log_file, flush=True)

def send_notify(evt: str, title: str) -> bool:
    try:
        if NOTIFY_SCRIPT.endswith('.py'):
            cmd = [sys.executable, NOTIFY_SCRIPT, evt, title]
        else:
            cmd = [NOTIFY_SCRIPT, evt, title]
        r = subprocess.run(cmd, timeout=10, capture_output=True, text=True)
        return r.returncode == 0
    except FileNotFoundError:
        _log(f"WARN: notify script not found: {NOTIFY_SCRIPT}")
        return False
    except Exception as e:
        _log(f"notify error: {e}")
        return False

def parse_state(line: str) -> dict | None:
    try:
        m = re.search(r'Received message from webview:\s*(\{.*\})', line)
        if not m: return None
        msg = json.loads(m.group(1))
        req = msg.get("request", {})
        if req.get("type") != "update_session_state": return None
        return {"sid": req.get("sessionId","?"), "state": req.get("state","?"), "title": req.get("title","")}
    except: return None

def on_state(sid: str, state: str, title: str):
    now = time.time()
    if sid not in sessions:
        sessions[sid] = {"state": state, "since": now, "title": title,
                         "last_idle": 0, "last_waiting": 0, "running_since": 0}
        return
    e = sessions[sid]
    if state == e["state"]: return
    _log(f"STATE: {e['state']} -> {state}  ({title[:50]})")
    e["state"] = state; e["since"] = now
    if title: e["title"] = title
    if state == "running" and e.get("running_since", 0) == 0:
        e["running_since"] = now

def check_notify():
    now = time.time()
    stale = []
    for sid, e in sessions.items():
        if now - e["since"] > SESSION_TTL: stale.append(sid); continue
        dur = now - e["since"]
        title = e.get("title", "")[:60]
        if e["state"] == "idle" and dur >= IDLE_CONFIRM:
            debounce_ok = (now - e["last_idle"] > DEBOUNCE_IDLE)
            retry_ok = (now - e["last_idle"] > FAIL_RETRY_DEBOUNCE and e["last_idle"] > 0)
            if debounce_ok or retry_ok:
                label = f"VSCode: {title}" if title else "VSCode Claude 任务完成"
                ok = send_notify("done", label)
                e["last_idle"] = now
                if ok:
                    e["running_since"] = 0
                    _log(f"NOTIFY done: {label}")
                else:
                    _log(f"NOTIFY done FAILED (will retry in {FAIL_RETRY_DEBOUNCE}s): {label}")
        elif e["state"] == "waiting_input" and dur >= WAITING_CONFIRM:
            debounce_ok = (now - e["last_waiting"] > DEBOUNCE_WAITING)
            retry_ok = (now - e["last_waiting"] > FAIL_RETRY_DEBOUNCE and e["last_waiting"] > 0)
            if debounce_ok or retry_ok:
                label = f"VSCode: {title}" if title else "VSCode Claude 需要关注"
                ok = send_notify("permission", label)
                e["last_waiting"] = now
                if ok:
                    _log(f"NOTIFY waiting: {label}")
                else:
                    _log(f"NOTIFY waiting FAILED (will retry in {FAIL_RETRY_DEBOUNCE}s): {label}")
    for sid in stale: del sessions[sid]

# ---- 主循环 ----
_line_queue: queue.Queue = queue.Queue()

def _watch_file(path: Path):
    try:
        for line in follow_file(path):
            _line_queue.put(line)
    except Exception as e:
        _log(f"watcher died: {path} — {e}")
        _line_queue.put(None)

def main():
    global _log_file
    if "--test" in sys.argv:
        print("Testing notify...")
        ok = send_notify("custom", "cc-monitor 测试消息")
        print(f"Notify {'OK' if ok else 'FAILED'}")
        return

    if "--install" in sys.argv:
        install_service()
        return

    # 文件日志
    _log_file = open(os.path.join(_script_dir, "cc-monitor.log"), 'a', encoding='utf-8')
    _log(f"cc-monitor starting ({platform.system()})")
    _log(f"notify: {NOTIFY_SCRIPT}")

    _start_watching()
    last_rescan = time.time()

    while True:
        try:
            line = _line_queue.get(timeout=1)
        except queue.Empty:
            check_notify()
            now = time.time()
            if now - last_rescan > 120:
                _start_watching()
                last_rescan = now
            continue

        if line is None:
            _start_watching()
            last_rescan = time.time()
            continue

        p = parse_state(line)
        if p:
            on_state(p["sid"], p["state"], p["title"])
        check_notify()

_watching: set = set()

def _start_watching():
    """启动所有日志文件的 watcher 线程"""
    global _watching
    logs = find_all_logs()
    for log in logs:
        if log in _watching:
            continue
        _watching.add(log)
        t = threading.Thread(target=_watch_file, args=(log,), daemon=True)
        t.start()
        _log(f"watching: {log}")

def install_service():
    script = os.path.abspath(__file__)
    system = platform.system()

    if system == "Linux":
        service = f"""[Unit]
Description=cc-monitor: VSCode Claude Code state watcher
After=network-online.target

[Service]
Type=simple
ExecStart={sys.executable} {script}
Restart=on-failure
RestartSec=10
StandardOutput=append:{Path.home()}/.local/cc-monitor/cc-monitor.log
StandardError=append:{Path.home()}/.local/cc-monitor/cc-monitor.log

[Install]
WantedBy=default.target"""
        svc_path = Path.home() / ".config/systemd/user/cc-monitor.service"
        svc_path.parent.mkdir(parents=True, exist_ok=True)
        svc_path.write_text(service)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "cc-monitor"], check=True)
        subprocess.run(["systemctl", "--user", "start", "cc-monitor"], check=True)
        print("systemd service installed.")

    elif system == "Darwin":
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.cc-monitor</string>
    <key>ProgramArguments</key>
    <array><string>{sys.executable}</string><string>{script}</string></array>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{Path.home()}/.local/cc-monitor/cc-monitor.log</string>
    <key>StandardErrorPath</key><string>{Path.home()}/.local/cc-monitor/cc-monitor.log</string>
    <key>RunAtLoad</key><true/>
</dict></plist>"""
        plist_path = Path.home() / "Library/LaunchAgents/com.cc-monitor.plist"
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist)
        subprocess.run(["launchctl", "load", str(plist_path)], check=True)
        print("launchd service installed.")

    elif system == "Windows":
        pythonw = sys.executable.replace('python.exe', 'pythonw.exe')
        print("Windows: run this in admin PowerShell:")
        print(f'  schtasks /create /tn cc-monitor /tr "{pythonw} -X utf8 {script}" /sc onlogon /rl highest /f')

    else:
        print(f"Unknown platform: {system}")

if __name__ == "__main__":
    main()
