# 隐私与安全说明

> 本文描述当前代码的隐私边界和公开前注意事项，不构成安全保证。

## 推荐部署模型

本项目只推荐单用户自托管：

- 每个人使用自己的 VPS；
- 每个人使用自己的 Supabase 项目；
- 每个人使用自己的小米账号和本地凭据；
- 每个人配置自己的 Slack、Nightscout、ntfy 或其他可选服务。

不要让多位朋友共用同一套部署。当前数据库没有 tenant_id，MCP 只支持一个 MCP_ALLOWED_SUBJECT，小米凭据和唤醒令牌也是全局配置。共享部署可能导致不同用户的数据互相覆盖或被错误读取。

## 项目会处理哪些敏感数据

根据启用的功能，系统可能处理：

- 小米账号 ID、设备 ID、serviceToken、ssecurity 和 passToken；
- 睡眠、心率、血氧、压力、体温、体重、经期、饮食、锻炼和血糖；
- 手机解锁时间、睡眠结束时间和同步时间；
- 手机位置、地级市、区县、国家和天气；
- Supabase 用户 ID、OAuth 访问令牌和 MCP 请求；
- Slack 频道 ID、用户 OAuth 令牌或 webhook；
- Nightscout 和 ntfy 的地址或访问令牌。

这些内容不应进入 Git、问题讨论、截图、聊天记录或公开日志。

## 第三方数据流

| 服务 | 可能收到的数据 | 是否必需 |
| --- | --- | --- |
| 小米账号与健康云 | 登录信息、会话 Cookie、设备标识、健康读写请求 | 核心同步必需 |
| Supabase | 归一化健康记录、同步状态、晨报快照、降精度位置、OAuth 身份 | 当前架构必需 |
| ChatGPT / MCP 客户端 | 用户主动查询的健康数据和晨报上下文 | 使用 AI 分析时必需 |
| Nominatim | 用于反向地理编码的经纬度 | 可选；启用城市解析时使用 |
| Open-Meteo | 用于天气查询的经纬度 | 可选；启用天气时使用 |
| Slack | 报告编号、报告类型、标题和固定触发指令；正常情况下不发送完整健康数据 | 可选 |
| Nightscout | 血糖读取请求和访问令牌 | 可选 |
| ntfy | 小米登录过期提醒；配置私有主题时还会发送鉴权令牌 | 可选 |
| jsDelivr | 不再使用；Supabase JavaScript SDK 已锁定版本并随项目本地发布 | 否 |

## 位置隐私

位置链路需要特别注意：

1. 手机向 VPS 上传经纬度。
2. VPS 会先把经纬度四舍五入到三位小数，约为百米级。
3. 降精度后的坐标会写入 Supabase，并发送给 Nominatim 解析城市。
4. 天气查询会把数据库中的降精度坐标发送给 Open-Meteo。
5. `get_morning_context` 只返回地级市、区县和国家，不返回经纬度。
6. 服务关闭了 Uvicorn 客户端 IP access log，也不会从公网 IP 推断位置。

原始坐标只在手机到 VPS 的请求中出现，第三方位置服务收到的是降精度坐标。不接受任何第三方位置处理的用户仍应关闭城市解析和天气功能，或改用自托管服务。

## 凭据保存

- .env、.env 的备份变体和 Xiaomi 凭据文件必须保持未跟踪状态。
- 交互式登录生成的 Xiaomi 凭据文件会尝试设置为 0600。
- 用户仍应手工确认 VPS 上 .env 和凭据文件权限为 0600。
- Supabase service-role key 只能存在于服务器，绝不能进入浏览器或 ChatGPT。
- MCP_PUBLIC_URL、Supabase publishable key 和普通项目 URL 可以公开；service-role key 不可以。
- Slack user token、webhook、Nightscout token 和 wake-probe token 都属于秘密。
- 不要把异常对象、完整请求头、Cookie 或原始 API 响应直接写入公开日志。

## 数据库权限

新部署使用 `migrations/00000000_bootstrap.sql`。其中 20 张健康、同步、位置和晨报表都启用了 RLS，并显式撤销 `anon` 和 `authenticated` 的表权限；服务端通过 `service_role` 访问。MCP 还会校验 Supabase JWT 的签名、issuer、audience、scope 和唯一允许的 subject。

仍需注意：

- `service_role` key 会绕过 RLS，一旦泄露等同于数据库高权限泄露；
- 旧部署需要执行 `migrations/20260917_harden_data_api_privileges.sql`，不能只更新代码；
- 当前没有多用户行级隔离设计，不允许多人共用一套部署；
- RLS 和 Data API 是否暴露表是两个不同的安全层，部署后应同时检查。

## OAuth 页面风险

OAuth 页面设置了 no-store、CSP、no-referrer、禁止 iframe 和 MIME 嗅探等安全响应头。Supabase SDK 已锁定版本并打包进项目静态资源，登录页面不会再从第三方 CDN 加载 JavaScript。

仓库中的 `web/oauth-consent.js` 是源码，`src/mi_health_link/static/oauth-consent.js` 是随 Python 包发布的浏览器文件。修改源码后应使用 Node.js 22 执行 `npm ci` 和 `npm run build:oauth`，并提交新的 bundle 与 `package-lock.json`。

## 日志与消息

当前实现采取了以下措施：

- MCP 服务关闭客户端 IP access log；
- 唤醒和位置接口不记录 Bearer 令牌、请求头、坐标、城市、设备别名或睡眠明细；
- 晨报日志只保留处理状态，不记录醒来时间、睡眠分钟数、报告编号或原始记录编号；
- Slack 的错误文本会尝试隐藏已配置的令牌；
- Slack 正常只发送 report_id 等触发元数据，健康内容由受保护的 MCP 读取。

仍应避免使用 debug HTTP 日志，因为底层库可能输出 URL、响应体或请求元数据。诊断命令可能打印原始小米响应，运行时应把终端和日志视为敏感环境。

## 原始数据和删除

普通同步已转向直接写入归一化记录，但以下场景仍可能保存原始 JSON：

- 特殊端点探索；
- 手工 probe；
- 已验证键的诊断工具；
- 旧版本留下的 raw_records；
- 饮食、锻炼等专用表中的 raw 字段。

当前没有统一的数据保留期限，也没有“一键删除我的全部数据”命令。停止使用时至少应：

1. 停止并禁用相关 systemd 服务和定时器；
2. 撤销小米、Slack、Nightscout、ntfy 等令牌；
3. 删除 VPS 上的 .env 和 Xiaomi 凭据；
4. 删除或销毁对应的 Supabase 项目；
5. 检查备份、日志、GitHub Actions artifact 和终端记录。

## 本次公开前审计结论

本地规则扫描没有在当前待发布文件中发现明显的真实 Slack、GitHub、Google、JWT、Supabase 项目地址或私钥格式。真实 `.env` 和 `xiaomi-credentials.json` 未被 Git 跟踪，当前权限均为 `0600`。

当前待发布文件已使用 gitleaks 8.30.1 扫描，除 `.gitleaks.toml` 中四个精确的文档/测试假值外，没有发现秘密。创建新仓库后仍应再次扫描完整的新历史。

旧 Git 历史包含真实作者邮箱，也曾包含个人域名、设备别名、测试 SID 和位置示例。计划创建全新的公开仓库是正确做法：只复制清理后的工作树，不复制 .git。

## 创建全新公开仓库前的检查表

- [ ] 不复制旧 .git 目录。
- [ ] 确认 .env、.env.save、凭据、数据库导出、APK、日志和缓存均未复制。
- [ ] 运行 git status 和 git ls-files，逐项检查待发布文件。
- [ ] 使用 gitleaks 或 TruffleHog 扫描。
- [ ] 选择并添加 LICENSE；未添加许可证前，其他人默认没有明确的复制和修改授权。
- [ ] 替换或撤销任何曾经出现在截图、终端或聊天中的令牌。
- [x] Supabase bootstrap 已包含全量建表、RLS 和公开角色权限撤销；仍需在全新项目实际运行验证。
- [x] 使用私人 VPS secrets 的工作流已改为只允许手工启动。
- [ ] 从全新 VPS、全新 Supabase 和全新小米凭据完成一次安装演练。
- [ ] 确认 README、PROJECT_STATUS.md 和本文与代码一致。
- [ ] 明确声明本项目不是医疗设备，经期和健康分析不能用于诊断或治疗。
