#!/usr/bin/env python3
"""Hourly Binance market watch for OpenClaw."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SPOT_BASE_URL = "https://api.binance.com"
FUTURES_BASE_URL = "https://fapi.binance.com"
DEFAULT_TIMEOUT = 10.0
DEFAULT_TOPN = 10
DEFAULT_COOLDOWN_HOURS = 6.0
DEFAULT_SLEEP_SECONDS = 0.05
DEFAULT_MAX_ALERTS = 3
DEFAULT_WORKERS = 4
STATE_VERSION = 1

LONG_PRICE_THRESHOLD = 2.0
SHORT_PRICE_THRESHOLD = -2.0
SPOT_CONFIRMATION_THRESHOLD = 1.5
VOLUME_RATIO_THRESHOLD = 1.8
OI_CHANGE_THRESHOLD = 1.5
LONG_TAKER_THRESHOLD = 1.12
SHORT_TAKER_THRESHOLD = 0.89

FUNDING_RISK_THRESHOLD_PCT = 0.03
PREMIUM_RISK_THRESHOLD_PCT = 0.80
SPREAD_RISK_THRESHOLD_BPS = 12.0
RETURN_DIVERGENCE_THRESHOLD_PCT = 1.0

MARGIN_RATE_JUMP_THRESHOLD_PCT = 50.0
MARGIN_INVENTORY_DROP_THRESHOLD_PCT = -40.0
MARGIN_BORROW_DROP_THRESHOLD_PCT = -30.0
MARGIN_RATE_JUMP_SEVERE_THRESHOLD_PCT = 100.0
MARGIN_INVENTORY_DROP_SEVERE_THRESHOLD_PCT = -60.0
MARGIN_BORROW_DROP_SEVERE_THRESHOLD_PCT = -50.0
MARGIN_HISTORY_LIMIT = 48
TOPN_STICKY_POOL = 15


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_z(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pct_change(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if current is None or baseline in (None, 0):
        return None
    return (current - baseline) / baseline * 100.0


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def score_linear(value: Optional[float], threshold: float, strong: float) -> float:
    if value is None or value < threshold:
        return 0.0
    if strong <= threshold:
        return 1.0
    return clamp((value - threshold) / (strong - threshold), 0.0, 1.0)


def median(values: Iterable[Optional[float]]) -> Optional[float]:
    seq = [value for value in values if value is not None]
    if not seq:
        return None
    return float(statistics.median(seq))


def average(values: Iterable[Optional[float]]) -> Optional[float]:
    seq = [value for value in values if value is not None]
    if not seq:
        return None
    return sum(seq) / len(seq)


def format_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}%"


def format_ratio(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def format_number(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def severity_rank(level: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(level, 0)


def visible_risk_tags(tags: Sequence[str]) -> List[str]:
    return [tag for tag in tags if tag != "margin_public_only"]


def pick_number(record: Dict[str, Any], keys: Sequence[str], fuzzy: Sequence[str] = ()) -> Optional[float]:
    for key in keys:
        if key in record:
            return to_float(record[key])
    lowered = {str(key).lower(): value for key, value in record.items()}
    for candidate in fuzzy:
        for key, value in lowered.items():
            if candidate in key:
                parsed = to_float(value)
                if parsed is not None:
                    return parsed
    return None


def pick_text(record: Dict[str, Any], keys: Sequence[str], fuzzy: Sequence[str] = ()) -> Optional[str]:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return str(value)
    lowered = {str(key).lower(): value for key, value in record.items()}
    for candidate in fuzzy:
        for key, value in lowered.items():
            if candidate in key and value is not None:
                return str(value)
    return None


def parse_symbols_arg(value: Optional[str]) -> List[str]:
    if not value:
        return []
    result: List[str] = []
    for item in value.replace(" ", ",").split(","):
        symbol = item.strip().upper()
        if not symbol:
            continue
        if symbol not in result:
            result.append(symbol)
    return result


class BinanceRestClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        spot_base_url: str = SPOT_BASE_URL,
        futures_base_url: str = FUTURES_BASE_URL,
        ca_bundle: Optional[str] = None,
        allow_insecure_ssl: bool = False,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.spot_base_url = spot_base_url.rstrip("/")
        self.futures_base_url = futures_base_url.rstrip("/")
        self.ca_bundle = ca_bundle
        self.allow_insecure_ssl = allow_insecure_ssl

    @property
    def has_margin_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _request(
        self,
        base_url: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        signed: bool = False,
    ) -> Any:
        clean_params = {key: value for key, value in (params or {}).items() if value is not None}
        headers = {"User-Agent": "binance-market-watch/1.0.0 (OpenClaw Skill)"}
        if signed:
            if not self.has_margin_credentials:
                raise RuntimeError("Signed margin endpoints require BINANCE_API_KEY and BINANCE_SECRET_KEY.")
            clean_params["timestamp"] = int(time.time() * 1000)
            query = urllib.parse.urlencode(clean_params, doseq=True)
            signature = hmac.new(
                self.api_secret.encode("utf-8"),
                query.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            query = f"{query}&signature={signature}"
            headers["X-MBX-APIKEY"] = self.api_key
        else:
            query = urllib.parse.urlencode(clean_params, doseq=True)
        url = f"{base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(url=url, method="GET", headers=headers)
        context = self._build_ssl_context()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {path}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error {path}: {exc}") from exc

    def _build_ssl_context(self) -> ssl.SSLContext:
        if self.allow_insecure_ssl:
            context = ssl._create_unverified_context()
            return context
        if self.ca_bundle:
            return ssl.create_default_context(cafile=self.ca_bundle)
        return ssl.create_default_context()

    def spot_exchange_info(self) -> Any:
        return self._request(self.spot_base_url, "/api/v3/exchangeInfo")

    def spot_klines(self, symbol: str, interval: str = "1h", limit: int = 3) -> Any:
        return self._request(
            self.spot_base_url,
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    def spot_book_ticker(self, symbol: str) -> Any:
        return self._request(
            self.spot_base_url,
            "/api/v3/ticker/bookTicker",
            {"symbol": symbol},
        )

    def spot_book_ticker_all(self) -> Any:
        return self._request(self.spot_base_url, "/api/v3/ticker/bookTicker")

    def futures_ticker_24h(self) -> Any:
        return self._request(self.futures_base_url, "/fapi/v1/ticker/24hr")

    def futures_klines(self, symbol: str, interval: str = "1h", limit: int = 25) -> Any:
        return self._request(
            self.futures_base_url,
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    def futures_open_interest_hist(self, symbol: str, period: str = "1h", limit: int = 2) -> Any:
        return self._request(
            self.futures_base_url,
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def futures_taker_ratio(self, symbol: str, period: str = "1h", limit: int = 1) -> Any:
        return self._request(
            self.futures_base_url,
            "/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def futures_global_long_short_ratio(self, symbol: str, period: str = "1h", limit: int = 1) -> Any:
        return self._request(
            self.futures_base_url,
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def futures_funding_rate(self, symbol: str, limit: int = 1) -> Any:
        return self._request(
            self.futures_base_url,
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": limit},
        )

    def futures_premium_index(self, symbol: str) -> Any:
        return self._request(
            self.futures_base_url,
            "/fapi/v1/premiumIndex",
            {"symbol": symbol},
        )

    def futures_premium_index_all(self) -> Any:
        return self._request(self.futures_base_url, "/fapi/v1/premiumIndex")

    def margin_next_hourly_interest_rate(self, assets: Sequence[str]) -> Any:
        return self._request(
            self.spot_base_url,
            "/sapi/v1/margin/next-hourly-interest-rate",
            {"assets": ",".join(assets), "isIsolated": "FALSE"},
            signed=True,
        )

    def margin_available_inventory(self) -> Any:
        return self._request(
            self.spot_base_url,
            "/sapi/v1/margin/available-inventory",
            {"type": "MARGIN"},
            signed=True,
        )

    def margin_max_borrowable(self, asset: str) -> Any:
        return self._request(
            self.spot_base_url,
            "/sapi/v1/margin/maxBorrowable",
            {"asset": asset},
            signed=True,
        )

    def margin_restricted_assets(self) -> Any:
        return self._request(self.spot_base_url, "/sapi/v1/margin/restricted-asset")

    def margin_delist_schedule(self) -> Any:
        return self._request(self.spot_base_url, "/sapi/v1/margin/delist-schedule")


@dataclass
class SymbolMetrics:
    symbol: str
    asset: str
    futures_quote_volume: Optional[float] = None
    futures_ret_1h_pct: Optional[float] = None
    futures_volume_ratio: Optional[float] = None
    spot_ret_1h_pct: Optional[float] = None
    spot_spread_bps: Optional[float] = None
    oi_change_1h_pct: Optional[float] = None
    taker_ratio: Optional[float] = None
    global_long_short_ratio: Optional[float] = None
    funding_rate_pct: Optional[float] = None
    premium_pct: Optional[float] = None
    margin_next_hourly_rate_pct: Optional[float] = None
    margin_rate_jump_pct: Optional[float] = None
    margin_inventory: Optional[float] = None
    margin_inventory_change_pct: Optional[float] = None
    margin_max_borrowable: Optional[float] = None
    margin_max_borrowable_change_pct: Optional[float] = None
    risk_tags: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def completeness(self) -> int:
        fields = [
            self.futures_ret_1h_pct,
            self.futures_volume_ratio,
            self.oi_change_1h_pct,
            self.taker_ratio,
            self.funding_rate_pct,
            self.premium_pct,
            self.spot_ret_1h_pct,
            self.spot_spread_bps,
        ]
        available = sum(1 for value in fields if value is not None)
        return round(available / len(fields) * 100)


@dataclass
class Alert:
    symbol: str
    signal_type: str
    severity: str
    score: int
    confidence: int
    summary: str
    reason: str
    detail_lines: List[str]
    metrics: Dict[str, Any]
    risk_tags: List[str]
    cooldown_key: str
    emitted: bool = False


def default_state_path() -> pathlib.Path:
    base_dir = pathlib.Path(__file__).resolve().parents[1]
    return base_dir / ".cache" / "state.json"


def load_state(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": STATE_VERSION,
            "updated_at": None,
            "top_symbols": [],
            "alerts": {},
            "margin_history": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state must be an object")
        data.setdefault("schema_version", STATE_VERSION)
        data.setdefault("updated_at", None)
        data.setdefault("top_symbols", [])
        data.setdefault("alerts", {})
        data.setdefault("margin_history", {})
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return {
            "schema_version": STATE_VERSION,
            "updated_at": None,
            "top_symbols": [],
            "alerts": {},
            "margin_history": {},
        }


def save_state(path: pathlib.Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["schema_version"] = STATE_VERSION
    state["updated_at"] = iso_z(utc_now())
    path.write_text(json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def parse_last_closed_return(klines: Sequence[Sequence[Any]]) -> Optional[float]:
    if len(klines) < 2:
        return None
    last_closed = klines[-2]
    open_price = to_float(last_closed[1]) if len(last_closed) > 4 else None
    close_price = to_float(last_closed[4]) if len(last_closed) > 4 else None
    return pct_change(close_price, open_price)


def parse_last_closed_volume_ratio(klines: Sequence[Sequence[Any]]) -> Optional[float]:
    if len(klines) < 4:
        return None
    last_closed = klines[-2]
    last_quote_volume = to_float(last_closed[7]) if len(last_closed) > 7 else None
    history = [to_float(item[7]) for item in klines[:-2] if len(item) > 7]
    baseline = average(history[-20:])
    if last_quote_volume is None or baseline in (None, 0):
        return None
    return last_quote_volume / baseline


def parse_spread_bps(book_ticker: Dict[str, Any]) -> Optional[float]:
    bid = to_float(book_ticker.get("bidPrice"))
    ask = to_float(book_ticker.get("askPrice"))
    if bid in (None, 0) or ask in (None, 0):
        return None
    mid = (bid + ask) / 2.0
    if mid == 0:
        return None
    return (ask - bid) / mid * 10000.0


def map_records_by_symbol(records: Any) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    if not isinstance(records, list):
        return result
    for item in records:
        if not isinstance(item, dict):
            continue
        symbol = pick_text(item, ["symbol"], ["symbol"])
        if symbol:
            result[symbol] = item
    return result


def parse_open_interest_change(records: Any) -> Optional[float]:
    if not isinstance(records, list) or len(records) < 2:
        return None
    current = pick_number(records[-1], ["sumOpenInterest"], ["openinterest"])
    previous = pick_number(records[-2], ["sumOpenInterest"], ["openinterest"])
    return pct_change(current, previous)


def parse_single_ratio(records: Any, keys: Sequence[str], fuzzy: Sequence[str]) -> Optional[float]:
    if not isinstance(records, list) or not records:
        return None
    latest = records[-1]
    if not isinstance(latest, dict):
        return None
    return pick_number(latest, keys, fuzzy)


def spot_symbols_from_exchange_info(exchange_info: Dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in exchange_info.get("symbols", []):
        if not isinstance(item, dict):
            continue
        if item.get("status") != "TRADING":
            continue
        if item.get("quoteAsset") != "USDT":
            continue
        symbol = item.get("symbol")
        if symbol:
            result.add(str(symbol))
    return result


def spot_symbols_from_book_tickers(records: Any) -> set[str]:
    result: set[str] = set()
    if not isinstance(records, list):
        return result
    for item in records:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        if isinstance(symbol, str) and symbol.endswith("USDT"):
            result.add(symbol)
    return result


def select_top_symbols(
    tickers: Sequence[Dict[str, Any]],
    spot_symbols: set[str],
    topn: int,
    previous_top: Sequence[str],
) -> List[Tuple[str, float]]:
    filtered: List[Tuple[str, float]] = []
    for item in tickers:
        symbol = str(item.get("symbol", ""))
        quote_volume = to_float(item.get("quoteVolume"))
        if not symbol.endswith("USDT") or symbol not in spot_symbols or quote_volume is None:
            continue
        filtered.append((symbol, quote_volume))
    filtered.sort(key=lambda pair: pair[1], reverse=True)
    sticky_pool = {symbol for symbol, _ in filtered[: max(topn, TOPN_STICKY_POOL)]}
    selected: List[Tuple[str, float]] = []
    selected_symbols: set[str] = set()
    score_map = dict(filtered)
    for symbol in previous_top:
        if symbol in sticky_pool and symbol in score_map and symbol not in selected_symbols:
            selected.append((symbol, score_map[symbol]))
            selected_symbols.add(symbol)
        if len(selected) >= topn:
            return selected
    for symbol, quote_volume in filtered:
        if symbol in selected_symbols:
            continue
        selected.append((symbol, quote_volume))
        selected_symbols.add(symbol)
        if len(selected) >= topn:
            break
    return selected


def margin_history_for_asset(state: Dict[str, Any], asset: str) -> List[Dict[str, Any]]:
    history = state.setdefault("margin_history", {}).setdefault(asset, [])
    if not isinstance(history, list):
        state["margin_history"][asset] = []
        return state["margin_history"][asset]
    return history


def update_margin_history(
    state: Dict[str, Any],
    asset: str,
    *,
    interest_rate_pct: Optional[float],
    inventory: Optional[float],
    max_borrowable: Optional[float],
) -> None:
    history = margin_history_for_asset(state, asset)
    history.append(
        {
            "ts": iso_z(utc_now()),
            "interest_rate_pct": interest_rate_pct,
            "inventory": inventory,
            "max_borrowable": max_borrowable,
        }
    )
    state["margin_history"][asset] = history[-MARGIN_HISTORY_LIMIT:]


def build_margin_baselines(history: Sequence[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    return {
        "interest_rate_pct": median(item.get("interest_rate_pct") for item in history),
        "inventory": median(item.get("inventory") for item in history),
        "max_borrowable": median(item.get("max_borrowable") for item in history),
    }


def parse_margin_interest_rates(raw: Any) -> Dict[str, float]:
    records = raw if isinstance(raw, list) else raw.get("data", []) if isinstance(raw, dict) else []
    result: Dict[str, float] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        asset = pick_text(item, ["asset"], ["asset"])
        value = pick_number(item, ["nextHourlyInterestRate"], ["interest"])
        if asset and value is not None:
            result[asset] = value * 100.0
    return result


def parse_margin_inventory(raw: Any) -> Dict[str, float]:
    records = raw if isinstance(raw, list) else raw.get("data", []) if isinstance(raw, dict) else []
    result: Dict[str, float] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        asset = pick_text(item, ["asset"], ["asset"])
        value = pick_number(
            item,
            ["availableInventory", "availableBorrowAmount", "inventory"],
            ["inventory", "borrow"],
        )
        if asset and value is not None:
            result[asset] = value
    return result


def parse_margin_max_borrowable(raw: Any) -> Optional[float]:
    if isinstance(raw, dict):
        return pick_number(raw, ["amount", "maxBorrowable", "borrowLimit"], ["borrow"])
    return None


def parse_restricted_assets(raw: Any) -> set[str]:
    records = raw if isinstance(raw, list) else raw.get("data", []) if isinstance(raw, dict) else []
    assets: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        asset = pick_text(item, ["asset"], ["asset"])
        if asset:
            assets.add(asset)
    return assets


def parse_delist_assets(raw: Any) -> set[str]:
    records = raw if isinstance(raw, list) else raw.get("data", []) if isinstance(raw, dict) else []
    assets: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        for value in item.values():
            if isinstance(value, str) and value.endswith("USDT"):
                assets.add(value.replace("USDT", ""))
    return assets


def build_metrics_for_symbol(
    client: BinanceRestClient,
    symbol: str,
    futures_quote_volume: float,
    margin_snapshots: Dict[str, Dict[str, Optional[float]]],
    state: Dict[str, Any],
    restricted_assets: set[str],
    delist_assets: set[str],
    sleep_seconds: float,
    spot_book_ticker_map: Optional[Dict[str, Dict[str, Any]]] = None,
    premium_index_map: Optional[Dict[str, Dict[str, Any]]] = None,
    include_optional_metrics: bool = True,
) -> SymbolMetrics:
    asset = symbol[:-4]
    metrics = SymbolMetrics(symbol=symbol, asset=asset, futures_quote_volume=futures_quote_volume)

    try:
        futures_klines = client.futures_klines(symbol, "1h", 25)
        metrics.futures_ret_1h_pct = parse_last_closed_return(futures_klines)
        metrics.futures_volume_ratio = parse_last_closed_volume_ratio(futures_klines)
    except Exception as exc:
        metrics.errors.append(f"futures_klines: {exc}")
    time.sleep(sleep_seconds)

    try:
        metrics.spot_ret_1h_pct = parse_last_closed_return(client.spot_klines(symbol, "1h", 3))
    except Exception as exc:
        metrics.errors.append(f"spot_klines: {exc}")
    time.sleep(sleep_seconds)

    if spot_book_ticker_map and symbol in spot_book_ticker_map:
        metrics.spot_spread_bps = parse_spread_bps(spot_book_ticker_map[symbol])
    else:
        try:
            metrics.spot_spread_bps = parse_spread_bps(client.spot_book_ticker(symbol))
        except Exception as exc:
            metrics.errors.append(f"spot_book_ticker: {exc}")
        time.sleep(sleep_seconds)

    try:
        metrics.oi_change_1h_pct = parse_open_interest_change(
            client.futures_open_interest_hist(symbol, "1h", 2)
        )
    except Exception as exc:
        metrics.errors.append(f"open_interest_hist: {exc}")
    time.sleep(sleep_seconds)

    try:
        metrics.taker_ratio = parse_single_ratio(
            client.futures_taker_ratio(symbol, "1h", 1),
            ["buySellRatio"],
            ["ratio"],
        )
    except Exception as exc:
        metrics.errors.append(f"taker_ratio: {exc}")
    time.sleep(sleep_seconds)

    if include_optional_metrics:
        try:
            metrics.global_long_short_ratio = parse_single_ratio(
                client.futures_global_long_short_ratio(symbol, "1h", 1),
                ["longShortRatio"],
                ["ratio"],
            )
        except Exception as exc:
            metrics.errors.append(f"global_long_short_ratio: {exc}")
        time.sleep(sleep_seconds)

    try:
        funding_records = client.futures_funding_rate(symbol, 1)
        funding = parse_single_ratio(funding_records, ["fundingRate"], ["funding"])
        metrics.funding_rate_pct = funding * 100.0 if funding is not None else None
    except Exception as exc:
        metrics.errors.append(f"funding_rate: {exc}")
    time.sleep(sleep_seconds)

    if premium_index_map and symbol in premium_index_map:
        premium = premium_index_map[symbol]
        mark_price = pick_number(premium, ["markPrice"], ["markprice"])
        index_price = pick_number(premium, ["indexPrice"], ["indexprice"])
        metrics.premium_pct = pct_change(mark_price, index_price)
    else:
        try:
            premium = client.futures_premium_index(symbol)
            mark_price = pick_number(premium, ["markPrice"], ["markprice"])
            index_price = pick_number(premium, ["indexPrice"], ["indexprice"])
            metrics.premium_pct = pct_change(mark_price, index_price)
        except Exception as exc:
            metrics.errors.append(f"premium_index: {exc}")
        time.sleep(sleep_seconds)

    if asset in restricted_assets:
        metrics.risk_tags.append("restricted_asset")
    if asset in delist_assets:
        metrics.risk_tags.append("delist_watch")

    if metrics.funding_rate_pct is not None and abs(metrics.funding_rate_pct) >= FUNDING_RISK_THRESHOLD_PCT:
        metrics.risk_tags.append("crowded_funding")
    if metrics.premium_pct is not None and abs(metrics.premium_pct) >= PREMIUM_RISK_THRESHOLD_PCT:
        metrics.risk_tags.append("premium_stretched")
    if metrics.spot_spread_bps is not None and metrics.spot_spread_bps >= SPREAD_RISK_THRESHOLD_BPS:
        metrics.risk_tags.append("wide_spread")
    if (
        metrics.futures_ret_1h_pct is not None
        and metrics.spot_ret_1h_pct is not None
        and abs(metrics.futures_ret_1h_pct - metrics.spot_ret_1h_pct) >= RETURN_DIVERGENCE_THRESHOLD_PCT
    ):
        metrics.risk_tags.append("cross_market_divergence")

    margin_snapshot = margin_snapshots.get(asset, {})
    history = margin_history_for_asset(state, asset)
    baselines = build_margin_baselines(history)
    metrics.margin_next_hourly_rate_pct = margin_snapshot.get("interest_rate_pct")
    metrics.margin_inventory = margin_snapshot.get("inventory")
    metrics.margin_max_borrowable = margin_snapshot.get("max_borrowable")
    metrics.margin_rate_jump_pct = pct_change(metrics.margin_next_hourly_rate_pct, baselines["interest_rate_pct"])
    metrics.margin_inventory_change_pct = pct_change(metrics.margin_inventory, baselines["inventory"])
    metrics.margin_max_borrowable_change_pct = pct_change(
        metrics.margin_max_borrowable,
        baselines["max_borrowable"],
    )
    if not client.has_margin_credentials:
        metrics.risk_tags.append("margin_public_only")

    return metrics


def score_long(metrics: SymbolMetrics) -> Optional[Alert]:
    price_score = score_linear(metrics.futures_ret_1h_pct, LONG_PRICE_THRESHOLD, 5.0)
    spot_score = score_linear(metrics.spot_ret_1h_pct, SPOT_CONFIRMATION_THRESHOLD, 4.0)
    volume_score = score_linear(metrics.futures_volume_ratio, VOLUME_RATIO_THRESHOLD, 3.0)
    oi_score = score_linear(metrics.oi_change_1h_pct, OI_CHANGE_THRESHOLD, 5.0)
    taker_score = score_linear(metrics.taker_ratio, LONG_TAKER_THRESHOLD, 1.40)
    raw_score = (price_score + spot_score + volume_score + oi_score + taker_score) / 5.0 * 100.0
    trigger = all(
        [
            metrics.futures_ret_1h_pct is not None and metrics.futures_ret_1h_pct >= LONG_PRICE_THRESHOLD,
            metrics.spot_ret_1h_pct is not None and metrics.spot_ret_1h_pct >= SPOT_CONFIRMATION_THRESHOLD,
            metrics.futures_volume_ratio is not None and metrics.futures_volume_ratio >= VOLUME_RATIO_THRESHOLD,
            metrics.oi_change_1h_pct is not None and metrics.oi_change_1h_pct >= OI_CHANGE_THRESHOLD,
            metrics.taker_ratio is not None and metrics.taker_ratio >= LONG_TAKER_THRESHOLD,
        ]
    )
    if not trigger:
        return None
    score = min(100, 55 + round(raw_score * 0.45))
    severity = "high" if score >= 88 else "medium" if score >= 72 else "low"
    summary = (
        f"[LONG] {metrics.symbol} | 1h {format_pct(metrics.futures_ret_1h_pct)} | "
        f"Vol x{format_ratio(metrics.futures_volume_ratio)} | "
        f"OI {format_pct(metrics.oi_change_1h_pct)} | Taker {format_ratio(metrics.taker_ratio)}"
    )
    reason = "量价齐升，增仓明显，主动买盘占优"
    detail_lines = build_detail_lines(metrics, score, severity)
    return Alert(
        symbol=metrics.symbol,
        signal_type="long_signal",
        severity=severity,
        score=score,
        confidence=metrics.completeness(),
        summary=summary,
        reason=reason,
        detail_lines=detail_lines,
        metrics=alert_metrics_dict(metrics),
        risk_tags=sorted(set(metrics.risk_tags)),
        cooldown_key=f"{metrics.symbol}:long_signal",
    )


def score_short(metrics: SymbolMetrics) -> Optional[Alert]:
    price_value = abs(metrics.futures_ret_1h_pct) if metrics.futures_ret_1h_pct is not None else None
    spot_value = abs(metrics.spot_ret_1h_pct) if metrics.spot_ret_1h_pct is not None else None
    sell_pressure = None
    if metrics.taker_ratio is not None and metrics.taker_ratio > 0:
        sell_pressure = 1.0 / metrics.taker_ratio if metrics.taker_ratio < 1.0 else 0.0
    price_score = score_linear(price_value, abs(SHORT_PRICE_THRESHOLD), 5.0) if (
        metrics.futures_ret_1h_pct is not None and metrics.futures_ret_1h_pct <= SHORT_PRICE_THRESHOLD
    ) else 0.0
    spot_score = score_linear(spot_value, SPOT_CONFIRMATION_THRESHOLD, 4.0) if (
        metrics.spot_ret_1h_pct is not None and metrics.spot_ret_1h_pct <= -SPOT_CONFIRMATION_THRESHOLD
    ) else 0.0
    volume_score = score_linear(metrics.futures_volume_ratio, VOLUME_RATIO_THRESHOLD, 3.0)
    oi_score = score_linear(metrics.oi_change_1h_pct, OI_CHANGE_THRESHOLD, 5.0)
    taker_score = score_linear(sell_pressure, 1.0 / SHORT_TAKER_THRESHOLD, 1.40) if sell_pressure else 0.0
    raw_score = (price_score + spot_score + volume_score + oi_score + taker_score) / 5.0 * 100.0
    trigger = all(
        [
            metrics.futures_ret_1h_pct is not None and metrics.futures_ret_1h_pct <= SHORT_PRICE_THRESHOLD,
            metrics.spot_ret_1h_pct is not None and metrics.spot_ret_1h_pct <= -SPOT_CONFIRMATION_THRESHOLD,
            metrics.futures_volume_ratio is not None and metrics.futures_volume_ratio >= VOLUME_RATIO_THRESHOLD,
            metrics.oi_change_1h_pct is not None and metrics.oi_change_1h_pct >= OI_CHANGE_THRESHOLD,
            metrics.taker_ratio is not None and metrics.taker_ratio <= SHORT_TAKER_THRESHOLD,
        ]
    )
    if not trigger:
        return None
    score = min(100, 55 + round(raw_score * 0.45))
    severity = "high" if score >= 88 else "medium" if score >= 72 else "low"
    summary = (
        f"[SHORT] {metrics.symbol} | 1h {format_pct(metrics.futures_ret_1h_pct)} | "
        f"Vol x{format_ratio(metrics.futures_volume_ratio)} | "
        f"OI {format_pct(metrics.oi_change_1h_pct)} | Taker {format_ratio(metrics.taker_ratio)}"
    )
    reason = "放量下跌，增仓下杀，主动卖盘占优"
    detail_lines = build_detail_lines(metrics, score, severity)
    return Alert(
        symbol=metrics.symbol,
        signal_type="short_signal",
        severity=severity,
        score=score,
        confidence=metrics.completeness(),
        summary=summary,
        reason=reason,
        detail_lines=detail_lines,
        metrics=alert_metrics_dict(metrics),
        risk_tags=sorted(set(metrics.risk_tags)),
        cooldown_key=f"{metrics.symbol}:short_signal",
    )


def score_margin_stress(metrics: SymbolMetrics, enhanced: bool) -> Optional[Alert]:
    if not enhanced:
        return None
    reasons: List[str] = []
    score = 0
    severe_count = 0
    moderate_count = 0
    if metrics.margin_rate_jump_pct is not None and metrics.margin_rate_jump_pct >= MARGIN_RATE_JUMP_THRESHOLD_PCT:
        reasons.append(f"借贷利率跳升 {format_pct(metrics.margin_rate_jump_pct)}")
        score += 35
        moderate_count += 1
        if metrics.margin_rate_jump_pct >= MARGIN_RATE_JUMP_SEVERE_THRESHOLD_PCT:
            severe_count += 1
    if (
        metrics.margin_inventory_change_pct is not None
        and metrics.margin_inventory_change_pct <= MARGIN_INVENTORY_DROP_THRESHOLD_PCT
    ):
        reasons.append(f"库存下降 {format_pct(metrics.margin_inventory_change_pct)}")
        score += 35
        moderate_count += 1
        if metrics.margin_inventory_change_pct <= MARGIN_INVENTORY_DROP_SEVERE_THRESHOLD_PCT:
            severe_count += 1
    if (
        metrics.margin_max_borrowable_change_pct is not None
        and metrics.margin_max_borrowable_change_pct <= MARGIN_BORROW_DROP_THRESHOLD_PCT
    ):
        reasons.append(f"可借额度收缩 {format_pct(metrics.margin_max_borrowable_change_pct)}")
        score += 30
        moderate_count += 1
        if metrics.margin_max_borrowable_change_pct <= MARGIN_BORROW_DROP_SEVERE_THRESHOLD_PCT:
            severe_count += 1
    if not reasons or not (severe_count >= 1 or moderate_count >= 2):
        return None
    severity = "high" if score >= 70 else "medium"
    summary = (
        f"[MARGIN] {metrics.symbol} | Rate {format_number(metrics.margin_next_hourly_rate_pct, 4)}% | "
        f"Inv {format_pct(metrics.margin_inventory_change_pct)} | "
        f"Borrow {format_pct(metrics.margin_max_borrowable_change_pct)}"
    )
    reason = "，".join(reasons)
    detail_lines = build_detail_lines(metrics, score, severity)
    return Alert(
        symbol=metrics.symbol,
        signal_type="margin_stress",
        severity=severity,
        score=score,
        confidence=metrics.completeness(),
        summary=summary,
        reason=reason,
        detail_lines=detail_lines,
        metrics=alert_metrics_dict(metrics),
        risk_tags=sorted(set(metrics.risk_tags)),
        cooldown_key=f"{metrics.symbol}:margin_stress",
    )


def alert_metrics_dict(metrics: SymbolMetrics) -> Dict[str, Any]:
    return {
        "symbol": metrics.symbol,
        "asset": metrics.asset,
        "futures_quote_volume": metrics.futures_quote_volume,
        "futures_ret_1h_pct": metrics.futures_ret_1h_pct,
        "futures_volume_ratio": metrics.futures_volume_ratio,
        "spot_ret_1h_pct": metrics.spot_ret_1h_pct,
        "spot_spread_bps": metrics.spot_spread_bps,
        "oi_change_1h_pct": metrics.oi_change_1h_pct,
        "taker_ratio": metrics.taker_ratio,
        "global_long_short_ratio": metrics.global_long_short_ratio,
        "funding_rate_pct": metrics.funding_rate_pct,
        "premium_pct": metrics.premium_pct,
        "margin_next_hourly_rate_pct": metrics.margin_next_hourly_rate_pct,
        "margin_rate_jump_pct": metrics.margin_rate_jump_pct,
        "margin_inventory": metrics.margin_inventory,
        "margin_inventory_change_pct": metrics.margin_inventory_change_pct,
        "margin_max_borrowable": metrics.margin_max_borrowable,
        "margin_max_borrowable_change_pct": metrics.margin_max_borrowable_change_pct,
        "risk_tags": sorted(set(metrics.risk_tags)),
        "errors": metrics.errors,
    }


def build_detail_lines(metrics: SymbolMetrics, score: int, severity: str) -> List[str]:
    risk_tags = ",".join(sorted(set(visible_risk_tags(metrics.risk_tags)))) or "none"
    return [
        (
            f"spot: ret_1h={format_pct(metrics.spot_ret_1h_pct)} | "
            f"spread={format_number(metrics.spot_spread_bps, 2)}bps"
        ),
        (
            f"futures: ret_1h={format_pct(metrics.futures_ret_1h_pct)} | "
            f"vol_ratio={format_ratio(metrics.futures_volume_ratio)} | "
            f"oi_change={format_pct(metrics.oi_change_1h_pct)} | "
            f"taker={format_ratio(metrics.taker_ratio)} | "
            f"funding={format_number(metrics.funding_rate_pct, 4)}% | "
            f"premium={format_pct(metrics.premium_pct)}"
        ),
        (
            f"margin: next_rate={format_number(metrics.margin_next_hourly_rate_pct, 4)}% | "
            f"inventory_change={format_pct(metrics.margin_inventory_change_pct)} | "
            f"max_borrowable_change={format_pct(metrics.margin_max_borrowable_change_pct)}"
        ),
        f"score={score}/100 | severity={severity} | confidence={metrics.completeness()} | risk_tags={risk_tags}",
    ]


def should_emit_alert(
    state: Dict[str, Any],
    alert: Alert,
    cooldown_hours: float,
    *,
    ignore_cooldown: bool,
) -> bool:
    if ignore_cooldown:
        return True
    previous = state.setdefault("alerts", {}).get(alert.cooldown_key)
    if not isinstance(previous, dict):
        return True
    previous_ts = parse_iso(previous.get("last_sent_at"))
    if previous_ts is None:
        return True
    age_hours = (utc_now() - previous_ts).total_seconds() / 3600.0
    if age_hours >= cooldown_hours:
        return True
    previous_score = int(previous.get("score", 0))
    previous_severity = str(previous.get("severity", "low"))
    if severity_rank(alert.severity) > severity_rank(previous_severity):
        return True
    if alert.score >= previous_score + 10:
        return True
    if previous.get("summary_hash") != hash_text(alert.summary) and alert.score >= previous_score + 5:
        return True
    return False


def mark_alert_emitted(state: Dict[str, Any], alert: Alert) -> None:
    state.setdefault("alerts", {})[alert.cooldown_key] = {
        "last_sent_at": iso_z(utc_now()),
        "score": alert.score,
        "severity": alert.severity,
        "summary_hash": hash_text(alert.summary),
    }


def render_heartbeat(alerts: Sequence[Alert], max_alerts: int) -> str:
    emitted = [alert for alert in alerts if alert.emitted][:max_alerts]
    if not emitted:
        return "HEARTBEAT_OK"
    lines: List[str] = []
    for alert in emitted:
        lines.append(alert.summary)
        lines.append(f"理由：{alert.reason}")
        lines.extend(alert.detail_lines)
        lines.append("")
    return "\n".join(lines).strip()


def render_alert(alerts: Sequence[Alert], max_alerts: int) -> str:
    selected = alerts[:max_alerts]
    if not selected:
        return "NO_ALERTS"
    lines: List[str] = []
    for alert in selected:
        lines.append(alert.summary)
        lines.append(f"理由：{alert.reason}")
        lines.extend(alert.detail_lines)
        lines.append("")
    return "\n".join(lines).strip()


def render_report(
    *,
    scan_time: str,
    margin_mode: str,
    top_symbols: Sequence[str],
    alerts: Sequence[Alert],
    suppressed_alerts: Sequence[Alert],
    symbol_metrics: Sequence[SymbolMetrics],
    errors: Sequence[str],
) -> str:
    lines = [
        f"Scan Time: {scan_time}",
        f"Margin Mode: {margin_mode}",
        f"Top Symbols: {', '.join(top_symbols)}",
        "",
        "## Alerts",
    ]
    if not alerts:
        lines.append("No active alerts.")
    else:
        for alert in alerts:
            prefix = "EMIT" if alert.emitted else "HOLD"
            lines.append(f"- {prefix} {alert.summary}")
            lines.append(f"  理由: {alert.reason}")
    lines.append("")
    lines.append("## Suppressed By Cooldown")
    if not suppressed_alerts:
        lines.append("None")
    else:
        for alert in suppressed_alerts:
            lines.append(f"- {alert.summary}")
    lines.append("")
    lines.append("## Metrics")
    for metrics in symbol_metrics:
        risk_tags = ",".join(sorted(set(visible_risk_tags(metrics.risk_tags)))) or "none"
        lines.append(
            (
                f"- {metrics.symbol}: ret_1h={format_pct(metrics.futures_ret_1h_pct)} | "
                f"vol_ratio={format_ratio(metrics.futures_volume_ratio)} | "
                f"oi={format_pct(metrics.oi_change_1h_pct)} | "
                f"taker={format_ratio(metrics.taker_ratio)} | "
                f"margin_rate={format_number(metrics.margin_next_hourly_rate_pct, 4)}% | "
                f"risk_tags={risk_tags}"
            )
        )
        if metrics.errors:
            lines.append(f"  errors: {'; '.join(metrics.errors)}")
    if errors:
        lines.append("")
        lines.append("## Global Errors")
        lines.extend(f"- {error}" for error in errors)
    return "\n".join(lines).strip()


def render_state_summary(state: Dict[str, Any]) -> str:
    lines = [
        f"Updated At: {state.get('updated_at') or 'never'}",
        f"Top Symbols: {', '.join(state.get('top_symbols', [])) or 'none'}",
    ]
    alerts = state.get("alerts", {})
    lines.append(f"Cooldown Entries: {len(alerts) if isinstance(alerts, dict) else 0}")
    if isinstance(alerts, dict) and alerts:
        lines.append("Recent Cooldowns:")
        sorted_alerts = sorted(
            alerts.items(),
            key=lambda item: item[1].get("last_sent_at", ""),
            reverse=True,
        )
        for key, value in sorted_alerts[:10]:
            lines.append(
                f"- {key} | sent={value.get('last_sent_at', 'n/a')} | "
                f"score={value.get('score', 'n/a')} | severity={value.get('severity', 'n/a')}"
            )
    margin_history = state.get("margin_history", {})
    if isinstance(margin_history, dict):
        lines.append(f"Margin History Assets: {len(margin_history)}")
    return "\n".join(lines)


def build_margin_snapshots(
    client: BinanceRestClient,
    assets: Sequence[str],
    global_errors: List[str],
    *,
    include_public_margin_tags: bool,
) -> Tuple[Dict[str, Dict[str, Optional[float]]], set[str], set[str]]:
    snapshots: Dict[str, Dict[str, Optional[float]]] = {asset: {} for asset in assets}
    restricted_assets: set[str] = set()
    delist_assets: set[str] = set()

    if include_public_margin_tags:
        try:
            restricted_assets = parse_restricted_assets(client.margin_restricted_assets())
        except Exception as exc:
            global_errors.append(f"restricted_assets: {exc}")
        try:
            delist_assets = parse_delist_assets(client.margin_delist_schedule())
        except Exception as exc:
            global_errors.append(f"delist_schedule: {exc}")

    if not client.has_margin_credentials:
        return snapshots, restricted_assets, delist_assets

    try:
        interest_rates = parse_margin_interest_rates(client.margin_next_hourly_interest_rate(assets))
        for asset, value in interest_rates.items():
            snapshots.setdefault(asset, {})["interest_rate_pct"] = value
    except Exception as exc:
        global_errors.append(f"margin_interest_rate: {exc}")

    try:
        inventories = parse_margin_inventory(client.margin_available_inventory())
        for asset, value in inventories.items():
            snapshots.setdefault(asset, {})["inventory"] = value
    except Exception as exc:
        global_errors.append(f"margin_available_inventory: {exc}")

    for asset in assets:
        try:
            borrowable = parse_margin_max_borrowable(client.margin_max_borrowable(asset))
            snapshots.setdefault(asset, {})["max_borrowable"] = borrowable
        except Exception as exc:
            global_errors.append(f"margin_max_borrowable({asset}): {exc}")

    return snapshots, restricted_assets, delist_assets


def resolve_requested_symbols(
    requested_symbols: Sequence[str],
    futures_tickers: Sequence[Dict[str, Any]],
    spot_symbols: set[str],
    global_errors: List[str],
) -> List[Tuple[str, float]]:
    ticker_map = {
        str(item.get("symbol", "")): to_float(item.get("quoteVolume")) or 0.0
        for item in futures_tickers
        if isinstance(item, dict)
    }
    selected: List[Tuple[str, float]] = []
    for symbol in requested_symbols:
        if symbol not in spot_symbols:
            global_errors.append(f"requested_symbol_missing_spot:{symbol}")
            continue
        if symbol not in ticker_map:
            global_errors.append(f"requested_symbol_missing_futures:{symbol}")
            continue
        selected.append((symbol, ticker_map[symbol]))
    return selected


def run_scan(args: argparse.Namespace) -> Dict[str, Any]:
    started_at = time.monotonic()
    timings: Dict[str, float] = {}
    phase_started = started_at

    def mark_phase(name: str) -> None:
        nonlocal phase_started
        now = time.monotonic()
        timings[name] = round(now - phase_started, 3)
        phase_started = now

    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_SECRET_KEY")
    client = BinanceRestClient(
        api_key=api_key,
        api_secret=api_secret,
        timeout=args.timeout,
        ca_bundle=args.ca_bundle,
        allow_insecure_ssl=args.allow_insecure_ssl,
    )
    state_path = pathlib.Path(args.state_path).expanduser().resolve()
    state = load_state(state_path)
    global_errors: List[str] = []
    scan_time = iso_z(utc_now())
    requested_symbols = parse_symbols_arg(args.symbols)
    spot_book_ticker_map: Dict[str, Dict[str, Any]] = {}

    try:
        spot_book_ticker_records = client.spot_book_ticker_all()
        spot_book_ticker_map = map_records_by_symbol(spot_book_ticker_records)
        spot_symbols = spot_symbols_from_book_tickers(spot_book_ticker_records)
        if not spot_symbols:
            raise RuntimeError("spot bookTicker returned no symbols")
    except Exception as primary_exc:
        try:
            exchange_info = client.spot_exchange_info()
            spot_symbols = spot_symbols_from_exchange_info(exchange_info)
        except Exception as fallback_exc:
            raise RuntimeError(
                f"Failed to load spot market universe: primary={primary_exc}; fallback={fallback_exc}"
            ) from fallback_exc
    mark_phase("spot_market_universe")

    try:
        futures_tickers = client.futures_ticker_24h()
        if not isinstance(futures_tickers, list):
            raise RuntimeError("futures ticker response is not a list")
    except Exception as exc:
        raise RuntimeError(f"Failed to load futures 24h tickers: {exc}") from exc
    mark_phase("futures_ticker_24h")

    if requested_symbols:
        top_pairs = resolve_requested_symbols(requested_symbols, futures_tickers, spot_symbols, global_errors)
        if not top_pairs:
            raise RuntimeError("No valid symbols remained after validation.")
    else:
        top_pairs = select_top_symbols(
            futures_tickers,
            spot_symbols,
            args.topn,
            state.get("top_symbols", []),
        )
    top_symbols = [symbol for symbol, _ in top_pairs]
    assets = [symbol[:-4] for symbol in top_symbols]
    heartbeat_mode = args.format == "heartbeat"

    margin_snapshots, restricted_assets, delist_assets = build_margin_snapshots(
        client,
        assets,
        global_errors,
        include_public_margin_tags=not heartbeat_mode and client.has_margin_credentials,
    )
    mark_phase("margin_snapshot")

    premium_index_map: Dict[str, Dict[str, Any]] = {}
    try:
        premium_index_map = map_records_by_symbol(client.futures_premium_index_all())
    except Exception as exc:
        global_errors.append(f"premium_index_all: {exc}")
    mark_phase("batch_maps")

    symbol_metrics: List[SymbolMetrics] = []
    alerts: List[Alert] = []
    worker_count = max(1, min(args.workers, len(top_pairs)))
    if worker_count == 1:
        for symbol, quote_volume in top_pairs:
            metrics = build_metrics_for_symbol(
                client,
                symbol,
                quote_volume,
                margin_snapshots,
                state,
                restricted_assets,
                delist_assets,
                args.sleep,
                spot_book_ticker_map=spot_book_ticker_map,
                premium_index_map=premium_index_map,
                include_optional_metrics=not heartbeat_mode,
            )
            symbol_metrics.append(metrics)
    else:
        futures = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for symbol, quote_volume in top_pairs:
                future = executor.submit(
                    build_metrics_for_symbol,
                    client,
                    symbol,
                    quote_volume,
                    margin_snapshots,
                    state,
                    restricted_assets,
                    delist_assets,
                    args.sleep,
                    spot_book_ticker_map,
                    premium_index_map,
                    not heartbeat_mode,
                )
                futures[future] = symbol
            for future in as_completed(futures):
                symbol_metrics.append(future.result())

    symbol_metrics.sort(key=lambda item: top_symbols.index(item.symbol))
    for metrics in symbol_metrics:
        for builder in (score_long, score_short):
            alert = builder(metrics)
            if alert is not None:
                alerts.append(alert)
        margin_alert = score_margin_stress(metrics, client.has_margin_credentials)
        if margin_alert is not None:
            alerts.append(margin_alert)
    mark_phase("symbol_metrics")

    alerts.sort(key=lambda alert: (severity_rank(alert.severity), alert.score), reverse=True)
    suppressed_alerts: List[Alert] = []
    for alert in alerts:
        if should_emit_alert(state, alert, args.cooldown_hours, ignore_cooldown=args.ignore_cooldown):
            alert.emitted = True
            mark_alert_emitted(state, alert)
        else:
            suppressed_alerts.append(alert)

    for asset, snapshot in margin_snapshots.items():
        update_margin_history(
            state,
            asset,
            interest_rate_pct=snapshot.get("interest_rate_pct"),
            inventory=snapshot.get("inventory"),
            max_borrowable=snapshot.get("max_borrowable"),
        )
    state["top_symbols"] = top_symbols
    save_state(state_path, state)
    mark_phase("state_saved")

    payload = {
        "scan_time": scan_time,
        "mode": args.format,
        "selection_mode": "manual" if requested_symbols else "topn",
        "requested_symbols": requested_symbols,
        "margin_mode": "enhanced" if client.has_margin_credentials else "public",
        "top_symbols": top_symbols,
        "alerts": [
            {
                "symbol": alert.symbol,
                "signal_type": alert.signal_type,
                "severity": alert.severity,
                "score": alert.score,
                "confidence": alert.confidence,
                "summary": alert.summary,
                "reason": alert.reason,
                "detail_lines": alert.detail_lines,
                "risk_tags": alert.risk_tags,
                "metrics": alert.metrics,
                "emitted": alert.emitted,
            }
            for alert in alerts
        ],
        "suppressed_alerts": [
            {
                "symbol": alert.symbol,
                "signal_type": alert.signal_type,
                "severity": alert.severity,
                "score": alert.score,
                "summary": alert.summary,
            }
            for alert in suppressed_alerts
        ],
        "metrics": [alert_metrics_dict(metrics) for metrics in symbol_metrics],
        "errors": global_errors,
        "state_path": str(state_path),
    }
    if args.debug_timings:
        payload["timings"] = timings
        payload["workers"] = worker_count
        payload["timings"]["total"] = round(time.monotonic() - started_at, 3)
    return payload


def run_state_show(args: argparse.Namespace) -> int:
    state_path = pathlib.Path(args.state_path).expanduser().resolve()
    state = load_state(state_path)
    if args.format == "json":
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(render_state_summary(state))
    return 0


def run_state_reset(args: argparse.Namespace) -> int:
    state_path = pathlib.Path(args.state_path).expanduser().resolve()
    state = load_state(state_path)
    if args.scope == "all":
        new_state = {
            "schema_version": STATE_VERSION,
            "updated_at": None,
            "top_symbols": [],
            "alerts": {},
            "margin_history": {},
        }
    else:
        new_state = dict(state)
        new_state["alerts"] = {}
        new_state["updated_at"] = iso_z(utc_now())
    save_state(state_path, new_state)
    print(
        json.dumps(
            {
                "ok": True,
                "scope": args.scope,
                "state_path": str(state_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def print_scan_output(result: Dict[str, Any], output_format: str, max_alerts: int) -> None:
    alerts = [
        Alert(
            symbol=item["symbol"],
            signal_type=item["signal_type"],
            severity=item["severity"],
            score=item["score"],
            confidence=item["confidence"],
            summary=item["summary"],
            reason=item["reason"],
            detail_lines=item["detail_lines"],
            metrics=item["metrics"],
            risk_tags=item["risk_tags"],
            cooldown_key=f"{item['symbol']}:{item['signal_type']}",
            emitted=item["emitted"],
        )
        for item in result["alerts"]
    ]
    suppressed = [alert for alert in alerts if not alert.emitted]
    metrics = []
    for item in result["metrics"]:
        metrics.append(
            SymbolMetrics(
                symbol=item["symbol"],
                asset=item["asset"],
                futures_quote_volume=item["futures_quote_volume"],
                futures_ret_1h_pct=item["futures_ret_1h_pct"],
                futures_volume_ratio=item["futures_volume_ratio"],
                spot_ret_1h_pct=item["spot_ret_1h_pct"],
                spot_spread_bps=item["spot_spread_bps"],
                oi_change_1h_pct=item["oi_change_1h_pct"],
                taker_ratio=item["taker_ratio"],
                global_long_short_ratio=item["global_long_short_ratio"],
                funding_rate_pct=item["funding_rate_pct"],
                premium_pct=item["premium_pct"],
                margin_next_hourly_rate_pct=item["margin_next_hourly_rate_pct"],
                margin_rate_jump_pct=item["margin_rate_jump_pct"],
                margin_inventory=item["margin_inventory"],
                margin_inventory_change_pct=item["margin_inventory_change_pct"],
                margin_max_borrowable=item["margin_max_borrowable"],
                margin_max_borrowable_change_pct=item["margin_max_borrowable_change_pct"],
                risk_tags=item["risk_tags"],
                errors=item["errors"],
            )
        )

    if output_format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if output_format == "heartbeat":
        print(render_heartbeat(alerts, max_alerts))
        return
    if output_format == "alert":
        print(render_alert(alerts, max_alerts))
        return
    print(
        render_report(
            scan_time=result["scan_time"],
            margin_mode=result["margin_mode"],
            top_symbols=result["top_symbols"],
            alerts=alerts,
            suppressed_alerts=suppressed,
            symbol_metrics=metrics,
            errors=result["errors"],
        )
    )


def run_doctor(args: argparse.Namespace) -> int:
    state_path = pathlib.Path(args.state_path).expanduser().resolve()
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_SECRET_KEY")
    payload = {
        "python": sys.version.split()[0],
        "state_path": str(state_path),
        "state_exists": state_path.exists(),
        "margin_mode": "enhanced" if api_key and api_secret else "public",
        "has_api_key": bool(api_key),
        "has_api_secret": bool(api_secret),
        "ca_bundle": args.ca_bundle,
        "allow_insecure_ssl": args.allow_insecure_ssl,
        "spot_base_url": SPOT_BASE_URL,
        "futures_base_url": FUTURES_BASE_URL,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hourly Binance market watch for OpenClaw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-path", default=str(default_state_path()))
    common.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    common.add_argument("--ca-bundle", default=os.environ.get("BINANCE_CA_BUNDLE", ""))
    common.add_argument("--allow-insecure-ssl", action="store_true")

    doctor = subparsers.add_parser("doctor", parents=[common], help="Show runtime configuration")
    doctor.set_defaults(func=run_doctor)

    scan = subparsers.add_parser("scan", parents=[common], help="Run one monitoring scan")
    scan.add_argument("--topn", type=int, default=DEFAULT_TOPN)
    scan.add_argument("--symbols", default="", help="Comma-separated symbols like BTCUSDT,ETHUSDT")
    scan.add_argument("--format", choices=["heartbeat", "alert", "report", "json"], default="heartbeat")
    scan.add_argument("--cooldown-hours", type=float, default=DEFAULT_COOLDOWN_HOURS)
    scan.add_argument("--ignore-cooldown", action="store_true")
    scan.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS)
    scan.add_argument("--max-alerts", type=int, default=DEFAULT_MAX_ALERTS)
    scan.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    scan.add_argument("--debug-timings", action="store_true")

    state = subparsers.add_parser("state", parents=[common], help="Inspect or reset monitor state")
    state_subparsers = state.add_subparsers(dest="state_command", required=True)

    state_show = state_subparsers.add_parser("show", parents=[common], help="Show current state")
    state_show.add_argument("--format", choices=["summary", "json"], default="summary")
    state_show.set_defaults(func=run_state_show)

    state_reset = state_subparsers.add_parser("reset", parents=[common], help="Reset state")
    state_reset.add_argument("--scope", choices=["alerts", "all"], default="alerts")
    state_reset.set_defaults(func=run_state_reset)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func in (run_doctor, run_state_show, run_state_reset):
        return func(args)
    if args.command == "scan":
        try:
            result = run_scan(args)
        except RuntimeError as exc:
            if args.format == "json":
                print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            else:
                print(f"MONITOR_ERROR: {exc}")
            return 1
        print_scan_output(result, args.format, args.max_alerts)
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
