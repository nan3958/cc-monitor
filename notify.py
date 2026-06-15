#!/usr/bin/env python3
"""cc-monitor 通知后端（Python 版，跨平台替代 notify.sh）
支持飞书 / 通用 Webhook
配置: ~/.config/cc-monitor/config (key=value 格式)
"""

import json, os, sys, time, urllib.request, platform
from pathlib import Path

# Windows 默认 GBK 会导致 emoji 编码失败，强制 UTF-8
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 服务以 SYSTEM 运行时 Path.home() 指向错误路径，Windows 上始终用用户目录
if platform.system() == "Windows":
    _USER_HOME = Path("C:/Users/Nan")
    CONFIG_FILE = _USER_HOME / ".config" / "cc-monitor" / "config"
    _TMP = _USER_HOME / "AppData" / "Local" / "Temp"
    TOKEN_FILE = _TMP / "cc-monitor-feishu-token"
    DEBOUNCE_DIR = _TMP / "cc-monitor-debounce"
else:
    CONFIG_FILE = Path.home() / ".config" / "cc-monitor" / "config"
    TOKEN_FILE = Path("/tmp/cc-monitor-feishu-token")
    DEBOUNCE_DIR = Path("/tmp/cc-monitor-debounce")

def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg

def main():
    cfg = load_config()
    app_id = os.environ.get("FEISHU_APP_ID", cfg.get("FEISHU_APP_ID", ""))
    app_secret = os.environ.get("FEISHU_APP_SECRET", cfg.get("FEISHU_APP_SECRET", ""))
    user_open_id = os.environ.get("FEISHU_OPEN_ID", cfg.get("FEISHU_OPEN_ID", ""))
    webhook_url = os.environ.get("WEBHOOK_URL", cfg.get("WEBHOOK_URL", ""))

    evt_type = sys.argv[1] if len(sys.argv) > 1 else "custom"
    arg1 = sys.argv[2] if len(sys.argv) > 2 else ""
    arg2 = sys.argv[3] if len(sys.argv) > 3 else ""

    now = int(time.time())

    # 去重
    cooldowns = {"start": 0, "done": 0, "stop": 120, "permission": 10, "error": 5, "custom": 0}
    cooldown = cooldowns.get(evt_type, 60)
    DEBOUNCE_DIR.mkdir(parents=True, exist_ok=True)
    debounce_file = DEBOUNCE_DIR / evt_type
    if debounce_file.exists() and cooldown > 0:
        try:
            last_time = int(debounce_file.read_text().strip())
            if now - last_time < cooldown:
                sys.exit(0)
        except:
            pass
    debounce_file.write_text(str(now))

    # 构建消息
    icons = {"start": "🚀", "done": "✅", "stop": "✅", "permission": "🔐", "error": "❌", "custom": "📢"}
    headings = {
        "start": "Claude 开始处理", "done": "Claude 任务完成", "stop": "Claude 任务完成",
        "permission": "Claude 需要权限确认", "error": "Claude 出现异常", "custom": ""
    }
    if evt_type == "custom":
        title = arg1
        body = arg2
    else:
        icon = icons.get(evt_type, "📢")
        heading = headings.get(evt_type, "通知")
        title = f"{icon} {heading}"
        if arg1:
            title = f"{title} — {arg1}"
        body = ""

    text = title
    if body:
        text = f"{title}\n{body}"

    # 飞书模式
    if app_id and app_secret and user_open_id:
        token = get_feishu_token(app_id, app_secret, now)
        if not token:
            print("Failed to get feishu token", file=sys.stderr)
            sys.exit(1)
        send_feishu(token, user_open_id, text, title)
    elif webhook_url:
        send_webhook(webhook_url, text)
    else:
        print("No notification backend configured.", file=sys.stderr)
        sys.exit(1)

def get_feishu_token(app_id, app_secret, now):
    if TOKEN_FILE.exists():
        try:
            cached_at = TOKEN_FILE.stat().st_mtime
            if now - cached_at < 7000:
                return TOKEN_FILE.read_text().strip()
        except:
            pass
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode('utf-8'),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            token = data.get("tenant_access_token", "")
            if token:
                TOKEN_FILE.write_text(token)
            return token
    except Exception as e:
        print(f"Feishu token error: {e}", file=sys.stderr)
        return ""

def send_feishu(token, open_id, text, title):
    payload = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": text})
    }
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        data=json.dumps(payload).encode('utf-8'),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            if result.get("code") != 0:
                print(f"Feishu send failed: {result}", file=sys.stderr)
                sys.exit(1)
            print(f"Feishu: {title}")
    except Exception as e:
        print(f"Feishu send error: {e}", file=sys.stderr)
        sys.exit(1)

def send_webhook(url, text):
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode('utf-8'),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"Webhook: {resp.status}")
    except Exception as e:
        print(f"Webhook error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
