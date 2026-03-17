"""
BTC Monitor — Telegram Bot
Corre en Railway/Render, chequea Binance cada 2 minutos
y manda alertas cuando tu checklist supera el umbral
"""

import os
import time
import math
import requests
from datetime import datetime

# ── Config ────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "TU_TOKEN_AQUI")
CHAT_ID        = os.environ.get("CHAT_ID", "TU_CHAT_ID_AQUI")
CHECK_INTERVAL = 120   # segundos entre cada chequeo
MIN_SCORE      = 70    # % mínimo para alertar
TF             = "1h"  # timeframe principal
SYMBOL         = "BTCUSDT"

last_alert_key = ""

# ── Binance fetch ─────────────────────────────────────
def fetch_candles(interval, limit=100):
    # Bybit API — no geographic restrictions
    interval_map = {"1h": "60", "4h": "240"}
    bybit_interval = interval_map.get(interval, "60")
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "spot",
        "symbol": SYMBOL,
        "interval": bybit_interval,
        "limit": limit
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("retCode") != 0:
        raise Exception(f"Bybit error: {data.get('retMsg')}")
    # Bybit returns newest first, reverse to oldest first
    raw = data["result"]["list"]
    raw.reverse()
    return [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
             "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
            for k in raw]

# ── Indicators ────────────────────────────────────────
def ema(arr, p):
    if len(arr) < p:
        return [None] * len(arr)
    k = 2 / (p + 1)
    v = sum(arr[:p]) / p
    result = [None] * (p - 1) + [v]
    for i in range(p, len(arr)):
        v = arr[i] * k + v * (1 - k)
        result.append(v)
    return result

def calc_macd(closes):
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    ml = [round(e12[i] - e26[i], 2) if e12[i] and e26[i] else None
          for i in range(len(closes))]
    valid = [v for v in ml if v is not None]
    if len(valid) < 9:
        return ml, [None]*len(ml), [None]*len(ml)
    sr = ema(valid, 9)
    sig = [None] * len(ml)
    si = 0
    for i in range(len(ml)):
        if ml[i] is not None and si < len(sr):
            sig[i] = round(sr[si], 2) if sr[si] is not None else None
            si += 1
    hist = [round(ml[i] - sig[i], 2) if ml[i] is not None and sig[i] is not None else None
            for i in range(len(ml))]
    return ml, sig, hist

# ── Auto S/R detection ────────────────────────────────
def detect_sr(candles, lookback=60, tol=0.008, min_touches=2):
    sl = candles[-lookback:] if len(candles) >= lookback else candles
    n = len(sl)
    supports, resistances = [], []

    for i in range(2, n - 2):
        c = sl[i]
        # Swing low
        if (c["l"] < sl[i-1]["l"] and c["l"] < sl[i-2]["l"] and
                c["l"] < sl[i+1]["l"] and c["l"] < sl[i+2]["l"]):
            touches = sum(1 for x in sl
                         if abs(x["l"] - c["l"]) / c["l"] < tol
                         or abs(x["c"] - c["l"]) / c["l"] < tol)
            if touches >= min_touches:
                supports.append(c["l"])
        # Swing high
        if (c["h"] > sl[i-1]["h"] and c["h"] > sl[i-2]["h"] and
                c["h"] > sl[i+1]["h"] and c["h"] > sl[i+2]["h"]):
            touches = sum(1 for x in sl
                         if abs(x["h"] - c["h"]) / c["h"] < tol
                         or abs(x["c"] - c["h"]) / c["h"] < tol)
            if touches >= min_touches:
                resistances.append(c["h"])
    return supports, resistances

# ── Checklist (12 items — igual que el monitor) ───────
def score_candle(candles, candles4h):
    if len(candles) < 10:
        return 0, 0, False, False

    last  = candles[-1]
    prev  = candles[-2]
    p2    = candles[-3]

    closes = [c["c"] for c in candles]
    vols   = [c["v"] for c in candles]
    ml, sig, hist = calc_macd(closes)
    i = len(closes) - 1

    e14 = ema(closes, 14)
    e21 = ema(closes, 21)

    # 4H trend
    h4 = candles4h[-8:]
    t_up = len(h4) >= 2 and h4[-1]["c"] > h4[0]["c"] and h4[-1]["l"] > h4[0]["l"]
    t_dn = len(h4) >= 2 and h4[-1]["c"] < h4[0]["c"] and h4[-1]["h"] < h4[0]["h"]

    # Structure
    is_hl = last["l"] > p2["l"]
    is_hh = last["h"] > prev["h"]
    is_lh = last["h"] < prev["h"]
    is_ll = last["l"] < prev["l"]

    # Candle
    bull  = last["c"] >= last["o"]
    bear  = last["c"] <  last["o"]
    body  = abs(last["c"] - last["o"])
    rng   = last["h"] - last["l"] or 1
    lwk   = min(last["c"], last["o"]) - last["l"]
    uwk   = last["h"] - max(last["c"], last["o"])
    hammer = lwk > body * 1.5 and bull
    star   = uwk > body * 1.5 and bear

    # EMAs
    e14v = e14[i]; e21v = e21[i]
    above_ema = bool(e14v and e21v and last["c"] > e14v and last["c"] > e21v)
    below_ema = bool(e14v and e21v and last["c"] < e14v and last["c"] < e21v)
    sl14u = bool(e14[i] and e14[i-3] and e14[i] > e14[i-3])
    sl14d = bool(e14[i] and e14[i-3] and e14[i] < e14[i-3])
    sl21u = bool(e21[i] and e21[i-3] and e21[i] > e21[i-3])
    sl21d = bool(e21[i] and e21[i-3] and e21[i] < e21[i-3])

    # MACD
    hist_green   = hist[i] is not None and hist[i] > 0
    hist_grow    = hist[i] is not None and hist[i-1] is not None and hist[i] > hist[i-1]
    hist_red     = hist[i] is not None and hist[i] < 0
    hist_red_grow = hist[i] is not None and hist[i-1] is not None and hist[i] < hist[i-1]
    hist_mom_bear = (hist_red and hist_red_grow and hist[i-1] is not None
                     and abs(hist[i]) > abs(hist[i-1]))

    # Volume
    avg_vol = sum(vols[-50:]) / min(len(vols), 50)
    vol_surge = vols[-1] > avg_vol * 1.2

    # Auto S/R with mutual exclusion
    supports, resistances = detect_sr(candles)
    price = last["c"]

    closest_sup = min(supports, key=lambda p: abs(p - price)) if supports else None
    closest_res = min(resistances, key=lambda p: abs(p - price)) if resistances else None
    dist_sup = abs(closest_sup - price) if closest_sup else float("inf")
    dist_res = abs(closest_res - price) if closest_res else float("inf")
    tol_val = price * 0.008

    near_sup = bool(closest_sup and dist_sup < tol_val and
                    (not closest_res or dist_sup <= dist_res))
    near_res = bool(closest_res and dist_res < tol_val and
                    (not closest_sup or dist_res < dist_sup))
    retest_sup = bool(closest_sup and last["l"] <= closest_sup * 1.003 and last["c"] > closest_sup)
    retest_res = bool(closest_res and last["h"] >= closest_res * 0.997 and last["c"] < closest_res)

    sup_ok = near_sup or retest_sup
    res_ok = near_res or retest_res

    # 12-item checklists
    long_items = [
        is_hl, is_hh or t_up,
        sup_ok, retest_sup, bool(supports),
        hammer, bool(near_sup and last["c"] > closest_sup) if closest_sup else False,
        above_ema, sl14u and sl21u,
        hist_green, hist_grow,
        vol_surge
    ]
    short_items = [
        is_lh, is_ll or t_dn,
        res_ok, retest_res, bool(resistances),
        star, bool(near_res and last["c"] < closest_res) if closest_res else False,
        below_ema, sl14d and sl21d,
        hist_red and hist_red_grow, hist_mom_bear,
        vol_surge and bear
    ]

    long_pct  = round(sum(long_items)  / len(long_items)  * 100)
    short_pct = round(sum(short_items) / len(short_items) * 100)

    long_mandatory  = sup_ok and hammer
    short_mandatory = res_ok and star

    return long_pct, short_pct, long_mandatory, short_mandatory

# ── Telegram ──────────────────────────────────────────
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=data, timeout=10)
        return r.ok
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def fmt_price(p):
    return f"${p:,.0f}"

# ── Main loop ─────────────────────────────────────────
def check_and_alert():
    global last_alert_key
    try:
        candles    = fetch_candles(TF, 100)
        candles4h  = fetch_candles("4h", 50)
    except Exception as e:
        print(f"[{now()}] Fetch error: {e}")
        return

    price = candles[-1]["c"]
    long_pct, short_pct, long_mand, short_mand = score_candle(candles, candles4h)

    print(f"[{now()}] BTC {fmt_price(price)} | LONG {long_pct}% | SHORT {short_pct}%")

    side = None
    pct  = 0

    if long_pct >= MIN_SCORE and long_pct >= short_pct:
        side = "LONG"
        pct  = long_pct
    elif short_pct >= MIN_SCORE:
        side = "SHORT"
        pct  = short_pct

    if not side:
        return

    key = f"{side}-{round(price / 200)}"
    if key == last_alert_key:
        return  # ya alertamos este nivel

    last_alert_key = key

    emoji  = "🟢" if side == "LONG" else "🔴"
    mand   = "✅ Obligatorias OK" if (long_mand if side == "LONG" else short_mand) else "⚠️ Sin obligatorias"
    h4_trend = "▲ Alcista" if candles4h[-1]["c"] > candles4h[0]["c"] else "▼ Bajista"

    msg = (
        f"{emoji} <b>SEÑAL {side} — BTC/USDT {TF.upper()}</b>\n\n"
        f"💰 Precio: <b>{fmt_price(price)}</b>\n"
        f"📊 Checklist: <b>{pct}%</b> ({pct * 12 // 100}/12 condiciones)\n"
        f"📈 Tendencia 4H: {h4_trend}\n"
        f"{mand}\n\n"
        f"⚠️ Verificá en Binance antes de entrar."
    )

    sent = send_telegram(msg)
    print(f"[{now()}] ✅ Alerta {side} enviada: {sent}")

def now():
    return datetime.now().strftime("%H:%M:%S")

# ── Entry point ───────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 BTC Monitor Bot iniciado — chequeando cada {CHECK_INTERVAL}s")
    print(f"   Símbolo: {SYMBOL} | TF: {TF} | Umbral: {MIN_SCORE}%")

    # Send startup message
    send_telegram(
        f"🚀 <b>BTC Monitor Bot iniciado</b>\n\n"
        f"📊 Chequeando {SYMBOL} en {TF.upper()} cada {CHECK_INTERVAL//60} minutos\n"
        f"🎯 Umbral de alerta: {MIN_SCORE}%\n\n"
        f"Te avisaré cuando haya una señal."
    )

    while True:
        check_and_alert()
        time.sleep(CHECK_INTERVAL)
