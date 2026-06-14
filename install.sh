#!/bin/bash
# cc-monitor 安装脚本 — 支持 Linux (systemd) 和 macOS (launchd)
# 用法:
#   curl -sSL https://.../install.sh | bash
#   或
#   git clone ... && cd cc-monitor && ./install.sh
#
# 安装前先配好通知后端:
#   飞书: ~/.config/cc-monitor/config
#   通用 webhook: WEBHOOK_URL 环境变量
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${HOME}/.local/cc-monitor"
BIN_DIR="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.config/cc-monitor"

echo "=== cc-monitor installer ==="
echo "Install dir: $INSTALL_DIR"

# 1. 复制文件
mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR"
cp "$SCRIPT_DIR/cc-monitor.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/notify.sh" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/cc-monitor.py" "$INSTALL_DIR/notify.sh"

# 2. 创建 notify 命令
cat > "$BIN_DIR/notify" << 'BINEOF'
#!/bin/bash
exec "$HOME/.local/cc-monitor/notify.sh" "$@"
BINEOF
chmod +x "$BIN_DIR/notify"

# 确保 ~/.local/bin 在 PATH 中
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "${HOME}/.bashrc"
    echo "  Added ~/.local/bin to PATH in .bashrc"
fi

# 3. 配置通知后端（如果还没配）
if [[ ! -f "$CONFIG_DIR/config" ]]; then
    cat > "$CONFIG_DIR/config" << 'CONFEOF'
# cc-monitor 通知配置
# 飞书模式（推荐）:
#   FEISHU_APP_ID="cli_xxxxxxxxxxxx"
#   FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#   FEISHU_OPEN_ID="ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#
# 通用 webhook 模式（ntfy / Slack / Discord）:
#   WEBHOOK_URL="https://ntfy.sh/your-topic"
CONFEOF
    echo "  Config template created at $CONFIG_DIR/config"
    echo "  >>> Edit this file to set up your notification backend <<<"
fi

# 4. 安装后台服务
case "$(uname -s)" in
    Linux)
        SERVICE_FILE="${HOME}/.config/systemd/user/cc-monitor.service"
        mkdir -p "$(dirname "$SERVICE_FILE")"
        cat > "$SERVICE_FILE" << SERVEOF
[Unit]
Description=cc-monitor: VSCode Claude Code state watcher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/cc-monitor.py
Restart=on-failure
RestartSec=10
StandardOutput=append:${INSTALL_DIR}/cc-monitor.log
StandardError=append:${INSTALL_DIR}/cc-monitor.log

[Install]
WantedBy=default.target
SERVEOF
        systemctl --user daemon-reload
        systemctl --user enable cc-monitor.service
        systemctl --user start cc-monitor.service
        echo "  systemd service installed and started"
        echo "  Manage: systemctl --user status/restart/stop cc-monitor"
        ;;

    Darwin)
        PLIST_FILE="${HOME}/Library/LaunchAgents/com.cc-monitor.plist"
        mkdir -p "$(dirname "$PLIST_FILE")"
        cat > "$PLIST_FILE" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cc-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${INSTALL_DIR}/cc-monitor.py</string>
    </array>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/cc-monitor.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/cc-monitor.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLISTEOF
        launchctl load "$PLIST_FILE"
        echo "  launchd service installed and started"
        echo "  Manage: launchctl unload/load $PLIST_FILE"
        ;;

    *)
        echo "  Unknown platform. Please set up the service manually."
        echo "  Run: python3 ${INSTALL_DIR}/cc-monitor.py &"
        ;;
esac

echo ""
echo "=== Installation complete ==="
echo "Logs: ${INSTALL_DIR}/cc-monitor.log"
echo "Config: ${CONFIG_DIR}/config"
echo ""
echo "Next steps:"
echo "  1. Edit ${CONFIG_DIR}/config with your notification credentials"
echo "  2. Test: notify custom 'Test' 'Hello from cc-monitor'"
echo "  3. Restart service after config: systemctl --user restart cc-monitor"
