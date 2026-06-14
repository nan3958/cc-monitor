#!/usr/bin/env python3
"""cc-monitor: 监控 Claude Code 会话状态，检测空闲/等待并推送通知。

跨平台支持：Linux / macOS / Windows
用法:
  cc-monitor.py                           # 默认
  CC_MONITOR_NOTIFY=/path/to/notify.sh cc-monitor.py  # 指定通知脚本
  cc-monitor.py --install                 # 安装后台服务
  cc-monitor.py --test                    # 测试通知链路
"""

import json, os, re, select, subprocess, sys, time, platform
from datetime import datetime
from pathlib import Path

# ---- 通知脚本查找 ----
NOTIFY_SCRIPT = os.environ.get("CC_MONITOR_NOTIFY",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "notify.sh"))

# ---- 跨平台 VSCode 日志路径 ----
def _find_vscode_logs() -> list[Path]:
    """返回可能的 VSCode 日志目录列表"""
    home = Path.home()
    candidates = []
    system = platform.system()

    if system == "Windows":
        # Windows 本地 VSCode
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / "Code" / "logs")
        # VSCode Remote Server (SSH 到 Linux 时 Windows 端也会有)
        candidates.append(home / ".vscode-server" / "data" / "logs")
    elif system == "Darwin":
        candidates.append(home / "Library" / "Application Support" / "Code" / "logs")
        candidates.append(home / ".vscode-server" / "data" / "logs")
    else:  # Linux
        candidates.append(home / ".vscode-server" / "data" / "logs")
        # 桌面版 VSCode
        candidates.append(home / ".config" / "Code" / "logs")

    return [d for d in candidates if d.exists()]

def find_latest_log() -> Path | None:
    """在所有 VSCode 日志目录中找到最新的 Claude VSCode.log"""
    all_logs = []
    for base in _find_vscode_logs():
        for log_dir in base.iterdir():
            if not log_dir.is_dir():
                continue
            for match in log_dir.glob("**/exthost*/Anthropic.claude-code/Claude VSCode.log"):
                all_logs.append(match)
    if all_logs:
        all_logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return all_logs[0]
    return None

# ---- Python 版 tail -F（替代系统 tail，跨平台） ----
def follow_file(path: Path):
    """生成器：持续 yield 文件的新行，自动处理 logrotate"""
    f = open(path, 'r', encoding='utf-8', errors='replace')
    # 跳到文件末尾
    f.seek(0, 2)
    ino = os.fstat(f.fileno()).st_ino

    while True:
        # 检查 inode 是否变化（logrotate: 旧文件被重命名/删除）
        try:
            cur_ino = os.stat(str(path)).st_ino
        except FileNotFoundError:
            cur_ino = None

        if cur_ino != ino:
            # 文件被轮转，重新打开
            f.close()
            f = open(path, 'r', encoding='utf-8', errors='replace')
            ino = os.fstat(f.fileno()).st_ino

        line = f.readline()
        if line:
            yield line
        else:
            # 文件可能被 truncate 了
            if os.stat(str(path)).st_size < f.tell():
                f.seek(0, 2)
            time.sleep(0.5)  # 没有新数据时等待

# ---- 状态监控核心 ----
IDLE_CONFIRM, DEBOUNCE_IDLE = 5, 120
WAITING_CONFIRM, DEBOUNCE_WAITING = 8, 60
SESSION_TTL = 7200

sessions: dict = {}
_notify_ok = True  # 通知脚本可用标志

def send_notify(evt: str, title: str) -> bool:
    global _notify_ok
    if not _notify_ok:
        return False
    try:
        r = subprocess.run([NOTIFY_SCRIPT, evt, title],
            timeout=10, capture_output=True, text=True)
        if r.returncode != 0 and "No notification backend" in (r.stdout + r.stderr):
            _notify_ok = False
        return r.returncode == 0
    except FileNotFoundError:
        print(f"[{datetime.now()}] WARN: notify script not found: {NOTIFY_SCRIPT}", file=sys.stderr)
        _notify_ok = False
        return False
    except Exception as e:
        print(f"[{datetime.now()}] notify error: {e}", file=sys.stderr)
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
    print(f"[{datetime.now()}] STATE: {e['state']} -> {state}  ({title[:50]})", file=sys.stderr, flush=True)
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
        if e["state"] == "idle" and dur >= IDLE_CONFIRM and (now - e["last_idle"] > DEBOUNCE_IDLE):
            label = f"VSCode: {title}" if title else "VSCode Claude 任务完成"
            if send_notify("done", label):
                e["last_idle"] = now; e["running_since"] = 0
                print(f"[{datetime.now()}] NOTIFY done: {label}", file=sys.stderr, flush=True)
        elif e["state"] == "waiting_input" and dur >= WAITING_CONFIRM and (now - e["last_waiting"] > DEBOUNCE_WAITING):
            label = f"VSCode: {title}" if title else "VSCode Claude 需要关注"
            if send_notify("permission", label):
                e["last_waiting"] = now
                print(f"[{datetime.now()}] NOTIFY waiting: {label}", file=sys.stderr, flush=True)
    for sid in stale: del sessions[sid]

def main():
    if "--test" in sys.argv:
        print("Testing notify...")
        ok = send_notify("custom", "cc-monitor 测试消息")
        print(f"Notify {'OK' if ok else 'FAILED'}")
        return

    if "--install" in sys.argv:
        install_service()
        return

    print(f"[{datetime.now()}] cc-monitor starting ({platform.system()})", file=sys.stderr, flush=True)
    print(f"[{datetime.now()}] notify: {NOTIFY_SCRIPT}", file=sys.stderr, flush=True)

    log = find_latest_log()
    if not log:
        print(f"[{datetime.now()}] ERROR: no Claude VSCode log found. Searched:", file=sys.stderr)
        for d in _find_vscode_logs():
            print(f"  - {d}", file=sys.stderr)
        sys.exit(1)

    print(f"[{datetime.now()}] watching: {log}", file=sys.stderr, flush=True)
    last_rotate_check = time.time()

    for line in follow_file(log):
        now = time.time()
        # 每 60s 检查日志轮转
        if now - last_rotate_check > 60:
            new_log = find_latest_log()
            if new_log and new_log != log:
                print(f"[{datetime.now()}] rotated: {log} -> {new_log}", file=sys.stderr)
                log = new_log
                break  # 跳出当前 follow，重新进入
            last_rotate_check = now

        p = parse_state(line)
        if p: on_state(p["sid"], p["state"], p["title"])
        check_notify()

    # 轮转后重新进入
    if log:
        main()

def install_service():
    """安装后台服务"""
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
        print("systemd service installed. Manage: systemctl --user status/restart/stop cc-monitor")

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
        print("launchd service installed. Manage: launchctl unload/load " + str(plist_path))

    elif system == "Windows":
        print("Windows: use Task Scheduler or nssm to run:")
        print(f"  {sys.executable} {script}")
        print("Or add a scheduled task:")
        print(f'  schtasks /create /tn cc-monitor /tr "{sys.executable} {script}" /sc onlogon /rl highest')

    else:
        print(f"Unknown platform: {system}")
        print(f"Run manually: {sys.executable} {script}")

if __name__ == "__main__":
    main()
