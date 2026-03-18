---
name: binance-market-watch
description: Monitor Binance top-10 high-volume USDT markets hourly and alert for long_signal, short_signal, and margin_stress with Telegram-ready summaries and metrics details. Use when the user asks to set up Binance monitoring, hourly scans, Telegram alerts, top-volume coin checks, or market watch status.
user-invocable: true
metadata: {"openclaw":{"emoji":"📈","requires":{"bins":["python3"]},"primaryEnv":"BINANCE_API_KEY"}}
---

# Binance Market Watch

用 Binance `spot`、`margin-trading`、`derivatives-trading-usds-futures` 三组接口做每小时巡检。

V1 只做监控，不做交易执行。固定目标是：

- 每小时扫描一次
- 动态选择成交量最大的 `Top 10` USDT 币种
- 识别 `long_signal`、`short_signal`、`margin_stress`
- 适合通过 OpenClaw cron 推送到 Telegram

## 适用范围

- 适合：搭建 Binance 小时级行情监控、生成 Telegram 异动提醒、查看当前监控状态、输出机器可读 JSON。
- 不适合：账户查询、下单、调杠杆、毫秒级盘口交易、WebSocket 高频监控。
- 若用户要求自动交易或分钟级高频推送，不要在本 skill 内临时扩展；应明确说明这是 V1 监控 skill。

## 固定工作流

1. 先运行 `doctor` 检查 Python、状态目录、Binance 密钥模式。
2. 监控任务统一运行 `scan --format heartbeat`。
3. 手动排查或审阅结果时，用 `scan --format report --ignore-cooldown`，必要时结合 `--symbols BTCUSDT,ETHUSDT`。
4. 想查看当前冷却和监控状态时，用 `state show`。
5. 若想重置冷却而保留借贷历史基线，用 `state reset --scope alerts`。
6. 需要 OpenClaw 定时任务时，使用 `cron add` 创建隔离任务，并把结果投递到 Telegram。

## CLI 用法

环境检查：

```bash
python3 {baseDir}/scripts/binance_market_watch.py doctor
```

如果你的网络对 Binance 做了自签名代理，可临时放宽 SSL 验证：

```bash
python3 {baseDir}/scripts/binance_market_watch.py doctor --allow-insecure-ssl
```

小时巡检，适合 cron：

```bash
python3 {baseDir}/scripts/binance_market_watch.py scan --format heartbeat --topn 10
```

如需调试请求耗时：

```bash
python3 {baseDir}/scripts/binance_market_watch.py scan --format json --topn 3 --debug-timings
```

如果需要指定 CA 证书：

```bash
python3 {baseDir}/scripts/binance_market_watch.py scan --format heartbeat --topn 10 --ca-bundle /path/to/ca.pem
```

只检查指定标的：

```bash
python3 {baseDir}/scripts/binance_market_watch.py scan --format report --symbols BTCUSDT,ETHUSDT --ignore-cooldown
```

人工查看完整报告：

```bash
python3 {baseDir}/scripts/binance_market_watch.py scan --format report --topn 10 --ignore-cooldown
```

输出机器可读 JSON：

```bash
python3 {baseDir}/scripts/binance_market_watch.py scan --format json --topn 10
```

自定义状态文件：

```bash
python3 {baseDir}/scripts/binance_market_watch.py scan --state-path /tmp/binance-market-watch-state.json
```

查看状态：

```bash
python3 {baseDir}/scripts/binance_market_watch.py state show
python3 {baseDir}/scripts/binance_market_watch.py state show --format json
```

重置冷却：

```bash
python3 {baseDir}/scripts/binance_market_watch.py state reset --scope alerts
```

## 输出模式

- `heartbeat`：默认监控模式。没有新异常时只输出 `HEARTBEAT_OK`；有异常时输出 Telegram 可直接发送的短摘要和指标明细。
- `alert`：始终输出短版扫描结果；适合人工快速浏览。
- `report`：输出完整巡检报告，包含监控列表、命中原因、风险标签、去重情况。
- `json`：结构化结果，适合下游工作流。
- `state show`：查看上次监控列表、冷却状态和借贷历史缓存规模。
- `state reset`：清理冷却或重置全部状态。
- `--workers`：控制并发抓取数量，默认 `4`。

## 网络兼容

- 默认走系统 SSL 信任链。
- 若环境存在公司代理或自签名证书，可优先使用 `--ca-bundle /path/to/ca.pem`。
- 只有在测试环境里确认风险可接受时，才使用 `--allow-insecure-ssl`。

## 默认行为

- 监控 universe：按 `futures 24h quoteVolume` 选 `Top 10`，并要求同时存在现货交易对。
- 巡检周期：建议 cron 固定在每小时 `05` 分执行，读取上一根关闭的 `1h` K 线。
- 告警去重：同币种同信号默认 `6` 小时冷却；方向反转或分数显著升级可突破冷却。
- Margin 增强模式：若设置 `BINANCE_API_KEY` 和 `BINANCE_SECRET_KEY`，会额外启用借贷利率、库存、可借额度监控；否则只运行公开市场扫描。

## OpenClaw 集成

放置位置任选其一：

1. `~/.openclaw/workspace/skills/binance-market-watch`
2. 任意目录后通过 `skills.load.extraDirs` 载入

检查 skill 是否可见：

```bash
openclaw skills info binance-market-watch
openclaw skills check
```

常见请求示例：

- “用 Binance Market Watch 检查一下当前小时级异常”
- “帮我创建一个每小时跑一次的 Binance Telegram 监控”
- “查看这个 Binance 监控 skill 的 doctor 输出”
- “给我当前 top 10 币种的 JSON 监控结果”
- “只看 BTCUSDT 和 ETHUSDT 当前有没有异常”
- “查看这个监控 skill 的状态”
- “清空 Binance 监控的冷却”

## 参考资料

- 指标、阈值、状态文件和输出契约见 `references/implementation.md`
- `openclaw.json`、cron 和 Telegram 配置示例见 `references/openclaw-setup.md`
