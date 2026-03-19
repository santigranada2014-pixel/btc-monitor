import os, time, requests
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
CHECK_INTERVAL = 120
MIN_SCORE      = 70
TF             = "4h"
levels         = []
last_alert_key = ""
last_update_id = 0

def now(): return datetime.now().strftime("%H:%M:%S")
def fmt(p): return f"${p:,.0f}"

def send(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=15)
        print(f"[{now()}] Telegram response: {r.status_code} {r.text[:200]}")
        return r.ok
    except Exception as e:
        print(f"[{now()}] Telegram EXCEPTION: {type(e).__name__}: {e}")
        return False

def fetch_candles(interval, limit=100):
    # Try Binance first
    try:
        r = requests.get("https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": interval, "limit": limit}, timeout=10)
        if r.ok:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                print(f"[{now()}] Usando Binance ✅")
                return [{"t": k[0], "o": float(k[1]), "h": float(k[2]),
                         "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in data]
    except Exception as e:
        print(f"[{now()}] Binance falló: {e}")

    # Try Kraken
    try:
        interval_map = {"1h": 60, "4h": 240}
        kraken_interval = interval_map.get(interval, 60)
        r = requests.get("https://api.kraken.com/0/public/OHLC",
            params={"pair": "XBTUSD", "interval": kraken_interval, "count": limit}, timeout=10)
        if r.ok:
            data = r.json()
            if not data.get("error"):
                raw = list(data["result"].values())[0]
                raw = raw[-limit:]
                print(f"[{now()}] Usando Kraken ✅")
                return [{"t": int(k[0])*1000, "o": float(k[1]), "h": float(k[2]),
                         "l": float(k[3]), "c": float(k[4]), "v": float(k[6])} for k in raw]
    except Exception as e:
        print(f"[{now()}] Kraken falló: {e}")

    # Fallback CryptoCompare
    aggregate = {"1h": 1, "4h": 4}.get(interval, 1)
    r = requests.get("https://min-api.cryptocompare.com/data/v2/histohour",
        params={"fsym": "BTC", "tsym": "USD", "limit": limit, "aggregate": aggregate}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("Response") != "Success":
        raise Exception(f"CryptoCompare error: {data.get('Message')}")
    print(f"[{now()}] Usando CryptoCompare ✅")
    return [{"t": k["time"]*1000, "o": k["open"], "h": k["high"],
             "l": k["low"], "c": k["close"], "v": k["volumefrom"]}
            for k in data["Data"]["Data"] if k["open"] > 0]

def ema(arr, p):
    if len(arr) < p: return [None]*len(arr)
    k = 2/(p+1); v = sum(arr[:p])/p
    result = [None]*(p-1)+[v]
    for i in range(p, len(arr)):
        v = arr[i]*k+v*(1-k); result.append(v)
    return result

def calc_macd(closes):
    e12=ema(closes,12); e26=ema(closes,26)
    ml=[round(e12[i]-e26[i],2) if e12[i] and e26[i] else None for i in range(len(closes))]
    valid=[v for v in ml if v is not None]
    if len(valid)<9: return ml,[None]*len(ml),[None]*len(ml)
    sr=ema(valid,9); sig=[None]*len(ml); si=0
    for i in range(len(ml)):
        if ml[i] is not None and si<len(sr):
            sig[i]=round(sr[si],2) if sr[si] is not None else None; si+=1
    hist=[round(ml[i]-sig[i],2) if ml[i] is not None and sig[i] is not None else None for i in range(len(ml))]
    return ml,sig,hist

def detect_sr_auto(candles,lookback=60,tol=0.008,min_touches=2):
    sl=candles[-lookback:] if len(candles)>=lookback else candles; n=len(sl); found=[]
    for i in range(2,n-2):
        c=sl[i]
        if c["l"]<sl[i-1]["l"] and c["l"]<sl[i-2]["l"] and c["l"]<sl[i+1]["l"] and c["l"]<sl[i+2]["l"]:
            if sum(1 for x in sl if abs(x["l"]-c["l"])/c["l"]<tol)>=min_touches: found.append(c["l"])
        if c["h"]>sl[i-1]["h"] and c["h"]>sl[i-2]["h"] and c["h"]>sl[i+1]["h"] and c["h"]>sl[i+2]["h"]:
            if sum(1 for x in sl if abs(x["h"]-c["h"])/c["h"]<tol)>=min_touches: found.append(c["h"])
    return found

def score_candle(candles,candles4h):
    if len(candles)<10: return 0,0
    last=candles[-1]; prev=candles[-2]; p2=candles[-3]
    closes=[c["c"] for c in candles]; vols=[c["v"] for c in candles]
    ml,sig,hist=calc_macd(closes); i=len(closes)-1
    e14=ema(closes,14); e21=ema(closes,21)
    h4=candles4h[-8:]
    t_up=len(h4)>=2 and h4[-1]["c"]>h4[0]["c"] and h4[-1]["l"]>h4[0]["l"]
    t_dn=len(h4)>=2 and h4[-1]["c"]<h4[0]["c"] and h4[-1]["h"]<h4[0]["h"]
    is_hl=last["l"]>p2["l"]; is_hh=last["h"]>prev["h"]
    is_lh=last["h"]<prev["h"]; is_ll=last["l"]<prev["l"]
    bull=last["c"]>=last["o"]; bear=last["c"]<last["o"]
    body=abs(last["c"]-last["o"]); rng=last["h"]-last["l"] or 1
    lwk=min(last["c"],last["o"])-last["l"]; uwk=last["h"]-max(last["c"],last["o"])
    hammer=lwk>body*1.5 and bull; star=uwk>body*1.5 and bear
    e14v=e14[i]; e21v=e21[i]
    above_ema=bool(e14v and e21v and last["c"]>e14v and last["c"]>e21v)
    below_ema=bool(e14v and e21v and last["c"]<e14v and last["c"]<e21v)
    sl14u=bool(e14[i] and e14[i-3] and e14[i]>e14[i-3])
    sl14d=bool(e14[i] and e14[i-3] and e14[i]<e14[i-3])
    sl21u=bool(e21[i] and e21[i-3] and e21[i]>e21[i-3])
    sl21d=bool(e21[i] and e21[i-3] and e21[i]<e21[i-3])
    hg=hist[i] is not None and hist[i]>0
    hgrow=hist[i] is not None and hist[i-1] is not None and hist[i]>hist[i-1]
    hr=hist[i] is not None and hist[i]<0
    hrgrow=hist[i] is not None and hist[i-1] is not None and hist[i]<hist[i-1]
    hmb=hr and hrgrow and hist[i-1] is not None and abs(hist[i])>abs(hist[i-1])
    avg_vol=sum(vols[-50:])/min(len(vols),50)
    rv=vols[-3:]; rs=candles[-3:]
    vs=any(v>avg_vol*1.2 for v in rv)
    vsb=any(c["c"]<c["o"] and rv[idx]>avg_vol*1.2 for idx,c in enumerate(rs))
    all_lvls=list(levels)+detect_sr_auto(candles); price=last["c"]
    ca=min([p for p in all_lvls if price>=p*0.992],key=lambda p:abs(p-price),default=None)
    cb=min([p for p in all_lvls if price<p],key=lambda p:abs(p-price),default=None)
    rs2=bool(ca and abs(ca-price)/price<0.008); rts=bool(ca and last["l"]<=ca*1.003 and last["c"]>ca)
    sb=bool(cb); rr=bool(cb and (cb-price)/cb<0.015)
    cab=bool(rs2 and ca and last["c"]>ca); cbl=bool(cb and last["c"]<cb)
    li=[is_hl,is_hh or t_up,rs2 or rts,rts,bool(all_lvls),hammer,cab,above_ema,sl14u and sl21u,hg,hgrow,vs]
    si2=[is_lh,is_ll or t_dn,sb or rr,rr,bool(all_lvls),star,cbl,below_ema,sl14d and sl21d,hr and hrgrow,hmb,vsb]
    return round(sum(li)/len(li)*100),round(sum(si2)/len(si2)*100)

def handle_commands():
    global last_update_id, MIN_SCORE
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": last_update_id+1, "timeout": 5}, timeout=10)
        if not r.ok: return
        for u in r.json().get("result", []):
            last_update_id = u["update_id"]
            text = u.get("message", {}).get("text", "").strip().lower()
            if not text: continue
            print(f"[{now()}] Comando recibido: {text}")

            if text in ("/start", "/ayuda"):
                send("🤖 <b>BTC Monitor Bot</b>\n\n"
                     "/estado — precio y % actual\n"
                     "/niveles — ver niveles\n"
                     "/nivel 72000 — agregar nivel\n"
                     "/borrar 72000 — eliminar nivel\n"
                     "/umbral 80 — cambiar umbral\n"
                     "/config — ver configuración")
            elif text == "/estado":
                try:
                    c1=fetch_candles(TF,100); c4=fetch_candles("4h",50)
                    lp,sp=score_candle(c1,c4); price=c1[-1]["c"]
                    trend="▲ Alcista" if c4[-1]["c"]>c4[0]["c"] else "▼ Bajista"
                    lvl_str=", ".join(fmt(l) for l in sorted(levels)) if levels else "Ninguno"
                    send(f"📊 <b>Estado actual</b>\n\n💰 BTC: <b>{fmt(price)}</b>\n"
                         f"📈 4H: {trend}\n🟢 LONG: <b>{lp}%</b>\n🔴 SHORT: <b>{sp}%</b>\n"
                         f"🎯 Umbral: {MIN_SCORE}%\n📍 Niveles: {lvl_str}")
                except Exception as e: send(f"❌ Error: {e}")
            elif text == "/niveles":
                if levels: send("📍 <b>Niveles:</b>\n"+"\n".join(f"  • {fmt(l)}" for l in sorted(levels)))
                else: send("📍 Sin niveles. Usá /nivel 72000")
            elif text.startswith("/nivel "):
                try:
                    p=float(text.split()[1].replace(",",""))
                    if p in levels: send(f"⚠️ {fmt(p)} ya existe.")
                    else:
                        levels.append(p)
                        send(f"✅ Nivel {fmt(p)} agregado.\nNiveles: {', '.join(fmt(l) for l in sorted(levels))}")
                except: send("❌ Usá: /nivel 72000")
            elif text.startswith("/borrar "):
                try:
                    p=float(text.split()[1].replace(",",""))
                    closest=min(levels,key=lambda x:abs(x-p),default=None)
                    if closest and abs(closest-p)/p<0.01:
                        levels.remove(closest); send(f"🗑 Nivel {fmt(closest)} eliminado.")
                    else: send(f"❌ No encontré nivel cerca de {fmt(p)}.")
                except: send("❌ Usá: /borrar 72000")
            elif text.startswith("/umbral "):
                try:
                    val=int(text.split()[1])
                    if 30<=val<=100:
                        MIN_SCORE=val; send(f"✅ Umbral actualizado a <b>{MIN_SCORE}%</b>")
                    else: send("❌ Umbral entre 30 y 100.")
                except: send("❌ Usá: /umbral 80")
            elif text == "/config":
                lvl_str=", ".join(fmt(l) for l in sorted(levels)) if levels else "Ninguno"
                send(f"⚙️ <b>Config</b>\n\nTF: {TF.upper()}\nUmbral: {MIN_SCORE}%\n"
                     f"Revisión: cada {CHECK_INTERVAL//60} min\nNiveles: {lvl_str}")
    except Exception as e:
        print(f"[{now()}] Command error: {type(e).__name__}: {e}")

def check_and_alert():
    global last_alert_key
    try:
        c1=fetch_candles(TF,100); c4=fetch_candles("4h",50)
    except Exception as e:
        print(f"[{now()}] Fetch error: {e}"); return
    price=c1[-1]["c"]; lp,sp=score_candle(c1,c4)
    print(f"[{now()}] BTC {fmt(price)} | LONG {lp}% | SHORT {sp}% | Umbral {MIN_SCORE}%")
    side=None
    if lp>=MIN_SCORE and lp>=sp: side,pct="LONG",lp
    elif sp>=MIN_SCORE: side,pct="SHORT",sp
    if not side: return
    key=f"{side}-{round(price/200)}"
    if key==last_alert_key: return
    last_alert_key=key
    emoji="🟢" if side=="LONG" else "🔴"
    trend="▲ Alcista" if c4[-1]["c"]>c4[0]["c"] else "▼ Bajista"
    lvl_str=f"\n📍 Niveles: {', '.join(fmt(l) for l in sorted(levels))}" if levels else ""
    msg=(f"{emoji} <b>SEÑAL {side} — BTC/USDT {TF.upper()}</b>\n\n"
         f"💰 Precio: <b>{fmt(price)}</b>\n📊 Checklist: <b>{pct}%</b>\n"
         f"📈 Tendencia 4H: {trend}{lvl_str}\n\n⚠️ Verificá en Binance antes de entrar.")
    sent=send(msg)
    print(f"[{now()}] ✅ Alerta {side} enviada: {sent}")

if __name__=="__main__":
    print(f"🚀 BTC Monitor Bot iniciado — chequeando cada {CHECK_INTERVAL}s")
    print(f"   Token: {TELEGRAM_TOKEN[:20]}... | Chat ID: {CHAT_ID}")
    send(f"🚀 <b>BTC Monitor Bot iniciado</b>\n\n"
         f"📊 Chequeando BTCUSDT en {TF.upper()} cada {CHECK_INTERVAL//60} minutos\n"
         f"🎯 Umbral: {MIN_SCORE}%\n\nComandos: /estado /niveles /nivel /borrar /umbral /config")
    while True:
        handle_commands()
        check_and_alert()
        time.sleep(CHECK_INTERVAL)
