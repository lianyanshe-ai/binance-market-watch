# Binance Market Watch for OpenClaw

简体中文 | [English](#english)

Binance Market Watch 是一个面向 OpenClaw 的小时级市场监控 skill，用于扫描 Binance 高成交量 USDT 标的，识别异常行情，并输出适合 Telegram 或自动化流程消费的结果。

Binance Market Watch is an OpenClaw skill for hourly Binance market monitoring. It scans high-volume USDT markets, detects unusual conditions, and produces output that works well for Telegram delivery or downstream automation.

## 简体中文

### 项目简介

这个仓库提供一个可直接集成到 OpenClaw 的 Binance 监控 skill，适合做小时级巡检和异动提醒。

它的目标很明确：

- 每小时扫描 Binance 成交量靠前的 `Top 10` USDT 标的
- 检测三类异常：`long_signal`、`short_signal`、`margin_stress`
- 输出适合 Telegram 推送的摘要，以及结构化 JSON 结果
- 维护本地状态，用于冷却去重和借贷基线追踪
- 支持无 API Key 的公开模式运行

### 适用场景

- 想在 OpenClaw 里搭建 Binance 小时级监控
- 想把扫描结果通过 Telegram 定时推送
- 想输出结构化结果给其他自动化任务继续处理

### 不适用场景

- 这不是交易机器人
- 这不会自动下单
- 这不是分钟级或实时 WebSocket 监控系统

### 仓库结构

```text
binance-market-watch-openclaw/
  skills/
    binance-market-watch/
  examples/
    openclaw.json.example
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

输出结构化 JSON：

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py scan --format json --topn 10
```

查看本地状态：

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py state show
```

### Telegram 定时推送

推荐使用 OpenClaw cron 进行投递，而不是在脚本里直接发送 Telegram 消息：

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

### 可选的 Binance API Key

公开模式无需密钥也能运行。

如果你希望 `margin_stress` 使用私有借贷利率、库存和可借额度数据，需要配置：

- `BINANCE_API_KEY`
- `BINANCE_SECRET_KEY`

示例配置见 [examples/openclaw.json.example](./examples/openclaw.json.example)。

### 备注

- 这是小时级监控工具，不是实时交易引擎
- `V1` 只负责监控与告警，不执行交易
- 如果运行环境无法访问 Binance，实时扫描会返回 `MONITOR_ERROR`
- 如果网络环境使用自签名证书，可通过 `--ca-bundle` 或 `--allow-insecure-ssl` 处理

## English

### Overview

This repository provides an OpenClaw skill for hourly Binance market monitoring and alerting.

It is designed to:

- Scan the top `10` high-volume USDT markets on Binance every hour
- Detect three anomaly types: `long_signal`, `short_signal`, and `margin_stress`
- Produce Telegram-friendly summaries and structured JSON output
- Keep local state for alert cooldowns and margin baselines
- Run in public mode without Binance API keys

### Use Cases

- Build an hourly Binance monitoring workflow in OpenClaw
- Send scheduled market alerts to Telegram
- Feed structured scan results into other automation steps

### Non-Goals

- This is not a trading bot
- It does not place orders
- It is not a real-time or minute-level WebSocket monitoring system

### Repository Layout

```text
binance-market-watch-openclaw/
  skills/
    binance-market-watch/
  examples/
    openclaw.json.example
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

Output structured JSON:

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py scan --format json --topn 10
```

Inspect local state:

```bash
python3 ~/.openclaw/workspace/skills/binance-market-watch/scripts/binance_market_watch.py state show
```

### Telegram Delivery

Use OpenClaw cron delivery instead of sending Telegram messages directly from the script:

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

### Optional Binance API Keys

Public mode works without keys.

If you want `margin_stress` to use private margin interest, inventory, and borrowable endpoints, configure:

- `BINANCE_API_KEY`
- `BINANCE_SECRET_KEY`

See [examples/openclaw.json.example](./examples/openclaw.json.example).

### Notes

- This is an hourly monitoring tool, not a trading engine
- `V1` focuses on monitoring and alerting only
- If your environment cannot reach Binance, live scans may return `MONITOR_ERROR`
- If your network uses a self-signed certificate, use `--ca-bundle` or `--allow-insecure-ssl`

## License

MIT
