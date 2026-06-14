# cc-monitor

**离开电脑也不会错过 Claude Code 的关键事件。**

监控 VSCode Claude Code 扩展的会话状态，当 Claude 完成任务或需要权限确认时，通过飞书/Webhook 推送通知到手机。

```
VSCode Claude 扩展日志 → cc-monitor 实时监控 → 状态变更检测 → 飞书/手机通知
```

## 为什么不用 Claude Code Hooks？

VSCode 扩展**不支持 hooks**（[Issue #21736](https://github.com/anthropics/claude-code/issues/21736)）。cc-monitor 在进程外旁路监听 VSCode 扩展的内部日志，无需 hooks、无需插件 API。

## 功能

- 🔔 **任务完成通知**：Claude 执行完长任务后，推送到手机
- 🔐 **权限请求通知**：Claude 等待审批时提醒你（Auto Mode 下较少触发）
- 🖥 **VSCode 进程外监控**：不依赖 Claude Code 任何内部 API
- 🌍 **跨平台**：Linux / macOS / Windows
- 🔗 **多后端**：飞书、ntfy、Slack、Discord 等任意 Webhook

## 依赖

- Python 3.9+
- VSCode + Claude Code 扩展
- （可选）飞书 Bot 或任意 Webhook 服务

## 快速安装

### Linux / macOS

```bash
# 1. 克隆
git clone https://github.com/nan3958/cc-monitor ~/.local/cc-monitor
cd ~/.local/cc-monitor

# 2. 配置通知后端
mkdir -p ~/.config/cc-monitor
cat > ~/.config/cc-monitor/config << 'EOF'
# 飞书模式（推荐）
FEISHU_APP_ID="cli_xxxxxxxxxxxx"
FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
FEISHU_OPEN_ID="ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# 或者通用 Webhook 模式
# WEBHOOK_URL="https://ntfy.sh/your-topic"
EOF

# 3. 测试通知
./notify.sh custom "🧪 安装测试" "如果你能看到这条消息，通知链路正常"

# 4. 安装后台服务
python3 cc-monitor.py --install

# 5. 管理服务
systemctl --user status cc-monitor   # 查看状态
systemctl --user restart cc-monitor  # 重启
systemctl --user stop cc-monitor     # 停止
```

### Windows

```powershell
# 1. 克隆
git clone https://github.com/nan3958/cc-monitor $env:USERPROFILE\.local\cc-monitor
cd $env:USERPROFILE\.local\cc-monitor

# 2. 配置通知后端
mkdir $env:USERPROFILE\.config\cc-monitor -Force
# 编辑 $env:USERPROFILE\.config\cc-monitor\config 填入飞书凭证或 Webhook URL

# 3. 测试 (PowerShell)
# 需要创建 notify.ps1 或使用 Webhook 后端

# 4. 使用任务计划程序运行
schtasks /create /tn cc-monitor /tr "pythonw $env:USERPROFILE\.local\cc-monitor\cc-monitor.py" /sc onlogon /rl highest
```

## 通知后端配置

### 飞书（推荐）

1. 在[飞书开放平台](https://open.feishu.cn)创建企业自建应用
2. 开通「机器人」能力
3. 获取 App ID、App Secret
4. 在飞书中找到你的用户 Open ID（cc-connect 已配置的可以直接复用）
5. 填入 `~/.config/cc-monitor/config`

```bash
FEISHU_APP_ID="cli_xxxxxxxxxxxx"
FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
FEISHU_OPEN_ID="ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### 通用 Webhook

适用于 ntfy、Slack、Discord、企业微信机器人等：

```bash
WEBHOOK_URL="https://ntfy.sh/your-private-topic"
```

## 与 cc-connect 配合

cc-monitor 是为 **[cc-connect](https://github.com/chenhg5/cc-connect)** 生态设计的补充组件：

| 组件 | 用途 | 通知触发方式 |
|------|------|------------|
| cc-connect | 微信/飞书 ↔ Claude Code 桥接 | `config.toml` 中的 hooks 事件 |
| cc-monitor | VSCode Claude Code 状态监控 | VSCode 扩展日志分析 |

**建议同时运行两者：**
- cc-connect 覆盖微信/飞书会话中的交互
- cc-monitor 覆盖 VSCode 中直接操作的 Claude

**⚠️ 微信已知问题：** 微信 iLink Bot 推送需要有效的 `context_token`，该 token 仅在用户近期主动发消息时存在。定时任务 / 后台推送可能因 token 过期而失败。**飞书无此限制，推荐优先使用飞书。**

cc-connect 的 hooks 配置示例（`~/.cc-connect/config.toml`）：

```toml
[[hooks]]
event = "permission.requested"
type = "command"
command = "/path/to/cc-monitor/notify.sh permission cc-connect"

[[hooks]]
event = "error"
type = "command"
command = "/path/to/cc-monitor/notify.sh error 'cc-connect: ${ERROR_MESSAGE}'"
```

## 运行日志

```bash
# Linux/macOS
tail -f ~/.local/cc-monitor/cc-monitor.log

# 正常运行时应该看到：
# [2026-06-15 03:00:00] cc-monitor starting (Linux)
# [2026-06-15 03:00:00] watching: /home/xxx/.vscode-server/data/logs/.../Claude VSCode.log
# [2026-06-15 03:01:00] STATE: running -> idle  (Research something)
# [2026-06-15 03:01:05] NOTIFY done: VSCode: Research something
```

## 故障排查

### 通知发不出去

1. 手动测试：`./notify.sh custom "test" "hello"`
2. 检查配置文件：`cat ~/.config/cc-monitor/config`
3. 检查飞书 App 是否已发布、机器人能力是否开通

### 监控不到状态变化

1. 确认 VSCode Claude 扩展在运行
2. 手动查找日志：`find ~/.vscode-server/data/logs -name "Claude VSCode.log"`
3. 检查日志中是否有 `update_session_state` 事件：
   ```bash
   grep "update_session_state" ~/.vscode-server/data/logs/*/exthost*/Anthropic.claude-code/Claude\ VSCode.log
   ```

### 让 Claude 自己修 🐛

把 [CLAUDE.md](CLAUDE.md) 放到项目目录下，Claude Code 会自动读取。里面包含了 cc-monitor 的架构说明和常见 bug 修复指南，Claude 可以自行诊断和修复大部分问题。

## License

MIT
