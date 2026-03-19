---
name: binance-market-watch
description: Unified Binance market watch skill. Covers (1) hourly top-10 cross-market monitoring for long_signal, short_signal, and margin_stress, and (2) Binance USDS-M public futures analysis for report, alert, heartbeat, and JSON snapshots. Use when the user asks for Binance monitoring, hourly scans, Telegram alerts, top-volume coin checks, futures heartbeat, funding/premium/open-interest analysis, Top 合约快照, or market watch status.
user-invocable: true
metadata: {"openclaw":{"emoji":"📈","requires":{"bins":["python3"]},"primaryEnv":"BINANCE_API_KEY"}}
---

# Binance Market Watch

统一后的 `binance-market-watch` 同时覆盖两条能力线：

1. `scan`：跨现货 / 合约 / 融资融券的 `Top 10` 小时级巡检，识别 `long_signal`、`short_signal`、`margin_stress`
2. `usds-futures`：内置公开合约分析能力，支持 `report`、`alert`、`heartbeat`、`json`、`report+json`

这是一个独立发布的 skill，新接入、心跳规则和文档都以 `$binance-market-watch` 为唯一入口。

## 适用范围

- 适合：搭建 Binance 小时级监控、生成 Telegram 异动提醒、查看当前监控状态、输出机器可读 JSON、分析 USDS-M 合约市场快照、运行 heartbeat 巡检、查看资金费率/多空比/持仓量/溢价。
- 不适合：账户查询、下单、调杠杆、读取私有持仓、毫秒级盘口交易、WebSocket 高频策略。
- 如果用户要求自动交易或账户级能力，不要在本 skill 内临时扩展；应明确说明这里只做公开市场分析和监控。

## 固定工作流

1. 先判断需求属于哪条能力线：
   - `scan`：偏 “Top10 小时级监控 / Telegram 异动 / 状态管理”
   - `usds-futures`：偏 “合约分析 / heartbeat / report / 资金费率 / 多空比 / 持仓量”
2. 小时监控或排查环境时，先运行 `doctor`。
3. `scan` 监控任务统一运行 `scan --format heartbeat`；人工复盘时用 `scan --format report --ignore-cooldown`。
4. `usds-futures` 的默认人工分析使用 `--format report`；心跳巡检使用 `--format heartbeat`。
5. 想查看当前冷却和监控状态时，用 `state show`；只清空监控冷却时用 `state reset --scope alerts`。
6. 需要 OpenClaw 定时任务时，优先让 cron/heartbeat 默认指向 `$binance-market-watch`。

## CLI 用法

### A. Top10 小时监控

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

### B. USDS-M 合约分析

完整中文报告：

```bash
python3 {baseDir}/scripts/binance_usds_futures_advisor.py --topn 10 --period 4h --lang zh --format report
```

指定标的并输出快讯：

```bash
python3 {baseDir}/scripts/binance_usds_futures_advisor.py --symbols BTCUSDT,ETHUSDT --period 1h --lang zh --format alert
```

heartbeat 巡检：

```bash
python3 {baseDir}/scripts/binance_usds_futures_advisor.py --period 4h --lang zh --format heartbeat
```

自定义 heartbeat 阈值：

```bash
python3 {baseDir}/scripts/binance_usds_futures_advisor.py --format heartbeat --lang zh --hb-price-move-pct 3.0 --hb-ls-high 1.4 --hb-ls-low 0.75 --hb-premium-pct 0.8
```

输出纯 JSON：

```bash
python3 {baseDir}/scripts/binance_usds_futures_advisor.py --symbols BTCUSDT --period 4h --lang zh --format json
```

完整报告后附 JSON：

```bash
python3 {baseDir}/scripts/binance_usds_futures_advisor.py --topn 10 --period 4h --lang zh --format report+json
```

## 输出模式

### `scan`

- `heartbeat`：默认监控模式。没有新异常时只输出 `HEARTBEAT_OK`；有异常时输出 Telegram 可直接发送的短摘要和指标明细。
- `alert`：始终输出短版扫描结果；适合人工快速浏览。
- `report`：输出完整巡检报告，包含监控列表、命中原因、风险标签、去重情况。
- `json`：结构化结果，适合下游工作流。
- `state show`：查看上次监控列表、冷却状态和借贷历史缓存规模。
- `state reset`：清理冷却或重置全部状态。
- `--workers`：控制并发抓取数量，默认 `4`。

### `usds-futures`

- `report`：完整中文/英文结构化报告，适合直接阅读。
- `alert`：短快讯模式，适合人工快速浏览、cron 或 IM 摘要。
- `heartbeat`：安静巡检模式。默认只检查 `BTCUSDT`，正常时只返回 `HEARTBEAT_OK`，异常时输出超短告警。
- `heartbeat` 既会提醒异常，也会在强方向 setup 出现时提醒交易机会；若存在信号分歧，会降级为观察机会。
- `json`：纯机器可读结构化输出，适合下游 agent、脚本、工作流消费。
- `report+json`：先出完整报告，再附 JSON。
- `--json`：快捷别名，等价于 `--format report+json`。

## 参数说明

### `scan`

- `--topn`：自动筛选数量，默认 `10`。
- `--symbols`：逗号分隔；提供后会覆盖自动 TopN。
- `--cooldown-hours`：同币种同信号的冷却小时数，默认 `6`。
- `--max-alerts`：人类可读输出最多显示多少条告警。
- `--workers`：控制并发抓取数量，默认 `4`。

### `usds-futures`

- `--topn`：自动筛选数量，默认 `10`。
- `--period`：`5m,15m,30m,1h,2h,4h,6h,12h,1d`，默认 `4h`。
- `--symbols`：逗号分隔，如 `BTCUSDT,ETHUSDT`；提供后会覆盖自动 TopN。
- `--format`：`report`、`alert`、`heartbeat`、`json`、`report+json`。
- `--base-url`：默认 `https://fapi.binance.com`。
- `--timeout`：HTTP 超时秒数，默认 `10`。
- `--sleep`：每个标的请求间隔秒数，默认 `0.08`。
- `--lang`：`zh` 或 `en`，默认 `zh`。
- `--hb-price-move-pct`：heartbeat 的 `12h` 波动阈值，默认 `4.0`。
- `--hb-ls-high`：heartbeat 的多空比上阈值，默认 `1.5`。
- `--hb-ls-low`：heartbeat 的多空比下阈值，默认 `0.67`。
- `--hb-premium-pct`：heartbeat 的正溢价阈值，默认 `1.0`。
- `--hb-opportunity-score`：heartbeat 提醒交易机会所需的最小绝对分数，默认 `0.45`。
- `--hb-opportunity-confidence`：heartbeat 提醒交易机会所需的最小置信度，默认 `35`。
- `--hb-disable-opportunity`：关闭交易机会提醒，只保留异常提醒。
- `--hb-max-alerts`：heartbeat 最多输出多少个异常标的，默认 `3`。

## 默认行为

- `scan` 的监控 universe：按 `futures 24h quoteVolume` 选 `Top 10`，并要求同时存在现货交易对。
- `scan` 巡检周期：建议 cron 固定在每小时 `05` 分执行，读取上一根关闭的 `1h` K 线。
- `scan` 告警去重：同币种同信号默认 `6` 小时冷却；方向反转或分数显著升级可突破冷却。
- `scan` 的 Margin 增强模式：若设置 `BINANCE_API_KEY` 和 `BINANCE_SECRET_KEY`，会额外启用借贷利率、库存、可借额度监控；否则只运行公开市场扫描。
- `usds-futures` 仅使用公开接口，不需要 API Key，不访问账户私有数据。
- `usds-futures` 的 heartbeat 默认只检查 `BTCUSDT`。

## 网络兼容

- 默认走系统 SSL 信任链。
- 若环境存在公司代理或自签名证书，可优先使用 `--ca-bundle /path/to/ca.pem`。
- 只有在测试环境里确认风险可接受时，才使用 `--allow-insecure-ssl`。

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
- “监控当前 4H 合约 Top10 并给建议”
- “分析 BTCUSDT,ETHUSDT 合约并给出风险提示”
- “给我一份币安合约市场快照和投资建议”
- “做一条 Binance Futures heartbeat 巡检”

## 参考资料

- `scan` 的指标、阈值、状态文件和输出契约见 `references/implementation.md`
- `usds-futures` 的端点覆盖、字段和回退策略见 `references/usds-futures-endpoints.md`
- `openclaw.json`、cron 和 Telegram 配置示例见 `references/openclaw-setup.md`
