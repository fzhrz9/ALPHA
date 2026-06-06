"""
ALPHA — Gate.io Dual Engine Sniper (Big Trader Standard)
Engine 1: Pullback SMC (H1 + H4) — 200 Candle Major Swing
Engine 2: Early Momentum (M15) — Volume Anomaly + Accumulation
Mode: /mode pullback | momentum | both
"""
import os, time, json, requests, threading, traceback, schedule
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from supabase import create_client, Client

# =================================================================
# 1. KONFIGURASI
# =================================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
VIP_CHANNEL_ID     = os.environ.get("VIP_CHANNEL_ID")
ADMIN_ID           = os.environ.get("ADMIN_ID")
SUPABASE_URL       = os.environ.get("SUPABASE_URL")
SUPABASE_KEY       = os.environ.get("SUPABASE_KEY")

bot = TeleBot(TELEGRAM_BOT_TOKEN)
START_TIME = time.time()
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

SCAN_MODE = os.environ.get("SCAN_MODE", "pullback").lower()

def alert_admin(text):
    try:
        bot.send_message(ADMIN_ID, f"🚨 <b>ALPHA SYSTEM</b>\n<pre>{str(text)[:800]}</pre>", parse_mode="HTML")
    except Exception: pass

# =================================================================
# 2. PRESETS & SUPABASE HELPERS
# =================================================================
PRESETS = {
    "soft":     {"min_vol_24h": 500_000,   "score_pass": 2, "label": "🟢 SOFT"},
    "standard": {"min_vol_24h": 1_000_000, "score_pass": 3, "label": " STANDARD"},
    "hard":     {"min_vol_24h": 2_500_000, "score_pass": 4, "label": "🔴 HARD"}
}

DEFAULT_CONFIG = {
    "min_vol_24h": 1_000_000,
    "score_pass": 3,
    "cooldown_hours": 24,
    "active_preset": "standard"
}

_config_cache = {}
_config_loaded_at = 0

def get_config():
    global _config_cache, _config_loaded_at
    if _config_cache and time.time() - _config_loaded_at < 300:
        return _config_cache
    try:
        rows = sb.table("config").select("key, value").execute().data
        cfg = DEFAULT_CONFIG.copy()
        for row in rows:
            k, v = row["key"], row["value"]
            if k in cfg:
                try: cfg[k] = type(DEFAULT_CONFIG[k])(v)
                except: pass
        _config_cache = cfg
        _config_loaded_at = time.time()
        return cfg
    except Exception:
        return _config_cache or DEFAULT_CONFIG.copy()

def set_config(key, value):
    try:
        sb.table("config").upsert({"key": key, "value": str(value)}).execute()
        _config_cache[key] = value
    except Exception as e: print(f"[CONFIG] error: {e}")

def apply_preset(preset_name):
    if preset_name not in PRESETS: return False, "Preset tidak wujud"
    p = PRESETS[preset_name]
    set_config("min_vol_24h", p["min_vol_24h"])
    set_config("score_pass", p["score_pass"])
    set_config("active_preset", preset_name)
    return True, p["label"]

def is_in_cooldown(contract):
    try:
        cfg = get_config()
        cutoff = int(time.time()) - int(cfg["cooldown_hours"] * 3600)
        rows = sb.table("sent_pool").select("sent_at").eq("key", contract).execute().data
        return bool(rows and rows[0]["sent_at"] > cutoff)
    except Exception: return False

def check_cooldown_override(contract, current_price):
    try:
        rows = sb.table("signals").select("entry").eq("contract", contract).execute().data
        if not rows: return False
        entry_price = rows[0].get("entry", 0)
        if entry_price <= 0: return False
        drop_pct = (entry_price - current_price) / entry_price * 100
        if drop_pct > 20:
            print(f"[OVERRIDE] {contract} turun {drop_pct:.1f}% — RESET COOLDOWN")
            sb.table("sent_pool").delete().eq("key", contract).execute()
            return True
        return False
    except Exception: return False

def add_cooldown(contract):
    try: sb.table("sent_pool").upsert({"key": contract, "sent_at": int(time.time())}).execute()
    except: pass

def save_signal(record: dict):
    try: sb.table("signals").upsert(record, on_conflict="contract").execute()
    except Exception as e: print(f"[SIGNAL SAVE] error: {e}")

def update_signal(contract, fields: dict):
    try: sb.table("signals").update(fields).eq("contract", contract).execute()
    except Exception as e: print(f"[SIGNAL UPDATE] error: {e}")

def get_active_trades():
    try:
        rows = sb.table("signals").select("*").eq("closed", False).execute().data
        return {r["contract"]: r for r in rows}
    except Exception: return {}

def get_signals_since(days=7):
    try:
        cutoff = int(time.time()) - days * 86400
        return sb.table("signals").select("*").gte("sent_at", cutoff).execute().data
    except Exception: return []

# =================================================================
# 3. HELPER & GATE.IO API + BLOCKLIST
# =================================================================
STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FRAX", "LUSD", "GUSD", "USDD", "FDUSD", "PYUSD", "USDK", "SUSD", "RSR", "EURS", "EURT", "UST", "ALUSD", "MIM", "CUSD", "CEUR", "XAUT", "PAXG"}
WRAPPED_TOKENS = {"WETH", "WBTC", "WBNB", "WSOL", "WMATIC", "WAVAX", "WFTM", "BETH", "STETH", "RETH", "CBETH"}
SYMBOL_BLACKLIST = STABLECOINS | WRAPPED_TOKENS

def is_blacklisted_symbol(sym):
    s = sym.upper().strip()
    if s in SYMBOL_BLACKLIST: return True, f"Blacklisted: {s}"
    for blacklisted in SYMBOL_BLACKLIST:
        if blacklisted in s: return True, f"Blacklisted (partial): {s}"
    for suffix in ["5L", "5S", "3L", "3S", "2L", "2S", "1L", "1S", "UP", "DOWN", "BULL", "BEAR"]:
        if s.endswith(suffix): return True, f"Leveraged: {s}"
    return False, None

def fmt(val):
    if val == 0: return "0.00"
    if abs(val) < 0.000001: return f"{val:.10f}"
    if abs(val) < 0.001: return f"{val:.8f}"
    if abs(val) < 1.0: return f"{val:.6f}"
    if abs(val) < 1000: return f"{val:.4f}"
    return f"{val:,.2f}"

def get_btc_24h_change():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=3).json()
        return float(r.get("priceChangePercent", 0))
    except Exception: return 0.0

def get_gateio_tickers():
    try:
        r = requests.get("https://api.gateio.ws/api/v4/spot/tickers", timeout=10).json()
        pairs = []
        for t in r:
            if t['currency_pair'].endswith('_USDT'):
                sym = t['currency_pair'].replace('_USDT', '')
                vol = float(t.get('quote_volume', 0))
                last_price = float(t.get('last', 0))
                pairs.append({"symbol": sym, "volume_24h": vol, "last_price": last_price, "pair": t['currency_pair']})
        return pairs
    except Exception as e:
        print(f"[GATEIO TICKERS] Error: {e}")
        return []

def get_gateio_price(sym):
    try:
        r = requests.get(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={sym}_USDT", timeout=3).json()
        if r: return float(r[0].get('last', 0))
        return 0
    except Exception: return 0

def get_gateio_klines(sym, interval="1h", limit=200):
    url = f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={sym}_USDT&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=8).json()
        candles = []
        for k in reversed(r):
            candles.append({
                't': int(k[0]), 'o': float(k[5]), 'h': float(k[3]),
                'l': float(k[4]), 'c': float(k[2]), 'v': float(k[1])
            })
        return candles
    except Exception: return []

# =================================================================
# 4. ENGINE 1: PULLBACK SMC (H1 + H4) — BIG TRADER STANDARD
# =================================================================
def analyze_smc_pa(sym, verbose=True):
    """
    BIG TRADER STANDARD:
    - Major Swing: 200 candle H1 (8 hari)
    - H4 Trend Confirmation
    - Discount Zone: Fib 0.5 - 0.786
    """
    log = lambda msg: print(f"[{sym}-H1] {msg}") if verbose else None

    # Ambil 200 candle H1 (Major Swing)
    candles = get_gateio_klines(sym, "1h", 200)
    if len(candles) < 100:
        log("❌ REJECT: Data H1 < 100 candle")
        return None

    # 1. H4 TREND CONFIRMATION (Big Trader Filter)
    candles_h4 = get_gateio_klines(sym, "4h", 50)
    if len(candles_h4) >= 20:
        h4_closes = [c['c'] for c in candles_h4[-20:]]
        h4_ema20 = sum(h4_closes) / 20
        h4_current = candles_h4[-1]['c']
        if h4_current < h4_ema20 * 0.95:
            log("❌ REJECT: H4 Downtrend kuat (bawah EMA20)")
            return None

    highs = [c['h'] for c in candles[-200:]]  # ← 200 CANDLE (8 HARI)
    lows = [c['l'] for c in candles[-200:]]
    closes = [c['c'] for c in candles[-200:]]
    volumes = [c['v'] for c in candles[-200:]]

    swing_high = max(highs)
    swing_low = min(lows)
    rng = swing_high - swing_low
    if rng <= 0:
        log("❌ REJECT: Range terlalu sempit")
        return None

    fib_500 = swing_high - (rng * 0.500)
    fib_786 = swing_high - (rng * 0.786)
    fib_zone = f"{fmt(fib_500)} - {fmt(fib_786)}"

    curr = candles[-1]
    prev = candles[-2]
    price = curr['c']

    in_discount = fib_786 <= price <= fib_500
    if not in_discount:
        if price > fib_500: log(f"❌ REJECT: PREMIUM ZONE")
        else: log(f"❌ REJECT: EXTREME (falling knife)")
        return None
    log(f"✅ FIBO PASS: Price ${fmt(price)} dalam DISCOUNT ZONE ({fib_zone})")

    ema20 = sum(closes[-20:]) / 20
    if price < ema20 * 0.90:
        log(f"❌ REJECT: Price terlalu jauh bawah EMA20 H1")
        return None

    avg_vol = sum(volumes[-20:]) / 20 if sum(volumes[-20:]) > 0 else 1
    curr_vol = curr['v']
    vpa_dry = curr_vol < (avg_vol * 0.8)

    setup_name = None
    score = 0

    # SETUP 7: LIQUIDITY SWEEP
    if curr['l'] < swing_low and curr['c'] > swing_low:
        setup_name = "💧 LIQUIDITY SWEEP"
        score += 3
        log("✅ SETUP 7: Liquidity Sweep detected")

    # SETUP 5: CANDLESTICK REVERSAL
    body = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and
                    curr['c'] > prev['o'] and curr['o'] < prev['c'])

    if is_pinbar:
        if not setup_name: setup_name = "🕯️ PINBAR REVERSAL"
        score += 2
    elif is_engulfing:
        if not setup_name: setup_name = "🐂 BULLISH ENGULFING"
        score += 2

    if vpa_dry:
        score += 1
    else:
        log("⚠️ VPA WEAK")

    if abs(price - ema20) / ema20 < 0.015:
        if not setup_name: setup_name = "📈 TREND PULLBACK"
        score += 1

    # SETUP 3: ORDER BLOCK
    for i in range(-15, -3):
        try:
            c = candles[i]
            c_next = candles[i+1]
            if c['c'] < c['o'] and c_next['c'] > c_next['o']:
                bos_size = c_next['c'] - c_next['o']
                if bos_size > (rng * 0.08) and c['l'] <= price <= c['h']:
                    if not setup_name: setup_name = " ORDER BLOCK"
                    score += 1
                    break
        except: pass

    if not setup_name or score < 2:
        log(f" REJECT: Score {score} < 2")
        return None

    sl = min(curr['l'], swing_low) * 0.995
    tp1 = swing_high
    tp2 = swing_low + (rng * 1.618)
    tp3 = swing_low + (rng * 2.618)

    risk = price - sl
    if risk <= 0: return None

    rr1 = (tp1 - price) / risk
    rr2 = (tp2 - price) / risk

    log(f" SETUP COMPLETE: {setup_name} | Score: {score}")

    return {
        "setup": setup_name, "entry": price, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": rr1, "rr2": rr2, "score": score,
        "fib_zone": fib_zone, "timeframe": "H1"
    }

# =================================================================
# 5. ENGINE 2: EARLY MOMENTUM (M15) — VOLUME ANOMALY
# =================================================================
def analyze_early_momentum(sym, verbose=True):
    """
    EARLY MOMENTUM:
    - Volume Anomaly (3x average)
    - Accumulation Pattern (range < 5%)
    - M15 Breakout
    """
    log = lambda msg: print(f"[{sym}-M15] {msg}") if verbose else None

    candles = get_gateio_klines(sym, "15m", 100)
    if len(candles) < 50:
        log("❌ REJECT: Data M15 < 50")
        return None

    avg_vol = sum(c['v'] for c in candles[-20:-1]) / 19 if len(candles) >= 20 else 1
    curr_vol = candles[-1]['v']
    
    if curr_vol < (avg_vol * 3):
        return None
    
    vol_spike = curr_vol / avg_vol
    log(f"✅ VOLUME ANOMALY: {vol_spike:.1f}x avg")

    highs = [c['h'] for c in candles[-50:]]
    lows = [c['l'] for c in candles[-50:]]
    
    range_pct = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100
    
    if range_pct > 5:
        return None
    
    log(f"✅ ACCUMULATION: Range {range_pct:.1f}%")

    curr = candles[-1]
    prev = candles[-2]
    price = curr['c']
    
    body = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and
                    curr['c'] > prev['o'] and curr['o'] < prev['c'])
    
    if not (is_pinbar or is_engulfing):
        return None
    
    setup_name = "⚡ PINBAR MOMENTUM" if is_pinbar else "⚡ ENGULFING MOMENTUM"

    entry = price
    sl = min(lows[-20:]) * 0.99
    tp1 = max(highs[-50:]) * 1.02
    tp2 = entry + (entry - sl) * 3
    tp3 = entry + (entry - sl) * 5
    
    risk = entry - sl
    if risk <= 0: return None
    
    log(f"⚡ MOMENTUM SETUP: {setup_name}")
    
    return {
        "setup": setup_name, "entry": entry, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": 0, "rr2": 0, "score": 3,
        "fib_zone": "N/A", "timeframe": "M15",
        "vol_spike": vol_spike, "range_pct": range_pct
    }

# =================================================================
# 6. SIGNAL GENERATOR
# =================================================================
def send_signal(sym, smc_data, vol_24h, btc_chg=0.0):
    cfg = get_config()
    if smc_data["score"] < cfg["score_pass"]: return False

    entry = smc_data["entry"]
    sl = smc_data["sl"]
    tp1, tp2, tp3 = smc_data["tp1"], smc_data["tp2"], smc_data["tp3"]
    timeframe = smc_data.get("timeframe", "H1")

    current_price = get_gateio_price(sym)
    if current_price > 0:
        price_gap = abs(current_price - entry) / entry * 100
        if price_gap > 2.0:
            print(f"[SKIP] {sym}: Harga dah bergerak {price_gap:.1f}%")
            return False

    sl_pct = (entry - sl) / entry * 100
    tp1_pct = (tp1 - entry) / entry * 100
    tp2_pct = (tp2 - entry) / entry * 100
    tp3_pct = (tp3 - entry) / entry * 100

    btc_warn = ""
    if btc_chg < -4.0:
        btc_warn = f"⚠️ <b>BTC ALERT:</b> BTC {btc_chg:+.2f}%\n\n"

    pair_name = f"{sym}USDT"

    if timeframe == "M15":
        engine_icon = "⚡"
        engine_label = "MOMENTUM"
    else:
        engine_icon = "🏴‍☠️"
        engine_label = "PULLBACK"

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(" Gate.io", url=f"https://www.gate.io/trade/{sym}_USDT"),
        InlineKeyboardButton("📈 TradingView", url=f"https://www.tradingview.com/chart/?symbol=GATEIO:{sym}USDT")
    )

    msg = (
        f"{engine_icon} <b>ALPHA {engine_label} — {sym}/USDT</b>\n"
        f"📋 <code>{pair_name}</code>\n\n"
        f"{btc_warn}"
        f"💰 <b>Entry:</b> <code>${fmt(entry)}</code>\n"
        f"📊 <b>Vol24H:</b> <code>${vol_24h/1e6:.2f}M</code>\n\n"
        f"🛑 <b>SL:</b> <code>${fmt(sl)}</code> <i>(-{sl_pct:.1f}%)</i>\n"
        f"📈 <b>TP1:</b> <code>${fmt(tp1)}</code> <i>(+{tp1_pct:.1f}%)</i>\n"
        f"📈 <b>TP2:</b> <code>${fmt(tp2)}</code> <i>(+{tp2_pct:.1f}%)</i>\n"
        f"📈 <b>TP3:</b> <code>${fmt(tp3)}</code> <i>(+{tp3_pct:.1f}%)</i>\n\n"
        f" <b>Setup:</b> <code>{smc_data['setup']}</code>\n"
        f"⏱️ <b>Timeframe:</b> <code>{timeframe}</code> | <b>Score:</b> <code>{smc_data['score']}/{cfg['score_pass']}</code>"
    )

    if timeframe == "M15" and "vol_spike" in smc_data:
        msg += f"\n📊 <b>Vol Spike:</b> <code>{smc_data['vol_spike']:.1f}x Avg</code>"

    try:
        sent = bot.send_message(VIP_CHANNEL_ID, msg, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
        record = {
            "contract": sym, "symbol": sym, "network": "GATEIO",
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "rr1": round(smc_data.get("rr1", 0), 2), "rr2": round(smc_data.get("rr2", 0), 2),
            "setup": smc_data["setup"], "fibo_zone": smc_data.get("fib_zone", "N/A"),
            "score": smc_data["score"], "volume_24h": vol_24h,
            "timeframe": timeframe,
            "msg_id": sent.message_id, "sent_at": int(time.time())
        }
        save_signal(record)
        add_cooldown(sym)
        print(f"[SIGNAL SENT ✅] {sym} | {smc_data['setup']} | {timeframe}")
        return True
    except Exception as e:
        alert_admin(f"Gagal hantar signal {sym}: {e}")
        return False

# =================================================================
# 7. SCANNER & TRADE MONITOR
# =================================================================
def scan_once():
    if not IS_SCANNING: return
    
    btc_chg = get_btc_24h_change()
    print(f"[BTC] 24H Change: {btc_chg:+.2f}%")

    cfg = get_config()
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")

    print(f"\n{'='*60}")
    print(f"🔍 [{datetime.now().strftime('%H:%M:%S')}] SCAN | Mode: {SCAN_MODE.upper()}")
    print(f"{'='*60}")

    tickers = get_gateio_tickers()
    print(f"[GATEIO] Total {len(tickers)} pairs")

    candidates = [t for t in tickers if t["volume_24h"] >= cfg["min_vol_24h"]]
    print(f"[SAFETY NET] {len(candidates)} pairs lulus Min Vol")

    passed = 0
    skipped_reasons = {"cooldown": 0, "active": 0, "no_data": 0, "no_setup": 0, "score_low": 0, "blacklisted": 0}

    # FIX API RATE LIMIT: M15 hanya scan top 100 volume pairs
    momentum_candidates = sorted(candidates, key=lambda x: x["volume_24h"], reverse=True)[:100]

    for t in candidates:
        sym = t["symbol"]
        current_price = t.get("last_price", 0)

        is_blacklisted, bl_reason = is_blacklisted_symbol(sym)
        if is_blacklisted:
            skipped_reasons["blacklisted"] += 1
            continue

        if is_in_cooldown(sym):
            if check_cooldown_override(sym, current_price):
                print(f"[{sym}] 🔄 OVERRIDE")
            else:
                skipped_reasons["cooldown"] += 1
                continue

        active = get_active_trades()
        if sym in active:
            skipped_reasons["active"] += 1
            continue

        print(f"\n[{sym}] 🔎 ANALYZING...")

        # ENGINE 1: PULLBACK (H1 + H4)
        if SCAN_MODE in ["pullback", "both"]:
            smc = analyze_smc_pa(sym, verbose=True)
            if smc and smc["score"] >= cfg["score_pass"]:
                if send_signal(sym, smc, t["volume_24h"], btc_chg=btc_chg):
                    passed += 1
                    time.sleep(2)

            if t in momentum_candidates:
                if SCAN_MODE == "both" and is_in_cooldown(sym):
                    continue
                
                # Ambil data M15 untuk check awal
                candles_m15 = get_gateio_klines(sym, "15m", 100)
                if len(candles_m15) < 50: continue
                
                # Check 1: Volume Anomaly
                avg_vol = sum(c['v'] for c in candles_m15[-20:-1]) / 19
                curr_vol = candles_m15[-1]['v']
                
                if curr_vol >= (avg_vol * 3):
                    # ✅ VOLUME ANOMALY LULUS! Simpan dalam Watchlist
                    if sym not in WATCHLIST:
                        WATCHLIST[sym] = time.time()
                        print(f"[{sym}] 📌 MASUK WATCHLIST: Volume Anomaly ({curr_vol/avg_vol:.1f}x)")
                    
                    # Check 2 & 3: Accumulation & Price Action
                    highs = [c['h'] for c in candles_m15[-50:]]
                    lows = [c['l'] for c in candles_m15[-50:]]
                    range_pct = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100
                    
                    curr = candles_m15[-1]
                    prev = candles_m15[-2]
                    body = abs(curr['c'] - curr['o'])
                    lower_wick = min(curr['o'], curr['c']) - curr['l']
                    is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
                    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and
                                    curr['c'] > prev['o'] and curr['o'] < prev['c'])
                    
                    # Jika SEMUA syarat cukup, terus tembak!
                    if range_pct <= 5 and (is_pinbar or is_engulfing):
                        smc_m15 = {
                            "setup": "⚡ PINBAR MOMENTUM" if is_pinbar else " ENGULFING MOMENTUM",
                            "entry": curr['c'], "sl": min(lows[-20:]) * 0.99,
                            "tp1": max(highs[-50:]) * 1.02,
                            "tp2": curr['c'] + (curr['c'] - min(lows[-20:]) * 0.99) * 3,
                            "tp3": curr['c'] + (curr['c'] - min(lows[-20:]) * 0.99) * 5,
                            "rr1": 0, "rr2": 0, "score": 3,
                            "fib_zone": "N/A", "timeframe": "M15",
                            "vol_spike": curr_vol / avg_vol, "range_pct": range_pct
                        }
                        if send_signal(sym, smc_m15, t["volume_24h"], btc_chg=btc_chg):
                            passed += 1
                            if sym in WATCHLIST: del WATCHLIST[sym]
                            time.sleep(2)
                else:
                    if sym in WATCHLIST: del WATCHLIST[sym]

    print(f"\n📊 SCAN SELESAI | {passed} signal dihantar")
    print(f"{'='*60}\n")

def monitor_active_trades():
    active = get_active_trades()
    if not active: return

    for sym, trade in active.items():
        try:
            candles = get_gateio_klines(sym, "1h", 5)
            if not candles: continue
            cp = candles[-1]['c']

            mid = trade.get("msg_id")

            def notify(text):
                kw = {"parse_mode": "HTML"}
                if mid: kw["reply_to_message_id"] = mid
                try: bot.send_message(VIP_CHANNEL_ID, text, **kw)
                except: bot.send_message(VIP_CHANNEL_ID, text, parse_mode="HTML")

            updates = {}

            if cp >= trade["tp1"] and not trade.get("tp1_hit"):
                updates["tp1_hit"] = True
                notify(f"✅ <b>{sym} — TP1 HIT!</b>\n💰 Harga: <code>${fmt(cp)}</code>\n Alih SL → BE: <code>${fmt(trade['entry'])}</code>")

            if cp >= trade["tp2"] and not trade.get("tp2_hit"):
                updates["tp2_hit"] = True
                notify(f"🚀 <b>{sym} — TP2 HIT!</b>\n💰 Harga: <code>${fmt(cp)}</code>\n Trail SL → TP1: <code>${fmt(trade['tp1'])}</code>")

            if cp >= trade["tp3"] and not trade.get("tp3_hit"):
                updates["tp3_hit"] = True
                updates["closed"] = True
                profit_pct = (cp - trade["entry"]) / trade["entry"] * 100
                notify(f"🏆 <b>{sym} — TP3 MOONSHOT!</b>\n💰 Tutup: <code>${fmt(cp)}</code>\n Profit: <code>+{profit_pct:.1f}%</code>")

            elif cp <= trade["sl"] and not trade.get("sl_hit"):
                updates["sl_hit"] = True
                updates["closed"] = True
                loss_pct = (cp - trade["entry"]) / trade["entry"] * 100
                notify(f"❌ <b>{sym} — SL HIT</b>\n💰 Tutup: <code>${fmt(cp)}</code>\n Loss: <code>{loss_pct:.1f}%</code>")

            if updates:
                update_signal(sym, updates)
                print(f"[MONITOR] {sym}: {list(updates.keys())}")

        except Exception as e:
            print(f"[MONITOR] {sym}: {e}")

# =================================================================
# 8. TELEGRAM COMMANDS
# =================================================================
IS_SCANNING = True
# ── FAST TRACK WATCHLIST ──────────────────────────────────────
WATCHLIST = {}  # Simpan token yang "hampir lulus"
WATCHLIST_TIMEOUT = 600  # 10 minit timeout
# ─ TAMAT WATCHLIST ───────────────────────────────────────────

@bot.message_handler(commands=["start", "menu"])
def cmd_start(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    cfg = get_config()
    active = get_active_trades()
    uptime_m = int((time.time() - START_TIME) / 60)
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")

    text = (
        f"🏴‍☠️ <b>ALPHA — Dual Engine Sniper</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Uptime   : <code>{uptime_m}m</code>\n"
        f"📊 Trade   : <code>{len(active)} aktif</code>\n"
        f"🔧 Scan    : <code>{'✅ AKTIF' if IS_SCANNING else ' STOP'}</code>\n\n"
        f"⚡ <b>Scan Mode:</b> <code>{SCAN_MODE.upper()}</code>\n"
        f"🎛️ <b>Preset:</b> <code>{preset_lbl}</code>\n\n"
        f"<b> Engine 1 — Pullback (H1+H4):</b>\n"
        f"200 Candle Major Swing | Fib 0.5-0.786\n\n"
        f"<b>⚡ Engine 2 — Momentum (M15):</b>\n"
        f"Volume Anomaly | Accumulation Pattern"
    )
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton(" Soft", callback_data="tune:soft"),
        InlineKeyboardButton("🟡 Standard", callback_data="tune:standard"),
        InlineKeyboardButton("🔴 Hard", callback_data="tune:hard")
    )
    kb.add(
        InlineKeyboardButton("🏴‍☠️ Pullback", callback_data="mode:pullback"),
        InlineKeyboardButton("⚡ Momentum", callback_data="mode:momentum"),
        InlineKeyboardButton("🔄 Both", callback_data="mode:both")
    )
    kb.add(
        InlineKeyboardButton("▶️ Mula", callback_data="scan_on"),
        InlineKeyboardButton("⏸ Henti", callback_data="scan_off"),
        InlineKeyboardButton(" Journal", callback_data="journal")
    )
    kb.add(
        InlineKeyboardButton(" Status", callback_data="status"),
        InlineKeyboardButton("❓ Help", callback_data="help")
    )
    bot.send_message(msg.chat.id, text, parse_mode="HTML", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tune:"))
def cb_tune(call):
    if str(call.message.chat.id) != str(ADMIN_ID): return
    bot.answer_callback_query(call.id)
    preset = call.data.split(":")[1]
    ok, lbl = apply_preset(preset)
    if ok:
        p = PRESETS[preset]
        text = f"✅ <b>PRESET DIAPLIKASI</b>\n\n{lbl}\n\n<i>Scan seterusnya akan guna preset ini.</i>"
        try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML")
        except: pass
        alert_admin(f"🎛️ Preset: {lbl}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("mode:"))
def cb_mode(call):
    global SCAN_MODE
    if str(call.message.chat.id) != str(ADMIN_ID): return
    bot.answer_callback_query(call.id)
    new_mode = call.data.split(":")[1]
    if new_mode in ["pullback", "momentum", "both"]:
        SCAN_MODE = new_mode
        bot.send_message(call.message.chat.id, f"✅ Scan mode ditukar ke: <b>{new_mode.upper()}</b>", parse_mode="HTML")
        alert_admin(f"⚡ Mode changed: {new_mode.upper()}")

@bot.callback_query_handler(func=lambda c: c.data in ["scan_on", "scan_off", "journal", "status", "help"])
def cb_actions(call):
    global IS_SCANNING
    if str(call.message.chat.id) != str(ADMIN_ID): return
    bot.answer_callback_query(call.id)

    if call.data == "scan_on":
        IS_SCANNING = True
        bot.send_message(call.message.chat.id, "▶️ Scan AKTIF.")
        threading.Thread(target=scan_once).start()
    elif call.data == "scan_off":
        IS_SCANNING = False
        bot.send_message(call.message.chat.id, "⏸ Scan BERHENTI.")
    elif call.data == "journal":
        bot.send_message(call.message.chat.id, generate_journal(), parse_mode="HTML")
    elif call.data == "status":
        cmd_status(call.message)
    elif call.data == "help":
        cmd_help(call.message)

@bot.message_handler(commands=["tune"])
def cmd_tune(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    parts = msg.text.split()
    if len(parts) < 2:
        active = get_config().get("active_preset", "standard")
        text = (
            f"🎛️ <b>TUNE PRESET</b>\n\n"
            f"<b>Aktif:</b> {PRESETS[active]['label']}\n\n"
            f" <code>/tune soft</code> — Vol $500K, Pass 2\n"
            f"🟡 <code>/tune standard</code> — Vol $1M, Pass 3\n"
            f"🔴 <code>/tune hard</code> — Vol $2.5M, Pass 4"
        )
        bot.reply_to(msg, text, parse_mode="HTML")
        return
    ok, lbl = apply_preset(parts[1].lower())
    bot.reply_to(msg, f"✅ {lbl}" if ok else " Preset tidak sah")

@bot.message_handler(commands=["mode"])
def cmd_mode(msg):
    global SCAN_MODE
    if str(msg.chat.id) != str(ADMIN_ID): return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, f"⚡ Mode semasa: <b>{SCAN_MODE.upper()}</b>\n\nGuna: <code>/mode pullback</code> | <code>/mode momentum</code> | <code>/mode both</code>", parse_mode="HTML")
        return
    new_mode = parts[1].lower()
    if new_mode in ["pullback", "momentum", "both"]:
        SCAN_MODE = new_mode
        bot.reply_to(msg, f"✅ Scan mode ditukar ke: <b>{new_mode.upper()}</b>", parse_mode="HTML")
        alert_admin(f"⚡ Mode changed: {new_mode.upper()}")
    else:
        bot.reply_to(msg, "❌ Mode tidak sah. Guna: pullback | momentum | both")

@bot.message_handler(commands=["pair"])
def cmd_pair(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /pair [SYMBOL] (Contoh: /pair BTC)")
        return
    sym = parts[1].upper()
    bot.reply_to(msg, f"🔍 Menganalisa <code>{sym}</code>...", parse_mode="HTML")

    def _do():
        smc_h1 = analyze_smc_pa(sym, verbose=False)
        smc_m15 = analyze_early_momentum(sym, verbose=False)
        
        if smc_h1:
            bot.send_message(msg.chat.id, f"🏴‍☠️ <b>{sym} — PULLBACK (H1)</b>\nSetup: <code>{smc_h1['setup']}</code>\nScore: <code>{smc_h1['score']}</code>", parse_mode="HTML")
        elif smc_m15:
            bot.send_message(msg.chat.id, f"⚡ <b>{sym} — MOMENTUM (M15)</b>\nSetup: <code>{smc_m15['setup']}</code>\nVol Spike: <code>{smc_m15['vol_spike']:.1f}x</code>", parse_mode="HTML")
        else:
            bot.send_message(msg.chat.id, f"❌ {sym}: Tiada setup", parse_mode="HTML")
    threading.Thread(target=_do).start()

@bot.message_handler(commands=["scan"])
def cmd_scan(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    bot.reply_to(msg, "⚙️ Scan dipaksa...")
    threading.Thread(target=scan_once).start()

@bot.message_handler(commands=["status"])
def cmd_status(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    cfg = get_config()
    active = get_active_trades()
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")
    bot.reply_to(msg, (
        f"📊 <b>STATUS</b>\n"
        f"Scan  : {'🟢 AKTIF' if IS_SCANNING else ' STOP'}\n"
        f"Mode  : <code>{SCAN_MODE.upper()}</code>\n"
        f"Trade : <code>{len(active)}</code> aktif\n\n"
        f"️ Preset: <code>{preset_lbl}</code>\n"
        f"Vol   : <code>${cfg['min_vol_24h']/1e6:.1f}M</code>\n"
        f"Pass  : <code>{cfg['score_pass']}</code>"
    ), parse_mode="HTML")

@bot.message_handler(commands=["journal"])
def cmd_journal(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    bot.reply_to(msg, generate_journal(), parse_mode="HTML")

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    bot.reply_to(msg, (
        "📖 <b>ARAHAN</b>\n\n"
        "/start          — Menu utama\n"
        "/scan           — Paksa kitaran scan\n"
        "/pair [SYM]     — Analisis manual\n"
        "/journal        — Laporan 7 hari\n"
        "/status         — Status semasa\n"
        "/mode [MODE]    — Tukar scan mode\n"
        "/tune [PRESET]  — Tukar preset"
    ), parse_mode="HTML")

# =================================================================
# 9. JOURNAL
# =================================================================
def generate_journal():
    trades = get_signals_since(7)
    if not trades: return " <b>JOURNAL (7D)</b>\n\nTiada signal dalam 7 hari lepas."

    total = len(trades)
    tp1_n = sum(1 for t in trades if t.get("tp1_hit"))
    tp2_n = sum(1 for t in trades if t.get("tp2_hit"))
    tp3_n = sum(1 for t in trades if t.get("tp3_hit"))
    sl_n = sum(1 for t in trades if t.get("sl_hit"))
    open_n = sum(1 for t in trades if not t.get("closed"))

    setups = {}
    for t in trades:
        s = t.get("setup", "Unknown")
        setups[s] = setups.get(s, 0) + 1
    setup_str = " | ".join(f"{k}: {v}" for k, v in sorted(setups.items(), key=lambda x: -x[1]))

    wr = tp1_n / total * 100 if total else 0

    return (
        f"📓 <b>ALPHA JOURNAL (7D)</b>\n\n"
        f"├ Total Signal : <code>{total}</code>\n"
        f" TP1 Hit      : <code>{tp1_n} ({wr:.0f}%)</code>\n"
        f"├ TP2 Hit      : <code>{tp2_n}</code>\n"
        f"├ TP3 Moonshot : <code>{tp3_n}</code>\n"
        f"├ SL Hit       : <code>{sl_n}</code>\n"
        f"└ Masih Buka   : <code>{open_n}</code>\n\n"
        f"<b>🧠 Setup Breakdown:</b>\n<code>{setup_str}</code>"
    )

# =================================================================
# 10. SCHEDULER & MAIN
# =================================================================
def fast_track_watchlist():
    """Micro-Scan setiap 30 saat untuk token dalam Watchlist."""
    if not IS_SCANNING or not WATCHLIST: return

    print(f"\n[FAST TRACK] Checking {len(WATCHLIST)} tokens...")
    symbols_to_remove = []

    for sym, added_time in list(WATCHLIST.items()):
        if time.time() - added_time > WATCHLIST_TIMEOUT:
            symbols_to_remove.append(sym)
            continue

        try:
            candles = get_gateio_klines(sym, "15m", 50)
            if len(candles) < 20: continue

            highs = [c['h'] for c in candles[-50:]]
            lows = [c['l'] for c in candles[-50:]]
            range_pct = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100
            
            curr = candles[-1]
            prev = candles[-2]
            body = abs(curr['c'] - curr['o'])
            lower_wick = min(curr['o'], curr['c']) - curr['l']
            is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
            is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and
                            curr['c'] > prev['o'] and curr['o'] < prev['c'])

            if range_pct <= 5 and (is_pinbar or is_engulfing):
                avg_vol = sum(c['v'] for c in candles[-20:-1]) / 19
                vol_spike = curr['v'] / avg_vol if avg_vol > 0 else 0
                
                smc_m15 = {
                    "setup": "⚡ PINBAR MOMENTUM" if is_pinbar else "⚡ ENGULFING MOMENTUM",
                    "entry": curr['c'], "sl": min(lows[-20:]) * 0.99,
                    "tp1": max(highs[-50:]) * 1.02,
                    "tp2": curr['c'] + (curr['c'] - min(lows[-20:]) * 0.99) * 3,
                    "tp3": curr['c'] + (curr['c'] - min(lows[-20:]) * 0.99) * 5,
                    "rr1": 0, "rr2": 0, "score": 3,
                    "fib_zone": "N/A", "timeframe": "M15",
                    "vol_spike": vol_spike, "range_pct": range_pct
                }
                
                if send_signal(sym, smc_m15, 0, btc_chg=0.0):
                    symbols_to_remove.append(sym)
                    print(f"[{sym}] 🚀 TRIGGERED FROM WATCHLIST!")
        except Exception as e:
            print(f"[FAST TRACK ERROR] {sym}: {e}")

    for sym in symbols_to_remove:
        if sym in WATCHLIST: del WATCHLIST[sym]

def run_scheduler():
    schedule.every(5).minutes.do(lambda: threading.Thread(target=scan_once).start())
    schedule.every(5).minutes.do(lambda: threading.Thread(target=monitor_active_trades).start())
    # Fast Track Micro-Scan setiap 30 saat
    schedule.every(30).seconds.do(lambda: threading.Thread(target=fast_track_watchlist).start())
    while True:
        schedule.run_pending()
        time.sleep(30)

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALPHA DUAL ENGINE ACTIVE")
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
    def log_message(self, *args): pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", port), RenderHandler).serve_forever(), daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()

    time.sleep(5)
    alert_admin(
        f"🏴‍☠️ ALPHA Dual Engine DEPLOYED\n"
        f"Mode: {SCAN_MODE.upper()}\n"
        f"Preset: {PRESETS[get_config()['active_preset']]['label']}"
    )
    threading.Thread(target=scan_once).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
