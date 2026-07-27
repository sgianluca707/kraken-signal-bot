#!/usr/bin/env python3
"""
Kraken Signal Bot 2.0
- Dati pubblici Kraken: nessuna API key necessaria.
- Analisi: trend 1h, struttura 15m, breakout/volume 5m.
- Notifiche Telegram raggruppate.
- Non esegue ordini.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

KRAKEN_API = "https://api.kraken.com/0/public"
ROME = ZoneInfo("Europe/Rome")
STATE_FILE = Path(__file__).with_name("state.json")
CONFIG_FILE = Path(__file__).with_name("config.json")


@dataclass
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
    instrument_note: str
    current_price: float
    resistance: float
    retest_low: float
    retest_high: float
    max_price: float
    stop: float
    tp1: float
    tp2: float
    rr1_gross: float
    rr2_gross: float
    rr1_net: float
    rr2_net: float
    risk: str
    quality: int
    volume_ratio: float
    extension_pct: float
    duration: str
    invalidation: str
    main_risk: str
    signal_key: str


def load_config() -> Dict[str, Any]:
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Le credenziali Telegram restano fuori dal file condivisibile.
    cfg["telegram_bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    cfg["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return cfg


def http_json(url: str, timeout: int = 15) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "KrakenSignalBot/2.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def kraken_public(method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params or {})
    url = f"{KRAKEN_API}/{method}"
    if query:
        url += "?" + query
    data = http_json(url)
    errors = data.get("error", [])
    if errors:
        raise RuntimeError(f"Kraken API error: {errors}")
    return data["result"]


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"signals": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"signals": {}}


def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def resolve_pairs(cfg: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    pairs = kraken_public("AssetPairs")
    resolved: Dict[str, Dict[str, str]] = {}

    # Indicizza sia wsname sia altname.
    by_ws = {}
    by_alt = {}
    for api_name, meta in pairs.items():
        ws = str(meta.get("wsname", "")).upper()
        alt = str(meta.get("altname", "")).upper()
        if ws:
            by_ws[ws] = api_name
        if alt:
            by_alt[alt] = api_name

    for item in cfg["assets"]:
        asset = item["asset"]
        found = None
        found_label = None
        for candidate in item["pair_candidates"]:
            c = candidate.upper()
            if c in by_ws:
                found = by_ws[c]
                found_label = c
                break
            compact = c.replace("/", "")
            if compact in by_alt:
                found = by_alt[compact]
                found_label = c
                break

        if found:
            resolved[asset] = {
                "api_pair": found,
                "pair_label": found_label or item["pair_candidates"][0],
                "instrument_note": item.get("instrument_note", "Spot senza leva"),
            }
        else:
            print(f"[WARN] Nessuna coppia Kraken trovata per {asset}: {item['pair_candidates']}")

    return resolved


def fetch_ohlc(pair: str, interval: int) -> List[Candle]:
    result = kraken_public("OHLC", {"pair": pair, "interval": interval})
    data_key = next(k for k in result.keys() if k != "last")
    rows = result[data_key]
    candles = []
    for r in rows:
        candles.append(Candle(
            ts=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
            close=float(r[4]), vwap=float(r[5]), volume=float(r[6]), count=int(r[7])
        ))
    return candles


def fetch_ticker(pair: str) -> float:
    result = kraken_public("Ticker", {"pair": pair})
    data_key = next(iter(result))
    return float(result[data_key]["c"][0])


def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def atr(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        trs.append(max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - prev_close),
            abs(candles[i].low - prev_close),
        ))
    return sum(trs[-period:]) / period


def trend_1h(candles: List[Candle]) -> Tuple[bool, float]:
    # Esclude la candela corrente non completata.
    closed = candles[:-1]
    closes = [c.close for c in closed]
    if len(closes) < 55:
        return False, 0.0
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    slope = (e20[-1] - e20[-4]) / e20[-4] if e20[-4] else 0.0
    bullish = e20[-1] > e50[-1] and slope > 0 and closes[-1] > e20[-1]
    strength = max(0.0, min(1.0, ((e20[-1] / e50[-1]) - 1) * 100 + slope * 200))
    return bullish, strength


def rr_values(entry: float, stop: float, tp: float, fee_each_side: float) -> Tuple[float, float]:
    risk_pct = (entry - stop) / entry
    reward_pct = (tp - entry) / entry
    if risk_pct <= 0:
        return 0.0, 0.0
    gross = reward_pct / risk_pct

    # Stima conservativa: costo di entrata + uscita.
    round_trip_fee = 2 * fee_each_side
    net_reward = reward_pct - round_trip_fee
    net_risk = risk_pct + round_trip_fee
    net = net_reward / net_risk if net_reward > 0 else 0.0
    return gross, net


def quality_score(
    trend_strength: float,
    volume_ratio: float,
    extension_pct: float,
    close_strength: float,
    rr2_net: float,
) -> int:
    score = 5.0
    score += min(1.0, trend_strength)
    score += min(1.5, max(0.0, volume_ratio - 1.2))
    score += 0.8 if extension_pct <= 0.25 else 0.3
    score += min(0.8, max(0.0, close_strength))
    score += 0.7 if rr2_net >= 1.3 else 0.0
    return max(1, min(10, round(score)))


def analyse_asset(
    asset: str,
    pair_info: Dict[str, str],
    cfg: Dict[str, Any],
) -> Optional[Setup]:
    pair = pair_info["api_pair"]
    c1h = fetch_ohlc(pair, 60)
    c15 = fetch_ohlc(pair, 15)
    c5 = fetch_ohlc(pair, 5)
    price = fetch_ticker(pair)

    bullish, trend_strength = trend_1h(c1h)
    if not bullish or len(c15) < 30 or len(c5) < 30:
        return None

    # Ultima candela completata e storico precedente.
    last5 = c5[-2]
    previous5 = c5[-23:-2]
    previous15 = c15[-23:-2]

    # Resistenza strutturale 15m prima del breakout.
    resistance = max(c.high for c in previous15)
    avg_vol = sum(c.volume for c in previous5) / len(previous5)
    volume_ratio = last5.volume / avg_vol if avg_vol > 0 else 0.0

    # Conferma: candela 5m chiusa sopra la resistenza.
    if last5.close <= resistance:
        return None

    extension_pct = ((price / resistance) - 1) * 100
    max_extension = float(cfg["strategy"]["max_breakout_extension_pct"])
    min_volume_ratio = float(cfg["strategy"]["min_volume_ratio"])

    if extension_pct < -0.05 or extension_pct > max_extension:
        return None
    if volume_ratio < min_volume_ratio:
        return None

    a = atr(c5[:-1], 14)
    if a <= 0:
        return None

    breakout_range = max(last5.high - last5.low, 1e-12)
    close_strength = (last5.close - last5.low) / breakout_range

    stop_buffer = max(
        resistance * float(cfg["strategy"]["min_stop_buffer_pct"]) / 100,
        a * float(cfg["strategy"]["atr_stop_multiplier"])
    )
    stop = resistance - stop_buffer
    risk_amount = price - stop
    if risk_amount <= 0:
        return None

    tp1 = price + risk_amount * float(cfg["strategy"]["tp1_r_multiple"])
    tp2 = price + risk_amount * float(cfg["strategy"]["tp2_r_multiple"])

    fee = float(cfg["strategy"]["estimated_fee_each_side"])
    rr1_gross, rr1_net = rr_values(price, stop, tp1, fee)
    rr2_gross, rr2_net = rr_values(price, stop, tp2, fee)

    min_net_rr2 = float(cfg["strategy"]["min_net_rr2"])
    if rr2_net < min_net_rr2:
        return None

    retest_width = max(resistance * 0.0015, a * 0.25)
    retest_low = resistance - retest_width
    retest_high = resistance + retest_width * 0.5
    max_price = resistance * (1 + max_extension / 100)

    quality = quality_score(
        trend_strength, volume_ratio, extension_pct, close_strength, rr2_net
    )
    risk = "ALTO" if asset in {"DOGE", "SOL", "AVAX"} else "MEDIO-ALTO"
    if asset == "GOLD":
        risk = "MEDIO"

    duration = "30 minuti–4 ore"
    invalidation = (
        f"Chiusura 5m sotto {fmt_price(resistance)} o perdita netta della zona di breakout"
    )
    main_risk = (
        "Falso breakout e rapido rientro sotto la resistenza"
        if asset != "GOLD"
        else "Scostamento del token dal prezzo dell’oro, liquidità e rischio emittente/custodia"
    )
    signal_key = f"{asset}:{round(resistance, 10)}:{last5.ts}"

    return Setup(
        asset=asset,
        pair_label=pair_info["pair_label"],
        instrument_note=pair_info["instrument_note"],
        current_price=price,
        resistance=resistance,
        retest_low=retest_low,
        retest_high=retest_high,
        max_price=max_price,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        rr1_gross=rr1_gross,
        rr2_gross=rr2_gross,
        rr1_net=rr1_net,
        rr2_net=rr2_net,
        risk=risk,
        quality=quality,
        volume_ratio=volume_ratio,
        extension_pct=extension_pct,
        duration=duration,
        invalidation=invalidation,
        main_risk=main_risk,
        signal_key=signal_key,
    )


def fmt_price(x: float) -> str:
    if x >= 1000:
        return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if x >= 1:
        return f"{x:.4f}".replace(".", ",")
    if x >= 0.01:
        return f"{x:.6f}".replace(".", ",")
    return f"{x:.8f}".replace(".", ",")


def format_setup(s: Setup, age_seconds: int) -> str:
    now_it = datetime.now(ROME).strftime("%d/%m/%Y %H:%M:%S")
    aggressive = (
        "A) COMPRA ORA — SPECULATIVO\n"
        f"• Zona ingresso: {fmt_price(s.current_price)}–{fmt_price(s.max_price)}\n"
        f"• Prezzo massimo accettabile: {fmt_price(s.max_price)}\n"
        f"• Trigger: breakout 5m sopra {fmt_price(s.resistance)}, "
        f"volume {s.volume_ratio:.2f}× media e trend 1h rialzista\n"
        f"• Stop: {fmt_price(s.stop)}\n"
        f"• TP1: {fmt_price(s.tp1)} | TP2: {fmt_price(s.tp2)}\n"
        f"• R/R lordo: {s.rr1_gross:.2f} / {s.rr2_gross:.2f}\n"
        f"• R/R netto stimato: {s.rr1_net:.2f} / {s.rr2_net:.2f}\n"
        "• Capitale massimo: 5%\n"
        "• Ordine: Spot, market solo con spread contenuto; preferibile limit immediatamente eseguibile"
    )
    prudent = (
        "B) NON COMPRARE ANCORA — ATTENDI RETEST\n"
        f"• Zona retest: {fmt_price(s.retest_low)}–{fmt_price(s.retest_high)}\n"
        "• Conferma richiesta: rifiuto della zona con chiusura 5m nuovamente sopra il livello "
        "e volume almeno in ripresa\n"
        f"• Stop dopo conferma: {fmt_price(s.stop)}\n"
        f"• TP1: {fmt_price(s.tp1)} | TP2: {fmt_price(s.tp2)}\n"
        "• Capitale massimo: 10%\n"
        "• Ordine: Spot limit dopo conferma"
    )
    return (
        f"🚨 {s.asset} — {s.pair_label}\n"
        f"Ora italiana: {now_it} | Anzianità dato: circa {age_seconds}s\n"
        f"Rischio: {s.risk} | Qualità: {s.quality}/10\n"
        f"Prezzo attuale: {fmt_price(s.current_price)}\n"
        f"Strumento: {s.instrument_note}\n"
        f"Estensione dal breakout: {s.extension_pct:.3f}%\n\n"
        f"{aggressive}\n\n"
        f"{prudent}\n\n"
        f"Durata prevista: {s.duration}\n"
        f"Invalidazione: {s.invalidation}\n"
        f"Rischio principale: {s.main_risk}\n"
    )


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        result = json.loads(r.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def chunks(text: str, max_len: int = 3900) -> List[str]:
    if len(text) <= max_len:
        return [text]
    parts, current = [], ""
    for section in text.split("\n\n"):
        candidate = current + ("\n\n" if current else "") + section
        if len(candidate) > max_len and current:
            parts.append(current)
            current = section
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def run_once(cfg: Dict[str, Any], pairs: Dict[str, Dict[str, str]]) -> None:
    started = time.time()
    state = load_state()
    old_signals = state.setdefault("signals", {})
    setups: List[Setup] = []

    for asset, info in pairs.items():
        try:
            setup = analyse_asset(asset, info, cfg)
            if setup is None:
                continue

            prior = old_signals.get(asset)
            # Non ripete lo stesso segnale. È nuovo se cambia breakout/candela.
            if prior == setup.signal_key:
                continue

            setups.append(setup)
        except Exception as exc:
            print(f"[ERROR] {asset}: {exc}")

    if not setups:
        print(f"[{datetime.now(ROME).isoformat(timespec='seconds')}] Nessun nuovo setup credibile.")
        return

    age = max(1, int(time.time() - started))
    header = (
        "📊 KRAKEN SIGNAL BOT 2.0 — NUOVI SETUP\n"
        f"Asset validi: {len(setups)}\n"
        "Analisi: trend 1h, struttura 15m, ingresso 5m e volume.\n"
        "Tutti i setup sono Spot senza leva.\n\n"
    )
    disclaimer = (
        "\n⚠️ Questo segnale non garantisce un guadagno e con leva le perdite sono amplificate."
    )
    body = header + "\n\n".join(format_setup(s, age) for s in setups) + disclaimer

    token = cfg["telegram_bot_token"]
    chat_id = cfg["telegram_chat_id"]
    if not token or not chat_id:
        print(body)
        print("\n[WARN] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancanti: notifica stampata soltanto.")
        return

    for part in chunks(body):
        send_telegram(token, chat_id, part)

    for s in setups:
        old_signals[s.asset] = s.signal_key
    save_state(state)
    print(f"[{datetime.now(ROME).isoformat(timespec='seconds')}] Inviati {len(setups)} setup.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kraken Signal Bot 2.0")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Esegue una sola scansione e termina; usare con GitHub Actions."
    )
    args = parser.parse_args()

    cfg = load_config()
    pairs = resolve_pairs(cfg)
    if not pairs:
        raise SystemExit("Nessuna coppia risolta. Controlla config.json.")

    interval = max(60, int(cfg.get("scan_interval_seconds", 60)))
    print("Coppie risolte:")
    for asset, info in pairs.items():
        print(f"  {asset}: {info['pair_label']} -> {info['api_pair']}")

    if args.once:
        run_once(cfg, pairs)
        return

    print(f"Scansione ogni {interval} secondi. Ctrl+C per fermare.")
    while True:
        cycle_start = time.time()
        try:
            run_once(cfg, pairs)
        except KeyboardInterrupt:
            print("\nBot arrestato.")
            break
        except Exception as exc:
            print(f"[FATAL CYCLE ERROR] {exc}")

        elapsed = time.time() - cycle_start
        time.sleep(max(1, interval - elapsed))


if __name__ == "__main__":
    main()
