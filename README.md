# Binance Market Watch for OpenClaw

简体中文 | [English](#english)

Binance Market Watch 是一个面向 OpenClaw 的独立 Binance skill，用于同时完成两类任务：

- 小时级 `Top 10` 市场巡检
- Binance USDS-M 合约公开市场分析

GitHub 发布版已经把 `binance_usds_futures_advisor` 的合约分析能力内置到 `binance-market-watch` 中，因此一个 skill 就可以覆盖 `scan`、`report`、`alert`、`heartbeat`、`json` 等常用场景。

Binance Market Watch is a standalone OpenClaw skill for two kinds of Binance workflows:

- Hourly `Top 10` market monitoring
- Binance USDS-M public futures analysis

This GitHub release ships both capabilities inside a single `binance-market-watch` skill, including `scan`, `report`, `alert`, `heartbeat`, and `json` workflows.

## 简体中文

### 项目简介

这个仓库提供一个可直接集成到 OpenClaw 的独立 skill，覆盖两条能力线：

1. 小时级市场监控
2. 合约市场分析

它的主要能力包括：

- 每小时扫描 Binance 成交量靠前的 `Top 10` USDT 标的
- 检测 `long_signal`、`short_signal`、`margin_stress`
- 生成 Binance USDS-M 合约 `report / alert / heartbeat / json / report+json`
- 输出适合 Telegram 推送的摘要，以及结构化 JSON 结果
- 维护本地状态，用于冷却去重和借贷基线追踪
- 支持无 API Key 的公开模式运行

### 适用场景

- 想在 OpenClaw 里搭建 Binance 小时级监控
- 想把扫描结果通过 Telegram 定时推送
- 想做 Binance 合约 heartbeat 巡检或 4H 市场快照
- 想输出结构化结果给其他自动化任务继续处理

### 不适用场景

- 这不是交易机器人
- 这不会自动下单
- 这不读取账户私有持仓或余额
- 这不是分钟级或实时 WebSocket 监控系统

### 仓库结构

```text
binance-market-watch-openclaw/
  skills/
    binance-market-watch/
  examples/
    openclaw.json.example
  CHANGELOG.md
  UPDATE-2026-03-19.md
```

### 安装方式

方式一：复制 skill 到 OpenClaw 工作区

```bash
cp -R skills/binance-market-watch ~/.openclaw/workspace/skills/
```

方式二：通过 `skills.load.extraDirs` 加载仓库内的 `skills` 目录

```json5
{
  skills: {
    load: {
      extraDirs: ["~/Projects/binance-market-watch-openclaw/skills"]
    }
  }
}
```

### 快速开始

先检查运行环境：

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py doctor
```

执行一次小时级扫描：

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py scan --format heartbeat --topn 10
```

执行一次合约 heartbeat：

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_usds_futures_advisor.py --format heartbeat --lang zh --period 4h
```

执行一次合约报告：

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_usds_futures_advisor.py --format report --lang zh --period 4h --symbols BTCUSDT,ETHUSDT
```

查看本地状态：

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py state show
```

### Telegram 定时推送

主监控 cron 示例：

```bash
openclaw cron add \
  --name "Binance Market Watch" \
  --cron "5 * * * *" \
  --session isolated \
  --message "Use $binance-market-watch to run an hourly scan in heartbeat mode and only reply when new alerts exist." \
  --announce \
  --channel telegram \
  --to "<telegram-chat-id>"
```

合约 heartbeat cron 示例：

```bash
openclaw cron add \
  --name "Binance Futures Heartbeat" \
  --cron "5 * * * *" \
  --session isolated \
  --message "Use $binance-market-watch to run the built-in USDS-M futures heartbeat workflow and only reply when anomalies or opportunities appear." \
  --announce \
  --channel telegram \
  --to "<telegram-chat-id>"
```

### 可选的 Binance API Key

公开模式无需密钥也能运行。

如果你希望 `margin_stress` 使用私有借贷利率、库存和可借额度数据，需要配置：

- `BINANCE_API_KEY`
- `BINANCE_SECRET_KEY`

合约分析脚本本身只使用公开接口，不要求 API Key。

示例配置见 [examples/openclaw.json.example](./examples/openclaw.json.example)。

### 备注

- 这是监控与研究工具，不是实时交易引擎
- 同一个 skill 同时覆盖小时巡检与合约分析
- 如果运行环境无法访问 Binance，实时扫描会返回网络错误
- 如果网络环境使用自签名证书，可通过 `--ca-bundle` 或 `--allow-insecure-ssl` 处理

## English

### Overview

This repository provides a standalone OpenClaw skill with two built-in workflows:

1. Hourly market monitoring
2. Futures market analysis

Core capabilities include:

- Hourly scans of the top `10` high-volume USDT markets on Binance
- Detection of `long_signal`, `short_signal`, and `margin_stress`
- Built-in Binance USDS-M futures `report`, `alert`, `heartbeat`, `json`, and `report+json`
- Telegram-friendly summaries and structured JSON output
- Local state for cooldown suppression and margin baselines
- Public-mode operation without Binance API keys

### Use Cases

- Build an hourly Binance monitoring workflow in OpenClaw
- Send scheduled market alerts to Telegram
- Run Binance futures heartbeat checks or 4H market snapshots
- Feed structured results into downstream automations

### Non-Goals

- This is not a trading bot
- It does not place orders
- It does not read private balances or positions
- It is not a real-time or minute-level WebSocket monitoring system

### Repository Layout

```text
binance-market-watch-openclaw/
  skills/
    binance-market-watch/
  examples/
    openclaw.json.example
  CHANGELOG.md
  UPDATE-2026-03-19.md
```

### Installation

Option 1: copy the skill into your OpenClaw workspace

```bash
cp -R skills/binance-market-watch ~/.openclaw/workspace/skills/
```

Option 2: load the repository `skills` directory through `skills.load.extraDirs`

```json5
{
  skills: {
    load: {
      extraDirs: ["~/Projects/binance-market-watch-openclaw/skills"]
    }
  }
}
```

### Quick Start

Check the runtime first:

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py doctor
```

Run one hourly scan:

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py scan --format heartbeat --topn 10
```

Run one futures heartbeat check:

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_usds_futures_advisor.py --format heartbeat --lang zh --period 4h
```

Run one futures report:

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_usds_futures_advisor.py --format report --lang zh --period 4h --symbols BTCUSDT,ETHUSDT
```

Inspect local state:

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py state show
```

### Telegram Delivery

Primary monitoring cron example:

```bash
openclaw cron add \
  --name "Binance Market Watch" \
  --cron "5 * * * *" \
  --session isolated \
  --message "Use $binance-market-watch to run an hourly scan in heartbeat mode and only reply when new alerts exist." \
  --announce \
  --channel telegram \
  --to "<telegram-chat-id>"
```

Futures heartbeat cron example:

```bash
openclaw cron add \
  --name "Binance Futures Heartbeat" \
  --cron "5 * * * *" \
  --session isolated \
  --message "Use $binance-market-watch to run the built-in USDS-M futures heartbeat workflow and only reply when anomalies or opportunities appear." \
  --announce \
  --channel telegram \
  --to "<telegram-chat-id>"
```

### Optional Binance API Keys

Public mode works without keys.

If you want `margin_stress` to use private margin interest, inventory, and borrowable endpoints, configure:

- `BINANCE_API_KEY`
- `BINANCE_SECRET_KEY`

The futures analysis workflow itself uses public endpoints only and does not require API keys.

See [examples/openclaw.json.example](./examples/openclaw.json.example).

### Notes

- This is a monitoring and research skill, not a trading engine
- One skill now covers both hourly scans and futures analysis
- If your environment cannot reach Binance, live scans may return network errors
- If your network uses a self-signed certificate, use `--ca-bundle` or `--allow-insecure-ssl`

## License

MIT
