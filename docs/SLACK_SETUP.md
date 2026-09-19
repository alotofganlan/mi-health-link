# Slack 自动触发晨报

这份文档说明如何让 VPS 在发现新的主睡眠后，通过 Slack 通知 ChatGPT 生成晨报。

Slack 在这里不保存完整晨报。VPS 只发送一条包含 `report_id`、报告类型和固定触发语的短消息；ChatGPT 收到后，再通过 Xiaomi Health MCP 的 `get_morning_context(report_id)` 读取睡眠、昨日健康、趋势、周期、天气和数据覆盖状态。

这部分完全可选。如果你只想在 ChatGPT 中主动查询健康数据，可以不配置 Slack。

## 1. 先分清两个 Slack 应用

整个链路涉及两个不同的应用：

1. **你自己创建的发送应用**：VPS 使用它的用户 OAuth Token 调用 Slack `chat.postMessage`。
2. **ChatGPT 的 Slack 应用或连接**：让 ChatGPT 能看到目标频道的新消息，并据此运行晨报任务。

它们不是同一个应用，也不能共用令牌。发送应用只负责发出安全的触发消息；ChatGPT 连接只负责读取触发消息。小米账号、Supabase Secret key、健康明细和坐标都不应发送到 Slack。

## 2. 准备一个专用频道

建议创建一个只用于自动化事件的频道，例如：

```text
mi-health-events
```

推荐使用私人频道，只邀请自己的 Slack 账号和 ChatGPT 应用。虽然触发消息不包含健康数值，但 `report_id`、报告类型和发送时间仍属于个人自动化信息。

稍后需要这个频道的 ID，而不是频道名称。使用浏览器打开频道时，地址通常类似：

```text
https://app.slack.com/client/TXXXXXXXX/CXXXXXXXX
```

最后一段以 `C` 开头的值就是频道 ID。私人频道有时以 `G` 开头，也可以直接使用。不要填写 `#mi-health-events`。

## 3. 创建负责发消息的 Slack App

打开 [Slack API 的应用管理页面](https://api.slack.com/apps)：

1. 选择 **Create New App**。
2. 选择 **From scratch**。
3. 名称可以填写 `mi-health-link`。
4. 选择刚才创建频道所在的同一个 Slack workspace。
5. 创建应用。

这个应用不需要 Event Subscriptions、Socket Mode、Slash Command 或公开发布。

### 添加最小权限

进入应用的 **OAuth & Permissions**：

1. 找到 **Scopes**。
2. 在 **User Token Scopes** 中添加 `chat:write`。
3. 不要为了方便添加读取所有频道或文件等无关权限。
4. 选择 **Install to Workspace**；已经安装过则选择 **Reinstall to Workspace**。
5. 审核授权内容并允许。

本项目需要的是用户 OAuth Token，因为部分 ChatGPT/Slack 触发配置可能不会把机器人身份发布的消息视为普通用户消息。`chat:write` 是调用 `chat.postMessage` 所需的权限。

安装完成后，在 **OAuth Tokens for Your Workspace** 中复制 **User OAuth Token**。它通常是用户令牌格式；不要把 Bot User OAuth Token 填到 `SLACK_USER_TOKEN`。

> Slack 后台界面会变化。如果添加 User Token Scope 后仍只显示 Bot Token，请不要把 Bot Token 当作用户令牌使用。重新确认权限添加在 **User Token Scopes**，然后重新安装应用；受管理的 workspace 可能还需要管理员批准。

## 4. 配置 VPS

编辑 VPS 项目目录中的 `.env`：

```dotenv
SLACK_USER_TOKEN=<USER_OAUTH_TOKEN>
SLACK_CHANNEL_ID=<CHANNEL_ID>
SLACK_WEBHOOK_URL=
```

项目优先使用 `SLACK_USER_TOKEN`。只有没有配置用户令牌时，才会退回旧的 Incoming Webhook；公开部署不推荐把 Webhook 作为默认方案。

保护配置文件：

```bash
chmod 600 .env
```

重启晨报服务：

```bash
systemctl --user restart xiaomi-health-mcp.service
```

不要把 `.env`、Slack Token、Webhook URL 或带 Authorization 头的请求截图上传到 GitHub 或 issue。

## 5. 连接 ChatGPT 与 Slack

ChatGPT 和 Slack 必须连接到同一个 workspace：

1. 在 Slack Marketplace 中找到 ChatGPT，并按当前界面安装或申请管理员批准。
2. 在 ChatGPT 的 **设置 → 应用**（部分账号显示为 Apps 或 Plugins）中找到 Slack。
3. 选择 **连接**，完成 Slack OAuth 授权。
4. 确认授权的是目标频道所在的 workspace。ChatGPT 同时只能连接一个 Slack workspace。
5. 把 `@ChatGPT` 添加到需要监控的公开或私人频道。

能否使用 Slack 事件触发任务，取决于 ChatGPT 套餐、地区、workspace 设置和管理员权限。受管理的 Business、Enterprise 或 Edu workspace 可能需要管理员启用 Slack 和事件触发任务。

## 6. 创建晨报触发任务

在 ChatGPT Work 或当前账号提供的任务界面中，创建一个由 Slack 新消息触发的任务：

- 监听第 2 步创建的频道；
- 只在消息包含下面这行固定文本时继续：

```text
Xiaomi Health morning report ready
```

- 忽略不包含该文本的其他消息；
- 从消息中读取 `report_id` 和 `report_kind`；
- 调用 Xiaomi Health MCP 的 `get_morning_context(report_id)`；
- 检查 `data_coverage`，已有数据可以报告，缺失或部分覆盖必须明确说明；
- 不根据 Slack 消息猜测健康数据、天气或位置；
- 把生成的晨报回复到 ChatGPT，而不是把完整健康内容重新发到 Slack。

可以使用下面的任务说明：

```text
监听这个 Slack 频道的新消息。只有消息包含
“Xiaomi Health morning report ready”时才继续。

读取消息中的 report_id 和 report_kind，调用 Xiaomi Health MCP 的
get_morning_context(report_id)。核验 data_coverage 后生成中文晨报。
已有数据正常汇报；partial 或 sync_failed 表示覆盖可能不完整，不代表
所有字段都不存在。不要猜测缺失数据，也不要从 Slack 消息推断坐标、
天气或健康值。忽略不包含固定触发语的消息。
```

创建完成后，确认任务所用的 ChatGPT 账号已经连接 Xiaomi Health MCP 和 Slack，并能访问目标频道。

## 7. 测试顺序

### 只检查触发消息格式

在 VPS 项目目录运行：

```bash
.venv/bin/python slack_morning_report.py --dry-run
```

输出必须以这行开头：

```text
Xiaomi Health morning report ready
```

`--dry-run` 不会发送 Slack 消息。

### 手工测试 Slack 监听

在目标频道用自己的 Slack 账号手工发送：

```text
Xiaomi Health morning report ready
```

这一步只验证 ChatGPT 是否能看到消息并启动任务。因为没有真实 `report_id`，任务应明确提示缺少报告编号，而不是编造晨报。

### 使用已有报告做完整测试

如果数据库中已经存在有效的晨报 `report_id`，可以临时运行：

```bash
WAKE_REPORT_ID=<已有的报告编号> \
WAKE_REPORT_KIND=morning \
.venv/bin/python slack_morning_report.py
```

成功时终端会显示触发消息已发送。ChatGPT 随后应调用 MCP，并生成与该 `report_id` 对应的固定快照。

不要编造 `report_id`。不存在的编号只能测试 Slack 消息发送，不能验证晨报上下文。

## 8. 正常触发流程

配置完成后不需要每天手工运行脚本：

1. 手机解锁后，Automate 调用 `/api/wake-probe`。
2. VPS 检查小米是否出现新的主睡眠。
3. 同一条逻辑睡眠尚未发送过晨报时，VPS 创建新的 `report_id`。
4. VPS 以用户身份向 Slack 频道发送固定触发消息。
5. ChatGPT 的 Slack 任务识别固定文本。
6. ChatGPT 调用 `get_morning_context(report_id)` 并生成晨报。

同一条睡眠不会因为重复解锁而反复发送。接口返回 `202` 只表示 VPS 已接受解锁事件；没有新增睡眠时不会创建新的 `report_id`。

## 9. 常见问题

### Slack 中完全没有消息

检查：

- `.env` 是否同时填写了 `SLACK_USER_TOKEN` 和 `SLACK_CHANNEL_ID`；
- Token 是否来自正确的 workspace；
- `chat:write` 是否加在 User Token Scopes；
- 修改权限后是否重新安装了应用；
- 运行脚本时是否出现 `missing_scope`、`invalid_auth` 或 `channel_not_found`；
- VPS 是否能访问 `https://slack.com/api/chat.postMessage`。

### 返回 `not_in_channel`

用户令牌代表的 Slack 用户必须能访问目标频道。确认该用户已经加入频道；私人频道需要被现有成员邀请。

如果使用的是兼容用 Bot Token 或 Webhook，对应应用也必须有目标频道的访问权限，但这不等于用户身份触发一定可用。

### 消息发出了，但 ChatGPT 没有触发

依次检查：

- 消息是否包含完整的 `Xiaomi Health morning report ready`，包括 `morning`；
- ChatGPT 是否连接到同一个 Slack workspace；
- `@ChatGPT` 是否已经加入目标频道；
- 事件任务是否监听正确频道；
- 任务是否错误限制了发送者、线程或频道；
- 消息是否由机器人/Webhook 身份发送。当前项目优先使用用户 OAuth Token，就是为了避免部分任务忽略机器人消息；
- 当前 ChatGPT 账号和 workspace 是否具备 Slack 事件触发任务能力。

### ChatGPT 触发了，但说没有健康数据

检查触发消息是否包含真实 `report_id`，以及任务是否确实调用了：

```text
get_morning_context(report_id)
```

`partial` 或 `sync_failed` 说明部分数据覆盖不完整，不等于睡眠和所有健康数据都不存在。任务必须逐项读取上下文中的实际值和 `data_coverage`。

### 重复触发

检查：

- Automate Flow 是否只运行一个实例；
- 是否同时配置了用户 Token 和另一个外部脚本重复发送；
- 数据库中的晨报发送记录是否正常；
- Slack 任务是否同时监听频道消息和线程回复。

## 10. 隐私与撤销

- Slack 消息只应包含固定触发语、`report_id`、`report_kind` 和标题；
- 不要发送睡眠阶段、心率、血糖、经期、坐标或密钥；
- 建议为晨报使用专用私人频道；
- 离开项目时，撤销 Slack App 的用户授权并删除 VPS 中的 Token；
- 如果令牌曾出现在截图、终端历史或公开仓库中，应立即撤销并重新生成；
- Slack 和 ChatGPT 会按照各自的保留、权限和隐私规则处理频道消息。

## 11. 官方参考

- [Slack：创建并安装应用](https://api.slack.com/apps)
- [Slack：`chat:write` 权限](https://api.slack.com/scopes/chat%3Awrite)
- [Slack：`chat.postMessage`](https://api.slack.com/methods/chat.postMessage)
- [Slack：OAuth 安装流程](https://api.slack.com/authentication/oauth-v2)
- [Slack：查找 workspace 和频道 ID](https://slack.com/help/articles/221769328-Locate-your-Slack-URL-or-ID)
- [OpenAI：Using Slack in ChatGPT](https://help.openai.com/en/articles/12525822)
- [OpenAI：Using ChatGPT in Slack](https://help.openai.com/en/articles/12462158)
