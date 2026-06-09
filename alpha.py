"""
ALPHA — Gate.io Dual Engine Sniper (Institutional Grade)
Engine 1: Pullback SMC (H1 + H4) — Fractal Swing + EMA + VPA Impulse
Engine 2: Early Momentum (M15) — Volume Anomaly + Accumulation
Mode: /mode pullback | momentum | both
v14.5: ALPHA RISK MANAGEMENT INTEGRATION
  - Position sizing dengan modal user
  - ATR + structure SL calculation
  - Hard cap 50% modal per trade
  - Command /modal untuk set capital
CHANGELOG v14.1 (TIMEOUT OPTIMIZATION):
[CONFIG] WATCHLIST timeout: 600s → 900s (15 minit, untuk M15 momentum)
[CONFIG] PULLBACK_TIMEOUT: 1200s → 28800s (8 jam, untuk SL hunt/reject/retest)
[CONFIG] BOS_WATCHLIST_TIMEOUT: 1800s → 28800s (8 jam, untuk breakout + retest)
[REASON] 8 jam = 2 × H4 candles = professional SL hunt window untuk pullback strategy
CHANGELOG v14 (AUDIT FIXES):
[FIX-1]  SL Engine 1: Guna ATR + Fib78.6% combo (bukan simple 0.5% sahaja)
[FIX-2]  SL/TP Engine 2: SL = min_low - ATR*0.75 | TP guna Fib ratio (1.618R, 2.618R, 4.236R)
[FIX-3]  H4 EMA loop: Was iterating empty list h4_closes[20:] — fixed ke calculate_ema() full
[FIX-4]  get_active_trades() dipindah LUAR loop — dari N×DB calls ke 1×DB call
[FIX-5]  Threading Lock ditambah untuk WATCHLIST, PULLBACK_WATCHLIST, BOS_WATCHLIST
[FIX-6]  add_pullback_watchlist: fib_500/fib_786 mapping betulkan (bukan tp1/sl)
[FIX-7]  analyze_smc_pa return dict: tambah fib_500, fib_786 keys
[FIX-8]  Minimum RR filter ditambah dalam send_signal() — RR1 >= 1.5
[FIX-9]  TP ordering validation — tp1 > entry > sl diperiksa sebelum signal dihantar
[FIX-10] Counter-trend TP cap: cek tp1 != tp2 selepas capping
[FIX-11] scan_once M15 inline: SL/TP guna ATR + Fib ratio (bukan arbitrary 3R/5R)
[FIX-12] scan_once: tambah IS_SCANNING_LOCK untuk elak double-scan race condition
[FIX-13] fib_618 dipakai secara bermakna untuk OB validation zone
"""
import os, time, json, requests, threading, traceback, schedule
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from supabase import create_client, Client

# ==========================================
# 1. KONFIGURASI
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
VIP_CHANNEL_ID = os.environ.get("VIP_CHANNEL_ID")
ADMIN_ID = os.environ.get("ADMIN_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
bot = TeleBot(TELEGRAM_BOT_TOKEN)
START_TIME = time.time()
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
SCAN_MODE = os.environ.get("SCAN_MODE", "pullback").lower()

# [FIX-5] Thread-safe locks untuk semua global dicts
_watchlist_lock = threading.Lock()
_scan_lock = threading.Lock()   # [FIX-12] Prevent double-scan
IS_SCANNING_ACTIVE = False       # Guard: elak concurrent scan_once

def alert_admin(text):
    try:
        bot.send_message(ADMIN_ID, f"🚨 <b>ALPHA SYSTEM</b>\n<pre>{str(text)[:800]}</pre>", parse_mode="HTML")
    except Exception:
        pass

# ==========================================
# 2. PRESETS & SUPABASE HELPERS
# ==========================================
PRESETS = {
    "soft ":     { "min_vol_24h ": 500_000,     "score_pass ": 2,   "label ":  "🟢 SOFT "},
    "standard ": { "min_vol_24h ": 1_000_000,   "score_pass ": 2,   "label ":  " STANDARD "},
    "hard ":     { "min_vol_24h ": 2_500_000,   "score_pass ": 3,   "label ":  "🔴 HARD "}
}

DEFAULT_CONFIG = {
    "min_vol_24h":   1_000_000,
    "score_pass":    2,
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
    set_config("score_pass",  p["score_pass"])
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

# ==========================================
# [ALPHA-RISK] PENGURUSAN RISIKO INSTITUTIONAL GRADE
# ==========================================

def set_user_capital(user_id, capital, risk_pct=2.0):
    """Simpan modal dan risk percentage pengguna ke Supabase."""
    try:
        sb.table("user_profiles").upsert({
            "user_id": user_id,
            "capital": capital,
            "risk_pct": risk_pct,
            "updated": int(time.time())
        }).execute()
    except Exception as e:
        print(f"[USER CAPITAL] Error: {e}")

def get_user_capital(user_id):
    """Ambil modal dan risk percentage pengguna dari Supabase."""
    try:
        rows = sb.table("user_profiles").select("*").eq("user_id", user_id).execute().data
        if rows:
            return rows[0].get("capital", 50.0), rows[0].get("risk_pct", 2.0)
    except Exception:
        pass
    return 50.0, 2.0  # Default: modal $50, risk 2%

def calculate_position_size(capital, risk_pct, entry, sl):
    """
    Position size dengan double cap:
    1. Cap kepada capital (no leverage)
    2. Hard cap: tidak melebihi 50% modal dalam satu trade
    """
    risk_usd = capital * (risk_pct / 100.0)
    risk_distance = entry - sl
    if risk_distance <= 0:
        return 0, 0, 0
    
    position_usd_raw = risk_usd / (risk_distance / entry)
    
    # Cap 1: tidak melebihi modal penuh
    position_usd = min(position_usd_raw, capital)
    
    # Cap 2: tidak melebihi 50% modal dalam satu trade (risk management)
    position_usd = min(position_usd, capital * 0.50)
    
    position_coins = position_usd / entry
    actual_risk_usd = position_coins * risk_distance
    
    return position_usd, position_coins, actual_risk_usd

def compute_final_sl(entry, structure_low, atr, atr_mult=1.5, max_sl_pct=0.08):
    """
    Combine ATR-based SL dengan structure SL.
    Pilih yang lebih jauh dari entry (lebih konservatif untuk crypto noise).
    Cap maksimum SL distance untuk elak position size mikroskopik.
    """
    sl_atr = entry - (atr_mult * atr) if atr > 0 else entry * 0.98
    sl_structure = structure_low * 0.995  # buffer 0.5% bawah structure
    sl_raw = min(sl_atr, sl_structure)  # ambil yang lebih jauh (lower price)
    sl_floor = entry * (1.0 - max_sl_pct)  # cap: tidak lebih 8% dari entry
    final_sl = max(sl_raw, sl_floor)
    return final_sl

# ==========================================
# 3. HELPER & GATE.IO API + BLOCKLIST + MATH FUNCTIONS
# ==========================================
STABLECOINS     = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FRAX", "LUSD", "GUSD", "USDD", "FDUSD", "PYUSD", "USDK", "SUSD", "RSR", "EURS", "EURT", "UST", "ALUSD", "MIM", "CUSD", "CEUR", "XAUT", "PAXG"}
WRAPPED_TOKENS  = {"WETH", "WBTC", "WBNB", "WSOL", "WMATIC", "WAVAX", "WFTM", "BETH", "STETH", "RETH", "CBETH"}
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
    if val == 0: return "0.00"
    if abs(val) < 0.000001: return f"{val:.10f}"
    if abs(val) < 0.001:    return f"{val:.8f}"
    if abs(val) < 1.0:      return f"{val:.6f}"
    if abs(val) < 1000:     return f"{val:.4f}"
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
                sym      = t['currency_pair'].replace('_USDT', '')
                vol      = float(t.get('quote_volume', 0))
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
    # Gate.io returns newest-first → reversed() = oldest-first → candles[-1] = latest ✓
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

# ==========================================
# MATH HELPERS (INSTITUTIONAL GRADE)
# ==========================================
def calculate_ema(data, period):
    """Standard EMA — seed dengan SMA(period), kemudian iterasi."""
    if len(data) < period:
        return sum(data) / len(data) if data else 0
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period       # seed = SMA
    for price in data[period:]:             # iterate semua baki candles
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_atr(candles, period=14):
    """Simple ATR (Wilder-style average TR). Cukup tepat untuk keperluan ini."""
    if len(candles) < period + 1:
        return 0
    trs = []
    for i in range(-period, 0):
        c    = candles[i]
        prev = candles[i - 1]
        tr   = max(c['h'] - c['l'], abs(c['h'] - prev['c']), abs(c['l'] - prev['c']))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def find_fractal_swings(candles, lookback=2):
    """
    Detect fractal swing highs/lows.
    Primary:   lookback=2, full range — strict, low noise.
    Secondary: lookback=1, last 4 candles — [FIX-14] catch forming HH/HL
    that are invisible due to 2-candle confirmation lag.
    """
    swings = []
    n = len(candles)
    
    # Primary pass — conservative
    for i in range(lookback, n - lookback):
        is_sh = is_sl = True
        for j in range(1, lookback + 1):
            if candles[i]['h'] < candles[i-j]['h'] or candles[i]['h'] < candles[i+j]['h']:
                is_sh = False
            if candles[i]['l'] > candles[i-j]['l'] or candles[i]['l'] > candles[i+j]['l']:
                is_sl = False
        if is_sh:
            swings.append({'type': 'SH', 'price': candles[i]['h'], 'index': i})
        elif is_sl:
            swings.append({'type': 'SL', 'price': candles[i]['l'], 'index': i})

    # [FIX-14] Secondary pass — recent 4 candles with lookback=1
    # Primary never checks candles[-1] and candles[-2]; this fills that gap.
    for i in range(max(n - 4, 1), n - 1):
        if any(s['index'] == i for s in swings):
            continue  # already found by primary
        prev_h = candles[i-1]['h']
        next_h = candles[i+1]['h'] if i + 1 < n else float('-inf')
        prev_l = candles[i-1]['l']
        next_l = candles[i+1]['l'] if i + 1 < n else float('inf')
        if candles[i]['h'] > prev_h and candles[i]['h'] > next_h:
            swings.append({'type': 'SH', 'price': candles[i]['h'], 'index': i})
        elif candles[i]['l'] < prev_l and candles[i]['l'] < next_l:
            swings.append({'type': 'SL', 'price': candles[i]['l'], 'index': i})

    swings.sort(key=lambda x: x['index'])
    return swings

def check_market_structure(swings):
    """
    [FIX-15] Dulu: swings[-4:] — 4 swing campur SH+SL, boleh dapat 3 SH 1 SL atau sebaliknya.
    Sekarang: ambil last 2 SH dan last 2 SL secara berasingan dari SEMUA swings.
    Ini lebih tepat — HH/HL yang baru tak terbenam dalam window lama.
    """
    if len(swings) < 2:
        return 'unknown'
    
    all_highs = sorted([s for s in swings if s['type'] == 'SH'], key=lambda x: x['index'])
    all_lows  = sorted([s for s in swings if s['type'] == 'SL'], key=lambda x: x['index'])

    if len(all_highs) < 2 and len(all_lows) < 2:
        return 'unknown'

    # Handle insufficient data for one side
    if len(all_highs) < 2:
        return 'uptrend' if all_lows[-1]['price'] > all_lows[-2]['price'] else 'downtrend'
    if len(all_lows) < 2:
        return 'uptrend' if all_highs[-1]['price'] > all_highs[-2]['price'] else 'downtrend'

    # Compare most recent pair from each type
    is_higher_high = all_highs[-1]['price'] > all_highs[-2]['price']
    is_higher_low  = all_lows[-1]['price']  > all_lows[-2]['price']
    is_lower_high  = all_highs[-1]['price'] < all_highs[-2]['price']
    is_lower_low   = all_lows[-1]['price']  < all_lows[-2]['price']

    if   is_higher_high and is_higher_low:    return 'uptrend'
    elif is_lower_high  and is_lower_low:     return 'downtrend'
    elif is_higher_high and not is_lower_low: return 'uptrend_breakout'
    elif is_higher_low  and is_lower_high:    return 'sideway'        # compression: LH + HL
    elif is_higher_low  and not is_lower_high: return 'uptrend'       # HL forming = early reversal
    else:                                     return 'sideway'

def find_fresh_swing_pair(swings):
    """
    [FIX-21] Cari swing pair yang logically connected untuk Fib.
    Masalah lama: shs[-1] dan sls[-1] boleh jadi dari gelombang BERBEZA.
    Contoh KITE: shs[-1]=HH 0.2022, sls[-1]=SL dari Jun bottom 0.163.
    Fib jadi terlalu besar → price 0.193 nampak PREMIUM → REJECT padahal betul entry!

    Fix: Cari SL paling terkini yang hadir SEBELUM SH paling terkini (same wave).
    """
    if not swings:
        return None, None
    
    shs = [s for s in swings if s['type'] == 'SH']
    sls = [s for s in swings if s['type'] == 'SL']
    
    if not shs or not sls:
        return None, None

    latest_sh    = shs[-1]
    sl_before_sh = [s for s in sls if s['index'] < latest_sh['index']]
    
    if not sl_before_sh:
        return None, None

    latest_sl = sl_before_sh[-1]

    # Validate: mesti valid range (SH > SL dan >= 0.3% range)
    # [FIX-05] Turunkan dari 1.0% ke 0.3% — micro-accumulation zones (tight consolidation)
    # yang sebelum ini dibuang kini boleh digunakan sebagai fresh fib pair.
    if latest_sh['price'] <= latest_sl['price']:
        return None, None
    if (latest_sh['price'] - latest_sl['price']) / latest_sl['price'] * 100 < 0.3:
        return None, None

    return latest_sh['price'], latest_sl['price']

def find_anchor_swing_pair(swings):
    """
    Cari swing pair untuk SWING TRADING Fib (gelombang besar/anchor).
    Berbeza dengan find_fresh_swing_pair():
    - Fresh  → SL paling terkini sebelum SH  (intraday, range kecil)
    - Anchor → SL paling RENDAH sebelum SH   (swing, range besar = recovery wave)

    Contoh KITE:
    - Fresh  → SL=0.18807 (HL), SH=0.20220 → range 7%   (intraday)
    - Anchor → SL=0.16350 (Jun bottom), SH=0.20220 → range 24% (swing)
    """
    shs = [s for s in swings if s['type'] == 'SH']
    sls = [s for s in swings if s['type'] == 'SL']
    
    if not shs or not sls:
        return None, None

    latest_sh    = shs[-1]
    sl_before_sh = [s for s in sls if s['index'] < latest_sh['index']]
    
    if not sl_before_sh:
        return None, None

    # Anchor = SL dengan harga TERENDAH (bukan terkini) — dasar gelombang besar
    anchor_sl = min(sl_before_sh, key=lambda s: s['price'])

    if latest_sh['price'] <= anchor_sl['price']:
        return None, None
    # Anchor mesti besar dari fresh (minimum 3% range)
    if (latest_sh['price'] - anchor_sl['price']) / anchor_sl['price'] * 100 < 3.0:
        return None, None

    return latest_sh['price'], anchor_sl['price']

def detect_fvg(candles, lookback=30):
    """
    [FIX-22] Fair Value Gap (FVG) — SMC/ICT imbalance zone.

    Bullish FVG: 3-candle pattern
      Candle A: candle sebelum impulse
      Candle B: impulse candle (mesti bullish)
      Candle C: candle selepas impulse
      FVG zone: Candle A high → Candle C low (price belum fill gap ini)

    Returns list FVG zones, sorted terkini dahulu.
    """
    fvgs = []
    n    = len(candles)
    for i in range(2, min(lookback, n - 1)):
        c_a = candles[-(i + 1)]
        c_b = candles[-i]
        c_c = candles[-(i - 1)]
        if c_b['c'] > c_b['o'] and c_c['l'] > c_a['h']:
            gap_pct = (c_c['l'] - c_a['h']) / c_b['c'] * 100
            if gap_pct >= 0.3:
                fvgs.append({
                    'top':        c_c['l'],
                    'bottom':     c_a['h'],
                    'mid':        (c_c['l'] + c_a['h']) / 2,
                    'size_pct':   gap_pct,
                    'candles_ago': i - 1
                })
    return fvgs   # terkini dahulu (loop dari i=2 ke atas)

# ==========================================
# 4. ENGINE 1: PULLBACK SMC (H1 + H4) — INSTITUTIONAL GRADE
# ==========================================
def analyze_smc_pa(sym, verbose=True):
    log = lambda msg: print(f"[{sym}-H1] {msg}") if verbose else None
    candles = get_gateio_klines(sym, "1h", 200)
    if len(candles) < 100:
        log("❌ REJECT: Data H1 < 100 candle")
        return None

    # 1. ATR — dikira awal, dipakai di semua peringkat
    atr = calculate_atr(candles, 14)

    # 2. H4 TREND CONFIRMATION
    candles_h4      = get_gateio_klines(sym, "4h", 50)
    is_counter_trend = False
    h4_swing_high    = 0

    if len(candles_h4) >= 20:
        h4_swings    = find_fractal_swings(candles_h4, lookback=1)
        h4_structure = check_market_structure(h4_swings)
        if h4_structure == 'downtrend':
            log("⚠️ WARNING: H4 structure downtrend (Counter-Trend Mode)")
            is_counter_trend = True
            h4_shs       = [s for s in h4_swings if s['type'] == 'SH']
            h4_swing_high = h4_shs[-1]['price'] if h4_shs else 0
        elif h4_structure == 'unknown':
            log("⚠️ H4 structure unknown, skip H4 filter")

    # 3. FRACTAL SWING POINTS (dengan secondary pass [FIX-14])
    swings = find_fractal_swings(candles, lookback=2)

    # [FIX-16] EMA dikira AWAL — dipakai untuk override fractal conflict DAN step 7
    closes    = [c['c'] for c in candles[-200:]]
    ema20     = calculate_ema(closes, 20)
    ema50     = calculate_ema(closes, 50)
    price_now = candles[-1]['c']
    # EMA jelas bullish jika EMA20 > EMA50 dan price tidak jauh bawah EMA20
    ema_bullish = ema20 > ema50 and price_now > ema20 * 0.95

    # 4. SEMAK STRUKTUR MARKET
    if len(swings) >= 4:
        structure = check_market_structure(swings)
    else:
        log("⚠️ Fractal swings tidak cukup, guna EMA trend detection")
        if ema20 > ema50 and price_now > ema20:
            structure = 'uptrend'
        elif ema20 < ema50 and price_now < ema20:
            structure = 'downtrend'
        else:
            structure = 'unknown'

    # [FIX-16] EMA OVERRIDE: fractal kata downtrend TAPI EMA jelas bullish?
    # Kes biasa: market baru lepas transition dari downtrend, fractal masih nampak
    # LH/LL lama dalam window tapi EMA20 > EMA50 dah confirm recovery.
    if structure == 'downtrend' and ema_bullish:
        log(f"⚠️ STRUCT CONFLICT: Fractal=downtrend tapi EMA20({fmt(ema20)}) > EMA50({fmt(ema50)}) → Override ke sideway")
        structure = 'sideway'
    elif structure == 'downtrend':
        log(f"❌ REJECT: Market structure downtrend (fractal + EMA kedua-dua confirm)")
        return None
    
    log(f"✅ STRUCTURE: {structure}")

    shs        = [s for s in swings if s['type'] == 'SH']
    sls        = [s for s in swings if s['type'] == 'SL']

    # [FIX-21] DUAL FIB SYSTEM — Fresh (Intraday) + Anchor (Swing)
    # ───────────────────────────────────────────────────────────────
    # FRESH  = SL terkini sebelum HH → Intraday/Scalping zone (range kecil)
    # ANCHOR = SL terendah sebelum HH → Swing zone (recovery wave besar)
    # Cuba FRESH dulu. Jika price tidak dalam fresh zone, cuba ANCHOR.
    # ─────────────────────────────────────────────────────────────────

    fresh_sh,  fresh_sl   = find_fresh_swing_pair(swings)
    anchor_sh, anchor_sl = find_anchor_swing_pair(swings)

    # Kira fib untuk kedua-dua
    def calc_fibs(sh, sl):
        if not sh or not sl or sh <= sl:
            return None
        r = sh - sl
        return {"sh": sh, "sl": sl, "rng": r,
                "fib500": sh - r*0.500,
                "fib618": sh - r*0.618,
                "fib786": sh - r*0.786}

    fresh_fib  = calc_fibs(fresh_sh,  fresh_sl)
    anchor_fib = calc_fibs(anchor_sh, anchor_sl)

    curr  = candles[-1]
    prev  = candles[-2]

    # [FIX-ENTRY] Guna Live Price untuk elak time-lag candle close.
    # Harga dalam signal akan sama dengan harga chart semasa bot scan.
    price = get_gateio_price(sym)
    if price == 0:
        price = curr['c'] # Fallback jika API fail

    in_fresh_zone   = fresh_fib  and fresh_fib["fib786"]   <= price <= fresh_fib["fib500"]
    in_anchor_zone = anchor_fib and anchor_fib["fib786"]  <= price <= anchor_fib["fib500"]

    # Fresh dan anchor sama? (hanya 1 SL tersedia) — jangan double count
    same_pair = (fresh_sh == anchor_sh and fresh_sl == anchor_sl)

    if not in_fresh_zone and not in_anchor_zone:  
        zone_ref = fresh_fib["fib500"] if fresh_fib else (anchor_fib["fib500"] if anchor_fib else 0)
    
        # [FIX-PREMIUM] Benarkan Premium Zone jika EMA Bullish dan Price > EMA20 (Strong Momentum)
        # Coin strong selalunya cuma pullback ke EMA20 (sekitar 38.2% Fib) dan terus pump.
        if price > zone_ref and ema_bullish and price > ema20:
            log(f"⚠️ PREMIUM ZONE OVERRIDE: Price > Fib500 tapi EMA Bullish → Allow (Strong Momentum)")
            # Set default variables supaya code bawah tak crash (UnboundLocalError)
            setup_mode = "INTRADAY"
            swing_high = fresh_sh or anchor_sh
            swing_low  = fresh_sl or anchor_sl
            rng        = swing_high - swing_low
            fib_500    = swing_high - rng * 0.500
            fib_618    = swing_high - rng * 0.618
            fib_786    = swing_high - rng * 0.786
            in_discount = False
            fib_zone = f"Premium (>{fmt(zone_ref)})"
        else:
            log("❌ REJECT: " + ("PREMIUM ZONE" if price > zone_ref else "EXTREME (di luar range)"))
            return None

    # Pilih mode: Intraday dapat keutamaan (lebih presisi)
    if in_fresh_zone and fresh_fib and not same_pair:
        setup_mode = "INTRADAY"
        swing_high = fresh_fib["sh"]
        swing_low  = fresh_fib["sl"]
        rng        = fresh_fib["rng"]
        fib_500    = fresh_fib["fib500"]
        fib_618    = fresh_fib["fib618"]
        fib_786    = fresh_fib["fib786"]
        log(f"⚡ FIBO INTRADAY: ${fmt(price)} dalam FRESH ZONE [{fmt(fib_786)} - {fmt(fib_500)}] "
            f" | SL={fmt(swing_low)} SH={fmt(swing_high)} | range {rng/swing_low*100:.1f}%")
    elif in_anchor_zone and anchor_fib:
        setup_mode = "SWING"
        swing_high = anchor_fib["sh"]
        swing_low  = anchor_fib["sl"]
        rng        = anchor_fib["rng"]
        fib_500    = anchor_fib["fib500"]
        fib_618    = anchor_fib["fib618"]
        fib_786    = anchor_fib["fib786"]
        log(f"⚖️ FIBO SWING: ${fmt(price)} dalam ANCHOR ZONE [{fmt(fib_786)} - {fmt(fib_500)}] "
            f" | SL={fmt(swing_low)} SH={fmt(swing_high)} | range {rng/swing_low*100:.1f}%")
    else:
        # Fallback: fresh == anchor (single pair)
        setup_mode = "INTRADAY" if in_fresh_zone else "SWING"
        swing_high = fresh_sh or anchor_sh
        swing_low  = fresh_sl or anchor_sl
        rng        = swing_high - swing_low
        fib_500    = swing_high - rng * 0.500
        fib_618    = swing_high - rng * 0.618
        fib_786    = swing_high - rng * 0.786
        log(f"✅ FIBO PASS: ${fmt(price)} dalam DISCOUNT ZONE [{fmt(fib_786)} - {fmt(fib_500)}]")

    in_discount = True  # dah confirmed above
    fib_zone = f"{fmt(fib_500)} - {fmt(fib_786)}"

    # 7. EMA SEBENAR (dikira di step 3 [FIX-16], reuse di sini)
    is_uptrend = ema20 > ema50

    if not is_uptrend:
        ema_gap_pct = abs(ema20 - ema50) / ema50 * 100

        # [FIX-19 v2] Relax EMA reject bila: sideway + discount zone + EMA converging (<5%)
        # EMA adalah lagging indicator — dalam sideway accumulation, crossover belum berlaku
        # tapi momentum sudah beralih. Gap <5% = EMA dalam proses crossing.
        # [FIX-02] Naikkan threshold dari 3% ke 5%: JTO (3.77%), coin hampir crossover dibenarkan.
        if structure == 'sideway' and in_discount and ema_gap_pct < 5.0:
            log(f"⚠️ EMA CONVERGING: gap {ema_gap_pct:.2f}% < 5%, sideway+discount → Allow (lagging indicator)")
            is_uptrend = True   # treat as converging uptrend
        else:
            log(f"❌ REJECT: EMA20 < EMA50 (gap {ema_gap_pct:.2f}% — bukan uptrend, bukan converging)")
            return None

    distance_from_ema = abs(price - ema20)
    threshold = atr * 0.5 if atr > 0 else ema20 * 0.015

    if price < ema20 * 0.90:
        log("❌ REJECT: Price terlalu jauh bawah EMA20 H1")
        return None

    # 8. VPA IMPULSE vs PULLBACK
    # [FIX-03] Guna candles[-21:-1] — exclude candle SEMASA dari average.
    # Masalah asal: candles[-20:] termasuk candles[-1] (candle semasa).
    # Kalau candle semasa merah, ia masuk pullback_vols → naikkan purata → vpa_dry susah True.
    impulse_vols  = [c['v'] for c in candles[-21:-1] if c['c'] > c['o']]
    pullback_vols = [c['v'] for c in candles[-21:-1] if c['c'] < c['o']]
    avg_impulse_vol  = sum(impulse_vols) / len(impulse_vols) if impulse_vols else 1
    avg_pullback_vol = sum(pullback_vols) / len(pullback_vols) if pullback_vols else 1
    curr_vol = curr['v']
    vpa_dry  = avg_pullback_vol < (avg_impulse_vol * 0.7)

    setup_name = None
    score = 0

    # [FIX-04] Base score: price dalam golden zone (fib_786 – fib_618) = lokasi SMC paling kuat.
    # Ini elak Score 0 untuk coin yang sah secara struktur tapi tiada pattern candle jelas lagi.
    # Golden zone = 61.8%–78.6% retracement = kawasan OTE (Optimal Trade Entry) dalam ICT/SMC.
    if fib_786 <= price <= fib_618:
        score += 1
        setup_name = "📍 FIB GOLDEN ZONE"
        log("✅ SETUP 9: Price dalam OTE golden zone (78.6–61.8%)")

    body       = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    wick_ratio = lower_wick / body if body > 0 else 0
    touches    = sum(1 for c in candles[-50:-1] if abs(c['l'] - swing_low) / swing_low < 0.01)

    if curr['l'] < swing_low and curr['c'] > swing_low and wick_ratio >= 2.0 and touches >= 2 and curr_vol > avg_impulse_vol:
        setup_name = "💧 LIQUIDITY SWEEP"
        score += 3
        log(f"✅ SETUP 7: Sweep ({touches} touches, wick {wick_ratio:.1f}x)")

    total_range = curr['h'] - curr['l']
    upper_wick  = curr['h'] - max(curr['o'], curr['c'])
    min_size_ok = (total_range > atr * 0.5) if atr > 0 else True
    is_pinbar   = (lower_wick > body * 2 and upper_wick < total_range * 0.1
                   and curr['c'] > curr['o'] and min_size_ok)
    prev_body   = abs(prev['c'] - prev['o'])
    curr_body   = abs(curr['c'] - curr['o'])
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                    and curr['c'] > prev['o'] and curr['o'] <= prev['c']
                    and curr_body > prev_body and curr_vol > prev['v'])

    if is_pinbar:
        if not setup_name: setup_name = "🕯️ PINBAR REVERSAL"
        score += 2
        log("✅ SETUP 5: Pinbar valid")
    elif is_engulfing:
        if not setup_name: setup_name = "🐂 BULLISH ENGULFING"
        score += 2
        log("✅ SETUP 5: Engulfing valid")

    if vpa_dry:
        score += 1
        log("✅ VPA PASS: Pullback vol < 70% impulse vol")
    else:
        log("⚠️ VPA WEAK (Optional - tidak reject)")

    if is_uptrend and distance_from_ema < threshold and price > ema20:
        if not setup_name: setup_name = "📈 TREND PULLBACK"
        score += 1
        log("✅ SETUP 2: Pullback ke EMA20 (uptrend)")

    # [FIX-13] Guna fib_618 secara bermakna: OB valid jika dalam golden zone (50–61.8%)
    for i in range(-100, -3):
        try:
            c      = candles[i]
            c_next = candles[i + 1]
            if c['c'] < c['o'] and c_next['c'] > c_next['o']:
                bos_size = c_next['c'] - c_next['o']
                if bos_size > rng * 0.01:
                    ob_high  = c['h']
                    ob_low   = c['l']
                    if ob_low <= price <= ob_high:
                        touches_after = sum(1 for j in range(i + 2, 0) if ob_low <= candles[j]['l'] <= ob_high)
                        if touches_after <= 1:
                            # [FIX-13] OB dalam golden zone (50–61.8%) = premium OB
                            ob_in_golden = fib_618 <= (ob_high + ob_low) / 2 <= fib_500
                            if not setup_name: setup_name = "🧱 FRESH ORDER BLOCK" + (" [GOLDEN]" if ob_in_golden else " ")
                            score += 3 if ob_in_golden else 2
                            log(f"✅ SETUP 3: Fresh OB {'[GOLDEN ZONE]' if ob_in_golden else 'detected'}")
                            break
        except Exception:
            pass

    # [FIX-22] CROSS-TF: M15 FVG dalam H1 discount zone
    # FVG = unfilled gap antara candle sebelum impulse dan candle selepas impulse.
    # Kalau FVG M15 berada dalam H1 discount zone dan price sekarang dalam FVG = strong entry.
    try:
        candles_m15_fvg = get_gateio_klines(sym, "15m", 50)
        if len(candles_m15_fvg) >= 10:
            fvgs_m15 = detect_fvg(candles_m15_fvg, lookback=30)
            for fvg in fvgs_m15:
                # FVG mesti: (1) overlap dengan H1 discount zone, (2) price sekarang dalam FVG
                if fib_786 <= fvg['bottom'] <= fib_500 and fvg['bottom'] <= price <= fvg['top']:
                    score += 3
                    if not setup_name:
                        setup_name = "🕳️ M15 FVG ZONE"
                    log(f"✅ SETUP 8: M15 FVG dalam H1 discount ({fmt(fvg['bottom'])}-{fmt(fvg['top'])},  "
                        f"{fvg['size_pct']:.2f}%, {fvg['candles_ago']} M15 candle lalu)")
                    break
    except Exception:
        pass

    if not setup_name or score < 2:
        log(f"❌ REJECT: Score {score} < 2")
        return None

    # ─────────────────────────────────────────────────────────────
    # [ALPHA-RISK] SL/TP Calculation — Institutional Grade
    #   SL: compute_final_sl() — ATR + structure low combo
    #       + [FIX-01a] Fib786 floor: untuk Fib pullback entry,
    #         SL terbaik = just below fib_786 (structural stop SMC/ICT).
    #         ATR SL sering terlalu lebar untuk tight fib range (<3%),
    #         menjadikan risk terlalu besar dan RR tidak layak.
    #   TP: Fibonacci Extensions (kekalkan logik asal)
    # ─────────────────────────────────────────────────────────────
    sl = compute_final_sl(price, swing_low, atr, atr_mult=1.0, max_sl_pct=0.08)

    # [FIX-01a] Fib786 sebagai structural stop:
    # fib_786 = level retracement terdalam yang valid sebelum setup batal.
    # Kalau fib786-based SL lebih TINGGI (lebih dekat ke entry = tighter SL) → guna ia.
    # Ini betulkan RR untuk tight fib zones tanpa melonggarkan SL secara arbitrary.
    sl_fib786_floor = fib_786 * 0.997   # buffer 0.3% bawah level 78.6%
    if sl_fib786_floor > sl and sl_fib786_floor < price:
        sl = sl_fib786_floor
        log(f"📐 SL dipertingkat ke Fib786 floor ${fmt(sl)} (lebih ketat dari ATR/structure)")

    # [FIX-TP] Guna Anchor Swing High untuk TP, bukan Fresh Swing High.
    # Fresh high selalunya micro-structure (contoh 0.1565). 
    # Anchor high adalah macro structure (contoh 0.16700).
    target_high = anchor_sh if anchor_sh and anchor_sh > swing_high else swing_high
    rng_target = target_high - swing_low

    # TP: Fibonacci Extensions (Guna Macro Range)
    tp1 = target_high                       # 100% measured move ke Major High
    tp2 = swing_low + (rng_target * 1.618)  # 161.8% Fib extension
    tp3 = swing_low + (rng_target * 2.618)  # 261.8% Fib extension

    # COUNTER-TREND: Cap TP di H4 Swing High
    if is_counter_trend and h4_swing_high > 0:
        tp1 = min(tp1, h4_swing_high)
        tp2 = min(tp2, h4_swing_high)
        tp3 = min(tp3, h4_swing_high)
        if tp1 == tp2 == tp3:
            log("❌ REJECT: Counter-trend TP semua sama selepas cap")
            return None
        log("⚠️ COUNTER-TREND: TP capped at H4 Swing High")

    risk = price - sl
    if risk <= 0:
        log("❌ REJECT: Risk invalid (sl >= entry)")
        return None

    # Validasi TP ordering
    if tp1 <= price:
        log(f"❌ REJECT: TP1 ({fmt(tp1)}) <= entry ({fmt(price)})")
        return None
    if tp2 <= tp1:
        tp2 = tp1 + atr
    if tp3 <= tp2:
        tp3 = tp2 + atr * 2

    rr1 = (tp1 - price) / risk
    rr2 = (tp2 - price) / risk
    log(f"📐 SL: ${fmt(sl)} | TP1 RR:{rr1:.2f} | TP2 RR:{rr2:.2f} | Score: {score}")

    # DETECT PENDING BOS
    global BOS_WATCHLIST
    shs_list = [s for s in swings if s['type'] == 'SH']
    sls_list = [s for s in swings if s['type'] == 'SL']

    if len(sls_list) >= 2:
        last_hl  = sls_list[-1]['price']
        distance = abs(price - last_hl) / last_hl * 100
        if distance <= 2.0 and price > last_hl:
            with _watchlist_lock:  # [FIX-5]
                if sym not in BOS_WATCHLIST:
                    BOS_WATCHLIST[sym] = {"level": last_hl, "type": "HL", "added": time.time()}
                    log(f"📌 PENDING BOS: HL ${fmt(last_hl)} (jarak {distance:.1f}%)")

    if len(shs_list) >= 2:
        last_lh  = shs_list[-1]['price']
        distance = abs(price - last_lh) / last_lh * 100
        if distance <= 2.0 and price < last_lh:
            with _watchlist_lock:  # [FIX-5]
                if sym not in BOS_WATCHLIST:
                    BOS_WATCHLIST[sym] = {"level": last_lh, "type": "LH", "added": time.time()}
                    log(f"📌 PENDING BOS: LH ${fmt(last_lh)} (jarak {distance:.1f}%)")

    return {
        "setup": f"{setup_name} ({'⚡INTRADAY' if setup_mode == 'INTRADAY' else '⚖️SWING'})",
        "entry": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr1": round(rr1, 2),
        "rr2": round(rr2, 2),
        "score": score,
        "fib_zone": fib_zone,
        "fib_500": fib_500,   # [FIX-7]
        "fib_618": fib_618,   # [FIX-7]
        "fib_786": fib_786,   # [FIX-7]
        "timeframe": "H1",
        "setup_mode": setup_mode,          # "INTRADAY" atau "SWING"
        "structure": structure,
        "is_counter_trend": is_counter_trend
    }

# ==========================================
# 5. ENGINE 2: EARLY MOMENTUM (M15) — VOLUME ANOMALY
# ==========================================
def analyze_early_momentum(sym, verbose=True):
    log = lambda msg: print(f"[{sym}-M15] {msg}") if verbose else None
    candles = get_gateio_klines(sym, "15m", 100)
    if len(candles) < 50:
        log("❌ REJECT: Data M15 < 50")
        return None

    avg_vol  = sum(c['v'] for c in candles[-20:-1]) / 19 if len(candles) >= 20 else 1
    curr_vol = candles[-1]['v']
    if curr_vol < avg_vol * 3:
        return None

    vol_spike = curr_vol / avg_vol
    log(f"✅ VOLUME ANOMALY: {vol_spike:.1f}x avg")

    highs     = [c['h'] for c in candles[-50:]]
    lows      = [c['l'] for c in candles[-50:]]
    range_pct = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100
    if range_pct > 5:
        return None

    log(f"✅ ACCUMULATION: Range {range_pct:.1f}%")

    curr  = candles[-1]
    prev  = candles[-2]
    price = curr['c']

    body       = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    is_pinbar   = lower_wick > body * 2 and curr['c'] > curr['o']
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                    and curr['c'] > prev['o'] and curr['o'] < prev['c'])
    if not (is_pinbar or is_engulfing):
        return None

    setup_name = "⚡ PINBAR MOMENTUM" if is_pinbar else "⚡ ENGULFING MOMENTUM"
    entry      = price

    # ─────────────────────────────────────────────────────────────
    # [ALPHA-RISK] SL/TP Calculation — Institutional Grade
    # ─────────────────────────────────────────────────────────────
    atr_m15 = calculate_atr(candles, 14)
    range_low = min(lows[-20:])
    range_high = max(highs[-50:])

    sl = compute_final_sl(entry, range_low, atr_m15, atr_mult=0.75, max_sl_pct=0.08)

    risk = entry - sl
    if risk <= 0:
        return None

    tp1 = range_high + (atr_m15 * 0.5 if atr_m15 > 0 else range_high * 0.01)
    tp2 = entry + risk * 2.618
    tp3 = entry + risk * 4.236

    # Pastikan TP teratur: tp1 < tp2 < tp3
    tp2 = max(tp1 * 1.005, tp2)
    tp3 = max(tp2 * 1.005, tp3)

    # [FIX-3] HTF BIAS — H4 EMA betul (guna calculate_ema() bukan loop kosong)
    h4_bias = "neutral"
    try:
        candles_h4 = get_gateio_klines(sym, "4h", 50)
        if len(candles_h4) >= 20:
            # BETUL: Guna semua 50 candles untuk EMA20 yang tepat
            h4_closes = [c['c'] for c in candles_h4]
            h4_ema    = calculate_ema(h4_closes, 20)     # [FIX-3] 
            h4_current = candles_h4[-1]['c']
            h4_bias    = "uptrend" if h4_current > h4_ema else "downtrend"
    except Exception:
        pass

    if h4_bias == "downtrend":
        sl        = range_low - (atr_m15 * 0.5 if atr_m15 > 0 else range_low * 0.005)
        risk      = entry - sl
        if risk <= 0: return None
        tp1       = range_high + (atr_m15 * 0.25 if atr_m15 > 0 else range_high * 0.005)
        tp2       = entry + risk * 1.618    # Reduced target for counter-trend
        tp3       = tp2                     # Cap TP3 = TP2 untuk counter-trend
        setup_name = "⚡ COUNTER-TREND (Risky)"
        log("⚠️ HTF BIAS: H4 Downtrend. SL tightened, TP capped.")
    else:
        log("✅ HTF BIAS: H4 Uptrend/Neutral. Full targets.")

    rr1 = (tp1 - entry) / risk
    rr2 = (tp2 - entry) / risk
    log(f"⚡ MOMENTUM: {setup_name} | H4: {h4_bias} | RR1:{rr1:.2f}")

    return {
        "setup": setup_name,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr1": round(rr1, 2),
        "rr2": round(rr2, 2),
        "score": 3,
        "fib_zone": "N/A",
        "timeframe": "M15",
        "vol_spike": vol_spike,
        "range_pct": range_pct,
        "h4_bias": h4_bias,
        "is_counter_trend": (h4_bias == "downtrend")
    }

# ==========================================
# 6. SIGNAL GENERATOR
# ==========================================
def send_signal(sym, smc_data, vol_24h, btc_chg=0.0):
    cfg   = get_config()
    entry = smc_data["entry"]
    sl    = smc_data["sl"]
    tp1, tp2, tp3 = smc_data["tp1"], smc_data["tp2"], smc_data["tp3"]
    timeframe     = smc_data.get("timeframe", "H1")
    risk          = entry - sl
    
    if smc_data["score"] < cfg["score_pass"]:
        return False

    # ─────────────────────────────────────────────────────────────
    # [ALPHA-RISK] Position Sizing & Risk Management
    # ─────────────────────────────────────────────────────────────
    # Ambil modal pengguna (default $50 jika belum set)
    user_cap, user_risk = get_user_capital(int(ADMIN_ID) if ADMIN_ID else 0)

    # Kira position size dengan double cap
    pos_usd, pos_coins, risk_usd = calculate_position_size(user_cap, user_risk, entry, sl)

    # Validasi: jika position size 0, skip
    if pos_usd <= 0 or pos_coins <= 0:
        print(f"[SKIP] {sym}: Position size invalid (pos_usd={pos_usd}, pos_coins={pos_coins})")
        return False

    # [FIX-01b] Minimum RR filter — dual check: RR1 (TP1=swing_high) ATAU RR2 (TP2=1.618 ext).
    # Masalah asal: TP1 = swing_high adalah target conservative; untuk tight fib range (<3%),
    # price di 50–78.6% retracement bermakna TP1 hanya 21–50% range jauhnya → RR1 sering < 1.5.
    # Fix: lulus jika SAMA ADA rr1 >= 1.5 ATAU rr2 >= 1.5 (TP2 = 1.618 extension adalah
    # target institutional yang lebih realistik untuk Fib pullback strategy).
    rr1 = (tp1 - entry) / risk if risk > 0 else 0
    rr2 = (tp2 - entry) / risk if risk > 0 else 0
    if rr1 < 1.5 and rr2 < 1.5:
        print(f"[SKIP] {sym}: RR1={rr1:.2f} RR2={rr2:.2f} — kedua-duanya < 1.5, tidak layak")
        return False

    # Validasi SL/TP ordering
    if sl >= entry:
        print(f"[SKIP] {sym}: SL ({fmt(sl)}) >= entry ({fmt(entry)}) — invalid")
        return False
    if tp1 <= entry:
        print(f"[SKIP] {sym}: TP1 ({fmt(tp1)}) <= entry ({fmt(entry)}) — invalid")
        return False

    current_price = get_gateio_price(sym)
    if current_price > 0:
        price_gap = abs(current_price - entry) / entry * 100
        if price_gap > 1.0:
            mode = smc_data.get("setup_mode", " ")
            print(f"[INFO] {sym}: Price bergerak {price_gap:.1f}% dari H1 entry ({fmt(entry)}) — signal diteruskan ({mode})")
        # Jika price dah bergerak SANGAT jauh (>15%), tambah ke pullback watchlist sahaja
        if price_gap > 15.0:
            if smc_data.get("score", 0) >= 2 and sym not in PULLBACK_WATCHLIST:
                add_pullback_watchlist(sym, smc_data)
            print(f"[SKIP] {sym}: Price bergerak {price_gap:.1f}% — terlalu jauh, tunggu pullback")
            return False

    sl_pct  = (entry - sl)   / entry * 100
    tp1_pct = (tp1 - entry)  / entry * 100
    tp2_pct = (tp2 - entry)  / entry * 100
    tp3_pct = (tp3 - entry)  / entry * 100
    # rr1 dan rr2 sudah dikira dalam RR filter block di atas

    btc_warn = f"⚠️ <b>BTC ALERT:</b> BTC {btc_chg:+.2f}%\n\n" if btc_chg < -4.0 else ""

    pair_name    = f"{sym}USDT"
    engine_icon  = "⚡" if timeframe == "M15" else "🏴‍☠️"
    engine_label = "MOMENTUM" if timeframe == "M15" else "PULLBACK"

    counter_trend_badge = ""
    if smc_data.get("is_counter_trend", False):
        counter_trend_badge = "⚠️ <b>COUNTER-TREND MODE:</b> TP capped\n"

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 Gate.io",     url=f"https://www.gate.io/trade/{sym}_USDT"),
        InlineKeyboardButton("📈 TradingView", url=f"https://www.tradingview.com/chart/?symbol=GATEIO:{sym}USDT")
    )

    msg = (
        f"{engine_icon} <b>ALPHA {engine_label} — {sym}/USDT</b>\n"
        f"📋 <code>{pair_name}</code>\n\n"
        f"{btc_warn}"
        f"{counter_trend_badge}"
        f"💰 <b>Entry:</b> <code>${fmt(entry)}</code>\n"
        f"📊 <b>Vol24H:</b> <code>${vol_24h/1e6:.2f}M</code>\n\n"
        f"🛑 <b>SL:</b> <code>${fmt(sl)}</code> <i>(-{sl_pct:.1f}%)</i>\n"
        f"📈 <b>TP1:</b> <code>${fmt(tp1)}</code> <i>(+{tp1_pct:.1f}%) [RR {rr1:.1f}]</i>\n"
        f"📈 <b>TP2:</b> <code>${fmt(tp2)}</code> <i>(+{tp2_pct:.1f}%) [RR {rr2:.1f}]</i>\n"
        f"📈 <b>TP3:</b> <code>${fmt(tp3)}</code> <i>(+{tp3_pct:.1f}%)</i>\n\n"
        f"💼 <b>Position Size:</b> <code>${pos_usd:.2f}</code> ({pos_coins:.4f} {sym})\n"
        f"⚠️ <b>Risk:</b> <code>${risk_usd:.2f}</code> ({user_risk}% of ${user_cap:.2f})\n\n"
        f"🧠 <b>Setup:</b> <code>{smc_data['setup']}</code>\n"
        f"⏱️ <b>TF:</b> <code>{timeframe}</code> | <b>Score:</b> <code>{smc_data['score']}/{cfg['score_pass']}</code>"
    )

    if timeframe == "M15" and "vol_spike" in smc_data:
        msg += f"\n⚡ <b>Vol Spike:</b> <code>{smc_data['vol_spike']:.1f}x Avg</code>"

    try:
        sent = bot.send_message(VIP_CHANNEL_ID, msg, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
        record = {
            "contract": sym,
            "symbol": sym,
            "network": "GATEIO",
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rr1": round(rr1, 2),
            "rr2": round(rr2, 2),
            "setup": smc_data["setup"],
            "fibo_zone": smc_data.get("fib_zone", "N/A"),
            "score": smc_data["score"],
            "volume_24h": vol_24h,
            "timeframe": timeframe,
            "msg_id": sent.message_id,
            "sent_at": int(time.time())
        }
        save_signal(record)
        add_cooldown(sym)
        print(f"[SIGNAL SENT ✅] {sym} | {smc_data['setup']} | {timeframe} | RR1:{rr1:.2f}")
        return True
    except Exception as e:
        alert_admin(f"Gagal hantar signal {sym}: {e}")
        return False

# ==========================================
# 7. SCANNER & TRADE MONITOR
# ==========================================
IS_SCANNING = True
WATCHLIST            = {}
WATCHLIST_TIMEOUT    = 900          # M15 momentum: 15 minit (pattern cepat)
PULLBACK_WATCHLIST   = {}
PULLBACK_TIMEOUT     = 28800        # M5 pullback: 8 JAM (allow 2 H4 candles for SL hunt/reject/retest)
BOS_WATCHLIST        = {}
BOS_WATCHLIST_TIMEOUT = 28800       # BOS breakout: 8 JAM (allow retest confirmation)

# [FIX-6] add_pullback_watchlist: guna fib_500/fib_786 yang betul dari return dict
def add_pullback_watchlist(sym, smc_data):
    with _watchlist_lock:  # [FIX-5]
        PULLBACK_WATCHLIST[sym] = {
            "entry":    smc_data["entry"],
            "fib_500":  smc_data.get("fib_500", smc_data["entry"]),         # [FIX-6]
            "fib_786":  smc_data.get("fib_786", smc_data["sl"]),            # [FIX-6]
            "sl":       smc_data["sl"],
            "added":    time.time(),
            "setup":    smc_data["setup"]
        }
        print(f"[{sym}] 📌 MASUK PULLBACK WATCHLIST: Fib zone {fmt(smc_data.get('fib_786', smc_data['sl']))} - {fmt(smc_data['entry'])}")

def scan_once():
    global IS_SCANNING_ACTIVE
    # [FIX-12] Prevent concurrent scan — jika scan sedang berjalan, skip
    with _scan_lock:
        if IS_SCANNING_ACTIVE:
            print("[SCAN] ⏭️ Scan sedang berjalan, skip cycle ini")
            return
        IS_SCANNING_ACTIVE = True
    
    try:
        if not IS_SCANNING:
            return

        btc_chg = get_btc_24h_change()
        print(f"[BTC] 24H Change: {btc_chg:+.2f}%")
        cfg = get_config()

        print(f"\n{'='*60}")
        print(f"🔍 [{datetime.now().strftime('%H:%M:%S')}] SCAN | Mode: {SCAN_MODE.upper()}")
        print(f"{'='*60}")

        tickers = get_gateio_tickers()
        print(f"[GATEIO] Total {len(tickers)} pairs")

        candidates = [t for t in tickers if t["volume_24h"] >= cfg["min_vol_24h"]]
        print(f"[SAFETY NET] {len(candidates)} pairs lulus Min Vol")

        # [FIX-4] get_active_trades() SEKALI sahaja, bukan dalam loop
        active = get_active_trades()
        print(f"[ACTIVE] {len(active)} trades aktif")

        passed = 0
        momentum_candidates = sorted(candidates, key=lambda x: x["volume_24h"], reverse=True)[:100]

        for t in candidates:
            sym           = t["symbol"]
            current_price = t.get("last_price", 0)

            is_blacklisted, bl_reason = is_blacklisted_symbol(sym)
            if is_blacklisted:
                continue

            if is_in_cooldown(sym):
                if check_cooldown_override(sym, current_price):
                    print(f"[{sym}] 🔄 OVERRIDE")
                else:
                    continue

            # [FIX-4] Semak dari dict yang sudah diambil sebelum loop
            if sym in active:
                continue

            print(f"\n[{sym}] 🔎 ANALYZING...")

            if SCAN_MODE in ["pullback", "both"]:
                smc = analyze_smc_pa(sym, verbose=True)
                if smc and smc["score"] >= cfg["score_pass"]:
                    if send_signal(sym, smc, t["volume_24h"], btc_chg=btc_chg):
                        passed += 1
                        time.sleep(2)

            if SCAN_MODE in ["momentum", "both"]:
                if t in momentum_candidates:
                    if SCAN_MODE == "both" and is_in_cooldown(sym):
                        continue

                    candles_m15 = get_gateio_klines(sym, "15m", 100)
                    if len(candles_m15) < 50:
                        continue

                    curr         = candles_m15[-1]
                    current_time = time.time()
                    if (current_time - curr['t']) < (15 * 60 * 0.90):
                        continue

                    avg_vol  = sum(c['v'] for c in candles_m15[-20:-1]) / 19
                    curr_vol = curr['v']

                    if curr_vol >= avg_vol * 3:
                        with _watchlist_lock:  # [FIX-5]
                            if sym not in WATCHLIST:
                                WATCHLIST[sym] = time.time()
                                print(f"[{sym}] 📌 MASUK WATCHLIST: Volume Anomaly ({curr_vol/avg_vol:.1f}x) [timeout: 15min]")

                        highs     = [c['h'] for c in candles_m15[-50:]]
                        lows      = [c['l'] for c in candles_m15[-50:]]
                        range_pct = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100

                        range_high  = max(highs)
                        range_low   = min(lows)
                        total_range = range_high - range_low
                        
                        if curr['c'] < range_low + (total_range * 0.75):
                            with _watchlist_lock:
                                if sym in WATCHLIST: del WATCHLIST[sym]
                            continue

                        recent_highs = [c['h'] for c in candles_m15[-20:-1]]
                        local_lh     = max(recent_highs) if recent_highs else range_high
                        
                        if curr['c'] <= local_lh:
                            with _watchlist_lock:
                                if sym in WATCHLIST: del WATCHLIST[sym]
                            continue

                        prev         = candles_m15[-2]
                        body         = abs(curr['c'] - curr['o'])
                        lower_wick   = min(curr['o'], curr['c']) - curr['l']
                        is_pinbar    = lower_wick > body * 2 and curr['c'] > curr['o']
                        is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                                        and curr['c'] > prev['o'] and curr['o'] < prev['c'])

                        if range_pct <= 5 and (is_pinbar or is_engulfing):
                            # ─────────────────────────────────────────────
                            # [ALPHA-RISK] M15 inline SL/TP: ATR + compute_final_sl
                            # ─────────────────────────────────────────────
                            atr_m15   = calculate_atr(candles_m15, 14)
                            sl_m15    = compute_final_sl(curr['c'], min(lows[-20:]), atr_m15, atr_mult=0.75, max_sl_pct=0.08)
                            risk_m15  = curr['c'] - sl_m15
                            if risk_m15 <= 0:
                                continue
                            tp1_m15   = max(highs[-50:]) + (atr_m15 * 0.5 if atr_m15 > 0 else max(highs[-50:]) * 0.01)
                            tp2_m15   = curr['c'] + risk_m15 * 2.618
                            tp3_m15   = curr['c'] + risk_m15 * 4.236
                            tp2_m15   = max(tp1_m15 * 1.005, tp2_m15)
                            tp3_m15   = max(tp2_m15 * 1.005, tp3_m15)

                            smc_m15 = {
                                "setup":              "⚡ PINBAR MOMENTUM" if is_pinbar else "⚡ ENGULFING MOMENTUM",
                                "entry":             curr['c'],
                                "sl":                sl_m15,
                                "tp1":               tp1_m15,
                                "tp2":               tp2_m15,
                                "tp3":               tp3_m15,
                                "rr1":               round((tp1_m15 - curr['c']) / risk_m15, 2),
                                "rr2":               round((tp2_m15 - curr['c']) / risk_m15, 2),
                                "score":             3,
                                "fib_zone":           "N/A",
                                "timeframe":          "M15",
                                "vol_spike":         curr_vol / avg_vol,
                                "range_pct":         range_pct,
                                "is_counter_trend":  False
                            }
                            if send_signal(sym, smc_m15, t["volume_24h"], btc_chg=btc_chg):
                                passed += 1
                                with _watchlist_lock:
                                    if sym in WATCHLIST: del WATCHLIST[sym]
                                time.sleep(2)
                    else:
                        with _watchlist_lock:
                            if sym in WATCHLIST: del WATCHLIST[sym]

        print(f"\n📊 SCAN SELESAI | {passed} signal dihantar")
        print(f"{'='*60}\n")

    finally:
        with _scan_lock:
            IS_SCANNING_ACTIVE = False  # [FIX-12] Sentiasa reset, walaupun exception

def monitor_active_trades():
    active = get_active_trades()
    if not active:
        return
    for sym, trade in active.items():
        try:
            candles = get_gateio_klines(sym, "1h", 5)
            if not candles:
                continue
            cp          = candles[-1]['c']
            mid         = trade.get("msg_id")
            entry_price = trade.get("entry", 0)

            def notify(text, _mid=mid):
                full_text = f"<b>{sym}</b>\n{text}"
                kw = {"parse_mode": "HTML"}
                if _mid:
                    kw["reply_to_message_id"] = _mid
                try:
                    bot.send_message(VIP_CHANNEL_ID, full_text, **kw)
                except Exception:
                    bot.send_message(VIP_CHANNEL_ID, full_text, parse_mode="HTML")

            updates = {}

            if cp >= trade["tp1"] and not trade.get("tp1_hit"):
                updates["tp1_hit"] = True
                profit_pct = (trade["tp1"] - entry_price) / entry_price * 100
                notify(
                    f"✅ <b>TP1 HIT!</b>\n"
                    f"💰 Harga: <code>${fmt(cp)}</code>\n"
                    f"📊 Profit: <code>+{profit_pct:.2f}%</code>\n"
                    f"🔒 Alih SL → BE: <code>${fmt(entry_price)}</code>"
                )

            if cp >= trade["tp2"] and not trade.get("tp2_hit"):
                updates["tp2_hit"] = True
                profit_pct = (trade["tp2"] - entry_price) / entry_price * 100
                notify(
                    f"🚀 <b>TP2 HIT!</b>\n"
                    f"💰 Harga: <code>${fmt(cp)}</code>\n"
                    f"📊 Profit: <code>+{profit_pct:.2f}%</code>\n"
                    f"📈 Trail SL → TP1: <code>${fmt(trade['tp1'])}</code>"
                )

            if cp >= trade["tp3"] and not trade.get("tp3_hit"):
                updates["tp3_hit"] = True
                updates["closed"] = True
                profit_pct = (cp - entry_price) / entry_price * 100
                notify(
                    f"🏆 <b>TP3 MOONSHOT!</b>\n"
                    f"💰 Tutup: <code>${fmt(cp)}</code>\n"
                    f"📊 Profit: <code>+{profit_pct:.2f}%</code>"
                )

            elif cp <= trade["sl"] and not trade.get("sl_hit"):
                updates["sl_hit"] = True
                updates["closed"] = True
                loss_pct = (cp - entry_price) / entry_price * 100
                notify(
                    f"❌ <b>SL HIT</b>\n"
                    f"💰 Tutup: <code>${fmt(cp)}</code>\n"
                    f"📉 Loss: <code>{loss_pct:.2f}%</code>"
                )

            if updates:
                update_signal(sym, updates)
                print(f"[MONITOR] {sym}: {list(updates.keys())}")

        except Exception as e:
            print(f"[MONITOR] {sym}: {e}")

# ==========================================
# 8. TELEGRAM COMMANDS
# ==========================================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    cfg       = get_config()
    active    = get_active_trades()
    uptime_m  = int((time.time() - START_TIME) / 60)
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")
    text = (
        f"🏴‍☠️ <b>ALPHA — Dual Engine Sniper</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Uptime  : <code>{uptime_m}m</code>\n"
        f"💼 Trade   : <code>{len(active)} aktif</code>\n"
        f"🔧 Scan   : <code>{'✅ AKTIF' if IS_SCANNING else '⛔ STOP'}</code>\n\n"
        f"⚡ <b>Scan Mode:</b> <code>{SCAN_MODE.upper()}</code>\n"
        f"🎛️ <b>Preset:</b> <code>{preset_lbl}</code>\n\n"
        f"<b>🏴‍️ Engine 1 — Pullback (H1+H4):</b>\n"
        f"Fractal Swing | EMA | ATR+Fib SL | Fib Ext TP\n\n"
        f"<b>⚡ Engine 2 — Momentum (M15):</b>\n"
        f"Volume Anomaly | ATR+Fib SL | Fib Ratio TP"
    )
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🟢 Soft",     callback_data="tune:soft"),
        InlineKeyboardButton("🟡 Standard", callback_data="tune:standard"),
        InlineKeyboardButton("🔴 Hard",     callback_data="tune:hard")
    )
    kb.add(
        InlineKeyboardButton("🏴‍☠️ Pullback", callback_data="mode:pullback"),
        InlineKeyboardButton("⚡ Momentum",  callback_data="mode:momentum"),
        InlineKeyboardButton("🔄 Both",      callback_data="mode:both")
    )
    kb.add(
        InlineKeyboardButton("▶️ Mula",    callback_data="scan_on"),
        InlineKeyboardButton("⏸ Henti",   callback_data="scan_off"),
        InlineKeyboardButton("📓 Journal", callback_data="journal")
    )
    kb.add(
        InlineKeyboardButton("📊 Status", callback_data="status"),
        InlineKeyboardButton("❓ Help",   callback_data="help")
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
        bot.send_message(call.message.chat.id, f"✅ Mode ditukar ke: <b>{new_mode.upper()}</b>", parse_mode="HTML")
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
            f"🟢 <code>/tune soft</code>     — Vol $500K, Pass 2\n"
            f"🟡 <code>/tune standard</code> — Vol $1M, Pass 3\n"
            f"🔴 <code>/tune hard</code>     — Vol $2.5M, Pass 4"
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
        bot.reply_to(msg, f"⚡ Mode: <b>{SCAN_MODE.upper()}</b>\n\nGuna: <code>/mode pullback | momentum | both</code>", parse_mode="HTML")
        return
    new_mode = parts[1].lower()
    if new_mode in ["pullback", "momentum", "both"]:
        SCAN_MODE = new_mode
        bot.reply_to(msg, f"✅ Mode ditukar ke: <b>{new_mode.upper()}</b>", parse_mode="HTML")
        alert_admin(f"⚡ Mode changed: {new_mode.upper()}")
    else:
        bot.reply_to(msg, "❌ Mode tidak sah. Guna: pullback | momentum | both")

@bot.message_handler(commands=["pair"])
def cmd_pair(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "/pair [SYMBOL] (Contoh: /pair BTC)")
        return
    sym = parts[1].upper()
    bot.reply_to(msg, f"🔍 Menganalisa <code>{sym}</code>...", parse_mode="HTML")
    def _do():
        smc_h1  = analyze_smc_pa(sym, verbose=False)
        smc_m15 = analyze_early_momentum(sym, verbose=False)
        if smc_h1:
            bot.send_message(msg.chat.id,
                f"🏴‍️ <b>{sym} — PULLBACK (H1)</b>\n"
                f"Setup: <code>{smc_h1['setup']}</code>\n"
                f"Score: <code>{smc_h1['score']}</code>\n"
                f"RR1: <code>{smc_h1.get('rr1', 0):.2f}</code>\n"
                f"Structure: <code>{smc_h1.get('structure', 'unknown')}</code>",
                parse_mode="HTML")
        elif smc_m15:
            bot.send_message(msg.chat.id,
                f"⚡ <b>{sym} — MOMENTUM (M15)</b>\n"
                f"Setup: <code>{smc_m15['setup']}</code>\n"
                f"Vol Spike: <code>{smc_m15['vol_spike']:.1f}x</code>\n"
                f"RR1: <code>{smc_m15.get('rr1', 0):.2f}</code>",
                parse_mode="HTML")
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
    cfg       = get_config()
    active    = get_active_trades()
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")
    with _watchlist_lock:
        pw_count = len(PULLBACK_WATCHLIST)
        bw_count = len(BOS_WATCHLIST)
        w_count  = len(WATCHLIST)
    bot.reply_to(msg, (
        f"📊 <b>STATUS</b>\n"
        f"Scan    : {'🟢 AKTIF' if IS_SCANNING else '⛔ STOP'}\n"
        f"Mode    : <code>{SCAN_MODE.upper()}</code>\n"
        f"Trade   : <code>{len(active)}</code> aktif\n\n"
        f"📌 <b>Watchlists:</b>\n"
        f"├ M15 Momentum  : <code>{w_count}</code> (15 min timeout)\n"
        f"├ M5 Pullback   : <code>{pw_count}</code> (8 hour timeout)\n"
        f"└ BOS Breakout  : <code>{bw_count}</code> (8 hour timeout)\n\n"
        f"🎛️ Preset: <code>{preset_lbl}</code>\n"
        f"Vol     : <code>${cfg['min_vol_24h']/1e6:.1f}M</code>\n"
        f"Pass    : <code>{cfg['score_pass']}</code>"
    ), parse_mode="HTML")

@bot.message_handler(commands=["journal"])
def cmd_journal(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    bot.reply_to(msg, generate_journal(), parse_mode="HTML")

@bot.message_handler(commands=["modal"])
def cmd_modal(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    args = msg.text.split()
    if len(args) < 2:
        cap, risk = get_user_capital(int(ADMIN_ID))
        bot.reply_to(msg, 
            f"💼 <b>Modal Semasa:</b> ${cap:,.2f}\n"
            f"⚠️ <b>Risk:</b> {risk}%\n\n"
            f"Cara set: <code>/modal 1000</code>", 
            parse_mode="HTML")
        return
    try:
        new_cap = float(args[1])
        if new_cap < 10:
            bot.reply_to(msg, "⚠️ Minimum modal: $10", parse_mode="HTML")
            return
        set_user_capital(int(ADMIN_ID), new_cap)
        bot.reply_to(msg, 
            f"✅ <b>Modal Dikemas Kini:</b> ${new_cap:,.2f}\n"
            f"Risk default: 2% (${new_cap * 0.02:,.2f} per trade)\n"
            f"Max position: 50% modal (${new_cap * 0.50:,.2f})", 
            parse_mode="HTML")
    except ValueError:
        bot.reply_to(msg, "️ Format: <code>/modal 1000</code>", parse_mode="HTML")

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
        "/tune [PRESET]  — Tukar preset\n"
        "/modal [AMOUNT] — Set modal trading"
    ), parse_mode="HTML")

# ==========================================
# 9. JOURNAL
# ==========================================
def generate_journal():
    trades = get_signals_since(7)
    if not trades:
        return "📓 <b>JOURNAL (7D)</b>\n\nTiada signal dalam 7 hari lepas."
    
    total  = len(trades)
    tp1_n  = sum(1 for t in trades if t.get("tp1_hit"))
    tp2_n  = sum(1 for t in trades if t.get("tp2_hit"))
    tp3_n  = sum(1 for t in trades if t.get("tp3_hit"))
    sl_n   = sum(1 for t in trades if t.get("sl_hit"))
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

# ==========================================
# 10. PULLBACK & BOS MONITORS
# ==========================================
def monitor_pullback_watchlist():
    if not IS_SCANNING or not PULLBACK_WATCHLIST:
        return
    with _watchlist_lock:
        items = list(PULLBACK_WATCHLIST.items())
    
    print(f"\n[PULLBACK MONITOR] Checking {len(items)} coins... (timeout: 8h)")
    symbols_to_remove = []
    
    for sym, data in items:
        try:
            elapsed_hours = (time.time() - data["added"]) / 3600
            if elapsed_hours > (28800 / 3600):  # 8 jam
                print(f"[{sym}] ⏱️ Timeout 8h — buang dari PULLBACK_WATCHLIST")
                symbols_to_remove.append(sym)
                continue

            candles_m5 = get_gateio_klines(sym, "5m", 50)
            if len(candles_m5) < 20:
                continue

            current_price = candles_m5[-1]['c']
            # [FIX-6] Guna fib_786 dan entry yang betul
            if current_price > data["entry"] or current_price < data["fib_786"]:
                continue

            recent_10  = candles_m5[-10:]
            red_candles = sum(1 for c in recent_10 if c['c'] < c['o'])
            if red_candles >= 8:
                print(f"[{sym}] SLOW DUMP ({red_candles}/10 merah). Buang dari watchlist.")
                symbols_to_remove.append(sym)
                continue

            avg_vol_m5 = sum(c['v'] for c in candles_m5[-20:-1]) / 19
            curr_vol_m5 = candles_m5[-1]['v']
            if curr_vol_m5 > avg_vol_m5 * 1.5:
                continue

            curr = candles_m5[-1]
            prev = candles_m5[-2]
            body       = abs(curr['c'] - curr['o'])
            lower_wick = min(curr['o'], curr['c']) - curr['l']
            is_pinbar   = lower_wick > body * 2 and curr['c'] > curr['o']
            is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                            and curr['c'] > prev['o'] and curr['o'] < prev['c'])

            if is_pinbar or is_engulfing:
                atr_m5    = calculate_atr(candles_m5, 14)
                # [ALPHA-RISK] Guna compute_final_sl
                sl_m5     = compute_final_sl(curr['c'], data["fib_786"], atr_m5, atr_mult=0.75, max_sl_pct=0.08)
                risk_m5   = curr['c'] - sl_m5
                if risk_m5 <= 0:
                    continue
                smc_pullback = {
                    "setup":             "🔄 PULLBACK RECOVERY (M5)",
                    "entry":            curr['c'],
                    "sl":               sl_m5,
                    "tp1":              data["entry"],
                    "tp2":              data["fib_500"],
                    "tp3":              data["fib_500"] * 1.05,
                    "rr1":              round((data["entry"] - curr['c']) / risk_m5, 2),
                    "rr2":              round((data["fib_500"] - curr['c']) / risk_m5, 2),
                    "score":            4,
                    "fib_zone":          "N/A",
                    "timeframe":         "M5",
                    "is_counter_trend": False
                }
                if send_signal(sym, smc_pullback, 0, btc_chg=0.0):
                    symbols_to_remove.append(sym)
                    print(f"[{sym}] 🚀 PULLBACK TRIGGERED! ({elapsed_hours:.1f}h elapsed)")
        except Exception as e:
            print(f"[PULLBACK ERROR] {sym}: {e}")

    with _watchlist_lock:
        for sym in symbols_to_remove:
            if sym in PULLBACK_WATCHLIST: del PULLBACK_WATCHLIST[sym]

def monitor_bos_breaks():
    if not IS_SCANNING or not BOS_WATCHLIST:
        return
    with _watchlist_lock:
        items = list(BOS_WATCHLIST.items())
    
    print(f"\n[BOS MONITOR] Checking {len(items)} pending BOS... (timeout: 8h)")
    symbols_to_remove = []
    
    for sym, data in items:
        try:
            elapsed_hours = (time.time() - data["added"]) / 3600
            if elapsed_hours > (BOS_WATCHLIST_TIMEOUT / 3600):  # 8 jam
                print(f"[{sym}] ⏱️ BOS timeout 8h — buang dari watchlist")
                symbols_to_remove.append(sym)
                continue

            current_price = get_gateio_price(sym)
            if current_price <= 0:
                continue

            level    = data["level"]
            bos_type = data["type"]

            if bos_type == "HL":
                # [FIX-17] Bot ini LONG ONLY — tiada short signal.
                # HL = Higher Low = support level.
                #
                # CASE A: Price MASIH atas HL (dalam 1.5%) + reversal candle → HL HOLD signal (LONG)
                # CASE B: Price dah JATUH bawah HL → structure break, jangan trade, remove dari watchlist
                proximity_pct = (current_price - level) / level * 100  # positif = atas level

                if current_price < level:
                    # HL dah tembus ke bawah — structure breakdown, skip pair
                    print(f"[{sym}] 🔴 STRUCTURE BREAK: HL ${fmt(level)} jatuh @ ${fmt(current_price)} ({elapsed_hours:.1f}h) — Long-only bot, skip.")
                    symbols_to_remove.append(sym)

                elif 0 <= proximity_pct <= 1.5:
                    # Price dalam 1.5% ATAS HL — periksa reversal candle untuk HL HOLD signal
                    candles_m15 = get_gateio_klines(sym, "15m", 30)
                    if len(candles_m15) >= 10:
                        curr = candles_m15[-1]
                        prev = candles_m15[-2]
                        body       = abs(curr['c'] - curr['o'])
                        lower_wick = min(curr['o'], curr['c']) - curr['l']
                        is_pinbar   = lower_wick > body * 2 and curr['c'] > curr['o']
                        is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                                        and curr['c'] > prev['o'] and curr['o'] < prev['c'])

                        if is_pinbar or is_engulfing:
                            atr_m15  = calculate_atr(candles_m15, 14)
                            # [ALPHA-RISK] Guna compute_final_sl
                            sl_hold  = compute_final_sl(current_price, level, atr_m15, atr_mult=1.0, max_sl_pct=0.08)
                            risk_hold = current_price - sl_hold
                            if risk_hold > 0:
                                print(f"[{sym}] 🏗️ HL HOLD! ${fmt(level)} dijaga @ ${fmt(current_price)} ({elapsed_hours:.1f}h)")
                                pattern = "Pinbar" if is_pinbar else "Engulfing"
                                smc_hold = {
                                    "setup": f"🏗️ HL HOLD — {pattern} (M15)",
                                    "entry": current_price,
                                    "sl": sl_hold,
                                    "tp1":   current_price + risk_hold * 1.618,
                                    "tp2":   current_price + risk_hold * 2.618,
                                    "tp3":   current_price + risk_hold * 4.236,
                                    "rr1":   1.618,
                                    "rr2":   2.618,
                                    "score": 4,
                                    "fib_zone": "N/A",
                                    "timeframe": "M15",
                                    "is_counter_trend": False,
                                    "fib_500": current_price + risk_hold * 2.618,
                                    "fib_786": sl_hold
                                }
                                if send_signal(sym, smc_hold, 0, btc_chg=0.0):
                                    symbols_to_remove.append(sym)

            elif bos_type == "LH" and current_price > level:
                # Bullish BOS (LH broken) — long bias
                print(f"[{sym}] 💥 BOS BREAK! LH ${fmt(level)} ditembusi @ ${fmt(current_price)} ({elapsed_hours:.1f}h)")
                candles_m5 = get_gateio_klines(sym, "5m", 30)
                atr_bos    = calculate_atr(candles_m5, 14) if len(candles_m5) >= 15 else current_price * 0.015
                # [ALPHA-RISK] Guna compute_final_sl
                sl_bos     = compute_final_sl(current_price, level * 0.98, atr_bos, atr_mult=1.0, max_sl_pct=0.08)
                risk_bos   = current_price - sl_bos
                if risk_bos <= 0:
                    symbols_to_remove.append(sym)
                    continue
                smc_bos = {
                    "setup": "💥 BOS BREAK (LH Tembus - Bullish)",
                    "entry": current_price,
                    "sl": sl_bos,
                    "tp1": current_price + risk_bos * 1.618,
                    "tp2": current_price + risk_bos * 2.618,
                    "tp3": current_price + risk_bos * 4.236,
                    "rr1": 1.618,
                    "rr2": 2.618,
                    "score": 4,
                    "fib_zone": "N/A",
                    "timeframe": "M5",
                    "is_counter_trend": False
                }
                if send_signal(sym, smc_bos, 0, btc_chg=0.0):
                    symbols_to_remove.append(sym)

        except Exception as e:
            print(f"[BOS ERROR] {sym}: {e}")

    with _watchlist_lock:
        for sym in symbols_to_remove:
            if sym in BOS_WATCHLIST: del BOS_WATCHLIST[sym]

def fast_track_watchlist():
    # Cleanup old WATCHLIST entries (15 min timeout)
    with _watchlist_lock:
        now = time.time()
        to_remove = [sym for sym, added_time in WATCHLIST.items()
                     if now - added_time > WATCHLIST_TIMEOUT]
        for sym in to_remove:
            del WATCHLIST[sym]
        if to_remove:
            print(f"[WATCHLIST] Cleanup: {len(to_remove)} entries expired (15min timeout)")
    
    monitor_pullback_watchlist()
    monitor_bos_breaks()

# ==========================================
# 11. SCHEDULER & MAIN
# ==========================================
def run_scheduler():
    schedule.every(5).minutes.do(lambda: threading.Thread(target=scan_once, daemon=True).start())
    schedule.every(5).minutes.do(lambda: threading.Thread(target=monitor_active_trades, daemon=True).start())
    schedule.every(30).seconds.do(lambda: threading.Thread(target=fast_track_watchlist, daemon=True).start())
    
    while True:
        schedule.run_pending()
        time.sleep(1)

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALPHA DUAL ENGINE v14.5 [ALPHA RISK MGMT] ACTIVE")
    
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
        f"🏴‍☠️ ALPHA Dual Engine v14.5 DEPLOYED\n"
        f"Mode: {SCAN_MODE.upper()}\n"
        f"Preset: {PRESETS[get_config()['active_preset']]['label']}\n"
        f"[ALPHA Risk Mgmt | Position Sizing | ATR+Structure SL]\n"
        f"Gunakan /modal untuk set modal trading"
    )
    threading.Thread(target=scan_once).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
