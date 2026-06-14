# CLAUDE.md — cc-monitor 项目指南

## 这是什么

cc-monitor 是一个 Python 守护进程，监控 VSCode Claude Code 扩展的内部日志，在 Claude 完成任务或需要权限确认时推送飞书/Webhook 通知。

## 架构

```
VSCode Claude 扩展
  └─ Claude VSCode.log (内部日志，包含 update_session_state 事件)
      └─ cc-monitor.py (tail 日志，解析状态 JSON)
          └─ notify.sh (飞书 / Webhook 推送)
              └─ 手机通知
```

## 关键文件

| 文件 | 作用 |
|------|------|
| `cc-monitor.py` | 主监控守护进程，跨平台 |
| `notify.sh` | 通知后端（飞书+Webhook），Unix |
| `README.md` | 用户文档 |

## cc-monitor.py 工作原理

1. `find_latest_log()` — 扫描 VSCode 日志目录，找最新的 `Claude VSCode.log`
2. `follow_file()` — Python 实现的 tail -F（跨平台，处理 logrotate）
3. `parse_state()` — 正则提取 `Received message from webview:` 中的 `update_session_state` JSON
4. `on_state()` — 追踪每个 session 的状态变化（running/idle/waiting_input）
5. `check_notify()` — 检查条件，触发通知：
   - `idle` 持续 ≥5s 且距上次通知 >120s → `notify done`
   - `waiting_input` 持续 ≥8s 且距上次通知 >60s → `notify permission`

## 关键约束

- **VSCode 扩展不支持 hooks**（上游 issue #21736），所以用日志监控绕过
- **微信推送不可靠**：微信 iLink Bot 需要 context_token，token 过期则推送失败。飞书无此问题
- **Auto Mode 下很少触发 waiting_input**：因为大多数权限被 Auto Mode 自动处理了
- **阈值设计依据**：`waiting_input` 的工具调用间隙通常 ≤3s，权限等待通常 ≥10s，所以确认时间设为 8s

## 依赖

- Python 3.9+
- bash (notify.sh)
- curl (notify.sh 飞书 API 调用)
- VSCode + Claude Code 扩展

## 常见 bug 修复

### 通知发不出去

1. 先手动跑 `./notify.sh custom "test" "hello"` 看报错
2. 检查 `~/.config/cc-monitor/config` 中的飞书凭证是否正确
3. 飞书 token 有效期 2h，脚本有缓存，但过期后会自动刷新
4. 飞书 App 必须已「发布」且开通「机器人」能力
5. `WEBHOOK_URL` 模式下检查 URL 是否可达

### 监控不到状态变化

1. 确认 VSCode Claude 扩展在运行且有会话
2. `find ~/.vscode-server/data/logs -name "Claude VSCode.log"` 看日志文件是否存在
3. `grep "update_session_state" <日志路径>` 确认有状态事件
4. macOS 路径不同：`~/Library/Application Support/Code/logs/`
5. Windows 路径：`%APPDATA%\Code\logs\`

### cc-monitor.py 启动失败

1. `python3 cc-monitor.py --test` 测试通知链路
2. 检查飞书 API 是否能连通：`curl https://open.feishu.cn`
3. 如果飞书 API 不通，需要配置代理（export HTTPS_PROXY=...）

### 安装脚本在飞牛 NAS (Debian) 上

飞牛 NAS 运行 cc-connect 作为 systemd user daemon。cc-monitor 用同样的机制：
- 服务文件：`~/.config/systemd/user/cc-monitor.service`
- 管理：`systemctl --user status/restart/stop cc-monitor`
- 日志：`~/.local/cc-monitor/cc-monitor.log`

## 修改原则

- `cc-monitor.py` 保持纯 Python 标准库（不引入 pip 依赖）
- `notify.sh` 保持单文件，不引入额外工具
- 跨平台代码用 `platform.system()` 分支
- 日志路径用 `_find_vscode_logs()` 统一管理
- 阈值修改前先看实际日志数据分布（用 `grep "update_session_state"` 分析）
