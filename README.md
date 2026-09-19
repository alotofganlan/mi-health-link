# mi-health-link

这是一个自托管的小米运动健康同步项目。它会把你的小米健康数据定期保存到自己的 Supabase 数据库，并在 ChatGPT 中添加一个名为 `Xiaomi Health` 的自定义 MCP 应用。连接完成后，你可以直接让 GPT 查询睡眠、活动、心率、锻炼和经期记录，也可以让它比较最近 7 天或 30 天的变化。

项目目前按单用户方式设计。每位使用者需要准备自己的小米账号、Supabase 项目、VPS 和 ChatGPT 授权。它不是小米官方项目，也不能代替医疗诊断或避孕建议。

## 我应该看哪份文档

| Purpose | Document |
| --- | --- |
| 先了解项目能做什么 | 继续阅读本 README |
| 从全新的 Supabase 和 VPS 开始部署 | [从零部署指南](DEPLOYMENT.md) |
| 配置手机解锁和位置更新 | [Automate 完整流程](docs/AUTOMATE_FLOW.md) |
| 让 Slack 自动触发 ChatGPT 晨报 | [Slack 自动触发晨报](docs/SLACK_SETUP.md) |
| 查看已经完成和仍在设计的功能 | [项目现状](PROJECT_STATUS.md) |
| 了解健康数据、位置和第三方服务的隐私边界 | [隐私与安全说明](PRIVACY.md) |

第一次部署建议先按部署指南完成主动查询，确认 ChatGPT 能读取健康数据后，再配置 Automate 和 Slack。

## 它是怎样工作的

部署后，数据会按下面的顺序流动：

```text
小米运动健康账号
        ↓
VPS 定期从小米云同步数据
        ↓
自己的 Supabase 数据库
        ↓
VPS 上的 Xiaomi Health MCP 服务
        ↓
ChatGPT 中的 Xiaomi Health 应用
```

这里的 MCP 是 ChatGPT 调用外部工具的一种协议。这个项目提供 MCP 服务，ChatGPT 通过它查询你的健康数据。Supabase 同时负责保存数据和处理登录授权；小米凭据、Supabase Secret key 等敏感信息只保存在 VPS 上，不会交给 ChatGPT。

## 目前可以做什么

已经完成的主要功能包括：

- 登录小米账号，并在会话过期后自动刷新；
- 把小米返回的数据整理成便于查询和比较的健康指标，并在部分同步与诊断流程中保留原始记录；
- 定期同步睡眠、步数、心率、血氧、压力、锻炼、体重、经期等数据；
- 在 ChatGPT 中查询某一天的数据，或比较 7 天、30 天和更长时间的趋势；
- 生成包含昨晚睡眠、昨日健康、天气和趋势信息的晨报；
- 通过 OAuth 限制只有指定的 Supabase 用户能够连接 MCP；
- 可选接入 Nightscout、手机位置、Automate 和 Slack。

小米在不同地区、设备和账号上返回的字段并不完全相同。小米 App 中显示某项数据，也不代表云端接口一定会返回同样的内容。项目会区分“没有数据”和“同步失败”，避免把接口错误当成零值。

仍在设计或尚未完全支持的内容包括小米日历、训练恢复时间，以及和小米 App 完全一致的经期预测。具体进度见[项目现状](PROJECT_STATUS.md)。

## 部署前需要准备

- 一个已经产生健康记录的小米账号；
- 一个 [Supabase](https://supabase.com/) 账号；
- 一台 Ubuntu VPS；
- 一个指向 VPS、能够使用 HTTPS 的域名；
- 一个支持自定义 MCP 应用或开发者模式的 ChatGPT 账号或工作区；
- 能够通过 SSH 登录 VPS，并执行基本的 Linux 命令。

如果只想在 ChatGPT 中主动查询数据，不需要安装 Automate，也不需要 Slack。它们只用于手机解锁检测、位置更新和自动晨报。

## 从零部署

完整操作请看[从零部署指南](DEPLOYMENT.md)。下面先说明整个过程，方便你判断每一步在做什么。

### 1. 创建 Supabase 项目

注册 Supabase 后，新建一个项目，并在 SQL Editor 中运行：

```text
migrations/00000000_bootstrap.sql
```

这会建立保存小米原始数据、睡眠记录、健康指标和晨报状态所需的数据库表。之后还需要在 Supabase Auth 中创建一个登录用户；这个用户供你连接 ChatGPT 插件时使用，不是小米账号。

### 2. 在 VPS 安装项目

```bash
git clone https://github.com/alotofganlan/mi-health-link.git
cd mi-health-link
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
cp .env.example .env
cp xiaomi-credentials.example.json xiaomi-credentials.json
chmod 600 .env xiaomi-credentials.json
```

然后在 `.env` 中填写 Supabase 项目地址、服务器密钥和小米账号所在区域。不要把 Supabase Secret key、`xiaomi-credentials.json` 或小米登录信息提交到 Git。

### 3. 登录小米并同步数据

```bash
.venv/bin/xiaomi-health-sync login
.venv/bin/xiaomi-health-sync discover
```

`login` 会引导你完成小米登录；`discover` 会读取小米实际返回的健康项目，并开始同步历史记录。确认数据已经写入 Supabase 后，再启用每 15 分钟运行一次的 systemd timer。

### 4. 启动 MCP 服务

在 `.env` 中补充 MCP 地址、Supabase Auth 地址和允许登录的用户 UUID，然后安装项目提供的 MCP systemd service。服务默认只监听 `127.0.0.1:8765`，再由 Caddy、Nginx 或其他反向代理提供公网 HTTPS。

不要把 `8765` 端口直接暴露到公网。

### 5. 配置 Supabase OAuth

在 Supabase Dashboard 中启用 OAuth 2.1 Server 和 Dynamic Client Registration。授权页面由这个项目提供；当 ChatGPT 连接时，你会先用刚才创建的 Supabase Auth 用户登录，再确认是否允许访问。

这里使用 OAuth 是为了让 ChatGPT 获得访问 MCP 的短期授权。ChatGPT 不需要、也不应该知道 Supabase Secret key。

### 6. 把 Xiaomi Health 添加到 ChatGPT

在 ChatGPT 的应用设置中启用开发者模式，然后创建自定义应用：

```text
名称：Xiaomi Health
MCP 地址：https://你的域名/mcp
认证方式：OAuth
```

完成登录和工具扫描后，新建一个对话，选择 `Xiaomi Health`，先尝试：

```text
列出你现在可以读取的健康指标。
```

再尝试：

```text
读取最近 7 天的睡眠，比较总时长、入睡时间和深睡变化。
```

ChatGPT 能否创建自定义 MCP 应用取决于套餐、工作区角色和管理员设置。当前支持范围以 [OpenAI 的说明](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)为准。

### 7. 按需配置自动晨报

主动查询正常后，再决定是否配置自动晨报。手机上的 Automate 会在解锁时通知 VPS，并在移动超过设定距离后更新位置；VPS 确认小米出现新的主睡眠记录后，再通过 Slack 触发 ChatGPT 生成晨报。

这部分涉及两个 HTTP 请求、状态码判断、失败重试和 Slack 事件任务。手机 Flow 见 [Automate 流程说明](docs/AUTOMATE_FLOW.md)，Slack 应用、用户令牌、频道和 ChatGPT 触发任务见 [Slack 自动触发晨报](docs/SLACK_SETUP.md)，服务端判断规则见 [晨报唤醒说明](docs/WAKE_MONITOR.md)。

## 可选：接入 Nightscout 动态血糖

如果欧态动态血糖仪能够把数据发送到你自己的 Nightscout，本项目可以继续转播这些血糖记录。完整链路是：

```text
欧态动态血糖仪
        ↓
Nightscout
        ↓
VPS 每 5 分钟读取一次新数据
        ↓
Supabase 的 glucose_samples 表
        ↓
CGM 镜像程序
        ↓
写回小米云的 blood_sugar 动态血糖通道
```

Nightscout 在这里是动态血糖仪和本项目之间的数据来源，Supabase 保存同步后的血糖记录，方便 ChatGPT 查询和进行趋势分析。随后，独立的 CGM 镜像程序会从数据库读取尚未处理的新记录，把时间对齐到分钟，检查重复值和同一分钟的冲突，再写回小米云并进行回读验证。

这是可选功能：项目不会替你创建 Nightscout，也不要求没有动态血糖仪的用户配置它。Nightscout 地址和访问令牌只应保存在 VPS 的 `.env` 或受保护的 Nightscout 配置文件中。安装方法见[从零部署指南中的 Nightscout 部分](DEPLOYMENT.md#nightscout)。

## 在 ChatGPT 中怎么用

查询新日期或新指标时，在该条消息中选择或提及 `Xiaomi Health`。例如：

```text
整理昨天的睡眠、步数、心率、压力和锻炼情况。没有数据的项目不要猜。
```

```text
比较最近 30 天和之前 30 天的静息心率、睡眠时长和运动量。
```

```text
预计还有多久进入经期？同时说明预测依据和数据是否完整。
```

如果只是继续讨论刚刚查到的结果，通常不需要重复调用应用。

## 本地开发和测试

安装开发依赖：

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

运行测试：

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/pytest -q
```

OAuth 授权页的 JavaScript 已经随 Python 包一起发布，普通部署不需要安装 Node.js。只有修改 `web/oauth-consent.js` 时，才需要使用 Node.js 22 重新打包：

```bash
npm ci
npm run build:oauth
```

构建后需要同时提交 `package-lock.json` 和生成的 `src/xiaomi_health_sync/static/oauth-consent.js`。

公开仓库前可以使用 gitleaks 检查待发布文件：

```bash
gitleaks dir . --redact
```

仓库中的 `.gitleaks.toml` 只允许四个明确的文档占位符和测试假值。

查看脱敏后的配置状态：

```bash
.venv/bin/xiaomi-health-sync show-config
```

`show-config` 不会直接输出 `.env` 中的密钥。排查问题时也不要把完整 `.env`、小米 Cookie、`passToken`、`ssecurity` 或 Supabase Secret key 粘贴到聊天和 issue 中。

## 隐私和安全

健康数据保存在你自己的 Supabase 项目中。MCP 服务会检查 OAuth token、scope 和允许登录的用户 UUID；数据库中的健康表不会直接开放给 ChatGPT。

### 什么是“原始健康 JSON”

小米接口返回的是一组 JSON 数据。项目会把其中常用的内容整理成睡眠时长、心率、步数、锻炼时间等独立字段，方便 ChatGPT 查询和比较；在部分同步、接口探索和故障诊断流程中，也会保留一份尚未完全整理的原始记录。数据库里的 `raw_records.payload`，以及部分健康表中的 `raw` 字段，保存的就是这类内容。

原始 JSON 可能包含精确测量时间、睡眠阶段、来源记录编号、设备或数据来源标识，以及当前版本尚未识别的小米字段。保留它有助于以后修正解析规则或找回新指标，但也意味着数据库中保存的信息可能比晨报实际展示的更多。常规晨报和趋势分析主要使用整理后的字段；具体有哪些内容会被 MCP 返回，仍取决于调用的工具，不能把原始表当作可以公开的数据。

目前还不能通过一个开关完全停止保存所有原始 JSON：睡眠自动触发等旧查询仍会读取 `raw` 中的 `sleep_day`、`sleep_source` 等字段，饮食、锻炼和诊断功能也可能使用原始内容。项目当前没有统一的数据保留期限，也没有一键删除全部数据的命令。停止使用时，最直接的完整删除方式是销毁自己的 Supabase 项目；不要把数据库导出、备份或诊断输出上传到 GitHub。每位使用者都应使用独立的 Supabase 项目，不要和朋友共用同一个数据库。

如果启用天气功能，手机坐标会发送到自己的 VPS，VPS 可能再向 Nominatim 和 Open-Meteo 查询地点与天气。项目不会使用公网 IP 推测位置。准备公开部署或邀请朋友使用前，请先阅读[隐私与安全说明](PRIVACY.md)。

## 相关文档

- [从零部署指南](DEPLOYMENT.md)：Supabase、VPS、OAuth、ChatGPT 和自动晨报的完整步骤；
- [项目现状](PROJECT_STATUS.md)：已经完成、部分完成和尚未实现的功能；
- [隐私与安全说明](PRIVACY.md)：敏感数据、第三方服务和公开前检查；
- [Automate 完整流程](docs/AUTOMATE_FLOW.md)：手机端所有 Block 的连接和参数；
- [Slack 自动触发晨报](docs/SLACK_SETUP.md)：创建 Slack App、获取用户令牌、连接 ChatGPT、测试触发和排错；
- [晨报唤醒说明](docs/WAKE_MONITOR.md)：解锁检测、位置与晨报生成规则。


## 开源许可证

本项目使用 [MIT License](LICENSE)。你可以自行使用、修改和分发代码，但需要保留许可证和版权声明。
