# Automate 完整流程：解锁触发晨报与按距离更新位置

这份文档说明如何在 Android 的 [Automate](https://llamalab.com/automate/) 中，从零搭建 Xiaomi Health Sync 的手机端 Flow。

最终 Flow 使用两个独立的 HTTP 接口：

- 每次成功解锁后调用 `POST /api/wake-probe`，通知 VPS 检查睡眠和晨报；
- 每次解锁后获取一次定位，但只有相对上次成功上传的位置移动至少 1 公里，才调用 `POST /api/location-update`。

手机只负责报告“解锁”和“位置”。是否需要同步小米、是否有新睡眠、是否已经发过晨报，都由 VPS 判断。

> 本文所有域名、令牌和坐标名称都是占位符。不要把真实令牌、经纬度或导出的 `.flo` 文件提交到 GitHub。

## 1. 开始前准备

先确认 VPS 已经能够访问：

```text
https://你的域名/api/wake-probe
https://你的域名/api/location-update
```

VPS `.env` 至少需要：

```dotenv
WAKE_PROBE_TOKEN=一个足够长的随机令牌
WAKE_PROBE_DEVICE=primary_phone
```

手机 Flow 中的令牌和设备名必须与 VPS 完全一致。

### Android 和 Automate 权限

为 Automate 开启：

- 精确位置；
- 后台位置信息；
- 网络访问；
- 显示通知；
- 忽略电池优化；
- 小米系统中的“自启动”；
- 小米系统中的“后台无限制”，并建议在最近任务中锁定 Automate。

这个 Flow 不需要“管理所有文件”。除非其他 Flow 使用文件功能，否则不要为了本流程额外授予该权限。

在 Automate 设置中启用随系统启动恢复 Flow。只启动一个实例；重复启动会产生多个 fiber，导致一次解锁发送多次请求。

## 2. 变量表

| 变量 | 用途 | 初始值或来源 |
| --- | --- | --- |
| `base_url` | VPS 的 HTTPS 根地址 | `"https://你的域名"` |
| `wake_token` | 两个接口共用的 Bearer 令牌 | VPS 的 `WAKE_PROBE_TOKEN` |
| `device_name` | 手机标识 | `"primary_phone"` |
| `last_lat` | 上一次成功上传的纬度 | `null` |
| `last_lon` | 上一次成功上传的经度 | `null` |
| `lat` | 当前定位纬度 | `Location get` 输出 |
| `lon` | 当前定位经度 | `Location get` 输出 |
| `accuracy` | 当前定位精度，单位米 | `Location get` 输出 |
| `location_time` | 定位产生时间，Unix 秒 | `Location get` 输出 |
| `wake_status` | 解锁请求 HTTP 状态码 | 第一个 `HTTP request` 输出 |
| `location_status` | 位置请求 HTTP 状态码 | 第二个 `HTTP request` 输出 |
| `failure_type` | 失败类型 | `Failure catch` 输出 |
| `failure_message` | 失败原因 | `Failure catch` 输出 |

`last_lat` 和 `last_lon` 只存在于当前运行的 fiber。Flow 或手机重启后它们会重新变为 `null`，所以下一次解锁会上传一次位置，这是预期行为。

## 3. 完整连线图

```text
[1 Flow beginning]
        |
[2 base_url = "https://你的域名"]
        |
[3 wake_token = "你的令牌"]
        |
[4 device_name = "primary_phone"]
        |
[5 last_lat = null]
        |
[6 last_lon = null]
        |
[7 Failure catch] --FAIL--> [19 Delay 30s] --OK--> [7 Failure catch]
        |
       OK
        v
[8 Device unlocked?]
   NO --------------------------------> [19 Delay 30s]
   YES
        v
[9 HTTP request: wake-probe]
        |
[10 Log append: wake status]
        |
[11 Expression true: wake_status 是 2xx]
   NO --------------------------------> [19 Delay 30s]
   YES
        v
[12 Location get]
        |
[13 Expression true: 首次定位或移动 >= 1000m]
   NO -------------------------------> [8 Device unlocked?]
   YES
        v
[14 HTTP request: location-update]
        |
[15 Log append: location status]
        |
[16 Expression true: location_status 是 2xx]
   NO --------------------------------> [19 Delay 30s]
   YES
        v
[17 last_lat = lat]
        |
[18 last_lon = lon]
        |
        +----------------------------> [8 Device unlocked?]
```

上图中的 `base_url`、`wake_token` 和 `device_name` 是公开模板为了方便部署而保留的配置 Block。你当前手机截图从 `last_lat`、`last_lon` 开始，说明前三项可能已经直接写在 HTTP Block 或其他变量中；初始化方式可以不同，后面的状态判断和回环逻辑应与上图一致。

## 4. 逐个创建 Block

下面的编号只是本文编号，不要求与你手机里自动生成的 Block ID 一致。

### Block 1：Flow beginning

类别：`Flow` → `Flow beginning`

- Title：`Xiaomi Health morning trigger`
- Flow 只能手工启动一个实例。
- `OK` 连接 Block 2。

### Block 2：Variable set — base_url

类别：`General` → `Variable set`

- Variable：`base_url`
- Value：切换到表达式模式，填写 `"https://你的域名"`
- 末尾不要带 `/`。
- `OK` 连接 Block 3。

### Block 3：Variable set — wake_token

- Variable：`wake_token`
- Value：表达式模式，填写 `"替换为 VPS 的 WAKE_PROBE_TOKEN"`
- `OK` 连接 Block 4。

令牌会保存在 Flow 中，导出或分享 `.flo` 文件前必须删掉真实值。

### Block 4：Variable set — device_name

- Variable：`device_name`
- Value：表达式模式，填写 `"primary_phone"`
- `OK` 连接 Block 5。

如果 VPS 使用其他 `WAKE_PROBE_DEVICE`，这里必须填写相同的值。

### Block 5：Variable set — last_lat

- Variable：`last_lat`
- Value：`null`
- `OK` 连接 Block 6。

### Block 6：Variable set — last_lon

- Variable：`last_lon`
- Value：`null`
- `OK` 连接 Block 7。

### Block 7：Failure catch

类别：`Flow` → `Failure catch`

- Retry limit：`1`
- Failure type 输出变量：`failure_type`
- Failure message 输出变量：`failure_message`
- `OK` 连接 Block 8。
- `FAIL` 连接 Block 19。

这个 Block 捕获后续网络或定位 Block 抛出的异常，防止整个 Flow 因一次 `SocketException` 停止。HTTP 400、401、500 等响应本身不是 Automate 异常，它们会作为状态码正常输出。

### Block 8：Device unlocked?

类别：`Interface` → `Device unlocked`

- Proceed：`When changed`
- `YES` 连接 Block 9。
- `NO` 连接 Block 19。

必须使用 `When changed`，这样 fiber 会等待锁屏状态变化，而不是在手机已经解锁时不断循环请求。`NO` 先经过 30 秒等待再回到监听入口，与当前实际 Flow 一致，也可避免系统状态异常时快速循环。

### Block 9：HTTP request — wake-probe

类别：`Connectivity` → `HTTP request`

- Request URL，表达式模式：`base_url ++ "/api/wake-probe"`
- Request method：`POST`
- Request content type：`application/json`
- Request content body，表达式模式：

```text
jsonEncode({
  "event": "unlock",
  "device": device_name,
  "timestamp": Now
})
```

- Request headers，表达式模式：

```text
{"Authorization": "Bearer " ++ wake_token}
```

- Timeout：`15` 秒
- Save response：`Don't save`
- Response status code：`wake_status`
- Response content、Response headers：留空
- `OK` 连接 Block 10。

正常状态码是 `202`。它表示 VPS 已接受事件并在后台处理，不代表晨报已经生成。不要等待 `report_id`，也不要因此重复请求。

### Block 10：Log append — wake-probe 状态

类别：`Flow` → `Log append`

- Message，表达式模式：`"wake-probe: status=" ++ wake_status`
- `OK` 连接 Block 11。

日志只记录状态码，不要记录令牌、Authorization 请求头或完整正文。

### Block 11：Expression true — 解锁请求是否成功

类别：`General` → `Expression true`

- Expression：`wake_status >= 200 && wake_status < 300`
- `YES` 连接 Block 12。
- `NO` 连接 Block 19。

Automate 的 HTTP Block 在服务器返回 `400`、`401` 或 `500` 时通常仍走 `OK` 出口，因此必须显式检查状态码。只有解锁通知被 VPS 接受后才继续定位；失败时等待 30 秒并回到监听入口。

### Block 12：Location get

类别：`Location` → `Location get`

- Proceed：`Maybe immediately`
- Location provider：`Balanced`
- Maximum fix age：`300` 秒，即 5 分钟
- Minimum distance：留空；本流程由下一个表达式判断 1 公里
- Location fix latitude：`lat`
- Location fix longitude：`lon`
- Location fix accuracy：`accuracy`
- Location fix timestamp：`location_time`
- 其他输出变量：留空
- `OK` 连接 Block 13。

不要在这个 Block 使用 `When changed` 等待 1 公里，否则 fiber 可能长期停在定位 Block，无法继续接收后续解锁事件。

### Block 13：Expression true — 是否需要上传位置

类别：`General` → `Expression true`

- Expression：

```text
last_lat = null || last_lon = null || distance(last_lat, last_lon, lat, lon) >= 1000
```

- `YES` 连接 Block 14。
- `NO` 连接 Block 8。

Automate 的 `distance` 返回近似米数。首次运行时旧坐标是 `null`，所以会上传一次；以后只有移动至少 1000 米才上传。

### Block 14：HTTP request — location-update

类别：`Connectivity` → `HTTP request`

- Request URL，表达式模式：`base_url ++ "/api/location-update"`
- Request method：`POST`
- Request content type：`application/json`
- Request content body，表达式模式：

```text
jsonEncode({
  "latitude": lat,
  "longitude": lon,
  "accuracy": accuracy,
  "location_time": location_time,
  "source": "automate"
})
```

- Request headers，表达式模式：

```text
{"Authorization": "Bearer " ++ wake_token}
```

- Timeout：`15` 秒
- Save response：`Don't save`
- Response status code：`location_status`
- Response content、Response headers：留空
- `OK` 连接 Block 15。

经纬度必须保持为 JSON 数字。使用 `jsonEncode` 可避免手工拼接 JSON 时误加引号或产生非法格式。

### Block 15：Log append — location-update 状态

- Message，表达式模式：`"location-update: status=" ++ location_status`
- `OK` 连接 Block 16。

### Block 16：Expression true — 位置请求是否成功

- Expression：`location_status >= 200 && location_status < 300`
- `YES` 连接 Block 17。
- `NO` 连接 Block 19。

只有服务端确认成功后才能更新 `last_lat` 和 `last_lon`。如果服务器返回错误，保留旧坐标，下一次解锁仍有机会重新上传。

### Block 17：Variable set — 保存成功纬度

- Variable：`last_lat`
- Value：表达式模式 `lat`
- `OK` 连接 Block 18。

### Block 18：Variable set — 保存成功经度

- Variable：`last_lon`
- Value：表达式模式 `lon`
- `OK` 连接 Block 8。

### Block 19：Delay — 失败或非成功状态后等待

类别：`Date & time` → `Delay`

- Duration：`30` 秒
- Wake up：开启
- Proceed：`Inexact` 即可
- `OK` 连接回 Block 7 的 `IN`。

`Failure catch` 的 `FAIL`、`Device unlocked?` 的 `NO`、`wake_status` 非 2xx 和 `location_status` 非 2xx 都进入这个 Block。不要直接接回失败的 HTTP Block。先等待 30 秒可避免网络断开或服务器异常时高速循环；之后回到 Block 7，再进入 Block 8 等待下一次状态变化。

## 5. 两个 HTTP Request 为什么这样连接

两个请求用途不同：

- `wake-probe` 每次解锁都发送，即使位置没有变化，也不影响 VPS 检查睡眠；
- `location-update` 只在首次定位或移动至少 1 公里时发送，减少精确位置传输和地理编码请求。

它们在同一个 fiber 中依次执行。当前 Flow 只有在 `wake_status` 为 2xx 时才继续定位；非 2xx 状态先等待 30 秒，再回到解锁监听。DNS、Socket、TLS、超时等导致 Block 本身失败时，则由 `Failure catch` 进入同一个等待路径。

## 6. 状态码说明

| 接口 | 状态码 | 含义 | 手机端处理 |
| --- | --- | --- | --- |
| `wake-probe` | `202` | 已接受，VPS 后台检查睡眠和同步 | 正常，继续获取位置 |
| `location-update` | `200` | 位置已保存 | 更新 `last_lat`、`last_lon` |
| 两者 | `400` | JSON 或字段不合法 | 检查 Body、数字类型、设备名和 source |
| 两者 | `401` | Bearer 令牌错误或缺失 | 核对手机和 VPS 的令牌 |
| 两者 | `503` | VPS 没有配置令牌 | 修复 VPS `.env` 并重启服务 |
| `location-update` | `500`/`502` | VPS 或地理编码处理失败 | 不更新旧坐标，下次解锁重试 |

HTTP 错误状态通常仍从 `HTTP request` 的 `OK` 出口离开，所以必须读取状态码，不能只看 Block 是否走到 `OK`。

## 7. 第一次测试

1. 打开 Automate Flow 日志。
2. 启动 Flow，确认只有一个运行实例。
3. 锁屏，再正常解锁一次。
4. 日志应出现：

```text
wake-probe: status=202
location-update: status=200
```

5. 不移动手机，再锁屏和解锁。应再次看到 `wake-probe: status=202`，但不应再次出现位置请求日志。
6. 在安全情况下移动超过 1 公里后再次解锁，应重新看到 `location-update: status=200`。
7. VPS 上检查：

```bash
journalctl --user -u mi-health-link-mcp.service --since today --no-pager \
  | grep -E 'location_update|wake_probe|xiaomi_sleep_sync|sleep_report'
```

## 8. 常见问题

### 解锁后完全没有日志

- 确认 Flow 正在运行且只有一个实例；
- 检查 `Device unlocked?` 是否为 `When changed`；
- 确认 NO 出口连接到 Block 19，Block 19 再连接回 Block 7；
- 检查 Automate 是否被小米后台策略停止；
- 开启自启动、后台无限制和通知权限。

### 出现 `SocketException: Socket closed`

- 检查手机网络和 VPS 域名；
- 关闭 Android 对 Automate 的“数据节省”限制；
- 确认 Automate 拥有完整网络访问权限；
- 保留 `Failure catch → Delay 30s → Failure catch` 路径，避免一次异常永久停止 Flow。

### 每次解锁都上传位置

- Block 16 的 YES 必须依次连接 Block 17、18；
- Block 17/18 保存的是 `lat` 和 `lon`；
- 距离表达式必须使用 `distance(last_lat, last_lon, lat, lon)`；
- Flow 或手机刚重启后的第一次上传属于正常行为。

### HTTP 401

手机中的 `wake_token` 与 VPS 的 `WAKE_PROBE_TOKEN` 不一致。Bearer 前缀必须是“Bearer + 一个空格 + 令牌”。不要把令牌写进日志或截图。

### HTTP 400

检查：

- 解锁请求必须是 `event="unlock"`；
- `device` 必须与 VPS 配置完全相同；
- 位置请求必须是 `source="automate"`；
- 经纬度、精度和时间戳必须是数字；
- 使用 `jsonEncode`，不要手工拼接 JSON。

### 返回 202，但没有立即收到晨报

`202` 只表示事件已进入 VPS 后台处理。没有未发送的新睡眠、睡眠仍未同步、处于同步冷却期、解锁时间不在睡后窗口内，都会导致不发送晨报。手机不应重复请求或等待接口返回晨报内容。

## 9. 隐私提醒

- Flow 中保存了 Bearer 令牌，导出的 `.flo` 文件等同于敏感文件；
- 不要在日志中输出请求头、令牌、经纬度或完整响应；
- 原始坐标会从手机发送到 VPS；VPS 可能进一步调用 Nominatim 和 Open-Meteo，具体边界见 [`PRIVACY.md`](../PRIVACY.md)；
- 1 公里限制只减少上传频率，不代表坐标经过匿名化；
- 停止使用时，应停止 Flow 并撤销或更换 `WAKE_PROBE_TOKEN`。

## 10. 官方参考

- [Device unlocked](https://llamalab.com/automate/doc/block/device_unlocked.html)
- [Location get](https://llamalab.com/automate/doc/block/location_get.html)
- [HTTP request](https://llamalab.com/automate/doc/block/http_request.html)
- [Failure catch](https://llamalab.com/automate/doc/block/failure_catch.html)
- [distance 函数](https://llamalab.com/automate/doc/function/distance.html)
- [内置变量 Now](https://llamalab.com/automate/doc/variable.html)
- [jsonEncode](https://llamalab.com/automate/doc/function/json_encode.html)
