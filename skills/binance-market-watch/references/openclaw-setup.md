# OpenClaw Setup

## 1. 将 skill 放到工作区

推荐目录：

```text
~/.openclaw/workspace/skills/binance-market-watch
```

如果仓库不是直接放在工作区，也可以在 `~/.openclaw/openclaw.json` 里配置：

```json5
{
  skills: {
    load: {
      extraDirs: ["~/Projects/binance-market-watch/skills"]
    }
  }
}
```

## 2. 配置可选的 Binance 密钥

无密钥也能运行公开市场监控。

如果要启用借贷库存/利率监控，在 `~/.openclaw/openclaw.json` 中添加：

```json5
{
  skills: {
    entries: {
      "binance-market-watch": {
        enabled: true,
        env: {
          BINANCE_API_KEY: "your_api_key",
          BINANCE_SECRET_KEY: "your_secret_key"
        }
      }
    }
  }
}
```

不要把真实密钥提交到 GitHub。

## 3. Telegram 推送

推荐用 OpenClaw cron 的 `delivery.channel=telegram` 投递，而不是在脚本内直接发 Telegram。

示例：

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

如果省略 `--channel` 和 `--to`，OpenClaw 会默认使用最近一次聊天路由。

## 4. 本地验证

```bash
python3 {baseDir}/scripts/binance_market_watch.py doctor
python3 {baseDir}/scripts/binance_market_watch.py scan --format heartbeat --topn 10
python3 {baseDir}/scripts/binance_market_watch.py state show
openclaw skills info binance-market-watch
```

如果你的网络对 Binance 证书链做了拦截：

```bash
python3 {baseDir}/scripts/binance_market_watch.py scan --format heartbeat --topn 10 --allow-insecure-ssl
```

更稳妥的方式是提供 CA 证书：

```bash
python3 {baseDir}/scripts/binance_market_watch.py scan --format heartbeat --topn 10 --ca-bundle /path/to/ca.pem
```

## 5. GitHub 开源发布建议

仓库建议结构：

```text
repo-root/
  skills/
    binance-market-watch/
      SKILL.md
      agents/openai.yaml
      scripts/binance_market_watch.py
      references/
```

安装方式：

- 复制 `skills/binance-market-watch` 到 `<workspace>/skills`
- 或让用户把仓库 `skills` 目录加入 `skills.load.extraDirs`

V1 发布说明建议明确写清：

- 这是小时级监控，不是实时交易系统
- 默认无密钥可运行
- `margin_stress` 需要 Binance 密钥才能启用增强模式
