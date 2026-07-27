#!/usr/bin/env python3
"""
Kraken Signal Bot 3.0 Definitivo

- Solo segnali: non invia ordini e non richiede chiavi Kraken.
- Dati pubblici Kraken REST.
- Analisi multi-timeframe 1h / 15m / 5m.
- Filtro regime BTC, spread, volume, ATR, qualità e R/R netto.
- Cooldown e deduplicazione.
- Paper trading persistente con riepilogo giornaliero.
- Compatibile con GitHub Actions e VPS.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

VERSION = "3.0.0"
KRAKEN_API = "https://api.kraken.com/0/public"
ROME = ZoneInfo("Europe/Rome")
ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
STATE_FILE = ROOT / "state.json"
HISTORY_FILE = ROOT / "signal_history.csv"
STATE_CHANGED_FILE = ROOT / ".state_changed"


@dataclass(frozen=True)
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    vwap: float
    volume: float
    count: int


@dataclass
class Setup:
    asset: str
    pair_label: str
    api_pair: str
    price: float
    resistance: float
    entry_low: float
    entry_high: float
    stop: float
    tp1: float
    tp2: float
    stop_pct: float
    rr1_net: float
    rr2_net: float
    spread_pct: float
    volume_ratio: float
    atr_pct: float
    breakout_extension_pct: float
    score: float
    reasons: List[str]
    candle_ts: int
    signal_key: str


class KrakenClient:
    def __init__(self, timeout: int = 18, retries: int = 4) -> None:
        self.timeout = timeout
        self.retries = retries
        self.cache: Dict[Tuple[str, str], Any] = {}

    def public(self, method: str, params: Optional[Dict[str, Any]] = None, *, use_cache: bool = False) -> Dict[str, Any]:
        params = params or {}
        cache_key = (method, urllib.parse.urlencode(sorted(params.items())))
        if use_cache and cache_key in self.cache:
            return self.cache[cache_key]

        query = urllib.parse.urlencode(params)
        url = f"{KRAKEN_API}/{method}" + (f"?{query}" if query else "")
        last_exc: Optional[Exception] = None

        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": f"KrakenSignalBot/{VERSION}"})
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                errors = payload.get("error") or []
                if errors:
                    raise RuntimeError("; ".join(map(str, errors)))
                result = payload["result"]
                if use_cache:
                    self.cache[cache_key] = result
                return result
            except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt + 1 < self.retries:
                    time.sleep((1.2 ** attempt) + random.random() * 0.35)

        raise RuntimeError(f"Kraken {method} non disponibile dopo {self.retries} tentativi: {last_exc}")

    def system_online(self) -> bool:
        result = self.public("SystemStatus", use_cache=True)
        return str(result.get("status", "")).lower() == "online"

    def asset_pairs(self) -> Dict[str, Any]:
        return self.public("AssetPairs", use_cache=True)

    def tickers(self) -> Dict[str, Any]:
        return self.public("Ticker", use_cache=True)

    def ohlc(self, pair: str, interval: int) -> List[Candle]:
        result = self.public("OHLC", {"pair": pair, "interval": interval})
        key = next(k for k in result if k != "last")
        rows = result[key]
        # Kraken documenta che l'ultima riga è la candela corrente non completata.
        rows = rows[:-1]
        return [
            Candle(
                ts=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
                close=float(r[4]), vwap=float(r[5]), volume=float(r[6]), count=int(r[7])
            )
            for r in rows
        ]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def atr(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    tr: List[float] = []
    for i in range(1, len(candles)):
        previous_close = candles[i - 1].close
        current = candles[i]
        tr.append(max(current.high - current.low, abs(current.high - previous_close), abs(current.low - previous_close)))
    return sum(tr[-period:]) / period


def median(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if value >= 1:
        return f"{value:.4f}".replace(".", ",")
    if value >= 0.01:
        return f"{value:.6f}".replace(".", ",")
    return f"{value:.10f}".rstrip("0").replace(".", ",")


def resolve_pairs(cfg: Dict[str, Any], raw_pairs: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    by_ws: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    by_alt: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for api_pair, meta in raw_pairs.items():
        ws = str(meta.get("wsname", "")).upper()
        alt = str(meta.get("altname", "")).upper()
        status = str(meta.get("status", "online")).lower()
        if status not in {"online", "post_only", "limit_only"}:
            continue
        if ws:
            by_ws[ws] = (api_pair, meta)
        if alt:
            by_alt[alt] = (api_pair, meta)

    resolved: Dict[str, Dict[str, str]] = {}
    for item in cfg["assets"]:
        asset = str(item["asset"]).upper()
        for candidate in item["pair_candidates"]:
            normalized = str(candidate).upper()
            found = by_ws.get(normalized) or by_alt.get(normalized.replace("/", ""))
            if found:
                api_pair, meta = found
                resolved[asset] = {
                    "api_pair": api_pair,
                    "pair_label": normalized,
                    "altname": str(meta.get("altname", api_pair)),
                }
                break
    return resolved


def ticker_metrics(ticker: Dict[str, Any]) -> Tuple[float, float, float]:
    ask = float(ticker["a"][0])
    bid = float(ticker["b"][0])
    last = float(ticker["c"][0])
    mid = (ask + bid) / 2.0
    spread_pct = ((ask - bid) / mid * 100.0) if mid else math.inf
    return last, spread_pct, mid


def find_ticker(tickers: Dict[str, Any], pair_info: Dict[str, str]) -> Optional[Dict[str, Any]]:
    for key in (pair_info["api_pair"], pair_info["altname"], pair_info["pair_label"].replace("/", "")):
        if key in tickers:
            return tickers[key]
    return None


def trend_snapshot(candles: List[Candle]) -> Dict[str, float | bool]:
    closes = [c.close for c in candles]
    if len(closes) < 55:
        return {"valid": False}
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    slope = (e20[-1] / e20[-4] - 1.0) if e20[-4] else 0.0
    return {
        "valid": True,
        "close": closes[-1],
        "ema20": e20[-1],
        "ema50": e50[-1],
        "slope": slope,
        "bullish": closes[-1] > e20[-1] > e50[-1] and slope > 0,
    }


def btc_market_regime(client: KrakenClient, btc_pair: Dict[str, str], cfg: Dict[str, Any]) -> Dict[str, Any]:
    c1h = client.ohlc(btc_pair["api_pair"], 60)
    snap = trend_snapshot(c1h)
    if not snap.get("valid"):
        return {"favourable": False, "reason": "Dati BTC insufficienti"}
    tolerance = float(cfg["strategy"]["btc_ema50_tolerance_pct"]) / 100.0
    favourable = bool(
        snap["close"] > snap["ema20"]
        and snap["ema20"] >= snap["ema50"] * (1.0 - tolerance)
        and snap["slope"] >= -0.0005
    )
    return {"favourable": favourable, **snap}


def preliminary_scan(client: KrakenClient, pair: Dict[str, str], cfg: Dict[str, Any], *, btc_favourable: bool, is_btc: bool) -> Optional[Dict[str, Any]]:
    c1h = client.ohlc(pair["api_pair"], 60)
    c15 = client.ohlc(pair["api_pair"], 15)
    if len(c1h) < 60 or len(c15) < 60:
        return None

    t1h = trend_snapshot(c1h)
    t15 = trend_snapshot(c15)
    if not t1h.get("bullish") or not t15.get("bullish"):
        return None
    if not (btc_favourable or is_btc or not cfg["strategy"]["require_btc_confirmation_for_alts"]):
        return None

    lookback = int(cfg["strategy"]["resistance_lookback_15m"])
    resistance = max(c.high for c in c15[-lookback - 1:-1])
    close15 = c15[-1].close
    distance_pct = (resistance / close15 - 1.0) * 100.0
    if distance_pct < -float(cfg["strategy"]["max_prebreak_above_resistance_pct"]):
        return None
    if distance_pct > float(cfg["strategy"]["max_distance_to_resistance_pct"]):
        return None

    return {"c1h": c1h, "c15": c15, "t1h": t1h, "t15": t15, "resistance": resistance}


def net_rr(entry: float, stop: float, target: float, fee_each_side: float) -> float:
    risk_pct = (entry - stop) / entry
    reward_pct = (target - entry) / entry
    round_trip = 2.0 * fee_each_side
    net_risk = risk_pct + round_trip
    net_reward = reward_pct - round_trip
    return net_reward / net_risk if net_risk > 0 and net_reward > 0 else 0.0


def evaluate_setup(
    client: KrakenClient,
    asset: str,
    pair: Dict[str, str],
    ticker: Dict[str, Any],
    prelim: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Optional[Setup]:
    strategy = cfg["strategy"]
    c5 = client.ohlc(pair["api_pair"], 5)
    if len(c5) < 70:
        return None

    last = c5[-1]
    prior = c5[-22:-1]
    resistance = float(prelim["resistance"])
    price, spread_pct, _ = ticker_metrics(ticker)
    a = atr(c5, 14)
    if a <= 0 or price <= 0:
        return None

    average_volume = sum(c.volume for c in prior) / len(prior)
    volume_ratio = last.volume / average_volume if average_volume else 0.0
    range_ = max(last.high - last.low, 1e-12)
    body_ratio = abs(last.close - last.open) / range_
    close_position = (last.close - last.low) / range_
    breakout_extension_pct = (price / resistance - 1.0) * 100.0
    atr_pct = a / price * 100.0

    hard_conditions = [
        last.close > resistance,
        price >= resistance * (1.0 - float(strategy["max_retest_slippage_pct"]) / 100.0),
        breakout_extension_pct <= float(strategy["max_breakout_extension_pct"]),
        volume_ratio >= float(strategy["min_volume_ratio"]),
        spread_pct <= float(strategy["max_spread_pct"]),
        body_ratio >= float(strategy["min_body_ratio"]),
        close_position >= float(strategy["min_close_position"]),
        float(strategy["min_atr_pct"]) <= atr_pct <= float(strategy["max_atr_pct"]),
    ]
    if not all(hard_conditions):
        return None

    stop_buffer = max(
        resistance * float(strategy["min_stop_buffer_pct"]) / 100.0,
        a * float(strategy["atr_stop_multiplier"]),
    )
    stop = resistance - stop_buffer
    risk = price - stop
    if risk <= 0:
        return None

    tp1 = price + risk * float(strategy["tp1_r_multiple"])
    tp2 = price + risk * float(strategy["tp2_r_multiple"])
    fee = float(strategy["estimated_fee_each_side"])
    rr1 = net_rr(price, stop, tp1, fee)
    rr2 = net_rr(price, stop, tp2, fee)
    if rr2 < float(strategy["min_net_rr2"]):
        return None

    score = 0.0
    reasons: List[str] = []
    score += 2.0; reasons.append("Trend 1h rialzista")
    score += 1.4; reasons.append("Struttura 15m positiva")
    score += 1.8; reasons.append("Breakout 5m confermato a candela chiusa")

    volume_points = min(1.6, max(0.0, (volume_ratio - 1.0) * 1.25))
    score += volume_points
    reasons.append(f"Volume {volume_ratio:.2f}× la media")

    if spread_pct <= float(strategy["excellent_spread_pct"]):
        score += 0.8
    else:
        score += 0.4
    reasons.append(f"Spread {spread_pct:.3f}%")

    if close_position >= 0.82 and body_ratio >= 0.60:
        score += 0.9
    else:
        score += 0.5
    reasons.append("Candela di conferma solida")

    if breakout_extension_pct <= float(strategy["ideal_breakout_extension_pct"]):
        score += 0.8
        reasons.append("Ingresso vicino al livello rotto")
    else:
        score += 0.35

    if rr2 >= 1.8:
        score += 0.7
    elif rr2 >= 1.4:
        score += 0.4
    reasons.append(f"R/R netto TP2 {rr2:.2f}")

    score = min(10.0, round(score, 1))
    if score < float(strategy["minimum_score"]):
        return None

    entry_padding = min(a * 0.18, price * 0.0015)
    entry_low = max(resistance, price - entry_padding)
    entry_high = min(resistance * (1.0 + float(strategy["max_breakout_extension_pct"]) / 100.0), price + entry_padding)
    stop_pct = (price - stop) / price * 100.0
    key = f"{asset}:{last.ts}:{round(resistance, 10)}"

    return Setup(
        asset=asset,
        pair_label=pair["pair_label"],
        api_pair=pair["api_pair"],
        price=price,
        resistance=resistance,
        entry_low=entry_low,
        entry_high=entry_high,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        stop_pct=stop_pct,
        rr1_net=rr1,
        rr2_net=rr2,
        spread_pct=spread_pct,
        volume_ratio=volume_ratio,
        atr_pct=atr_pct,
        breakout_extension_pct=breakout_extension_pct,
        score=score,
        reasons=reasons,
        candle_ts=last.ts,
        signal_key=key,
    )


def telegram_send(text: str, *, silent_if_missing: bool = True) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        if not silent_if_missing:
            raise RuntimeError("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancanti")
        print("[WARN] Telegram non configurato; messaggio stampato soltanto.")
        print(text)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={"User-Agent": f"KrakenSignalBot/{VERSION}"})
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")
    return True


def chunks(text: str, limit: int = 3900) -> List[str]:
    if len(text) <= limit:
        return [text]
    out: List[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            out.append(current)
            current = block
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def position_size_example(setup: Setup, cfg: Dict[str, Any]) -> Tuple[float, float]:
    capital = float(cfg["risk"]["example_capital_eur"])
    risk_pct = float(cfg["risk"]["risk_per_trade_pct"])
    max_position_pct = float(cfg["risk"]["max_position_pct"])
    euro_risk = capital * risk_pct / 100.0
    theoretical = euro_risk / (setup.stop_pct / 100.0) if setup.stop_pct > 0 else 0.0
    cap = capital * max_position_pct / 100.0
    return euro_risk, min(theoretical, cap)


def format_signal(setup: Setup, cfg: Dict[str, Any]) -> str:
    euro_risk, position = position_size_example(setup, cfg)
    reasons = "\n".join(f"• {x}" for x in setup.reasons)
    return (
        f"🟢 <b>KRAKEN SETUP — {setup.pair_label}</b>\n"
        f"<b>Qualità:</b> {setup.score:.1f}/10\n"
        f"<b>Zona ingresso:</b> € {fmt_price(setup.entry_low)} – € {fmt_price(setup.entry_high)}\n"
        f"<b>Prezzo rilevato:</b> € {fmt_price(setup.price)}\n"
        f"<b>Stop tecnico:</b> € {fmt_price(setup.stop)} (-{setup.stop_pct:.2f}%)\n"
        f"<b>TP1:</b> € {fmt_price(setup.tp1)} | R/R netto {setup.rr1_net:.2f}\n"
        f"<b>TP2:</b> € {fmt_price(setup.tp2)} | R/R netto {setup.rr2_net:.2f}\n"
        f"<b>Volume:</b> {setup.volume_ratio:.2f}× | <b>Spread:</b> {setup.spread_pct:.3f}%\n"
        f"<b>Volatilità ATR:</b> {setup.atr_pct:.2f}%\n\n"
        f"<b>Perché è stato selezionato</b>\n{reasons}\n\n"
        f"<b>Esempio prudente su € {cfg['risk']['example_capital_eur']:.0f}</b>\n"
        f"Rischio massimo teorico: € {euro_risk:.2f}\n"
        f"Posizione teorica massima: € {position:.2f}\n\n"
        "⚠️ Segnale informativo, non ordine automatico. Controlla sempre il prezzo su Kraken Pro."
    )


def history_append(setup: Setup, now: datetime) -> None:
    exists = HISTORY_FILE.exists()
    fields = [
        "signal_key", "opened_at_utc", "asset", "pair", "score", "entry", "stop", "tp1", "tp2",
        "spread_pct", "volume_ratio", "atr_pct", "rr2_net", "status", "closed_at_utc", "exit_price", "result_r"
    ]
    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({
            "signal_key": setup.signal_key,
            "opened_at_utc": now.isoformat(),
            "asset": setup.asset,
            "pair": setup.pair_label,
            "score": setup.score,
            "entry": setup.price,
            "stop": setup.stop,
            "tp1": setup.tp1,
            "tp2": setup.tp2,
            "spread_pct": setup.spread_pct,
            "volume_ratio": setup.volume_ratio,
            "atr_pct": setup.atr_pct,
            "rr2_net": setup.rr2_net,
            "status": "OPEN_SIMULATED",
            "closed_at_utc": "",
            "exit_price": "",
            "result_r": "",
        })


def update_history_row(signal_key: str, status: str, exit_price: float, result_r: float, closed_at: datetime) -> None:
    if not HISTORY_FILE.exists():
        return
    with HISTORY_FILE.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return
    fields = list(rows[0].keys())
    for row in rows:
        if row.get("signal_key") == signal_key:
            row["status"] = status
            row["closed_at_utc"] = closed_at.isoformat()
            row["exit_price"] = str(exit_price)
            row["result_r"] = str(round(result_r, 3))
    tmp = HISTORY_FILE.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(HISTORY_FILE)


def update_paper_trades(client: KrakenClient, state: Dict[str, Any], resolved: Dict[str, Dict[str, str]], cfg: Dict[str, Any]) -> List[str]:
    notifications: List[str] = []
    now = utc_now()
    max_hours = float(cfg["paper_trading"]["max_trade_duration_hours"])

    for trade in state.setdefault("paper_trades", []):
        if trade.get("status") != "OPEN_SIMULATED":
            continue
        asset = trade["asset"]
        pair = resolved.get(asset)
        if not pair:
            continue
        opened_at = parse_dt(trade.get("opened_at")) or now
        try:
            candles = client.ohlc(pair["api_pair"], 5)
            relevant = [c for c in candles if c.ts > int(trade.get("candle_ts", 0))]
            outcome: Optional[Tuple[str, float, float]] = None
            for candle in relevant:
                hit_stop = candle.low <= float(trade["stop"])
                hit_tp2 = candle.high >= float(trade["tp2"])
                hit_tp1 = candle.high >= float(trade["tp1"])
                if hit_stop and (hit_tp1 or hit_tp2):
                    outcome = ("STOP_AMBIGUOUS", float(trade["stop"]), -1.0)
                    break
                if hit_stop:
                    outcome = ("STOP", float(trade["stop"]), -1.0)
                    break
                if hit_tp2:
                    outcome = ("TP2", float(trade["tp2"]), float(cfg["strategy"]["tp2_r_multiple"]))
                    break
                if hit_tp1 and not trade.get("tp1_reached"):
                    trade["tp1_reached"] = True
                    notifications.append(f"🎯 <b>{trade['pair_label']}</b>: TP1 simulato raggiunto.")

            if outcome is None and now - opened_at >= timedelta(hours=max_hours):
                ticker = find_ticker(client.tickers(), pair)
                if ticker:
                    last, _, _ = ticker_metrics(ticker)
                    risk = float(trade["entry"]) - float(trade["stop"])
                    r_value = (last - float(trade["entry"])) / risk if risk > 0 else 0.0
                    outcome = ("EXPIRED", last, r_value)

            if outcome:
                status, exit_price, result_r = outcome
                trade["status"] = status
                trade["closed_at"] = now.isoformat()
                trade["exit_price"] = exit_price
                trade["result_r"] = round(result_r, 3)
                update_history_row(trade["signal_key"], status, exit_price, result_r, now)
                emoji = "✅" if result_r > 0 else "🛑" if result_r < 0 else "⏱️"
                notifications.append(f"{emoji} <b>{trade['pair_label']}</b>: paper trade chiuso {status}, risultato {result_r:+.2f}R.")
        except Exception as exc:
            print(f"[WARN] Aggiornamento paper trade {asset}: {exc}")

    return notifications


def daily_summary_if_due(state: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    if not cfg["paper_trading"]["daily_summary_enabled"]:
        return None
    now_it = datetime.now(ROME)
    summary_hour = int(cfg["paper_trading"]["daily_summary_hour_rome"])
    today = now_it.date().isoformat()
    if now_it.hour < summary_hour or state.get("last_daily_summary_date") == today:
        return None

    trades = state.get("paper_trades", [])
    closed = [t for t in trades if t.get("status") not in {None, "OPEN_SIMULATED"}]
    today_closed = [t for t in closed if (parse_dt(t.get("closed_at")) or datetime.min.replace(tzinfo=timezone.utc)).astimezone(ROME).date().isoformat() == today]
    open_count = sum(1 for t in trades if t.get("status") == "OPEN_SIMULATED")
    wins = sum(1 for t in today_closed if float(t.get("result_r", 0)) > 0)
    losses = sum(1 for t in today_closed if float(t.get("result_r", 0)) < 0)
    total_r = sum(float(t.get("result_r", 0)) for t in today_closed)
    win_rate = wins / len(today_closed) * 100 if today_closed else 0.0
    state["last_daily_summary_date"] = today
    return (
        f"📊 <b>Riepilogo paper trading — {now_it.strftime('%d/%m/%Y')}</b>\n"
        f"Trade chiusi oggi: {len(today_closed)}\n"
        f"Vincenti: {wins} | Perdenti: {losses}\n"
        f"Win rate: {win_rate:.1f}%\n"
        f"Risultato teorico: {total_r:+.2f}R\n"
        f"Trade simulati ancora aperti: {open_count}\n\n"
        "Le statistiche sono simulate e non includono slippage reale completo."
    )


def cooldown_active(state: Dict[str, Any], asset: str, cfg: Dict[str, Any], now: datetime) -> bool:
    last = parse_dt(state.setdefault("last_signal_at", {}).get(asset))
    if not last:
        return False
    return now - last < timedelta(hours=float(cfg["strategy"]["cooldown_hours_per_asset"]))


def validate_config(cfg: Dict[str, Any]) -> None:
    required = ["assets", "strategy", "risk", "paper_trading", "runtime"]
    missing = [key for key in required if key not in cfg]
    if missing:
        raise ValueError(f"Sezioni config mancanti: {', '.join(missing)}")
    score = float(cfg["strategy"]["minimum_score"])
    if not 1 <= score <= 10:
        raise ValueError("minimum_score deve essere tra 1 e 10")
    if float(cfg["risk"]["risk_per_trade_pct"]) <= 0:
        raise ValueError("risk_per_trade_pct deve essere positivo")
    if not cfg["assets"]:
        raise ValueError("La lista assets è vuota")


def run_once(cfg: Dict[str, Any], *, dry_run: bool = False, max_assets_override: Optional[int] = None) -> int:
    validate_config(cfg)
    STATE_CHANGED_FILE.unlink(missing_ok=True)
    client = KrakenClient(
        timeout=int(cfg["runtime"]["http_timeout_seconds"]),
        retries=int(cfg["runtime"]["http_retries"]),
    )
    if not client.system_online():
        raise RuntimeError("Kraken non risulta online: scansione annullata")

    state = load_json(STATE_FILE, {})
    state.setdefault("version", VERSION)
    state.setdefault("signals", {})
    state.setdefault("last_signal_at", {})
    state.setdefault("paper_trades", [])

    raw_pairs = client.asset_pairs()
    resolved = resolve_pairs(cfg, raw_pairs)
    missing = [x["asset"] for x in cfg["assets"] if x["asset"] not in resolved]
    print(f"Kraken Signal Bot {VERSION}")
    print(f"Coppie risolte: {len(resolved)}/{len(cfg['assets'])}")
    if missing:
        print("Coppie non disponibili:", ", ".join(missing))

    tickers = client.tickers()
    btc_pair = resolved.get("BTC") or resolved.get("XBT")
    if not btc_pair:
        raise RuntimeError("BTC/EUR non risolto: impossibile valutare il regime di mercato")
    btc_regime = btc_market_regime(client, btc_pair, cfg)
    print(f"Regime BTC favorevole: {btc_regime['favourable']}")

    paper_updates = update_paper_trades(client, state, resolved, cfg)
    meaningful_state_change = bool(paper_updates)
    for message in paper_updates:
        if not dry_run:
            telegram_send(message)
        else:
            print(message)

    candidates: List[Setup] = []
    now = utc_now()
    max_assets = max_assets_override or int(cfg["runtime"]["max_assets_per_run"])

    for index, item in enumerate(cfg["assets"]):
        if index >= max_assets:
            break
        asset = item["asset"]
        pair = resolved.get(asset)
        if not pair:
            continue
        try:
            ticker = find_ticker(tickers, pair)
            if not ticker:
                print(f"{asset}: ticker non trovato")
                continue
            _, spread_pct, _ = ticker_metrics(ticker)
            if spread_pct > float(cfg["strategy"]["max_spread_pct"]):
                print(f"{asset}: escluso al pre-filtro spread ({spread_pct:.3f}%)")
                continue
            if cooldown_active(state, asset, cfg, now):
                print(f"{asset}: cooldown attivo")
                continue
            prelim = preliminary_scan(
                client, pair, cfg,
                btc_favourable=bool(btc_regime["favourable"]),
                is_btc=asset in {"BTC", "XBT"},
            )
            if not prelim:
                print(f"{asset}: nessun pre-setup")
                continue
            setup = evaluate_setup(client, asset, pair, ticker, prelim, cfg)
            if not setup:
                print(f"{asset}: trigger 5m non valido")
                continue
            if state["signals"].get(asset) == setup.signal_key:
                print(f"{asset}: segnale duplicato")
                continue
            candidates.append(setup)
            print(f"{asset}: candidato {setup.score:.1f}/10")
        except Exception as exc:
            print(f"[ERROR] {asset}: {exc}")
        finally:
            time.sleep(float(cfg["runtime"]["api_pause_seconds"]))

    candidates.sort(key=lambda x: (x.score, x.rr2_net, x.volume_ratio), reverse=True)
    selected = candidates[:int(cfg["runtime"]["max_signals_per_run"])]

    for setup in selected:
        message = format_signal(setup, cfg)
        if dry_run:
            print("\n" + message + "\n")
        else:
            for part in chunks(message):
                telegram_send(part)
        state["signals"][setup.asset] = setup.signal_key
        state["last_signal_at"][setup.asset] = now.isoformat()
        paper_trade = {
            **asdict(setup),
            "opened_at": now.isoformat(),
            "entry": setup.price,
            "pair_label": setup.pair_label,
            "status": "OPEN_SIMULATED",
            "tp1_reached": False,
        }
        state["paper_trades"].append(paper_trade)
        history_append(setup, now)
        meaningful_state_change = True

    # Mantiene lo stato entro dimensioni ragionevoli.
    state["paper_trades"] = state["paper_trades"][-500:]
    state["last_run_utc"] = now.isoformat()
    state["last_scan_assets"] = min(max_assets, len(cfg["assets"]))
    state["last_candidates"] = len(candidates)
    state["last_signals_sent"] = len(selected)
    state["version"] = VERSION

    summary = daily_summary_if_due(state, cfg)
    if summary:
        meaningful_state_change = True
        if dry_run:
            print(summary)
        else:
            telegram_send(summary)

    save_json_atomic(STATE_FILE, state)
    if meaningful_state_change and not dry_run:
        STATE_CHANGED_FILE.write_text(now.isoformat(), encoding="utf-8")
    if selected:
        print(f"Segnali selezionati e inviati: {len(selected)}")
    else:
        print("Nessun nuovo setup credibile.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Kraken Signal Bot {VERSION}")
    parser.add_argument("--once", action="store_true", help="Esegue una scansione e termina")
    parser.add_argument("--dry-run", action="store_true", help="Non invia Telegram; stampa soltanto")
    parser.add_argument("--validate", action="store_true", help="Controlla configurazione e coppie Kraken")
    parser.add_argument("--test-telegram", action="store_true", help="Invia un messaggio di prova Telegram")
    parser.add_argument("--max-assets", type=int, default=None, help="Limita gli asset, utile per test")
    args = parser.parse_args()

    cfg = load_json(CONFIG_FILE, {})
    validate_config(cfg)

    if args.test_telegram:
        telegram_send(f"✅ Test riuscito: Kraken Signal Bot {VERSION} è collegato a Telegram.", silent_if_missing=False)
        print("Test Telegram inviato.")
        return 0

    if args.validate:
        client = KrakenClient()
        online = client.system_online()
        resolved = resolve_pairs(cfg, client.asset_pairs())
        print(f"Kraken online: {online}")
        print(f"Coppie risolte: {len(resolved)}/{len(cfg['assets'])}")
        print(", ".join(resolved.keys()))
        return 0 if online and resolved else 1

    if args.once or os.getenv("GITHUB_ACTIONS") == "true":
        return run_once(cfg, dry_run=args.dry_run, max_assets_override=args.max_assets)

    interval = max(60, int(cfg["runtime"]["scan_interval_seconds"]))
    print(f"Modalità VPS/locale: scansione ogni {interval} secondi. Ctrl+C per fermare.")
    while True:
        started = time.monotonic()
        try:
            run_once(cfg, dry_run=args.dry_run, max_assets_override=args.max_assets)
        except KeyboardInterrupt:
            print("Bot arrestato.")
            return 0
        except Exception as exc:
            print(f"[FATAL] {exc}", file=sys.stderr)
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, interval - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
