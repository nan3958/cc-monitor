#!/bin/bash
# 通用通知脚本 — 支持飞书 / 自定义 webhook
# 配置方式（优先级从高到低）：
#   1. 环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_OPEN_ID
#   2. 配置文件 ~/.config/cc-monitor/config
#   3. WEBHOOK_URL 环境变量（通用 webhook 模式）
set -euo pipefail

# ---- 加载配置 ----
CONFIG_FILE="${HOME}/.config/cc-monitor/config"
if [[ -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
fi

APP_ID="${FEISHU_APP_ID:-}"
APP_SECRET="${FEISHU_APP_SECRET:-}"
USER_OPEN_ID="${FEISHU_OPEN_ID:-}"
WEBHOOK_URL="${WEBHOOK_URL:-}"
TOKEN_FILE="/tmp/cc-monitor-feishu-token"
DEBOUNCE_DIR="/tmp/cc-monitor-debounce"

TYPE="${1:-custom}"
ARG1="${2:-}"
ARG2="${3:-}"
NOW=$(date +%s)

# ---- 去重 ----
mkdir -p "$DEBOUNCE_DIR"
debounce_file="${DEBOUNCE_DIR}/${TYPE}"
declare -A COOLDOWNS=([start]=0 [done]=0 [stop]=120 [permission]=10 [error]=5 [custom]=0)
cooldown="${COOLDOWNS[$TYPE]:-60}"

if [[ -f "$debounce_file" ]]; then
    last_time=$(cat "$debounce_file" 2>/dev/null || echo 0)
    if (( cooldown > 0 && NOW - last_time < cooldown )); then
        exit 0
    fi
fi

# ---- Stop 事件：检查是否涉及工具调用 ----
if [[ "$TYPE" == "stop" ]]; then
    stdin_json=$(cat 2>/dev/null || echo "")
    if [[ -n "$stdin_json" ]]; then
        has_tools=$(echo "$stdin_json" | python3 -c "
import sys, json
found = False
try:
    data = json.load(sys.stdin)
    msgs = data.get('messages', data.get('transcript', []))
    for m in (msgs if isinstance(msgs, list) else []):
        if isinstance(m, dict) and m.get('role') == 'assistant':
            for block in (m.get('content', []) if isinstance(m.get('content'), list) else []):
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    found = True
except Exception: pass
print('yes' if found else 'no')
" 2>/dev/null)
        if [[ "$has_tools" != "yes" ]]; then
            exit 0
        fi
    fi
fi

echo "$NOW" > "$debounce_file"

# ---- 构建消息 ----
declare -A ICONS=([start]="🚀" [done]="✅" [stop]="✅" [permission]="🔐" [error]="❌" [custom]="📢")
declare -A HEADINGS=(
    [start]="开始处理"
    [done]="完成"
    [stop]="完成"
    [permission]="需要确认"
    [error]="异常"
    [custom]=""
)

if [[ "$TYPE" == "custom" ]]; then
    TITLE="$ARG1"
    BODY="$ARG2"
else
    ICON="${ICONS[$TYPE]:-📢}"
    HEADING="${HEADINGS[$TYPE]:-通知}"
    TITLE="${ICON} ${HEADING}"
    [[ -n "$ARG1" ]] && TITLE="${TITLE} — ${ARG1}"
    BODY=""
fi

# ---- 发送 ----
if [[ -n "$APP_ID" && -n "$APP_SECRET" && -n "$USER_OPEN_ID" ]]; then
    # 飞书模式
    get_token() {
        if [[ -f "$TOKEN_FILE" ]]; then
            local cached_at=$(stat -c %Y "$TOKEN_FILE" 2>/dev/null || echo 0)
            if (( NOW - cached_at < 7000 )); then cat "$TOKEN_FILE"; return; fi
        fi
        curl -sf --max-time 5 -X POST \
            'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal' \
            -H 'Content-Type: application/json' \
            -d "{\"app_id\":\"$APP_ID\",\"app_secret\":\"$APP_SECRET\"}" 2>&1 | \
            python3 -c "import sys,json; print(json.load(sys.stdin)['tenant_access_token'])"
    }

    TOKEN=$(get_token)
    [[ -z "$TOKEN" ]] && { echo "Failed to get feishu token" >&2; exit 1; }
    echo "$TOKEN" > "$TOKEN_FILE"

    TEXT="${TITLE}${BODY:+\n}${BODY}"
    export TEXT USER_OPEN_ID TOKEN TITLE
    python3 -c "
import json, os, sys, urllib.request
text = os.environ['TEXT']
payload = {'receive_id': os.environ['USER_OPEN_ID'], 'msg_type': 'text', 'content': json.dumps({'text': text})}
req = urllib.request.Request(
    'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id',
    data=json.dumps(payload).encode(),
    headers={'Authorization': f'Bearer {os.environ[\"TOKEN\"]}', 'Content-Type': 'application/json'},
    method='POST')
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = json.loads(resp.read())
        if result.get('code') != 0:
            print(f'Feishu send failed: {result}', file=sys.stderr); sys.exit(1)
        print(f'Feishu: {os.environ[\"TITLE\"]}')
except Exception as e:
    print(f'Feishu send error: {e}', file=sys.stderr); sys.exit(1)
"

elif [[ -n "$WEBHOOK_URL" ]]; then
    # 通用 webhook 模式（ntfy / Slack / Discord / 自定义）
    curl -sf --max-time 5 -X POST "$WEBHOOK_URL" \
        -H 'Content-Type: application/json' \
        -d "{\"text\":\"$TITLE${BODY:+\n}${BODY}\"}" 2>&1
else
    echo "No notification backend configured. Set FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_OPEN_ID or WEBHOOK_URL." >&2
    exit 1
fi
