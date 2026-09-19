# 从零部署：在 ChatGPT 中使用小米运动健康

这份教程会带你从一个空的 Supabase 项目开始，直到 ChatGPT 中出现可以正常使用的 `Xiaomi Health` 插件。请按顺序完成每一步；每一节都会说明需要填写什么，以及正常情况下会看到什么。

Automate、Slack 和自动晨报不是必需项。先确认 GPT 能读取小米健康数据，再决定是否继续配置这些功能。

> 当前项目是单用户架构。每个人应使用自己的 VPS、Supabase 项目、小米账号和 ChatGPT 授权，不要多人共用。

## 需要准备的账号和设备

你需要：

- 一个小米账号，并已经在小米运动健康中产生数据；
- 一个 [Supabase](https://supabase.com/) 账号；
- 一台 Ubuntu VPS，建议使用仍受支持的 LTS 版本；
- 一个指向 VPS 的域名，并能提供公网 HTTPS；
- 一个支持创建自定义 MCP 应用或开发者模式的 ChatGPT 账号/工作区；
- SSH 和 Linux 命令行基础。

可选：

- Android 手机和 Automate，用于解锁触发与位置更新；
- Slack，用于自动触发晨报；
- Nightscout，用于合并血糖数据；
- ntfy，用于小米登录过期提醒。

## 第 1 步：注册 Supabase 并创建项目

### 1.1 创建账号和项目

1. 打开 [Supabase Dashboard](https://supabase.com/dashboard) 并注册或登录。
2. 创建一个新组织；如果已经有个人组织，可以直接使用。
3. 点击创建新项目。
4. 设置项目名称和强数据库密码。
5. 选择离 VPS 较近、且符合你隐私需求的区域。
6. 等待项目初始化完成。

这个项目会把健康数据保存在 Supabase，并用 Supabase 账号确认“正在连接插件的人是你”。部署完成后，你在 ChatGPT 里看到和使用的是 `Xiaomi Health` 插件；Supabase 是插件背后的数据库和登录服务。

Supabase 官方也建议从 Dashboard 创建项目，再通过 SQL Editor 建表。[查看 Supabase 项目入门说明](https://supabase.com/docs/guides/getting-started/quickstarts/nextjs#create-a-supabase-project)

### 1.2 建立数据库

在 Supabase Dashboard 打开 **SQL Editor**，新建查询，把下面文件的全部内容复制进去并运行：

```text
migrations/00000000_bootstrap.sql
```

这个文件会一次建立新部署需要的表、索引、函数和访问权限。

如果一切正常，你会看到：

- SQL Editor 显示执行成功；
- Table Editor 中能看到 `health_records`、`sleep_sessions` 等表；
- 不要为了“方便”给 `anon` 或 `authenticated` 开放健康表。

项目使用 VPS 上的服务器密钥访问数据库。公开 schema 中的表必须保留 RLS 和最小权限；RLS 与 Data API 是否暴露表是两个不同的安全层。

### 1.3 找到项目 URL 和 API keys

在项目 Dashboard 的 **Connect** 面板或 **Project Settings → API Keys** 中找到：

- Project URL，例如 `https://abcdefghijk.supabase.co`；
- Publishable key；
- Secret key；旧项目也可以使用 legacy `service_role` key。

Project URL 中 `.supabase.co` 前面的那一段就是后文所说的 `PROJECT_REF`。例如上面的 `PROJECT_REF` 是 `abcdefghijk`。看到 `<PROJECT_REF>` 时，用自己的这一段替换即可。

它们的用途不同：

| 值 | 放在哪里 | 能否公开 |
| --- | --- | --- |
| Project URL | VPS 和 OAuth 授权页 | 可以 |
| Publishable key | VPS 配置，并由授权页发送到浏览器 | 可以，但仍应配合 RLS |
| Secret/service-role key | 只放在 VPS `.env` | 绝对不可以 |

不要把 Secret/service-role key 粘贴给 ChatGPT、写进 Automate、放进网页或提交到 Git。该密钥可以绕过 RLS，一旦泄露应立即轮换。

### 1.4 创建只供自己登录 MCP 的用户

打开 **Authentication → Users**，选择添加用户。创建一个只有你知道邮箱和密码的用户。

创建后，复制这个用户的 UUID，后面填写到：

```dotenv
MCP_ALLOWED_SUBJECT=<SUPABASE_USER_UUID>
```

这个用户不是你的小米账号，也不是 Supabase Dashboard 管理员账号。它只是 ChatGPT 在 OAuth 授权页面登录时使用的身份。MCP 会拒绝 UUID 不匹配的其他用户。

## 第 2 步：准备 VPS 和域名

### 2.1 域名解析

在域名服务商处创建 DNS 记录，让一个子域名指向 VPS：

```text
health.example.com → 你的 VPS 公网 IP
```

本文后续用 `health.example.com` 表示你的 `PUBLIC_HOST`。请替换成自己的域名，不要照抄示例地址。

ChatGPT 访问的是公网远程 MCP，不能直接连接 `127.0.0.1`。正式连接前，域名必须可以通过有效的 HTTPS 证书访问。

### 2.2 安装系统依赖和项目

SSH 登录 VPS：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv curl
cd "$HOME"
git clone https://github.com/alotofganlan/mi-health-link.git
cd mi-health-link
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
cp .env.example .env
cp xiaomi-credentials.example.json xiaomi-credentials.json
chmod 600 .env xiaomi-credentials.json
```

如果一切正常，你会看到：

```bash
.venv/bin/xiaomi-health-sync --help
```

应该显示命令帮助，而不是 Python 导入错误。

## 第 3 步：填写基础配置

编辑 VPS 上的 `.env`。基础同步先填写：

```dotenv
SUPABASE_URL=https://<PROJECT_REF>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<SERVER_SECRET_KEY>

XIAOMI_REGION=cn
XIAOMI_CREDENTIALS_FILE=./xiaomi-credentials.json
```

虽然变量名仍叫 `SUPABASE_SERVICE_ROLE_KEY`，新项目可以填写 Supabase Secret key；旧项目可以填写 legacy `service_role` key。

`XIAOMI_REGION` 常见值：

- 中国区：`cn`
- 新加坡：`sg`
- 美国：`us`
- 德国：`de`
- 俄罗斯：`ru`
- 印度区：`i2`

应选择小米运动健康账号实际所在区域，而不是 VPS 所在区域。

检查配置时使用：

```bash
.venv/bin/xiaomi-health-sync show-config
```

该命令用于查看脱敏后的配置状态。不要用会把 `.env` 原文打印到终端或聊天中的命令。

## 第 4 步：登录小米并完成第一次同步

### 4.1 登录小米账号

```bash
cd "$HOME/mi-health-link"
.venv/bin/xiaomi-health-sync login
```

按终端提示完成登录。如果小米要求验证码、设备确认或风险验证，先在官方页面完成，再重新执行命令。

成功后，账号会话保存在：

```text
xiaomi-credentials.json
```

该文件和 `.env` 一样敏感，不能提交、发送或截图公开。

### 4.2 第一次同步

```bash
.venv/bin/xiaomi-health-sync discover
```

第一次运行可能比日常同步慢，因为它会发现小米实际返回过的健康键并回填历史。

如果一切正常，你会看到：

- 命令正常结束，而不是以非零状态退出；
- Supabase 中开始出现 `health_records`、`sleep_sessions` 等记录；
- `discover` 没有把接口错误误报成“空数据”。

到这里，小米数据已经能够进入 Supabase。可以先观察一段时间，确认同步稳定后再继续连接 ChatGPT。

## 第 5 步：启用每 15 分钟自动同步

安装用户级 systemd 服务和定时器：

```bash
mkdir -p "$HOME/.config/systemd/user"
install -m 644 deploy/xiaomi-health-auto-sync.service "$HOME/.config/systemd/user/"
install -m 644 deploy/xiaomi-health-auto-sync.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now xiaomi-health-auto-sync.timer
sudo loginctl enable-linger "$USER"
```

检查：

```bash
systemctl --user status xiaomi-health-auto-sync.timer
journalctl --user -u xiaomi-health-auto-sync.service -n 100 --no-pager
```

如果一切正常，定时器状态会显示为 active，最近一次同步也不会出现认证或数据库错误。

## 第 6 步：配置 MCP 服务

现在补充 `.env` 中与 MCP 和 OAuth 有关的变量：

```dotenv
MCP_PUBLIC_URL=https://<PUBLIC_HOST>/mcp
MCP_HOST=127.0.0.1
MCP_PORT=8765

SUPABASE_AUTH_ISSUER_URL=https://<PROJECT_REF>.supabase.co/auth/v1
SUPABASE_AUTH_JWKS_URL=https://<PROJECT_REF>.supabase.co/auth/v1/.well-known/jwks.json
SUPABASE_AUTH_AUDIENCE=authenticated
MCP_ALLOWED_SUBJECT=<SUPABASE_USER_UUID>
MCP_REQUIRED_SCOPES=openid

SUPABASE_PROJECT_URL=https://<PROJECT_REF>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<PUBLISHABLE_KEY>
```

其中：

- `MCP_ALLOWED_SUBJECT` 必须是第 1.4 步创建的 Auth 用户 UUID；
- `SUPABASE_PROJECT_URL` 和 Publishable key 会用于浏览器中的 OAuth 登录页；
- Secret key 仍然只通过前面的 `SUPABASE_SERVICE_ROLE_KEY` 留在 VPS；
- MCP 默认只监听 `127.0.0.1`，不会直接暴露内部端口。

安装并启动 MCP 服务：

```bash
install -m 644 deploy/xiaomi-health-mcp.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now xiaomi-health-mcp.service
curl --fail http://127.0.0.1:8765/health
```

成功时会返回包含 `"status":"ok"` 的 JSON。

## 第 7 步：为 MCP 配置公网 HTTPS

使用 Caddy、Nginx 或你已有的反向代理，把：

```text
https://<PUBLIC_HOST>
```

转发到：

```text
http://127.0.0.1:8765
```

例如 Caddy 的核心站点配置是：

```caddyfile
<PUBLIC_HOST> {
    reverse_proxy 127.0.0.1:8765
}
```

请把 `<PUBLIC_HOST>` 替换为真实域名，并按照所用反向代理的官方方式加载配置。不要让端口 `8765` 直接对公网监听。

检查：

```bash
curl --fail https://<PUBLIC_HOST>/health
curl -i https://<PUBLIC_HOST>/mcp
curl --fail https://<PUBLIC_HOST>/.well-known/oauth-protected-resource/mcp
```

预期结果：

- `/health` 返回成功；
- 未授权访问 `/mcp` 被拒绝，这是正常的；
- protected-resource 元数据可以正常读取；
- 浏览器访问证书有效，没有 HTTPS 警告。

## 第 8 步：在 Supabase 启用 OAuth 2.1 Server

Supabase Auth 会显示插件的登录和授权页面。你登录后，ChatGPT 获得的是访问 `Xiaomi Health` 插件的权限，不是数据库服务器密钥。

### 8.1 设置 Site URL

在 Supabase Dashboard 打开 **Authentication → URL Configuration**，把 Site URL 设置为：

```text
https://<PUBLIC_HOST>
```

不要在 Site URL 后面加 `/mcp`。

### 8.2 启用 OAuth Server

打开 **Authentication → OAuth Server**：

1. 启用 OAuth 2.1 Server；
2. Authorization Path 填写 `/oauth/consent`；
3. 启用 Dynamic Client Registration，让 ChatGPT 可以自动注册 OAuth 客户端；
4. 保存设置。

Supabase 会把 Site URL 和 Authorization Path 组合成：

```text
https://<PUBLIC_HOST>/oauth/consent
```

项目已经提供这个中文登录和授权页面。

### 8.3 确认 JWT 签名

OAuth 的 `openid` scope 需要非对称 JWT 签名。新项目通常已经使用合适的签名键；请在 Supabase 的 JWT/Signing Keys 设置中确认使用 RS256 或 ES256，而不是旧的 HS256。

检查发现端点：

```bash
curl --fail https://<PROJECT_REF>.supabase.co/auth/v1/.well-known/jwks.json
curl --fail https://<PROJECT_REF>.supabase.co/.well-known/oauth-authorization-server/auth/v1
```

Supabase 官方的 OAuth Server 和 MCP 认证说明：

- [启用 OAuth 2.1 Server](https://supabase.com/docs/guides/auth/oauth-server/getting-started)
- [为 MCP 配置 Supabase Auth](https://supabase.com/docs/guides/auth/oauth-server/mcp-authentication)

## 第 9 步：在 ChatGPT 中连接 Xiaomi Health

这一步会把前面部署好的服务添加到 ChatGPT。你在 ChatGPT 中使用的是 `Xiaomi Health` 插件；插件通过你的 VPS 读取 Supabase 中的健康数据，并在需要时触发新的小米同步。

### 9.1 先确认账号支持

ChatGPT 的自定义 MCP 应用和开发者模式会受到套餐、工作区角色与管理员设置影响。根据当前官方说明，完整 MCP 主要面向 Business、Enterprise 和 Edu；Pro 的能力可能限于读取和获取类工具。

如果设置中没有开发者模式或“创建应用”，不是 VPS 配置错误，而是当前账号或工作区尚未获得该功能。请以 [OpenAI 当前说明](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) 为准。

### 9.2 创建自定义应用

当前界面通常位于：

```text
设置 → 应用 → 高级设置 → 开发者模式
```

启用后，在用户设置或工作区设置中进入：

```text
应用 → 创建
```

然后：

1. 名称填写 `Xiaomi Health`；
2. MCP endpoint 填写 `https://<PUBLIC_HOST>/mcp`；
3. 选择 OAuth 认证；
4. 点击 Scan Tools；
5. 浏览器会跳转到项目提供的 Xiaomi Health 授权页；
6. 使用第 1.4 步创建的 Supabase Auth 邮箱和密码登录；
7. 检查请求的客户端、回调地址和 scope，再点击允许；
8. 等待工具扫描完成，然后创建应用。

不要在 ChatGPT 的任何表单或聊天中填写 Supabase Secret key、小米密码、Cookie、`passToken` 或 `ssecurity`。

### 9.3 在对话中验证

新建对话，从工具或应用菜单选择 Xiaomi Health。先输入：

```text
列出可用的健康指标。
```

再输入：

```text
读取最近 7 天的睡眠，并总结总时长、入睡时间和深睡趋势。
```

如果一切正常，你会看到：

- ChatGPT 能看到 Xiaomi Health 工具；
- 授权后不再返回 401；
- 查询结果来自你自己的数据；
- 日志中没有出现其他用户 UUID。

每次需要读取新数据时，可能需要在该条消息中重新选择或提及应用，这是 ChatGPT 当前应用使用方式的一部分。

## 第 10 步：配置自动晨报（可选）

到这里，你已经可以在 ChatGPT 中主动查询健康数据。下面继续配置的是自动晨报，不需要晨报可以直接跳过。

### 10.1 VPS 配置

在 `.env` 生成并填写一个独立的随机令牌：

```dotenv
WAKE_PROBE_TOKEN=<随机长字符串>
WAKE_PROBE_DEVICE=primary_phone
WAKE_PROBE_SYNC_COOLDOWN_SECONDS=720
WAKE_UNLOCK_WINDOW_MINUTES=120
WAKE_LOCATION_WINDOW_MINUTES=120
WAKE_TIMEZONE=Asia/Shanghai
```

不要重复使用 Supabase key 或小米令牌。

### 10.2 手机 Automate

按照 [Automate 完整流程](docs/AUTOMATE_FLOW.md) 逐个创建 Block。手机会：

- 每次成功解锁调用 `/api/wake-probe`；
- 获取定位，但只有移动至少 1 公里才调用 `/api/location-update`；
- 遇到网络异常时等待后继续运行，不让 Flow 永久停止。

### 10.3 Slack 触发

如果希望由 Slack 唤醒 ChatGPT，在 `.env` 填写：

```dotenv
SLACK_USER_TOKEN=<SLACK_USER_OAUTH_TOKEN>
SLACK_CHANNEL_ID=<CHANNEL_ID>
```

优先使用用户 OAuth token；只有兼容旧配置时才使用 `SLACK_WEBHOOK_URL`。Slack 消息只应包含报告类型、`report_id` 和固定触发语，不应包含完整健康数据或坐标。

Slack 应用、频道和 ChatGPT 工作区权限因账号而异。先验证手工消息能够触发对应 GPT，再启用自动晨报。创建发送应用、添加 `chat:write` 用户权限、取得频道 ID、连接 ChatGPT、配置固定触发语和排错的完整步骤见 [Slack 自动触发晨报](docs/SLACK_SETUP.md)。

服务端判断与接口验证见 [VPS 唤醒与位置配置](docs/WAKE_MONITOR.md)。

## 第 11 步：其他可选功能

### 小米登录过期提醒

填写 `.env.example` 中的：

```dotenv
XIAOMI_AUTH_NTFY_URL=
XIAOMI_AUTH_NTFY_TOKEN=
```

当前 ntfy 只用于小米会话失效提醒，不是完整健康异常告警系统。

### Nightscout

填写 Nightscout URL 和 token，再安装仓库中的 Nightscout service/timer。没有 Nightscout 的用户可以全部留空。

### GitHub Actions

现有 workflow 更适合项目维护者在首次部署完成后更新 VPS。第一次安装不要依赖它；先按本文手工完成一遍，确认目录、虚拟环境、`.env` 和 systemd 服务都正确。

## 常见问题

### Supabase 能写入，但 ChatGPT 返回 401

检查：

- `MCP_ALLOWED_SUBJECT` 是否等于登录用户的 UUID；
- JWT 是否使用非对称签名；
- `SUPABASE_AUTH_ISSUER_URL`、JWKS URL 和项目 ref 是否属于同一项目；
- ChatGPT 是否完成了 OAuth 登录和允许操作；
- 修改 OAuth 配置后是否重新创建或刷新了 ChatGPT 应用。

### ChatGPT 找不到 MCP 工具

检查：

```bash
curl --fail https://<PUBLIC_HOST>/health
curl --fail https://<PUBLIC_HOST>/.well-known/oauth-protected-resource/mcp
journalctl --user -u xiaomi-health-mcp.service -n 100 --no-pager
```

另外确认 ChatGPT 账号拥有创建自定义 MCP 应用的权限。

### ChatGPT 能调用工具，但没有健康数据

先检查同步：

```bash
.venv/bin/xiaomi-health-sync discover
journalctl --user -u xiaomi-health-auto-sync.service -n 100 --no-pager
```

确认小米区域正确、登录会话没有过期，并在 Supabase Table Editor 检查是否已有记录。

### 修改 `.env` 后没有生效

重启相关服务：

```bash
systemctl --user restart xiaomi-health-mcp.service
systemctl --user start xiaomi-health-auto-sync.service
```

### 手机解锁后没有晨报

先区分三件事：

1. Automate 是否成功收到解锁事件；
2. `/api/wake-probe` 是否返回 202；
3. VPS 后台是否找到未发送的新睡眠。

详细排查见 [Automate 完整流程](docs/AUTOMATE_FLOW.md) 和 [唤醒接口说明](docs/WAKE_MONITOR.md)。

## 日常维护

常用检查命令：

```bash
systemctl --user status xiaomi-health-mcp.service
systemctl --user status xiaomi-health-auto-sync.timer
journalctl --user -u xiaomi-health-mcp.service -n 100 --no-pager
journalctl --user -u xiaomi-health-auto-sync.service -n 100 --no-pager
```

定期完成：

- 更新系统和项目依赖；
- 检查 Supabase 容量和 Auth 用户；
- 撤销不用的 Slack、ntfy、Nightscout 和小米令牌；
- 检查 OAuth 应用和 ChatGPT 工具权限；
- 备份前确认备份中是否包含敏感健康数据；
- 停止使用时删除 VPS 凭据并销毁对应 Supabase 项目。

## 安全底线

永远不要提交或公开：

- `.env`、`.env.save`；
- `xiaomi-credentials.json`；
- Supabase Secret/service-role key；
- 小米 Cookie、`passToken`、`serviceToken`、`ssecurity`；
- Slack、Nightscout、ntfy token；
- Automate 中的 `WAKE_PROBE_TOKEN`；
- 数据库导出、运行日志和包含个人位置的截图。

完整的数据流和第三方边界见[隐私与安全说明](PRIVACY.md)，已完成功能与已知限制见[项目现状](PROJECT_STATUS.md)。
