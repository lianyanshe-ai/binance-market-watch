# Binance Market Watch Futures Endpoints

对应脚本：`scripts/binance_usds_futures_advisor.py`

本 skill 仅使用公开接口（无需 API Key），覆盖如下能力。

## 0) 能力边界

- 不读取账户余额、持仓、委托或成交。
- 不调用任何下单、改单、撤单或杠杆设置接口。
- 用户如果要求认证能力，应切换到独立的带认证 skill，而不是给本 skill 补密钥。

## 1) 标的筛选

- REST: `GET /fapi/v1/exchangeInfo`
  - 过滤条件：`quoteAsset=USDT`、`contractType=PERPETUAL`、`status=TRADING`
- REST: `GET /fapi/v1/ticker/24hr`
  - 字段：`quoteVolume`
  - 用途：对可交易 USDT 永续按成交额排序并选 TopN

## 2) 核心市场指标

- REST: `GET /fapi/v1/klines`
  - 参数：`symbol`, `interval`, `limit`
  - 用途：提取周期趋势与波动率
- REST: `GET /fapi/v1/premiumIndex`
  - 参数：`symbol`
  - 字段：`markPrice`, `indexPrice`
  - 用途：计算溢价率
- REST: `GET /fapi/v1/fundingRate`
  - 参数：`symbol`, `limit`
  - 字段：`fundingRate`
  - 用途：计算拥挤度

## 3) 合约结构指标

- REST: `GET /futures/data/openInterestHist`
  - 参数：`symbol`, `period`, `limit`
  - 字段：`sumOpenInterest`
  - 用途：计算持仓量变化
- REST: `GET /futures/data/globalLongShortAccountRatio`
  - 参数：`symbol`, `period`, `limit`
  - 字段：`longShortRatio`
  - 用途：全市场多空偏向
- REST: `GET /futures/data/topLongShortPositionRatio`
  - 参数：`symbol`, `period`, `limit`
  - 字段：`longShortRatio`
  - 用途：大户持仓多空偏向
- REST: `GET /futures/data/topLongShortAccountRatio`
  - 参数：`symbol`, `period`, `limit`
  - 字段：`longShortRatio`
  - 用途：大户账户多空偏向
- REST: `GET /futures/data/takerlongshortRatio`
  - 参数：`symbol`, `period`, `limit`
  - 字段：`buySellRatio`
  - 用途：主动买卖方向

## 4) 评分维度

- 趋势：最近 `kline` 周期涨跌幅
- 拥挤度：资金费率 + 溢价率
- 持仓结构：持仓量变化与价格方向是否共振
- 情绪偏向：全市场多空比、大户多空比
- 主动流向：主动买卖比
- 可靠度：数据完整度 `data_completeness`

## 5) 输出模式

- `report`：完整结构化报告
- `alert`：短快讯模式
- `heartbeat`：默认仅检查 `BTCUSDT`，未命中时返回 `HEARTBEAT_OK`，阈值可由 `--hb-*` 参数覆盖
- `heartbeat` 还会在强方向、低风险 setup 出现时提醒交易机会
- `json`：纯机器可读结果
- `report+json`：完整报告后附 JSON

## 6) SDK / REST 回退策略

脚本内部使用三层回退：

1. 新命名空间：`binance.derivatives_trading_usds_futures`（如可用）
2. 旧命名空间：`binance.um_futures.UMFutures`
3. 直接 REST 请求

即使本地完全没有安装 Binance SDK，只要公网可访问，公开数据分析仍可工作。

## 7) 周期约束

支持：`5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d`  
默认：`4h`
