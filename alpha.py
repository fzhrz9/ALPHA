"""
ALPHA — Gate.io Dual Engine Sniper (Institutional Grade)
Engine 1: Pullback SMC (H1 + H4) — Fractal Swing + EMA + VPA Impulse
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
VIP_CHANNEL_ID = os.environ.get("VIP_CHANNEL_ID")
ADMIN_ID = os.environ.get("ADMIN_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

bot = TeleBot(TELEGRAM_BOT_TOKEN)
START_TIME = time.time()
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

SCAN_MODE = os.environ.get("SCAN_MODE", "pullback").lower()

def alert_admin(text):
    try:
        bot.send_message(ADMIN_ID, f"🚨 <b>ALPHA SYSTEM</b>\n<pre>{str(text)[:800]}</pre>", parse_mode="HTML")
    except Exception:
        pass

# =================================================================
# 2. PRESETS & SUPABASE HELPERS
# =================================================================
PRESETS = {
    "soft": {"min_vol_24h": 500_000, "score_pass": 2, "label": "🟢 SOFT"},
    "standard": {"min_vol_24h": 1_000_000, "score_pass": 3, "label": "🟡 STANDARD"},
    "hard": {"min_vol_24h": 2_500_000, "score_pass": 4, "label": "🔴 HARD"}
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
                try:
                    cfg[k] = type(DEFAULT_CONFIG[k])(v)
                except Exception:
                    pass
        _config_cache = cfg
        _config_loaded_at = time.time()
        return cfg
    except Exception:
        return _config_cache or DEFAULT_CONFIG.copy()

def set_config(key, value):
    try:
        sb.table("config").upsert({"key": key, "value": str(value)}).execute()
        _config_cache[key] = value
    except Exception as e:
        print(f"[CONFIG] error: {e}")

def apply_preset(preset_name):
    if preset_name not in PRESETS:
        return False, "Preset tidak wujud"
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
    except Exception:
        return False

def check_cooldown_override(contract, current_price):
    try:
        rows = sb.table("signals").select("entry").eq("contract", contract).execute().data
        if not rows:
            return False
        entry_price = rows[0].get("entry", 0)
        if entry_price <= 0:
            return False
        drop_pct = (entry_price - current_price) / entry_price * 100
        if drop_pct > 20:
            print(f"[OVERRIDE] {contract} turun {drop_pct:.1f}% — RESET COOLDOWN")
            sb.table("sent_pool").delete().eq("key", contract).execute()
            return True
        return False
    except Exception:
        return False

def add_cooldown(contract):
    try:
        sb.table("sent_pool").upsert({"key": contract, "sent_at": int(time.time())}).execute()
    except Exception:
        pass

def save_signal(record: dict):
    try:
        sb.table("signals").upsert(record, on_conflict="contract").execute()
    except Exception as e:
        print(f"[SIGNAL SAVE] error: {e}")

def update_signal(contract, fields: dict):
    try:
        sb.table("signals").update(fields).eq("contract", contract).execute()
    except Exception as e:
        print(f"[SIGNAL UPDATE] error: {e}")

def get_active_trades():
    try:
        rows = sb.table("signals").select("*").eq("closed", False).execute().data
        return {r["contract"]: r for r in rows}
    except Exception:
        return {}

def get_signals_since(days=7):
    try:
        cutoff = int(time.time()) - days * 86400
        return sb.table("signals").select("*").gte("sent_at", cutoff).execute().data
    except Exception:
        return []

# =================================================================
# 3. HELPER & GATE.IO API + BLOCKLIST + MATH FUNCTIONS
# =================================================================
STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FRAX", "LUSD", "GUSD", "USDD", "FDUSD", "PYUSD", "USDK", "SUSD", "RSR", "EURS", "EURT", "UST", "ALUSD", "MIM", "CUSD", "CEUR", "XAUT", "PAXG"}
WRAPPED_TOKENS = {"WETH", "WBTC", "WBNB", "WSOL", "WMATIC", "WAVAX", "WFTM", "BETH", "STETH", "RETH", "CBETH"}
SYMBOL_BLACKLIST = STABLECOINS | WRAPPED_TOKENS

def is_blacklisted_symbol(sym):
    s = sym.upper().strip()
    if s in SYMBOL_BLACKLIST:
        return True, f"Blacklisted: {s}"
    for blacklisted in SYMBOL_BLACKLIST:
        if blacklisted in s:
            return True, f"Blacklisted (partial): {s}"
    for suffix in ["5L", "5S", "3L", "3S", "2L", "2S", "1L", "1S", "UP", "DOWN", "BULL", "BEAR"]:
        if s.endswith(suffix):
            return True, f"Leveraged: {s}"
    return False, None

def fmt(val):
    if val == 0:
        return "0.00"
    if abs(val) < 0.000001:
        return f"{val:.10f}"
    if abs(val) < 0.001:
        return f"{val:.8f}"
    if abs(val) < 1.0:
        return f"{val:.6f}"
    if abs(val) < 1000:
        return f"{val:.4f}"
    return f"{val:,.2f}"

def get_btc_24h_change():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=3).json()
        return float(r.get("priceChangePercent", 0))
    except Exception:
        return 0.0

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
        if r:
            return float(r[0].get('last', 0))
        return 0
    except Exception:
        return 0

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
    except Exception:
        return []

# =================================================================
# MATH HELPERS (INSTITUTIONAL GRADE)
# =================================================================
def calculate_ema(data, period):
    """Kira Exponential Moving Average (EMA) sebenar."""
    if len(data) < period:
        return sum(data) / len(data) if data else 0
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_atr(candles, period=14):
    """Kira Average True Range (ATR) untuk dynamic threshold."""
    if len(candles) < period + 1:
        return 0
    trs = []
    for i in range(-period, 0):
        c = candles[i]
        prev = candles[i - 1]
        tr = max(c['h'] - c['l'], abs(c['h'] - prev['c']), abs(c['l'] - prev['c']))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def find_fractal_swings(candles, lookback=2):
    """
    Cari Swing High/Low menggunakan Fractal Pattern (5-candle).
    Returns: list of {'type': 'SH'/'SL', 'price': float, 'index': int}
    """
    swings = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        # Swing High: candle tengah paling tinggi berbanding 2 kiri & 2 kanan
        is_sh = True
        is_sl = True
        for j in range(1, lookback + 1):
            if candles[i]['h'] < candles[i - j]['h'] or candles[i]['h'] < candles[i + j]['h']:
                is_sh = False
            if candles[i]['l'] > candles[i - j]['l'] or candles[i]['l'] > candles[i + j]['l']:
                is_sl = False
        if is_sh:
            swings.append({'type': 'SH', 'price': candles[i]['h'], 'index': i})
        elif is_sl:
            swings.append({'type': 'SL', 'price': candles[i]['l'], 'index': i})
    return swings

def check_market_structure(swings):
    """Semak struktur market secara fleksibel (Institutional SMC)."""
    if len(swings) < 2:
        return 'unknown'
        
    recent_swings = swings[-4:]
    highs = [s for s in recent_swings if s['type'] == 'SH']
    lows = [s for s in recent_swings if s['type'] == 'SL']
    
    # Fallback jika data bercampur (contoh: 3 SH, 1 SL)
    if len(highs) < 2 or len(lows) < 2:
        last_swing = recent_swings[-1]
        prev_swing = recent_swings[-2] if len(recent_swings) >= 2 else None
        
        if last_swing['type'] == 'SH' and prev_swing and prev_swing['type'] == 'SH':
            return 'uptrend' if last_swing['price'] > prev_swing['price'] else 'downtrend'
        elif last_swing['type'] == 'SL' and prev_swing and prev_swing['type'] == 'SL':
            return 'downtrend' if last_swing['price'] < prev_swing['price'] else 'uptrend'
        return 'sideway'

    # Susun mengikut urutan kronologi
    highs.sort(key=lambda x: x['index'])
    lows.sort(key=lambda x: x['index'])
    
    is_higher_high = highs[-1]['price'] > highs[-2]['price']
    is_higher_low = lows[-1]['price'] > lows[-2]['price']
    
    is_lower_high = highs[-1]['price'] < highs[-2]['price']
    is_lower_low = lows[-1]['price'] < lows[-2]['price']
    
    # Keputusan Struktur
    if is_higher_high and is_higher_low:
        return 'uptrend'
    elif is_lower_high and is_lower_low:
        return 'downtrend'
    elif is_higher_high and not is_lower_low:
        return 'uptrend_breakout'
    else:
        return 'sideway'

# =================================================================
# 4. ENGINE 1: PULLBACK SMC (H1 + H4) — INSTITUTIONAL GRADE
# =================================================================
def analyze_smc_pa(sym, verbose=True):
    """
    INSTITUTIONAL GRADE:
    - Fractal Swing Points (5-candle)
    - Market Structure Check (HH/HL)
    - EMA Sebenar (bukan SMA)
    - VPA Impulse vs Pullback
    - Strict Setup Detection
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
        h4_swings = find_fractal_swings(candles_h4, lookback=1)
        h4_structure = check_market_structure(h4_swings)
        if h4_structure == 'downtrend':
            log("❌ REJECT: H4 structure downtrend (LH/LL)")
            return None
        elif h4_structure == 'unknown':
            log("⚠️ H4 structure unknown, skip H4 filter")

    # 2. FRACTAL SWING POINTS (BUKAN max/min)
    swings = find_fractal_swings(candles, lookback=2)
    
    # 3. SEMAK STRUKTUR MARKET
    if len(swings) >= 4:
        # Guna fractal structure jika cukup data
        structure = check_market_structure(swings)
    else:
        # FALLBACK: Guna EMA20 vs EMA50 (Big Trader Standard)
        log("⚠️ Fractal swings tidak cukup, guna EMA trend detection")
        closes = [c['c'] for c in candles[-200:]]
        ema20 = calculate_ema(closes, 20)
        ema50 = calculate_ema(closes, 50)
        current_price = candles[-1]['c']
        
        if ema20 > ema50 and current_price > ema20:
            structure = 'uptrend'
        elif ema20 < ema50 and current_price < ema20:
            structure = 'downtrend'
        else:
            structure = 'unknown'
    
    if structure == 'downtrend':
        log(f"❌ REJECT: Market structure downtrend (LH/LL)")
        return None
    elif structure in ['uptrend', 'uptrend_breakout', 'sideway']:
        log(f"✅ STRUCTURE: {structure}")
    else:
        log(f"⚠️ STRUCTURE: {structure} (Proceed with caution)")

    # Ambil swing high/low dari fractal (bukan max/min)
    shs = [s for s in swings if s['type'] == 'SH']
    sls = [s for s in swings if s['type'] == 'SL']
    swing_high = shs[-1]['price'] if shs else max(c['h'] for c in candles[-200:])
    swing_low = sls[-1]['price'] if sls else min(c['l'] for c in candles[-200:])

    rng = swing_high - swing_low
    if rng <= 0:
        log("❌ REJECT: Range terlalu sempit")
        return None

    # 4. KIRA FIBONACCI
    fib_500 = swing_high - (rng * 0.500)
    fib_618 = swing_high - (rng * 0.618)
    fib_786 = swing_high - (rng * 0.786)
    fib_zone = f"{fmt(fib_500)} - {fmt(fib_786)}"

    curr = candles[-1]
    prev = candles[-2]
    price = curr['c']

    # 5. ANTI-FOMO: WAJIB di Discount Zone
    in_discount = fib_786 <= price <= fib_500
    if not in_discount:
        if price > fib_500:
            log(f"❌ REJECT: PREMIUM ZONE")
        else:
            log(f"❌ REJECT: EXTREME (falling knife)")
        return None
    log(f"✅ FIBO PASS: Price ${fmt(price)} dalam DISCOUNT ZONE ({fib_zone})")

    # 6. EMA SEBENAR (BUKAN SMA)
    closes = [c['c'] for c in candles[-200:]]
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)

    # Semak trend: EMA20 > EMA50 = uptrend
    is_uptrend = ema20 > ema50
    if not is_uptrend:
        log("❌ REJECT: EMA20 < EMA50 (bukan uptrend)")
        return None

    # Dynamic threshold menggunakan ATR
    atr = calculate_atr(candles, 14)
    distance_from_ema = abs(price - ema20)
    threshold = atr * 0.5 if atr > 0 else ema20 * 0.015

    if price < ema20 * 0.90:
        log(f"❌ REJECT: Price terlalu jauh bawah EMA20 H1")
        return None

    # 7. VPA IMPULSE vs PULLBACK (BUKAN purata rawak)
    impulse_vols = [c['v'] for c in candles[-20:] if c['c'] > c['o']]
    avg_impulse_vol = sum(impulse_vols) / len(impulse_vols) if impulse_vols else 1
    pullback_vols = [c['v'] for c in candles[-20:] if c['c'] < c['o']]
    avg_pullback_vol = sum(pullback_vols) / len(pullback_vols) if pullback_vols else 1

    curr_vol = curr['v']
    vpa_dry = avg_pullback_vol < (avg_impulse_vol * 0.7)

    # 8. KIRA ATR UNTUK SETUP DETECTION
    atr = calculate_atr(candles, 14)

    setup_name = None
    score = 0

    # ─── SETUP 7: LIQUIDITY SWEEP (STRICT) ────────────────────
    body = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    wick_ratio = lower_wick / body if body > 0 else 0

    # Kira berapa kali swing low di-test sebelum ini
    touches = sum(1 for c in candles[-50:-1] if abs(c['l'] - swing_low) / swing_low < 0.01)

    if (curr['l'] < swing_low and
        curr['c'] > swing_low and
        wick_ratio >= 2.0 and
        touches >= 2 and
        curr_vol > avg_impulse_vol):
        setup_name = "💧 LIQUIDITY SWEEP"
        score += 3
        log(f"✅ SETUP 7: Sweep ({touches} touches, wick {wick_ratio:.1f}x)")

    # ─── SETUP 5: CANDLESTICK REVERSAL (STRICT) ───────────────
    total_range = curr['h'] - curr['l']
    upper_wick = curr['h'] - max(curr['o'], curr['c'])

    is_pinbar = (lower_wick > (body * 2) and
                 upper_wick < (total_range * 0.1) and
                 curr['c'] > curr['o'] and
                 total_range > (atr * 0.5) if atr > 0 else True)

    prev_body = abs(prev['c'] - prev['o'])
    curr_body = abs(curr['c'] - curr['o'])

    is_engulfing = (curr['c'] > curr['o'] and
                    prev['c'] < prev['o'] and
                    curr['c'] > prev['o'] and
                    curr['o'] <= prev['c'] and
                    curr_body > prev_body and
                    curr_vol > prev['v'])

    if is_pinbar:
        if not setup_name:
            setup_name = "🕯️ PINBAR REVERSAL"
        score += 2
        log("✅ SETUP 5: Pinbar valid")
    elif is_engulfing:
        if not setup_name:
            setup_name = "🐂 BULLISH ENGULFING"
        score += 2
        log("✅ SETUP 5: Engulfing valid")

    # ─── SETUP 4: VPA CONFIRMATION ────────────────────────────
    if vpa_dry:
        score += 1
        log(f"✅ VPA PASS: Pullback vol < 70% impulse vol")
    else:
        log("️ VPA WEAK (Optional - tidak reject)")

    # ─── SETUP 2: TREND PULLBACK (EMA SEBENAR) ────────────────
    if (is_uptrend and
        distance_from_ema < threshold and
        price > ema20):
        if not setup_name:
            setup_name = "📈 TREND PULLBACK"
        score += 1
        log("✅ SETUP 2: Pullback ke EMA20 (uptrend)")

    # ─── SETUP 3: ORDER BLOCK (STRICT - 100 CANDLE) ───────────
    for i in range(-100, -3):
        try:
            c = candles[i]
            c_next = candles[i + 1]
            if c['c'] < c['o'] and c_next['c'] > c_next['o']:
                bos_size = c_next['c'] - c_next['o']
                if bos_size > (rng * 0.01):
                    ob_high = c['h']
                    ob_low = c['l']
                    if ob_low <= price <= ob_high:
                        # Semak OB belum di-test (fresh)
                        touches_after = sum(1 for j in range(i + 2, 0)
                                          if ob_low <= candles[j]['l'] <= ob_high)
                        if touches_after <= 1:
                            if not setup_name:
                                setup_name = "🧱 FRESH ORDER BLOCK"
                            score += 2
                            log(f"✅ SETUP 3: Fresh OB detected")
                            break
        except Exception:
            pass

    # Gagal score minimum
    if not setup_name or score < 2:
        log(f"❌ REJECT: Score {score} < 2")
        return None

    # 9. KIRA SL & TP
    sl = min(curr['l'], swing_low) * 0.995
    tp1 = swing_high
    tp2 = swing_low + (rng * 1.618)
    tp3 = swing_low + (rng * 2.618)

    risk = price - sl
    if risk <= 0:
        log("❌ REJECT: Risk invalid")
        return None

    rr1 = (tp1 - price) / risk
    rr2 = (tp2 - price) / risk

    log(f" SETUP COMPLETE: {setup_name} | Score: {score}")
    # ── DETECT PENDING BOS (Untuk Real-Time Signal) ───────────
    # Letak di sini sebab variable 'price' dah wujud
    shs_list = [s for s in swings if s['type'] == 'SH']
    sls_list = [s for s in swings if s['type'] == 'SL']
    
    if len(sls_list) >= 2:
        last_hl = sls_list[-1]['price']
        distance = abs(price - last_hl) / last_hl * 100
        if distance <= 2.0 and price > last_hl:
            if sym not in BOS_WATCHLIST:
                BOS_WATCHLIST[sym] = {"level": last_hl, "type": "HL", "added": time.time()}
                log(f"📌 PENDING BOS: HL ${fmt(last_hl)} (jarak {distance:.1f}%)")
                
    if len(shs_list) >= 2:
        last_lh = shs_list[-1]['price']
        distance = abs(price - last_lh) / last_lh * 100
        if distance <= 2.0 and price < last_lh:
            if sym not in BOS_WATCHLIST:
                BOS_WATCHLIST[sym] = {"level": last_lh, "type": "LH", "added": time.time()}
                log(f"📌 PENDING BOS: LH ${fmt(last_lh)} (jarak {distance:.1f}%)")
    # ── TAMAT DETECT PENDING BOS ──────────────────────────────

    return {
        "setup": setup_name, "entry": price, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": rr1, "rr2": rr2, "score": score,
        "fib_zone": fib_zone, "timeframe": "H1",
        "structure": structure
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

    # ── HTF BIAS (H4 Trend Check) ─────────────────────────────
    h4_bias = "neutral"
    try:
        # Ambil 50 candle H4 untuk kira EMA20
        candles_h4 = get_gateio_klines(sym, "4h", 50)
        if len(candles_h4) >= 20:
            h4_closes = [c['c'] for c in candles_h4[-20:]]
            
            # Kira EMA20 H4 (Formula Sebenar)
            multiplier = 2 / 21
            h4_ema = sum(h4_closes[:20]) / 20
            for close in h4_closes[20:]:
                h4_ema = (close - h4_ema) * multiplier + h4_ema
            
            h4_current = candles_h4[-1]['c']
            
            if h4_current > h4_ema:
                h4_bias = "uptrend"    # WITH-TREND (Safe)
            else:
                h4_bias = "downtrend"  # COUNTER-TREND (Risky)
    except Exception:
        pass

    # LARAS TARGET BERDASARKAN H4 BIAS
    if h4_bias == "downtrend":
        # Counter-trend: Ketatkan SL dan Potong TP3
        sl = min(lows[-20:]) * 0.995  # SL lebih ketat (0.5% instead of 1%)
        tp3 = tp2                     # Potong Moonshot, exit di TP2
        setup_name = "⚡ COUNTER-TREND (Risky)"
        log(f"⚠️ HTF BIAS: H4 Downtrend. SL tightened, TP3 capped.")
    else:
        log(f"✅ HTF BIAS: H4 Uptrend/Neutral. Full targets.")
    # ── TAMAT HTF BIAS ────────────────────────────────────────

    risk = entry - sl
    if risk <= 0: return None

    log(f"⚡ MOMENTUM SETUP: {setup_name} | H4 Bias: {h4_bias}")

    return {
         "setup": setup_name, "entry": entry, "sl": sl,
         "tp1": tp1, "tp2": tp2, "tp3": tp3,
         "rr1": 0, "rr2": 0, "score": 3,
         "fib_zone": "N/A", "timeframe": "M15",
         "vol_spike": vol_spike, "range_pct": range_pct,
         "h4_bias": h4_bias
    }

# =================================================================
# 6. SIGNAL GENERATOR
# =================================================================
def send_signal(sym, smc_data, vol_24h, btc_chg=0.0):
    cfg = get_config()
    if smc_data["score"] < cfg["score_pass"]:
        return False

    entry = smc_data["entry"]
    sl = smc_data["sl"]
    tp1, tp2, tp3 = smc_data["tp1"], smc_data["tp2"], smc_data["tp3"]
    timeframe = smc_data.get("timeframe", "H1")

    current_price = get_gateio_price(sym)
    if current_price > 0:
        price_gap = abs(current_price - entry) / entry * 100
        
        # ── DYNAMIC THRESHOLD (ATR BASED) ─────────────────────
        # Ambil ATR dari data H1 (14 candle) untuk kira volatiliti coin
        candles_atr = get_gateio_klines(sym, "1h", 20)
        threshold = 2.0 # Default minimum threshold
        if len(candles_atr) >= 15:
            trs = []
            for i in range(-14, 0):
                c = candles_atr[i]
                prev = candles_atr[i-1]
                tr = max(c['h'] - c['l'], abs(c['h'] - prev['c']), abs(c['l'] - prev['c']))
                trs.append(tr)
            atr = sum(trs) / len(trs)
            # Threshold: 2x ATR percentage (Minimum 2.0%)
            atr_pct = (atr / entry) * 100
            threshold = max(2.0, atr_pct * 2.0) 
        # ── TAMAT DYNAMIC THRESHOLD ───────────────────────────

        if price_gap > threshold:
            print(f"[SKIP] {sym}: Harga dah bergerak {price_gap:.1f}% (Threshold: {threshold:.1f}%)")
            
            # ─ TAMBAH KE PULLBACK WATCHLIST ─────────────────
            # Jika setup cukup kuat (score >= 2), simpan untuk monitor pullback
            if smc_data["score"] >= 2 and sym not in PULLBACK_WATCHLIST:
                add_pullback_watchlist(sym, smc_data)
            # ── TAMAT TAMBAH ──────────────────────────────────
            
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
        InlineKeyboardButton("📊 Gate.io", url=f"https://www.gate.io/trade/{sym}_USDT"),
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
        f"🧠 <b>Setup:</b> <code>{smc_data['setup']}</code>\n"
        f"⏱️ <b>Timeframe:</b> <code>{timeframe}</code> | <b>Score:</b> <code>{smc_data['score']}/{cfg['score_pass']}</code>"
    )

    if timeframe == "M15" and "vol_spike" in smc_data:
        msg += f"\n <b>Vol Spike:</b> <code>{smc_data['vol_spike']:.1f}x Avg</code>"

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
IS_SCANNING = True
WATCHLIST = {}  # Simpan token yang "hampir lulus"
WATCHLIST_TIMEOUT = 600  # 10 minit timeout

# ── PULLBACK WATCHLIST (V-SHAPE RECOVERY) ─────────────────────
PULLBACK_WATCHLIST = {}  # Format: {"SYM": {"entry": x, "fib_500": y, "fib_786": z, "added": time}}
PULLBACK_TIMEOUT = 1200  # 20 minit timeout

def add_pullback_watchlist(sym, smc_data):
    """Simpan coin ke Pullback Watchlist bila harga dah pump jauh."""
    PULLBACK_WATCHLIST[sym] = {
        "entry": smc_data["entry"],
        "fib_500": smc_data.get("tp1", smc_data["entry"] * 1.05), # Fallback jika tiada fib
        "fib_786": smc_data["sl"], # Guna SL sebagai zon extreme
        "added": time.time(),
        "setup": smc_data["setup"]
    }
    print(f"[{sym}] 📌 MASUK PULLBACK WATCHLIST: Tunggu harga balik ke zon {fmt(smc_data['sl'])} - {fmt(smc_data['entry'])}")
# ── TAMAT PULLBACK WATCHLIST ──────────────────────────────────

# ── BOS (Break of Structure) WATCHLIST ────────────────────────
BOS_WATCHLIST = {}  # Format: {"SYMBOL": {"level": price, "type": "HL"/"LH", "added": time}}
# ── TAMAT BOS WATCHLIST ───────────────────────────────────────

def scan_once():
    if not IS_SCANNING:
        return

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

        # ENGINE 2: MOMENTUM (M15) - HANYA TOP 100 VOLUME
        if SCAN_MODE in ["momentum", "both"]:
            if t in momentum_candidates:
                if SCAN_MODE == "both" and is_in_cooldown(sym):
                    continue
                
                # FIX 2: TREND ALIGNMENT (H1 Filter) - Jangan ambil M15 long kalau H1 bearish
                candles_h1 = get_gateio_klines(sym, "1h", 50)
                if len(candles_h1) >= 20:
                    h1_closes = [c['c'] for c in candles_h1[-20:]]
                    h1_ema20 = sum(h1_closes) / 20 # Simple EMA approximation for speed
                    if candles_h1[-1]['c'] < h1_ema20:
                        continue # Skip, H1 downtrend

                # Ambil data M15
                candles_m15 = get_gateio_klines(sym, "15m", 100)
                if len(candles_m15) < 50: continue
                
                curr = candles_m15[-1]
                
                # FIX 1: UNCLOSED CANDLE ILLUSION - Tunggu candle close atau 90% masa
                current_time = time.time()
                if (current_time - curr['t']) < (15 * 60 * 0.90):
                    continue # Skip, candle belum close

                # Check 1: Volume Anomaly
                avg_vol = sum(c['v'] for c in candles_m15[-20:-1]) / 19
                curr_vol = curr['v']
                 
                if curr_vol >= (avg_vol * 3):
                    if sym not in WATCHLIST:
                        WATCHLIST[sym] = time.time()
                        print(f"[{sym}] 📌 MASUK WATCHLIST: Volume Anomaly ({curr_vol/avg_vol:.1f}x)")
                    
                    highs = [c['h'] for c in candles_m15[-50:]]
                    lows = [c['l'] for c in candles_m15[-50:]]
                    range_pct = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100
                    
                    # FIX 3: CLOSE LOCATION - Close mesti di atas 75% dari range accumulation
                    range_high = max(highs)
                    range_low = min(lows)
                    total_range = range_high - range_low
                    if curr['c'] < (range_low + (total_range * 0.75)):
                        if sym in WATCHLIST: del WATCHLIST[sym]
                        continue # Weak close, skip

                    # FIX 4: MICRO BOS - Mesti break local lower high terdekat
                    recent_highs = [c['h'] for c in candles_m15[-20:-1]]
                    local_lh = max(recent_highs) if recent_highs else range_high
                    if curr['c'] <= local_lh:
                        if sym in WATCHLIST: del WATCHLIST[sym]
                        continue # Belum ada BOS, skip

                    # Price Action Check
                    prev = candles_m15[-2]
                    body = abs(curr['c'] - curr['o'])
                    lower_wick = min(curr['o'], curr['c']) - curr['l']
                    is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
                    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and
                                    curr['c'] > prev['o'] and curr['o'] < prev['c'])
                    
                    # Jika SEMUA syarat Institutional cukup, terus tembak!
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
    if not active:
        return

    for sym, trade in active.items():
        try:
            candles = get_gateio_klines(sym, "1h", 5)
            if not candles:
                continue
            cp = candles[-1]['c']

            mid = trade.get("msg_id")

            def notify(text):
                kw = {"parse_mode": "HTML"}
                if mid:
                    kw["reply_to_message_id"] = mid
                try:
                    bot.send_message(VIP_CHANNEL_ID, text, **kw)
                except Exception as e:
                    print(f"[MONITOR] Reply gagal untuk {sym}: {e}")
                    bot.send_message(VIP_CHANNEL_ID, text, parse_mode="HTML")

            updates = {}

            if cp >= trade["tp1"] and not trade.get("tp1_hit"):
                updates["tp1_hit"] = True
                notify(f"✅ <b>{sym} — TP1 HIT!</b>\n💰 Harga: <code>${fmt(cp)}</code>\n🔒 Alih SL → BE: <code>${fmt(trade['entry'])}</code>")

            if cp >= trade["tp2"] and not trade.get("tp2_hit"):
                updates["tp2_hit"] = True
                notify(f"🚀 <b>{sym} — TP2 HIT!</b>\n💰 Harga: <code>${fmt(cp)}</code>\n📈 Trail SL → TP1: <code>${fmt(trade['tp1'])}</code>")

            if cp >= trade["tp3"] and not trade.get("tp3_hit"):
                updates["tp3_hit"] = True
                updates["closed"] = True
                profit_pct = (cp - trade["entry"]) / trade["entry"] * 100
                notify(f"🏆 <b>{sym} — TP3 MOONSHOT!</b>\n💰 Tutup: <code>${fmt(cp)}</code>\n📊 Profit: <code>+{profit_pct:.1f}%</code>")

            elif cp <= trade["sl"] and not trade.get("sl_hit"):
                updates["sl_hit"] = True
                updates["closed"] = True
                loss_pct = (cp - trade["entry"]) / trade["entry"] * 100
                notify(f"❌ <b>{sym} — SL HIT</b>\n💰 Tutup: <code>${fmt(cp)}</code>\n📉 Loss: <code>{loss_pct:.1f}%</code>")

            if updates:
                update_signal(sym, updates)
                print(f"[MONITOR] {sym}: {list(updates.keys())}")

        except Exception as e:
            print(f"[MONITOR] {sym}: {e}")

# =================================================================
# 8. TELEGRAM COMMANDS
# =================================================================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    cfg = get_config()
    active = get_active_trades()
    uptime_m = int((time.time() - START_TIME) / 60)
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")

    text = (
        f"🏴☠️ <b>ALPHA — Dual Engine Sniper</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f" Uptime   : <code>{uptime_m}m</code>\n"
        f" Trade   : <code>{len(active)} aktif</code>\n"
        f"🔧 Scan    : <code>{'✅ AKTIF' if IS_SCANNING else '⛔ STOP'}</code>\n\n"
        f"⚡ <b>Scan Mode:</b> <code>{SCAN_MODE.upper()}</code>\n"
        f"🎛️ <b>Preset:</b> <code>{preset_lbl}</code>\n\n"
        f"<b>‍☠️ Engine 1 — Pullback (H1+H4):</b>\n"
        f"Fractal Swing | EMA Sebenar | VPA Impulse\n\n"
        f"<b>⚡ Engine 2 — Momentum (M15):</b>\n"
        f"Volume Anomaly | Accumulation Pattern"
    )
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🟢 Soft", callback_data="tune:soft"),
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
        InlineKeyboardButton("📓 Journal", callback_data="journal")
    )
    kb.add(
        InlineKeyboardButton("📊 Status", callback_data="status"),
        InlineKeyboardButton("❓ Help", callback_data="help")
    )
    bot.send_message(msg.chat.id, text, parse_mode="HTML", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tune:"))
def cb_tune(call):
    if str(call.message.chat.id) != str(ADMIN_ID):
        return
    bot.answer_callback_query(call.id)
    preset = call.data.split(":")[1]
    ok, lbl = apply_preset(preset)
    if ok:
        p = PRESETS[preset]
        text = f"✅ <b>PRESET DIAPLIKASI</b>\n\n{lbl}\n\n<i>Scan seterusnya akan guna preset ini.</i>"
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML")
        except Exception:
            pass
        alert_admin(f"🎛️ Preset: {lbl}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("mode:"))
def cb_mode(call):
    global SCAN_MODE
    if str(call.message.chat.id) != str(ADMIN_ID):
        return
    bot.answer_callback_query(call.id)
    new_mode = call.data.split(":")[1]
    if new_mode in ["pullback", "momentum", "both"]:
        SCAN_MODE = new_mode
        bot.send_message(call.message.chat.id, f"✅ Scan mode ditukar ke: <b>{new_mode.upper()}</b>", parse_mode="HTML")
        alert_admin(f"⚡ Mode changed: {new_mode.upper()}")

@bot.callback_query_handler(func=lambda c: c.data in ["scan_on", "scan_off", "journal", "status", "help"])
def cb_actions(call):
    global IS_SCANNING
    if str(call.message.chat.id) != str(ADMIN_ID):
        return
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
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        active = get_config().get("active_preset", "standard")
        text = (
            f"🎛️ <b>TUNE PRESET</b>\n\n"
            f"<b>Aktif:</b> {PRESETS[active]['label']}\n\n"
            f"🟢 <code>/tune soft</code> — Vol $500K, Pass 2\n"
            f"🟡 <code>/tune standard</code> — Vol $1M, Pass 3\n"
            f" <code>/tune hard</code> — Vol $2.5M, Pass 4"
        )
        bot.reply_to(msg, text, parse_mode="HTML")
        return
    ok, lbl = apply_preset(parts[1].lower())
    bot.reply_to(msg, f"✅ {lbl}" if ok else "❌ Preset tidak sah")

@bot.message_handler(commands=["mode"])
def cmd_mode(msg):
    global SCAN_MODE
    if str(msg.chat.id) != str(ADMIN_ID):
        return
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
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, " /pair [SYMBOL] (Contoh: /pair BTC)")
        return
    sym = parts[1].upper()
    bot.reply_to(msg, f"🔍 Menganalisa <code>{sym}</code>...", parse_mode="HTML")

    def _do():
        smc_h1 = analyze_smc_pa(sym, verbose=False)
        smc_m15 = analyze_early_momentum(sym, verbose=False)

        if smc_h1:
            bot.send_message(msg.chat.id, f"🏴‍️ <b>{sym} — PULLBACK (H1)</b>\nSetup: <code>{smc_h1['setup']}</code>\nScore: <code>{smc_h1['score']}</code>\nStructure: <code>{smc_h1.get('structure', 'unknown')}</code>", parse_mode="HTML")
        elif smc_m15:
            bot.send_message(msg.chat.id, f"⚡ <b>{sym} — MOMENTUM (M15)</b>\nSetup: <code>{smc_m15['setup']}</code>\nVol Spike: <code>{smc_m15['vol_spike']:.1f}x</code>", parse_mode="HTML")
        else:
            bot.send_message(msg.chat.id, f"❌ {sym}: Tiada setup", parse_mode="HTML")
    threading.Thread(target=_do).start()

@bot.message_handler(commands=["scan"])
def cmd_scan(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    bot.reply_to(msg, "⚙️ Scan dipaksa...")
    threading.Thread(target=scan_once).start()

@bot.message_handler(commands=["status"])
def cmd_status(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    cfg = get_config()
    active = get_active_trades()
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")
    bot.reply_to(msg, (
        f"📊 <b>STATUS</b>\n"
        f"Scan  : {'🟢 AKTIF' if IS_SCANNING else '⛔ STOP'}\n"
        f"Mode  : <code>{SCAN_MODE.upper()}</code>\n"
        f"Trade : <code>{len(active)}</code> aktif\n\n"
        f"🎛️ Preset: <code>{preset_lbl}</code>\n"
        f"Vol   : <code>${cfg['min_vol_24h']/1e6:.1f}M</code>\n"
        f"Pass  : <code>{cfg['score_pass']}</code>"
    ), parse_mode="HTML")

@bot.message_handler(commands=["journal"])
def cmd_journal(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    bot.reply_to(msg, generate_journal(), parse_mode="HTML")

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
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
    if not trades:
        return "📓 <b>JOURNAL (7D)</b>\n\nTiada signal dalam 7 hari lepas."

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
        f"├ TP1 Hit      : <code>{tp1_n} ({wr:.0f}%)</code>\n"
        f"├ TP2 Hit      : <code>{tp2_n}</code>\n"
        f"├ TP3 Moonshot : <code>{tp3_n}</code>\n"
        f"├ SL Hit       : <code>{sl_n}</code>\n"
        f"└ Masih Buka   : <code>{open_n}</code>\n\n"
        f"<b>🧠 Setup Breakdown:</b>\n<code>{setup_str}</code>"
    )

# =================================================================
# 10. SCHEDULER & MAIN
# =================================================================
def monitor_pullback_watchlist():
    """
    Monitor Pullback Watchlist setiap 30 saat guna M5.
    Filter: Anti-Bleed (Candle Ratio), Volume Dry, Trigger M5.
    """
    if not IS_SCANNING or not PULLBACK_WATCHLIST:
        return

    print(f"\n[PULLBACK MONITOR] Checking {len(PULLBACK_WATCHLIST)} coins...")
    symbols_to_remove = []

    for sym, data in list(PULLBACK_WATCHLIST.items()):
        try:
            # 1. Timeout Check (20 minit)
            if time.time() - data["added"] > PULLBACK_TIMEOUT:
                symbols_to_remove.append(sym)
                continue

            # Ambil data M5 (50 candle = 4 jam lepas)
            candles_m5 = get_gateio_klines(sym, "5m", 50)
            if len(candles_m5) < 20: continue

            current_price = candles_m5[-1]['c']
            
            # 2. LOCATION CHECK: Adakah harga dah masuk zon Discount (antara Entry dan SL)?
            # Kita guna Entry sebagai Fib 0.5 (approx) dan SL sebagai Fib 0.786
            if current_price > data["entry"] or current_price < data["fib_786"]:
                continue # Harga belum pullback atau dah jatuh terlalu dalam (falling knife)

            # 3. ANTI-BLEED FILTER (Candle Ratio): 10 candle M5 terakhir
            recent_10 = candles_m5[-10:]
            red_candles = sum(1 for c in recent_10 if c['c'] < c['o'])
            if red_candles >= 8:
                print(f"[{sym}]  SLOW DUMP DETECTED ({red_candles}/10 merah). Buang dari watchlist.")
                symbols_to_remove.append(sym)
                continue

            # 4. VOLUME DRY CHECK: Volume mesti rendah semasa pullback
            avg_vol_m5 = sum(c['v'] for c in candles_m5[-20:-1]) / 19
            curr_vol_m5 = candles_m5[-1]['v']
            if curr_vol_m5 > (avg_vol_m5 * 1.5):
                continue # Volume masih tinggi (seller aktif), belum safe

            # 5. TRIGGER CHECK: Pinbar atau Engulfing di M5
            curr = candles_m5[-1]
            prev = candles_m5[-2]
            body = abs(curr['c'] - curr['o'])
            lower_wick = min(curr['o'], curr['c']) - curr['l']
            
            is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
            is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and
                            curr['c'] > prev['o'] and curr['o'] < prev['c'])

            if is_pinbar or is_engulfing:
                # ✅ LULUS SEMUA FILTER! Hantar Signal Pullback
                setup_name = "🔄 PULLBACK RECOVERY (M5)"
                smc_pullback = {
                    "setup": setup_name,
                    "entry": curr['c'],
                    "sl": data["fib_786"] * 0.99, # SL bawah zon extreme
                    "tp1": data["entry"], # TP1 balik ke harga asal pump
                    "tp2": data["entry"] * 1.05,
                    "tp3": data["entry"] * 1.10,
                    "rr1": 2.0, "rr2": 4.0, "score": 4, # High score sebab confirm pullback
                    "fib_zone": "N/A", "timeframe": "M5"
                }
                
                if send_signal(sym, smc_pullback, 0, btc_chg=0.0):
                    symbols_to_remove.append(sym)
                    print(f"[{sym}] 🚀 PULLBACK TRIGGERED! Signal dihantar.")

        except Exception as e:
            print(f"[PULLBACK ERROR] {sym}: {e}")

    # Cleanup
    for sym in symbols_to_remove:
        if sym in PULLBACK_WATCHLIST: del PULLBACK_WATCHLIST[sym]


def monitor_bos_breaks():
    """Monitor BOS Watchlist setiap 15 saat. Instant signal bila break."""
    if not IS_SCANNING or not BOS_WATCHLIST:
        return

    symbols_to_remove = []
    for sym, data in list(BOS_WATCHLIST.items()):
        try:
            if time.time() - data["added"] > 1800: # Timeout 30 minit
                symbols_to_remove.append(sym)
                continue

            current_price = get_gateio_price(sym)
            if current_price <= 0: continue

            level = data["level"]
            bos_type = data["type"]

            # DETECT BREAK!
            if bos_type == "HL" and current_price < level:
                print(f"[{sym}] 💥 BOS BREAK! HL ${fmt(level)} ditembusi @ ${fmt(current_price)}")
                smc_bos = {
                    "setup": "💥 BOS BREAK (HL Tembus - Bearish)",
                    "entry": current_price, "sl": level * 1.02,
                    "tp1": current_price * 0.95, "tp2": current_price * 0.90, "tp3": current_price * 0.85,
                    "rr1": 2.5, "rr2": 5.0, "score": 4, "fib_zone": "N/A", "timeframe": "M5"
                }
                if send_signal(sym, smc_bos, 0, btc_chg=0.0):
                    symbols_to_remove.append(sym)

            elif bos_type == "LH" and current_price > level:
                print(f"[{sym}] 💥 BOS BREAK! LH ${fmt(level)} ditembusi @ ${fmt(current_price)}")
                smc_bos = {
                    "setup": "💥 BOS BREAK (LH Tembus - Bullish)",
                    "entry": current_price, "sl": level * 0.98,
                    "tp1": current_price * 1.05, "tp2": current_price * 1.10, "tp3": current_price * 1.15,
                    "rr1": 2.5, "rr2": 5.0, "score": 4, "fib_zone": "N/A", "timeframe": "M5"
                }
                if send_signal(sym, smc_bos, 0, btc_chg=0.0):
                    symbols_to_remove.append(sym)
        except Exception as e:
            print(f"[BOS ERROR] {sym}: {e}")

    for sym in symbols_to_remove:
        if sym in BOS_WATCHLIST: del BOS_WATCHLIST[sym]

def run_scheduler():
    schedule.every(5).minutes.do(lambda: threading.Thread(target=scan_once).start())
    schedule.every(5).minutes.do(lambda: threading.Thread(target=monitor_active_trades).start())
    
    # Fast Track Micro-Scan setiap 30 saat
    schedule.every(30).seconds.do(lambda: threading.Thread(target=fast_track_watchlist).start())
    
    # ── TAMBAH INI: Pullback Monitor setiap 30 saat ───────────
    schedule.every(30).seconds.do(lambda: threading.Thread(target=monitor_pullback_watchlist).start())
    # ─ TAMAT PULLBACK MONITOR ────────────────────────────────
    
    while True:
        schedule.run_pending()
        time.sleep(1) # Tukar dari 30 ke 1 saat untuk ketepatan scheduler)

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALPHA DUAL ENGINE v2 ACTIVE")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", port), RenderHandler).serve_forever(), daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()

    time.sleep(5)
    alert_admin(
        f"🏴‍☠️ ALPHA Dual Engine v2 DEPLOYED (Institutional Grade)\n"
        f"Mode: {SCAN_MODE.upper()}\n"
        f"Preset: {PRESETS[get_config()['active_preset']]['label']}"
    )
    threading.Thread(target=scan_once).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
