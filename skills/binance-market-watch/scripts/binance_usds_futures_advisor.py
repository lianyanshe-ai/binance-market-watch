#!/usr/bin/env python3
"""Binance USDS-M futures monitor with conservative advice output.

This script uses only public endpoints. It prefers the newer
`derivatives_trading_usds_futures` namespace when available and falls back
to `binance.um_futures` from `binance-futures-connector`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

SUPPORTED_PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
SUPPORTED_FORMATS = {"report", "alert", "heartbeat", "json", "report+json"}
DEFAULT_BASE_URL = "https://fapi.binance.com"
DEFAULT_PERIOD = "4h"
HEARTBEAT_DEFAULT_SYMBOLS = ["BTCUSDT"]
HEARTBEAT_PRICE_MOVE_THRESHOLD_PCT = 4.0
HEARTBEAT_LS_HIGH = 1.5
HEARTBEAT_LS_LOW = 0.67
HEARTBEAT_PREMIUM_THRESHOLD_PCT = 1.0
HEARTBEAT_OPPORTUNITY_MIN_SCORE = 0.45
HEARTBEAT_OPPORTUNITY_MIN_CONFIDENCE = 35
FALLBACK_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "TRXUSDT",
    "LTCUSDT",
    "LINKUSDT",
]

def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    return a / b


def _avg(values: Iterable[Optional[float]]) -> Optional[float]:
    seq = [x for x in values if x is not None]
    if not seq:
        return None
    return sum(seq) / len(seq)


class PublicFuturesClient:
    """Public Binance futures data client with SDK + REST fallback."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client, self.sdk_init_errors = self._init_sdk_client(base_url)

    @staticmethod
    def _init_sdk_client(base_url: str) -> Tuple[Optional[Any], List[str]]:
        errors: List[str] = []

        # Newer namespace (if available).
        try:
            from binance.derivatives_trading_usds_futures import Market  # type: ignore

            return Market(base_url=base_url), errors
        except Exception as exc:  # pragma: no cover - environment dependent
            errors.append(f"new-namespace-import-failed: {exc}")

        try:
            from binance.derivatives_trading_usds_futures.market import Market  # type: ignore

            return Market(base_url=base_url), errors
        except Exception as exc:  # pragma: no cover - environment dependent
            errors.append(f"new-market-import-failed: {exc}")

        # Legacy connector namespace.
        try:
            from binance.um_futures import UMFutures  # type: ignore

            return UMFutures(base_url=base_url), errors
        except Exception as exc:  # pragma: no cover - environment dependent
            errors.append(f"um-futures-import-failed: {exc}")

        return None, errors

    def _find_method(self, names: Sequence[str]) -> Optional[Callable[..., Any]]:
        if self.client is None:
            return None
        for name in names:
            fn = getattr(self.client, name, None)
            if callable(fn):
                return fn
        return None

    def _rest_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        req = urllib.request.Request(url=url, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)

    def exchange_info(self) -> Any:
        fn = self._find_method(["exchange_info"])
        if fn:
            try:
                return fn()
            except Exception:
                pass
        return self._rest_get("/fapi/v1/exchangeInfo")

    def ticker_24hr_price_change(self, symbol: Optional[str] = None) -> Any:
        fn = self._find_method(["ticker_24hr_price_change", "ticker_24h", "ticker_24hr"])
        if fn:
            try:
                if symbol:
                    return fn(symbol=symbol)
                return fn()
            except Exception:
                pass
        return self._rest_get("/fapi/v1/ticker/24hr", {"symbol": symbol})

    def klines(self, symbol: str, interval: str, limit: int = 5) -> Any:
        fn = self._find_method(["klines", "kline"])
        if fn:
            try:
                return fn(symbol=symbol, interval=interval, limit=limit)
            except TypeError:
                try:
                    return fn(symbol=symbol, period=interval, limit=limit)
                except Exception:
                    pass
            except Exception:
                pass
        return self._rest_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    def mark_price(self, symbol: str) -> Any:
        fn = self._find_method(["mark_price", "premium_index"])
        if fn:
            try:
                return fn(symbol=symbol)
            except Exception:
                pass
        return self._rest_get("/fapi/v1/premiumIndex", {"symbol": symbol})

    def funding_rate(self, symbol: str, limit: int = 3) -> Any:
        fn = self._find_method(["funding_rate", "funding_rate_history"])
        if fn:
            try:
                return fn(symbol=symbol, limit=limit)
            except Exception:
                pass
        return self._rest_get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})

    def open_interest_hist(self, symbol: str, period: str, limit: int = 3) -> Any:
        fn = self._find_method(["open_interest_hist", "open_interest_history"])
        if fn:
            try:
                return fn(symbol=symbol, period=period, limit=limit)
            except Exception:
                pass
        return self._rest_get(
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def long_short_account_ratio(self, symbol: str, period: str, limit: int = 3) -> Any:
        fn = self._find_method(
            [
                "long_short_account_ratio",
                "global_long_short_account_ratio",
                "global_long_short_account_ratio_hist",
            ]
        )
        if fn:
            try:
                return fn(symbol=symbol, period=period, limit=limit)
            except Exception:
                pass
        return self._rest_get(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def top_long_short_position_ratio(self, symbol: str, period: str, limit: int = 3) -> Any:
        fn = self._find_method(
            ["top_long_short_position_ratio", "top_trader_long_short_position_ratio"]
        )
        if fn:
            try:
                return fn(symbol=symbol, period=period, limit=limit)
            except Exception:
                pass
        return self._rest_get(
            "/futures/data/topLongShortPositionRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def top_long_short_account_ratio(self, symbol: str, period: str, limit: int = 3) -> Any:
        fn = self._find_method(
            ["top_long_short_account_ratio", "top_trader_long_short_account_ratio"]
        )
        if fn:
            try:
                return fn(symbol=symbol, period=period, limit=limit)
            except Exception:
                pass
        return self._rest_get(
            "/futures/data/topLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def taker_long_short_ratio(self, symbol: str, period: str, limit: int = 3) -> Any:
        fn = self._find_method(["taker_long_short_ratio", "taker_buy_sell_vol"])
        if fn:
            try:
                return fn(symbol=symbol, period=period, limit=limit)
            except Exception:
                pass
        return self._rest_get(
            "/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )


@dataclass
class SymbolMetrics:
    symbol: str
    mark_price: Optional[float] = None
    index_price: Optional[float] = None
    premium_pct: Optional[float] = None
    funding_rate: Optional[float] = None
    oi_change_pct: Optional[float] = None
    global_long_short: Optional[float] = None
    top_position_ratio: Optional[float] = None
    top_account_ratio: Optional[float] = None
    taker_ratio: Optional[float] = None
    trend_return_pct: Optional[float] = None
    volatility_pct: Optional[float] = None
    score: float = 0.0
    signal: str = "中性"
    confidence: int = 0
    risk_tags: List[str] = field(default_factory=list)
    advice: str = ""
    errors: List[str] = field(default_factory=list)

    def data_completeness(self) -> float:
        fields = [
            self.premium_pct,
            self.funding_rate,
            self.oi_change_pct,
            self.global_long_short,
            self.taker_ratio,
            self.trend_return_pct,
        ]
        valid = sum(1 for x in fields if x is not None)
        return valid / len(fields)


@dataclass
class HeartbeatAlert:
    symbol: str
    triggers: List[str]
    advice: str
    signal: str
    confidence: int
    has_anomaly: bool = False
    has_opportunity: bool = False
    price_move_12h_pct: Optional[float] = None
    premium_pct: Optional[float] = None
    global_long_short: Optional[float] = None
    data_issue: bool = False


@dataclass(frozen=True)
class HeartbeatConfig:
    price_move_threshold_pct: float = HEARTBEAT_PRICE_MOVE_THRESHOLD_PCT
    ls_high: float = HEARTBEAT_LS_HIGH
    ls_low: float = HEARTBEAT_LS_LOW
    premium_threshold_pct: float = HEARTBEAT_PREMIUM_THRESHOLD_PCT
    opportunity_min_score: float = HEARTBEAT_OPPORTUNITY_MIN_SCORE
    opportunity_min_confidence: int = HEARTBEAT_OPPORTUNITY_MIN_CONFIDENCE
    enable_opportunity: bool = True
    max_alerts: int = 3


def _extract_latest_ratio(rows: Any, keys: Sequence[str]) -> Optional[float]:
    if not isinstance(rows, list) or not rows:
        return None
    item = rows[-1]
    if not isinstance(item, dict):
        return None
    for key in keys:
        value = _to_float(item.get(key))
        if value is not None:
            return value
    return None


def _extract_oi_change_pct(rows: Any) -> Optional[float]:
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    first = rows[0]
    last = rows[-1]
    if not isinstance(first, dict) or not isinstance(last, dict):
        return None
    a = _to_float(first.get("sumOpenInterest") or first.get("openInterest"))
    b = _to_float(last.get("sumOpenInterest") or last.get("openInterest"))
    ratio = _safe_div((b - a) if (a is not None and b is not None) else None, a)
    if ratio is None:
        return None
    return ratio * 100.0


def _extract_trend(klines: Any) -> Tuple[Optional[float], Optional[float]]:
    if not isinstance(klines, list) or len(klines) < 2:
        return None, None
    closes: List[float] = []
    for row in klines:
        if not isinstance(row, list) or len(row) < 5:
            continue
        close = _to_float(row[4])
        if close is not None and close > 0:
            closes.append(close)
    if len(closes) < 2:
        return None, None
    trend = ((closes[-1] - closes[0]) / closes[0]) * 100.0
    returns: List[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        cur = closes[i]
        returns.append(((cur - prev) / prev) * 100.0)
    vol = statistics.pstdev(returns) if len(returns) >= 2 else abs(returns[0])
    return trend, vol


def _score_trend(trend_return_pct: Optional[float]) -> float:
    if trend_return_pct is None:
        return 0.0
    if trend_return_pct >= 2.0:
        return 1.0
    if trend_return_pct >= 1.0:
        return 0.5
    if trend_return_pct <= -2.0:
        return -1.0
    if trend_return_pct <= -1.0:
        return -0.5
    return 0.0


def _score_funding(funding_rate: Optional[float]) -> float:
    if funding_rate is None:
        return 0.0
    if funding_rate >= 0.0008:
        return -1.0
    if funding_rate >= 0.0003:
        return -0.5
    if funding_rate <= -0.0008:
        return 1.0
    if funding_rate <= -0.0003:
        return 0.5
    return 0.0


def _score_premium(premium_pct: Optional[float]) -> float:
    if premium_pct is None:
        return 0.0
    if premium_pct >= 0.20:
        return -1.0
    if premium_pct >= 0.08:
        return -0.5
    if premium_pct <= -0.20:
        return 1.0
    if premium_pct <= -0.08:
        return 0.5
    return 0.0


def _score_oi(oi_change_pct: Optional[float], trend_return_pct: Optional[float]) -> float:
    if oi_change_pct is None or trend_return_pct is None:
        return 0.0
    if oi_change_pct >= 5.0 and trend_return_pct > 0:
        return 1.0
    if oi_change_pct >= 5.0 and trend_return_pct < 0:
        return -1.0
    if oi_change_pct >= 2.0 and trend_return_pct > 0:
        return 0.5
    if oi_change_pct >= 2.0 and trend_return_pct < 0:
        return -0.5
    return 0.0


def _score_global_ls(global_long_short: Optional[float]) -> float:
    if global_long_short is None:
        return 0.0
    if global_long_short >= 1.8:
        return -0.7
    if global_long_short >= 1.2:
        return -0.3
    if global_long_short <= 0.55:
        return 0.7
    if global_long_short <= 0.8:
        return 0.3
    return 0.0


def _score_taker(taker_ratio: Optional[float]) -> float:
    if taker_ratio is None:
        return 0.0
    if taker_ratio >= 1.10:
        return 0.5
    if taker_ratio >= 1.03:
        return 0.2
    if taker_ratio <= 0.90:
        return -0.5
    if taker_ratio <= 0.97:
        return -0.2
    return 0.0


def _compose_advice(signal: str, risks: List[str], confidence: int, lang: str) -> str:
    if lang == "en":
        if signal == "Bullish":
            base = "Bias long with small size and strict stop-loss."
        elif signal == "Bearish":
            base = "Bias short defensively; avoid oversized positions."
        else:
            base = "Stay neutral and wait for clearer confirmation."
        if "crowding" in risks:
            return f"{base} Crowding risk detected; reduce leverage (confidence {confidence}%)."
        if "insufficient_data" in risks:
            return f"{base} Data quality is limited; treat as watchlist only (confidence {confidence}%)."
        return f"{base} Confidence {confidence}%."

    if signal == "偏多":
        base = "偏多处理，但建议轻仓顺势并设置止损。"
    elif signal == "偏空":
        base = "偏空处理，建议控制仓位并避免追跌。"
    else:
        base = "中性观望，等待趋势与资金信号进一步共振。"

    if "拥挤风险" in risks:
        return f"{base} 当前有拥挤风险，优先降杠杆（置信度 {confidence}%）。"
    if "数据不足" in risks:
        return f"{base} 数据完整度不足，仅作观察（置信度 {confidence}%）。"
    return f"{base} 置信度 {confidence}%。"


def _risk_tags(metrics: SymbolMetrics) -> List[str]:
    tags: List[str] = []
    if (
        metrics.funding_rate is not None
        and abs(metrics.funding_rate) >= 0.0008
    ) or (
        metrics.premium_pct is not None
        and abs(metrics.premium_pct) >= 0.20
    ):
        tags.append("拥挤风险")

    if (
        metrics.trend_return_pct is not None
        and metrics.global_long_short is not None
        and ((metrics.trend_return_pct > 0 and metrics.global_long_short < 0.8) or
             (metrics.trend_return_pct < 0 and metrics.global_long_short > 1.2))
    ):
        tags.append("信号分歧")

    if metrics.volatility_pct is not None and metrics.volatility_pct >= 1.8:
        tags.append("波动放大")

    if metrics.data_completeness() < 0.7:
        tags.append("数据不足")
    return tags


def _evaluate_signal(metrics: SymbolMetrics, lang: str) -> None:
    trend_score = _score_trend(metrics.trend_return_pct)
    crowd_score = _avg([_score_funding(metrics.funding_rate), _score_premium(metrics.premium_pct)]) or 0.0
    oi_score = _score_oi(metrics.oi_change_pct, metrics.trend_return_pct)
    ls_score = _score_global_ls(metrics.global_long_short)
    taker_score = _score_taker(metrics.taker_ratio)

    score = (
        trend_score * 0.25
        + crowd_score * 0.20
        + oi_score * 0.20
        + ls_score * 0.20
        + taker_score * 0.15
    )
    metrics.score = score

    completeness = metrics.data_completeness()
    conf = int(round(min(95.0, abs(score) * 100.0) * (0.6 + 0.4 * completeness)))
    metrics.confidence = max(conf, 18)

    if lang == "en":
        if score >= 0.25:
            metrics.signal = "Bullish"
        elif score <= -0.25:
            metrics.signal = "Bearish"
        else:
            metrics.signal = "Neutral"
    else:
        if score >= 0.25:
            metrics.signal = "偏多"
        elif score <= -0.25:
            metrics.signal = "偏空"
        else:
            metrics.signal = "中性"

    metrics.risk_tags = _risk_tags(metrics)
    risk_for_advice = metrics.risk_tags
    if lang == "en":
        risk_for_advice = [
            "crowding" if x == "拥挤风险" else
            "conflict" if x == "信号分歧" else
            "high_volatility" if x == "波动放大" else
            "insufficient_data" if x == "数据不足" else x
            for x in metrics.risk_tags
        ]
    metrics.advice = _compose_advice(metrics.signal, risk_for_advice, metrics.confidence, lang)


def _normalize_symbols(raw: str) -> List[str]:
    values = [x.strip().upper() for x in raw.split(",") if x.strip()]
    deduped: List[str] = []
    seen = set()
    for v in values:
        if v not in seen:
            deduped.append(v)
            seen.add(v)
    return deduped


def _select_top_symbols(client: PublicFuturesClient, topn: int) -> List[str]:
    allowed = set()
    try:
        exchange = client.exchange_info()
        symbols_meta = exchange.get("symbols", []) if isinstance(exchange, dict) else []
        for item in symbols_meta:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            if not isinstance(symbol, str):
                continue
            if item.get("quoteAsset") != "USDT":
                continue
            if item.get("contractType") != "PERPETUAL":
                continue
            if item.get("status") != "TRADING":
                continue
            allowed.add(symbol)
    except Exception:
        # Degrade gracefully: ranking can still proceed from ticker symbols.
        allowed = set()

    ranked: List[Tuple[str, float]] = []
    try:
        tickers = client.ticker_24hr_price_change()
        if isinstance(tickers, dict):
            tickers = [tickers]
        for ticker in tickers if isinstance(tickers, list) else []:
            if not isinstance(ticker, dict):
                continue
            symbol = ticker.get("symbol")
            if not isinstance(symbol, str):
                continue
            if allowed and symbol not in allowed:
                continue
            if not allowed and (not symbol.endswith("USDT") or "_" in symbol):
                continue
            vol = _to_float(ticker.get("quoteVolume"))
            if vol is None:
                continue
            ranked.append((symbol, vol))
    except Exception:
        ranked = []

    ranked.sort(key=lambda x: x[1], reverse=True)
    if ranked:
        return [s for s, _ in ranked[:topn]]
    if allowed:
        return sorted(list(allowed))[:topn]
    return FALLBACK_SYMBOLS[:topn]


def _analyze_symbol(client: PublicFuturesClient, symbol: str, period: str, lang: str) -> SymbolMetrics:
    m = SymbolMetrics(symbol=symbol)

    try:
        mark = client.mark_price(symbol=symbol)
        if isinstance(mark, list):
            mark = mark[0] if mark else {}
        if isinstance(mark, dict):
            m.mark_price = _to_float(mark.get("markPrice"))
            m.index_price = _to_float(mark.get("indexPrice"))
            if m.mark_price is not None and m.index_price not in (None, 0):
                m.premium_pct = ((m.mark_price - m.index_price) / m.index_price) * 100.0
    except Exception as exc:
        m.errors.append(f"mark_price: {exc}")

    try:
        rows = client.funding_rate(symbol=symbol, limit=3)
        m.funding_rate = _extract_latest_ratio(rows, ["fundingRate"])
    except Exception as exc:
        m.errors.append(f"funding_rate: {exc}")

    try:
        rows = client.open_interest_hist(symbol=symbol, period=period, limit=3)
        m.oi_change_pct = _extract_oi_change_pct(rows)
    except Exception as exc:
        m.errors.append(f"open_interest_hist: {exc}")

    try:
        rows = client.long_short_account_ratio(symbol=symbol, period=period, limit=3)
        m.global_long_short = _extract_latest_ratio(rows, ["longShortRatio"])
    except Exception as exc:
        m.errors.append(f"long_short_account_ratio: {exc}")

    try:
        rows = client.top_long_short_position_ratio(symbol=symbol, period=period, limit=3)
        m.top_position_ratio = _extract_latest_ratio(rows, ["longShortRatio"])
    except Exception as exc:
        m.errors.append(f"top_long_short_position_ratio: {exc}")

    try:
        rows = client.top_long_short_account_ratio(symbol=symbol, period=period, limit=3)
        m.top_account_ratio = _extract_latest_ratio(rows, ["longShortRatio"])
    except Exception as exc:
        m.errors.append(f"top_long_short_account_ratio: {exc}")

    try:
        rows = client.taker_long_short_ratio(symbol=symbol, period=period, limit=3)
        m.taker_ratio = _extract_latest_ratio(rows, ["buySellRatio", "longShortRatio"])
    except Exception as exc:
        m.errors.append(f"taker_long_short_ratio: {exc}")

    try:
        klines = client.klines(symbol=symbol, interval=period, limit=5)
        m.trend_return_pct, m.volatility_pct = _extract_trend(klines)
    except Exception as exc:
        m.errors.append(f"klines: {exc}")

    _evaluate_signal(m, lang)
    return m


def _fmt_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None or math.isnan(value):
        return "N/A"
    return f"{value:.{digits}f}%"


def _fmt_num(value: Optional[float], digits: int = 4) -> str:
    if value is None or math.isnan(value):
        return "N/A"
    return f"{value:.{digits}f}"


def _render_completeness(metrics: SymbolMetrics) -> str:
    return f"{metrics.data_completeness() * 100.0:.0f}%"


def _extract_window_return_pct(client: PublicFuturesClient, symbol: str, interval: str, limit: int) -> Optional[float]:
    try:
        klines = client.klines(symbol=symbol, interval=interval, limit=limit)
    except Exception:
        return None
    change_pct, _ = _extract_trend(klines)
    return change_pct


def _heartbeat_opportunity_trigger(metrics: SymbolMetrics, lang: str, config: HeartbeatConfig) -> Optional[str]:
    if not config.enable_opportunity:
        return None
    if metrics.signal in {"中性", "Neutral"}:
        return None
    if abs(metrics.score) < config.opportunity_min_score:
        return None
    if metrics.confidence < config.opportunity_min_confidence:
        return None
    blocked_tags = {"数据不足", "拥挤风险"}
    if any(tag in blocked_tags for tag in metrics.risk_tags):
        return None
    watch_only = "信号分歧" in metrics.risk_tags

    if lang == "en":
        if watch_only:
            side = "long watchlist" if metrics.signal == "Bullish" else "short watchlist"
        else:
            side = "long opportunity" if metrics.signal == "Bullish" else "short opportunity"
        return f"{side} score {metrics.score:.3f} / confidence {metrics.confidence}%"

    if watch_only:
        side = "做多观察机会" if metrics.signal == "偏多" else "做空观察机会"
    else:
        side = "做多机会" if metrics.signal == "偏多" else "做空机会"
    return f"{side} score {metrics.score:.3f} / 置信度 {metrics.confidence}%"


def _render_report(
    metrics_list: List[SymbolMetrics],
    period: str,
    topn: int,
    explicit_symbols: Optional[List[str]],
    lang: str,
) -> str:
    now = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    if lang == "en":
        bullish = sum(1 for x in metrics_list if x.signal == "Bullish")
        bearish = sum(1 for x in metrics_list if x.signal == "Bearish")
        neutral = sum(1 for x in metrics_list if x.signal == "Neutral")
        title = "# Binance USDS Futures Monitoring Report"
        snap = "## Market Snapshot"
        detail = "## Metric Details"
        score = "## Composite Scores"
        advice = "## Advice and Risks"
        disclaimer = "## Disclaimer"
        scope = ", ".join(explicit_symbols) if explicit_symbols else f"auto Top{topn}"
        lines = [
            title,
            "",
            f"- Generated at: {now}",
            f"- Period: `{period}`",
            f"- Universe: {scope}",
            "",
            snap,
            f"- Bullish: `{bullish}` | Neutral: `{neutral}` | Bearish: `{bearish}`",
            "",
            detail,
            "",
        ]
    else:
        bullish = sum(1 for x in metrics_list if x.signal == "偏多")
        bearish = sum(1 for x in metrics_list if x.signal == "偏空")
        neutral = sum(1 for x in metrics_list if x.signal == "中性")
        title = "# Binance USDS 合约监控报告"
        snap = "## 市场快照"
        detail = "## 指标明细"
        score = "## 综合评分"
        advice = "## 建议与风险"
        disclaimer = "## 免责声明"
        scope = ",".join(explicit_symbols) if explicit_symbols else f"自动 Top{topn}"
        lines = [
            title,
            "",
            f"- 生成时间: {now}",
            f"- 分析周期: `{period}`",
            f"- 分析范围: {scope}",
            "",
            snap,
            f"- 偏多: `{bullish}` | 中性: `{neutral}` | 偏空: `{bearish}`",
            "",
            detail,
            "",
        ]

    for m in metrics_list:
        tags = ",".join(m.risk_tags) if m.risk_tags else ("none" if lang == "en" else "无")
        lines.extend(
            [
                f"### {m.symbol}",
                f"- {'Signal' if lang == 'en' else '方向'}: `{m.signal}` ({'confidence' if lang == 'en' else '置信度'} {m.confidence}%)",
                f"- {'Mark/Index' if lang == 'en' else '标记/指数价格'}: `{_fmt_num(m.mark_price, 4)}` / `{_fmt_num(m.index_price, 4)}`",
                f"- {'Premium' if lang == 'en' else '溢价率'}: `{_fmt_pct(m.premium_pct)}`",
                f"- {'Funding rate' if lang == 'en' else '资金费率'}: `{_fmt_num(m.funding_rate, 6)}`",
                f"- {'Open interest change' if lang == 'en' else '持仓量变化'}: `{_fmt_pct(m.oi_change_pct)}`",
                f"- {'Global L/S ratio' if lang == 'en' else '全市场多空比'}: `{_fmt_num(m.global_long_short, 4)}`",
                f"- {'Top pos ratio' if lang == 'en' else '大户持仓多空比'}: `{_fmt_num(m.top_position_ratio, 4)}`",
                f"- {'Top acct ratio' if lang == 'en' else '大户账户多空比'}: `{_fmt_num(m.top_account_ratio, 4)}`",
                f"- {'Taker ratio' if lang == 'en' else '主动买卖比'}: `{_fmt_num(m.taker_ratio, 4)}`",
                f"- {'Trend return' if lang == 'en' else '周期趋势涨跌'}: `{_fmt_pct(m.trend_return_pct)}`",
                f"- {'Volatility' if lang == 'en' else '波动率'}: `{_fmt_pct(m.volatility_pct)}`",
                f"- {'Data completeness' if lang == 'en' else '数据完整度'}: `{_render_completeness(m)}`",
                f"- {'Risk tags' if lang == 'en' else '风险标签'}: `{tags}`",
                f"- {'Advice' if lang == 'en' else '建议'}: {m.advice}",
            ]
        )
        if m.errors:
            lines.append(f"- {'Errors' if lang == 'en' else '接口降级信息'}: `{'; '.join(m.errors)}`")
        lines.append("")

    lines.extend(
        [
            score,
            "",
            "| Symbol | Score | Signal | Confidence |",
            "|---|---:|---|---:|",
        ]
    )
    ranked = sorted(metrics_list, key=lambda x: x.score, reverse=True)
    for m in ranked:
        lines.append(f"| {m.symbol} | {m.score:.3f} | {m.signal} | {m.confidence}% |")

    lines.extend(["", advice, ""])
    if lang == "en":
        lines.extend(
            [
                "- Use this output as a risk-aware research view, not a direct order signal.",
                "- If multiple symbols show crowding/conflict at once, reduce leverage and wait.",
                "",
                disclaimer,
                "",
                "This report is for educational and research purposes only and is not investment advice.",
            ]
        )
    else:
        lines.extend(
            [
                "- 本报告以稳健风控为主，信号冲突时优先观望或减仓。",
                "- 若多数标的出现拥挤/分歧信号，优先降低杠杆，避免追涨杀跌。",
                "",
                disclaimer,
                "",
                "本报告仅用于学习与研究，不构成任何投资建议。",
            ]
        )
    return "\n".join(lines)


def _build_payload(
    metrics_list: List[SymbolMetrics],
    period: str,
    topn: int,
    explicit_symbols: Optional[List[str]],
    backend: str,
) -> Dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc).astimezone().isoformat()
    ranked = sorted(metrics_list, key=lambda x: x.score, reverse=True)
    ranked_bearish = sorted(metrics_list, key=lambda x: x.score)

    def _first_signal(name: str, items: Sequence[SymbolMetrics]) -> Optional[str]:
        for item in items:
            if item.signal == name:
                return item.symbol
        return None

    bullish_label = "Bullish" if any(x.signal == "Bullish" for x in metrics_list) else "偏多"
    bearish_label = "Bearish" if any(x.signal == "Bearish" for x in metrics_list) else "偏空"
    neutral_label = "Neutral" if any(x.signal == "Neutral" for x in metrics_list) else "中性"

    return {
        "generated_at": now,
        "period": period,
        "backend": backend,
        "universe": {
            "mode": "explicit" if explicit_symbols else "auto",
            "symbols": explicit_symbols or [],
            "topn": topn if not explicit_symbols else None,
        },
        "summary": {
            "bullish": sum(1 for x in metrics_list if x.signal == bullish_label),
            "neutral": sum(1 for x in metrics_list if x.signal == neutral_label),
            "bearish": sum(1 for x in metrics_list if x.signal == bearish_label),
            "top_bullish_symbol": _first_signal(bullish_label, ranked),
            "top_bearish_symbol": _first_signal(bearish_label, ranked_bearish),
            "crowded_symbols": [x.symbol for x in metrics_list if "拥挤风险" in x.risk_tags],
            "conflict_symbols": [x.symbol for x in metrics_list if "信号分歧" in x.risk_tags],
            "avg_data_completeness": round(
                _avg([x.data_completeness() for x in metrics_list]) or 0.0,
                4,
            ),
        },
        "results": [
            {
                "symbol": x.symbol,
                "signal": x.signal,
                "score": round(x.score, 6),
                "confidence": x.confidence,
                "data_completeness": round(x.data_completeness(), 4),
                "risk_tags": x.risk_tags,
                "advice": x.advice,
                "premium_pct": x.premium_pct,
                "funding_rate": x.funding_rate,
                "oi_change_pct": x.oi_change_pct,
                "global_long_short": x.global_long_short,
                "top_position_ratio": x.top_position_ratio,
                "top_account_ratio": x.top_account_ratio,
                "taker_ratio": x.taker_ratio,
                "trend_return_pct": x.trend_return_pct,
                "volatility_pct": x.volatility_pct,
                "mark_price": x.mark_price,
                "index_price": x.index_price,
                "errors": x.errors,
            }
            for x in metrics_list
        ],
    }


def _build_heartbeat_alerts(
    client: PublicFuturesClient,
    metrics_list: List[SymbolMetrics],
    lang: str,
    config: HeartbeatConfig,
) -> List[HeartbeatAlert]:
    alerts: List[HeartbeatAlert] = []
    for metrics in metrics_list:
        price_move_12h_pct = _extract_window_return_pct(
            client=client,
            symbol=metrics.symbol,
            interval="1h",
            limit=13,
        )
        triggers: List[str] = []
        has_anomaly = False
        if (
            price_move_12h_pct is not None
            and abs(price_move_12h_pct) >= config.price_move_threshold_pct
        ):
            has_anomaly = True
            if lang == "en":
                triggers.append(f"12h move {_fmt_pct(price_move_12h_pct)}")
            else:
                triggers.append(f"12h波动 {_fmt_pct(price_move_12h_pct)}")

        if metrics.global_long_short is not None and (
            metrics.global_long_short >= config.ls_high or metrics.global_long_short <= config.ls_low
        ):
            has_anomaly = True
            if lang == "en":
                triggers.append(f"global L/S {_fmt_num(metrics.global_long_short, 4)}")
            else:
                triggers.append(f"多空比 {_fmt_num(metrics.global_long_short, 4)}")

        if metrics.premium_pct is not None and metrics.premium_pct >= config.premium_threshold_pct:
            has_anomaly = True
            if lang == "en":
                triggers.append(f"premium {_fmt_pct(metrics.premium_pct)}")
            else:
                triggers.append(f"溢价 {_fmt_pct(metrics.premium_pct)}")

        data_issue = False
        if not triggers:
            core_missing = sum(
                1
                for item in (price_move_12h_pct, metrics.global_long_short, metrics.premium_pct)
                if item is None
            )
            if core_missing >= 2 and metrics.errors:
                data_issue = True
                has_anomaly = True
                triggers.append("data unavailable" if lang == "en" else "关键数据不足")

        opportunity_trigger = _heartbeat_opportunity_trigger(metrics=metrics, lang=lang, config=config)
        has_opportunity = opportunity_trigger is not None
        if opportunity_trigger is not None:
            triggers.append(opportunity_trigger)

        if triggers:
            if not has_anomaly and has_opportunity and "观察机会" in opportunity_trigger:
                advice = "列入观察列表，等待下一根 4h 收线或更多顺势信号确认。"
            elif not has_anomaly and has_opportunity and lang == "en" and "watchlist" in opportunity_trigger:
                advice = "Add it to the watchlist and wait for one more candle or cleaner confirmation."
            else:
                advice = (
                    "Data check degraded; verify endpoints manually."
                    if data_issue and lang == "en"
                    else "数据检查降级，建议手动复核接口状态。"
                    if data_issue
                    else metrics.advice
                )
            alerts.append(
                HeartbeatAlert(
                    symbol=metrics.symbol,
                    triggers=triggers,
                    advice=advice,
                    signal=metrics.signal,
                    confidence=metrics.confidence,
                    has_anomaly=has_anomaly,
                    has_opportunity=has_opportunity,
                    price_move_12h_pct=price_move_12h_pct,
                    premium_pct=metrics.premium_pct,
                    global_long_short=metrics.global_long_short,
                    data_issue=data_issue,
                )
            )
    return alerts


def _render_heartbeat(
    client: PublicFuturesClient,
    metrics_list: List[SymbolMetrics],
    lang: str,
    config: HeartbeatConfig,
) -> str:
    alerts = _build_heartbeat_alerts(
        client=client,
        metrics_list=metrics_list,
        lang=lang,
        config=config,
    )
    if not alerts:
        return "HEARTBEAT_OK"

    lines: List[str] = []
    if lang == "en":
        lines.append("[Binance Futures Heartbeat]")
        for alert in alerts[: config.max_alerts]:
            lines.append(f"{alert.symbol}: {'; '.join(alert.triggers)}")
        if len(alerts) > 1 and any(alert.has_anomaly for alert in alerts):
            lines.append("Advice: Multiple symbols triggered. Reduce leverage and wait for confirmation.")
        elif len(alerts) > 1:
            lines.append("Advice: Multiple watchlist setups appeared. Keep them on watch and wait for cleaner confirmation.")
        else:
            lines.append(f"Advice: {alerts[0].advice}")
        return "\n".join(lines)

    lines.append("[Binance Futures Heartbeat]")
    for alert in alerts[: config.max_alerts]:
        lines.append(f"{alert.symbol}: {'；'.join(alert.triggers)}")
    if len(alerts) > 1 and any(alert.has_anomaly for alert in alerts):
        lines.append("建议: 多标的同时异常，先降总风险敞口，等待下一根 4h 收线确认。")
    elif len(alerts) > 1:
        lines.append("建议: 出现多标的观察机会，先加入观察列表，等待更强确认再行动。")
    else:
        lines.append(f"建议: {alerts[0].advice}")
    return "\n".join(lines)


def _render_alert(
    metrics_list: List[SymbolMetrics],
    period: str,
    topn: int,
    explicit_symbols: Optional[List[str]],
    lang: str,
) -> str:
    now = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    ranked = sorted(metrics_list, key=lambda x: abs(x.score), reverse=True)
    focus = ranked[: min(3, len(ranked))]

    if lang == "en":
        bullish = sum(1 for x in metrics_list if x.signal == "Bullish")
        bearish = sum(1 for x in metrics_list if x.signal == "Bearish")
        neutral = sum(1 for x in metrics_list if x.signal == "Neutral")
        scope = ", ".join(explicit_symbols) if explicit_symbols else f"auto Top{topn}"
        crowded = ", ".join(x.symbol for x in metrics_list if "拥挤风险" in x.risk_tags) or "none"
        lines = [
            "# Binance USDS Futures Alert",
            "",
            f"- Generated at: {now}",
            f"- Period: `{period}`",
            f"- Universe: {scope}",
            f"- Snapshot: Bullish `{bullish}` | Neutral `{neutral}` | Bearish `{bearish}`",
            f"- Crowding risk: {crowded}",
            "- Focus list:",
        ]
        for item in focus:
            lines.append(
                f"- {item.symbol}: `{item.signal}` / score `{item.score:.3f}` / confidence `{item.confidence}%` / {item.advice}"
            )
        return "\n".join(lines)

    bullish = sum(1 for x in metrics_list if x.signal == "偏多")
    bearish = sum(1 for x in metrics_list if x.signal == "偏空")
    neutral = sum(1 for x in metrics_list if x.signal == "中性")
    scope = ",".join(explicit_symbols) if explicit_symbols else f"自动 Top{topn}"
    crowded = "、".join(x.symbol for x in metrics_list if "拥挤风险" in x.risk_tags) or "无"
    lines = [
        "# Binance USDS 合约快讯",
        "",
        f"- 生成时间: {now}",
        f"- 分析周期: `{period}`",
        f"- 分析范围: {scope}",
        f"- 情绪快照: 偏多 `{bullish}` | 中性 `{neutral}` | 偏空 `{bearish}`",
        f"- 拥挤风险标的: {crowded}",
        "- 重点关注:",
    ]
    for item in focus:
        lines.append(
            f"- {item.symbol}: `{item.signal}` / 分数 `{item.score:.3f}` / 置信度 `{item.confidence}%` / {item.advice}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor Binance USDS futures public data and generate conservative advice."
    )
    parser.add_argument("--topn", type=int, default=10, help="Top symbols by quoteVolume for auto mode.")
    parser.add_argument(
        "--period",
        type=str,
        default=DEFAULT_PERIOD,
        help="Period for ratio/open interest and kline interval (default: 4h).",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT. Overrides auto TopN.",
    )
    parser.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL, help="Binance futures base URL.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds.")
    parser.add_argument("--sleep", type=float, default=0.08, help="Sleep seconds between symbols.")
    parser.add_argument("--lang", type=str, default="zh", choices=["zh", "en"], help="Output language.")
    parser.add_argument(
        "--hb-price-move-pct",
        type=float,
        default=HEARTBEAT_PRICE_MOVE_THRESHOLD_PCT,
        help="Heartbeat trigger threshold for absolute 12h price move in percent.",
    )
    parser.add_argument(
        "--hb-ls-high",
        type=float,
        default=HEARTBEAT_LS_HIGH,
        help="Heartbeat trigger threshold for high global long/short ratio.",
    )
    parser.add_argument(
        "--hb-ls-low",
        type=float,
        default=HEARTBEAT_LS_LOW,
        help="Heartbeat trigger threshold for low global long/short ratio.",
    )
    parser.add_argument(
        "--hb-premium-pct",
        type=float,
        default=HEARTBEAT_PREMIUM_THRESHOLD_PCT,
        help="Heartbeat trigger threshold for positive premium percent.",
    )
    parser.add_argument(
        "--hb-opportunity-score",
        type=float,
        default=HEARTBEAT_OPPORTUNITY_MIN_SCORE,
        help="Minimum absolute score required to notify a heartbeat trade opportunity.",
    )
    parser.add_argument(
        "--hb-opportunity-confidence",
        type=int,
        default=HEARTBEAT_OPPORTUNITY_MIN_CONFIDENCE,
        help="Minimum confidence required to notify a heartbeat trade opportunity.",
    )
    parser.add_argument(
        "--hb-disable-opportunity",
        action="store_true",
        help="Disable heartbeat trade opportunity reminders and only report anomalies.",
    )
    parser.add_argument(
        "--hb-max-alerts",
        type=int,
        default=3,
        help="Maximum number of symbols to include in heartbeat alert output.",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="report",
        choices=sorted(SUPPORTED_FORMATS),
        help="Output format: report, alert, heartbeat, json, or report+json.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Legacy alias for --format report+json.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.period not in SUPPORTED_PERIODS:
        parser.error(f"--period must be one of: {', '.join(sorted(SUPPORTED_PERIODS))}")
    if args.topn <= 0:
        parser.error("--topn must be > 0")
    if args.hb_price_move_pct < 0:
        parser.error("--hb-price-move-pct must be >= 0")
    if args.hb_ls_low <= 0:
        parser.error("--hb-ls-low must be > 0")
    if args.hb_ls_high <= args.hb_ls_low:
        parser.error("--hb-ls-high must be > --hb-ls-low")
    if args.hb_premium_pct < 0:
        parser.error("--hb-premium-pct must be >= 0")
    if args.hb_opportunity_score < 0:
        parser.error("--hb-opportunity-score must be >= 0")
    if args.hb_opportunity_confidence < 0 or args.hb_opportunity_confidence > 100:
        parser.error("--hb-opportunity-confidence must be between 0 and 100")
    if args.hb_max_alerts <= 0:
        parser.error("--hb-max-alerts must be > 0")
    if args.json and args.format != "report":
        parser.error("--json cannot be combined with --format other than report")

    explicit_symbols = _normalize_symbols(args.symbols) if args.symbols else []
    output_format = "report+json" if args.json else args.format
    heartbeat_config = HeartbeatConfig(
        price_move_threshold_pct=args.hb_price_move_pct,
        ls_high=args.hb_ls_high,
        ls_low=args.hb_ls_low,
        premium_threshold_pct=args.hb_premium_pct,
        opportunity_min_score=args.hb_opportunity_score,
        opportunity_min_confidence=args.hb_opportunity_confidence,
        enable_opportunity=not args.hb_disable_opportunity,
        max_alerts=args.hb_max_alerts,
    )
    client = PublicFuturesClient(base_url=args.base_url, timeout=args.timeout)

    if explicit_symbols:
        symbols = explicit_symbols
    elif output_format == "heartbeat":
        symbols = HEARTBEAT_DEFAULT_SYMBOLS[:]
    else:
        try:
            symbols = _select_top_symbols(client, args.topn)
        except Exception as exc:
            print(f"[ERROR] Failed to select Top{args.topn} symbols: {exc}", file=sys.stderr)
            return 2

    if not symbols:
        print("[ERROR] No symbols available for analysis.", file=sys.stderr)
        return 3

    results: List[SymbolMetrics] = []
    for symbol in symbols:
        results.append(_analyze_symbol(client, symbol=symbol, period=args.period, lang=args.lang))
        if args.sleep > 0:
            time.sleep(args.sleep)

    explicit = explicit_symbols if explicit_symbols else None
    payload = _build_payload(
        metrics_list=results,
        period=args.period,
        topn=args.topn,
        explicit_symbols=explicit,
        backend="sdk" if client.client is not None else "rest-only",
    )

    if output_format == "report":
        print(
            _render_report(
                metrics_list=results,
                period=args.period,
                topn=args.topn,
                explicit_symbols=explicit,
                lang=args.lang,
            )
        )
    elif output_format == "alert":
        print(
            _render_alert(
                metrics_list=results,
                period=args.period,
                topn=args.topn,
                explicit_symbols=explicit,
                lang=args.lang,
            )
        )
    elif output_format == "heartbeat":
        print(
            _render_heartbeat(
                client=client,
                metrics_list=results,
                lang=args.lang,
                config=heartbeat_config,
            )
        )
    elif output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            _render_report(
                metrics_list=results,
                period=args.period,
                topn=args.topn,
                explicit_symbols=explicit,
                lang=args.lang,
            )
        )
        print("\n## JSON")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
