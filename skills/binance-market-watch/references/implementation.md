# Binance Market Watch V1

本文件只描述 `binance_market_watch.py scan` 这条小时监控工作流。

已并入的 USDS-M 合约分析能力见 `references/usds-futures-endpoints.md`。

## 目标

V1 固定做三件事：

1. 每小时扫描一次 Binance `Top 10` 高成交量 USDT 币种
2. 检测 `long_signal`、`short_signal`、`margin_stress`
3. 输出适合 Telegram 的简洁摘要和指标明细

附加的运维命令：

- `state show`：查看当前状态和冷却
- `state reset --scope alerts`：只清理冷却
- `state reset --scope all`：清理全部状态

## 数据来源

### Spot

- `/api/v3/exchangeInfo`
- `/api/v3/klines`
- `/api/v3/ticker/bookTicker`

用途：

- 过滤必须同时有现货交易对的标的
- 计算现货 `1h` 涨跌幅
- 计算当前 spread，作为流动性健康度指标

### USDS Futures

- `/fapi/v1/ticker/24hr`
- `/fapi/v1/klines`
- `/futures/data/openInterestHist`
- `/futures/data/takerlongshortRatio`
- `/futures/data/globalLongShortAccountRatio`
- `/fapi/v1/fundingRate`
- `/fapi/v1/premiumIndex`

用途：

- 选取 `Top 10`
- 计算 `1h` 价格动量和量比
- 识别增仓、主动买卖偏向、资金费率和溢价

### Margin

公开：

- `/sapi/v1/margin/restricted-asset`
- `/sapi/v1/margin/delist-schedule`

增强模式，需要 `BINANCE_API_KEY` + `BINANCE_SECRET_KEY`：

- `/sapi/v1/margin/next-hourly-interest-rate`
- `/sapi/v1/margin/available-inventory`
- `/sapi/v1/margin/maxBorrowable`

用途：

- 识别借贷利率跳升、可借库存骤降、可借额度收缩
- 给 `margin_stress` 加权

## Universe 选择

默认规则：

1. 取 `futures 24h quoteVolume` 最大的 USDT 标的
2. 必须同时存在现货交易对
3. 使用轻量滞后，上一轮在榜且仍在前 15 的币优先保留

如果调用 `scan --symbols BTCUSDT,ETHUSDT`，则跳过自动选币，按给定列表执行。

## 三类异常

### `long_signal`

建议阈值：

- `futures_ret_1h >= 2.0%`
- `spot_ret_1h >= 1.5%`
- `futures_volume_ratio >= 1.8`
- `open_interest_change_1h >= 1.5%`
- `taker_buy_sell_ratio >= 1.12`

评分说明：

- 只有全部核心条件同时满足时才触发
- 一旦触发，先给基础分，再根据强度加分，避免出现“已触发但分数过低”的问题

### `short_signal`

建议阈值：

- `futures_ret_1h <= -2.0%`
- `spot_ret_1h <= -1.5%`
- `futures_volume_ratio >= 1.8`
- `open_interest_change_1h >= 1.5%`
- `taker_buy_sell_ratio <= 0.89`

### `margin_stress`

增强模式下计算：

- `next_hourly_interest_rate` 相对历史基线跳升 `50%+`
- `available_inventory` 相对历史基线下降 `40%+`
- `max_borrowable` 相对历史基线下降 `30%+`

触发规则：

- 至少命中两个中等异常，或
- 命中一个严重异常

严重异常建议阈值：

- 利率跳升 `100%+`
- 库存下降 `60%+`
- 可借额度下降 `50%+`

## 状态文件

默认写入 `{baseDir}/.cache/state.json`。

关键字段：

- `top_symbols`
- `alerts`
- `margin_history`
- `updated_at`

`alerts` 用于去重，`margin_history` 用于计算借贷利率、库存、可借额度的历史中位数基线。

### 去重规则

- 同币种同信号默认 `6` 小时冷却
- 如果分数比上次高出 `10+`，可突破冷却
- `long_signal` 与 `short_signal` 分别记账，方向反转不共享冷却

## 输出契约

### `heartbeat`

无新信号：

```text
HEARTBEAT_OK
```

有新信号时，每条告警包含：

1. 一行简洁摘要
2. 一行原因
3. 一到三行指标明细

### `json`

根字段固定包含：

- `scan_time`
- `mode`
- `margin_mode`
- `top_symbols`
- `alerts`
- `suppressed_alerts`
- `errors`

## 风险标签

可附加：

- `crowded_funding`
- `premium_stretched`
- `wide_spread`
- `cross_market_divergence`
- `margin_public_only`
- `restricted_asset`
- `delist_watch`

说明：

- `margin_public_only` 仅表示当前运行在公开模式下，默认不会出现在面向 Telegram 的人类可读风险标签中。

## 性能策略

- 使用 `spot bookTicker all` 代替 `exchangeInfo` 作为现货可交易 universe 来源
- 使用批量 `premiumIndex` 和 `bookTicker` 结果，避免逐币重复请求
- 默认启用受控并发，`scan --workers 4`
- `heartbeat` 模式默认跳过非必要的附加指标，只保留小时级信号判断所需数据
- 用 `--debug-timings` 输出每个阶段耗时，便于排查慢请求
