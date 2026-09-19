# 早安我的少年：解锁与位置更新

手机端从零搭建、全部 Block 参数和连线说明见 [`AUTOMATE_FLOW.md`](AUTOMATE_FLOW.md)。

手机 Automate 使用两个独立接口：每次解锁调用 `wake-probe`；只有距上次成功上传位置至少 1 公里时，才调用 `location-update`。VPS 不通过请求 IP 判断位置，也不会主动启动小米运动健康。

触发简报必须同时满足：有一条从未发送过的手表睡眠，并且该睡眠结束后两小时内发生过手机解锁。主睡眠生成晨间简报并包含昨日健康数据；午睡生成午睡简报，不再总结昨日。同一个睡眠编号永久只发送一次，即使之后数据增长也不重发。

`wake-probe` 会启动现有小米云睡眠同步后立即返回，不等待同步完成，因此手机端不会因同步较慢而超时；默认有 720 秒冷却。原有每 15 分钟同步继续运行；任何包含睡眠的同步完成后都会执行同一套待发送检查，因此“先解锁、后上传睡眠”也能触发。

天气由 VPS 使用该睡眠结束时间附近的有效手机位置查询。附近没有记录时使用最近一次有效位置；完全没有位置时仍可生成简报，并明确标记天气位置不可用。坐标入库前保留三位小数。反向地理编码和晨报只保留地级市、区县和国家，不保存街道、门牌号、完整地址或请求公网 IP。

## VPS 配置

新部署请按 [`DEPLOYMENT.md`](../DEPLOYMENT.md) 的统一顺序执行全部迁移；已有部署至少需要确认解锁、双接口和区县字段相关迁移已经执行。

VPS `.env` 配置：

```dotenv
WAKE_PROBE_TOKEN=请生成一个足够长的随机令牌
WAKE_PROBE_DEVICE=primary_phone
WAKE_PROBE_SYNC_COOLDOWN_SECONDS=720
WAKE_UNLOCK_WINDOW_MINUTES=120
WAKE_LOCATION_WINDOW_MINUTES=120
WAKE_TIMEZONE=Asia/Shanghai
```

真实令牌不能写入源码、`.env.example` 或 GitHub。部署仍由 GitHub Actions 完成，不需要手动拉取代码。

## Automate：位置更新

地址：`https://health.example.com/api/location-update`

```json
{
  "latitude": "<LATITUDE_NUMBER>",
  "longitude": "<LONGITUDE_NUMBER>",
  "accuracy": 35,
  "location_time": 1780000000,
  "source": "automate"
}
```

上面两个带引号的值是模板变量；在 Automate 中替换时必须使用 JSON 数字，不要保留引号或尖括号。

## Automate：解锁通知

地址：`https://health.example.com/api/wake-probe`

```json
{
  "event": "unlock",
  "device": "primary_phone",
  "timestamp": 1780000000
}
```

两个请求都使用：

```text
Authorization: Bearer 你的令牌
Content-Type: application/json
```

时间字段是 Unix 秒时间戳；无效或缺失时使用服务器收到请求的时间。

## 命令行验证

位置更新：

```bash
LATITUDE="<LATITUDE_NUMBER>"
LONGITUDE="<LONGITUDE_NUMBER>"
curl --fail-with-body -X POST 'https://health.example.com/api/location-update' \
  -H 'Authorization: Bearer 你的令牌' \
  -H 'Content-Type: application/json' \
  --data "{\"latitude\":${LATITUDE},\"longitude\":${LONGITUDE},\"accuracy\":35,\"location_time\":1780000000,\"source\":\"automate\"}"
```

解锁通知：

```bash
curl --fail-with-body -X POST 'https://health.example.com/api/wake-probe' \
  -H 'Authorization: Bearer 你的令牌' \
  -H 'Content-Type: application/json' \
  --data '{"event":"unlock","device":"primary_phone","timestamp":1780000000}'
```

位置成功返回 HTTP 200 和 `location_updated`。解锁接口成功时固定立即返回 HTTP 202 和 `accepted`；`no_new_sleep`、`sync_cooldown`、`waiting_for_sleep_data`、`report_triggered` 等是 VPS 后台处理状态，可在服务日志中查看，不会由这次 HTTP 请求同步返回。

## 日志确认

```bash
journalctl --user -u xiaomi-health-mcp.service --since today --no-pager \
  | grep -E 'location_update|wake_probe|xiaomi_sleep_sync|sleep_report'
```

定位成功会看到 `location_update received` 和 `location_update completed`。解锁会看到 `wake_probe received`；之后可看到同步开始、完成或冷却跳过。成功发送会看到 `sleep_report candidate_found` 和 `sleep_report report_triggered`。日志不会打印令牌、完整请求头、公网 IP、坐标或精确地址。
