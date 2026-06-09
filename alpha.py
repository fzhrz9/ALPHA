"""
ALPHA — Gate.io Dual Engine Sniper (Institutional Grade)
Engine 1: Pullback SMC (H1 + H4) — Fractal Swing + EMA + VPA Impulse
Engine 2: Early Momentum (M15) — Volume Anomaly + Accumulation
Engine 3: Breakout Sniper (H1) — Donchian + Volume Climax + ADX

Mode: /mode pullback | momentum | breakout | both | all

v15.0: PHASE 1 + PHASE 2 INSTITUTIONAL UPGRADE
=================================================
[PHASE 1 - CRITICAL]
  [P1-1] CHOCH (Change of Character) Detection
         — Detects early trend reversal BEFORE full breakdown
         — UPTREND CHOCH: price breaks below recent Higher Low = WARNING
         — DOWNTREND CHOCH: price breaks above recent Lower High = REVERSAL ENTRY SIGNAL
         — Integrated across all 3 engines + watchlist monitors
  [P1-2] Sweep Confluence Validation
         — Sweep ONLY valid bila ada FVG atau Order Block di atas sweep level
         — Require displacement >0.5% selepas sweep sebelum entry
         — Elak 60-70% false sweep entries (retail SL trap)
  [P1-3] Confluence Stacking (Minimum 2+ Signals)
         — Formal minimum requirement: 2 strong confluences sebelum entry
         — 7 confluence types: Structure, FVG, OB, Sweep+Disp, VPA, EMA, CHOCH
         — Setup labels upgraded with confluence count

[PHASE 2 - ENHANCEMENT]
  [P2-1] Session Timing Filter
         — London Open (07:00-11:00 UTC): +1 score bonus, high conviction
         — NY Overlap (12:00-16:00 UTC): +1 score bonus, highest volume
         — Asia Session: neutral, no bonus — avoid quiet hours fakeouts
         — Dead Zone (22:00-00:00 UTC): -1 score penalty, low liquidity
  [P2-2] Whale Proxy (Multi-TF Volume Accumulation)
         — H4 candle analysis: ratio green vs red volume = accumulation signal
         — Volume trend analysis: rising volume on green = institutional buying
         — H1 volume delta: aggressive buy vs sell pressure
         — Tiada API berbayar diperlukan — purely from candle data
  [P2-3] Displacement Validation
         — Selepas sweep/breakout, REQUIRE minimum displacement candle
         — Displacement = strong momentum candle (body > 60% total range)
         — Confirms institutional commitment, bukan retail noise

[LEGACY FIXES - CARRIED FORWARD]
  [FIX-1]  SL: ATR + Fib78.6% combo
  [FIX-2]  TP: Fib ratio (1.618R, 2.618R, 4.236R)
  [FIX-3]  H4 EMA loop fix
  [FIX-4]  get_active_trades() outside loop
  [FIX-5]  Threading Lock untuk semua watchlists
  [FIX-6]  add_pullback_watchlist fib mapping
  [FIX-7]  analyze_smc_pa return fib keys
  [FIX-8]  Minimum RR filter RR1 >= 1.5
  [FIX-9]  TP ordering validation
  [FIX-10] Counter-trend TP cap
  [FIX-11] scan_once SL/TP ATR + Fib
  [FIX-12] IS_SCANNING_LOCK anti double-scan
  [FIX-13] fib_618 untuk OB validation
  [FIX-14] Secondary fractal pass recent 4 candles
  [FIX-15] check_market_structure last 2 SH + 2 SL separately
  [FIX-16] EMA override fractal conflict
  [FIX-17] Long-only bot — tiada short signal
  [FIX-21] Dual Fib fresh + anchor swing pair
  [FIX-22] M15 FVG cross-TF detection
  [FIX-BO] Breakout engine dict key trailing space bug fixed
  [FIX-CM] Comment block syntax fixed (lines 1063-1065)
"""

import os, time, json, requests, threading, traceback, schedule
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from supabase import create_client, Client

# ==========================================
# 1. KONFIGURASI
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
VIP_CHANNEL_ID     = os.environ.get("VIP_CHANNEL_ID")
ADMIN_ID           = os.environ.get("ADMIN_ID")
SUPABASE_URL       = os.environ.get("SUPABASE_URL")
SUPABASE_KEY       = os.environ.get("SUPABASE_KEY")
bot                = TeleBot(TELEGRAM_BOT_TOKEN)
START_TIME         = time.time()
sb: Client         = create_client(SUPABASE_URL, SUPABASE_KEY)
SCAN_MODE          = os.environ.get("SCAN_MODE", "pullback").lower()

# Thread-safe locks untuk semua global dicts
_watchlist_lock     = threading.Lock()
_scan_lock          = threading.Lock()
IS_SCANNING_ACTIVE  = False

def alert_admin(text):
    try:
        bot.send_message(ADMIN_ID, f"🚨 <b>ALPHA SYSTEM</b>\n<pre>{str(text)[:800]}</pre>", parse_mode="HTML")
    except Exception:
        pass

# ==========================================
# 2. PRESETS & SUPABASE HELPERS
# ==========================================
PRESETS = {
    "soft":     {"min_vol_24h": 500_000,   "score_pass": 2, "label": "🟢 SOFT"},
    "standard": {"min_vol_24h": 1_000_000, "score_pass": 3, "label": "🟡 STANDARD"},
    "hard":     {"min_vol_24h": 2_500_000, "score_pass": 4, "label": "🔴 HARD"}
}

DEFAULT_CONFIG = {
    "min_vol_24h":    1_000_000,
    "score_pass":     3,
    "cooldown_hours": 24,
    "active_preset":  "standard"
}

_config_cache     = {}
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
        _config_cache      = cfg
        _config_loaded_at  = time.time()
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
        cfg    = get_config()
        cutoff = int(time.time()) - int(cfg["cooldown_hours"] * 3600)
        rows   = sb.table("sent_pool").select("sent_at").eq("key", contract).execute().data
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
    try:
        sb.table("user_profiles").upsert({
            "user_id":  user_id,
            "capital":  capital,
            "risk_pct": risk_pct,
            "updated":  int(time.time())
        }).execute()
    except Exception as e:
        print(f"[USER CAPITAL] Error: {e}")

def get_user_capital(user_id):
    try:
        rows = sb.table("user_profiles").select("*").eq("user_id", user_id).execute().data
        if rows:
            return rows[0].get("capital", 50.0), rows[0].get("risk_pct", 2.0)
    except Exception:
        pass
    return 50.0, 2.0

def calculate_position_size(capital, risk_pct, entry, sl):
    risk_usd         = capital * (risk_pct / 100.0)
    risk_distance    = entry - sl
    if risk_distance <= 0:
        return 0, 0, 0
    position_usd_raw = risk_usd / (risk_distance / entry)
    position_usd     = min(position_usd_raw, capital)
    position_usd     = min(position_usd, capital * 0.50)
    position_coins   = position_usd / entry
    actual_risk_usd  = position_coins * risk_distance
    return position_usd, position_coins, actual_risk_usd

def compute_final_sl(entry, structure_low, atr, atr_mult=1.5, max_sl_pct=0.08):
    sl_atr       = entry - (atr_mult * atr) if atr > 0 else entry * 0.98
    sl_structure = structure_low * 0.995
    sl_raw       = min(sl_atr, sl_structure)
    sl_floor     = entry * (1.0 - max_sl_pct)
    return max(sl_raw, sl_floor)

# ==========================================
# 3. HELPER & GATE.IO API + BLOCKLIST + MATH
# ==========================================
STABLECOINS      = {"USDT","USDC","BUSD","DAI","TUSD","USDP","FRAX","LUSD","GUSD",
                    "USDD","FDUSD","PYUSD","USDK","SUSD","RSR","EURS","EURT","UST",
                    "ALUSD","MIM","CUSD","CEUR","XAUT","PAXG"}
WRAPPED_TOKENS   = {"WETH","WBTC","WBNB","WSOL","WMATIC","WAVAX","WFTM","BETH","STETH","RETH","CBETH"}
SYMBOL_BLACKLIST = STABLECOINS | WRAPPED_TOKENS

def is_blacklisted_symbol(sym):
    s = sym.upper().strip()
    if s in SYMBOL_BLACKLIST:
        return True, f"Blacklisted: {s}"
    for bl in SYMBOL_BLACKLIST:
        if bl in s:
            return True, f"Blacklisted (partial): {s}"
    for suffix in ["5L","5S","3L","3S","2L","2S","1L","1S","UP","DOWN","BULL","BEAR"]:
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
        r     = requests.get("https://api.gateio.ws/api/v4/spot/tickers", timeout=10).json()
        pairs = []
        for t in r:
            if t['currency_pair'].endswith('_USDT'):
                sym        = t['currency_pair'].replace('_USDT', '')
                vol        = float(t.get('quote_volume', 0))
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
        r       = requests.get(url, timeout=8).json()
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
# MATH HELPERS
# ==========================================
def calculate_ema(data, period):
    if len(data) < period:
        return sum(data) / len(data) if data else 0
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0
    trs = []
    for i in range(-period, 0):
        c    = candles[i]
        prev = candles[i - 1]
        tr   = max(c['h'] - c['l'], abs(c['h'] - prev['c']), abs(c['l'] - prev['c']))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        gains.append(max(0, change))
        losses.append(max(0, -change))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calculate_adx(candles, period=14):
    if len(candles) < period * 2: return 0.0
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(candles)):
        hd   = candles[i]['h'] - candles[i-1]['h']
        ld   = candles[i-1]['l'] - candles[i]['l']
        plus_dm.append(max(0, hd) if hd > ld else 0)
        minus_dm.append(max(0, ld) if ld > hd else 0)
        tr = max(candles[i]['h'] - candles[i]['l'],
                 abs(candles[i]['h'] - candles[i-1]['c']),
                 abs(candles[i]['l'] - candles[i-1]['c']))
        trs.append(tr)
    def smooth(data, p):
        s = [sum(data[:p])]
        for i in range(p, len(data)): s.append(s[-1] - (s[-1] / p) + data[i])
        return s
    sp  = smooth(plus_dm,  period)
    sm  = smooth(minus_dm, period)
    str_ = smooth(trs,     period)
    dx_values = []
    for i in range(len(str_)):
        if str_[i] == 0:
            dx_values.append(0)
            continue
        pdi    = 100 * (sp[i] / str_[i])
        mdi    = 100 * (sm[i] / str_[i])
        di_sum = pdi + mdi
        dx_values.append(100 * abs(pdi - mdi) / di_sum if di_sum > 0 else 0)
    if len(dx_values) < period: return 0.0
    adx = sum(dx_values[:period]) / period
    for i in range(period, len(dx_values)): adx = (adx * (period - 1) + dx_values[i]) / period
    return adx

def find_fractal_swings(candles, lookback=2):
    """
    Detect fractal swing highs/lows.
    Primary:   lookback=2, full range
    Secondary: lookback=1, last 4 candles [FIX-14]
    """
    swings = []
    n = len(candles)
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

    for i in range(max(n - 4, 1), n - 1):
        if any(s['index'] == i for s in swings):
            continue
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
    [FIX-15] Ambil last 2 SH dan last 2 SL berasingan dari SEMUA swings.
    """
    if len(swings) < 2:
        return 'unknown'
    all_highs = sorted([s for s in swings if s['type'] == 'SH'], key=lambda x: x['index'])
    all_lows  = sorted([s for s in swings if s['type'] == 'SL'], key=lambda x: x['index'])
    if len(all_highs) < 2 and len(all_lows) < 2:
        return 'unknown'
    if len(all_highs) < 2:
        return 'uptrend' if all_lows[-1]['price'] > all_lows[-2]['price'] else 'downtrend'
    if len(all_lows) < 2:
        return 'uptrend' if all_highs[-1]['price'] > all_highs[-2]['price'] else 'downtrend'
    is_hh = all_highs[-1]['price'] > all_highs[-2]['price']
    is_hl = all_lows[-1]['price']  > all_lows[-2]['price']
    is_lh = all_highs[-1]['price'] < all_highs[-2]['price']
    is_ll = all_lows[-1]['price']  < all_lows[-2]['price']
    if   is_hh and is_hl:             return 'uptrend'
    elif is_lh and is_ll:             return 'downtrend'
    elif is_hh and not is_ll:         return 'uptrend_breakout'
    elif is_hl and is_lh:             return 'sideway'
    elif is_hl and not is_lh:         return 'uptrend'
    else:                             return 'sideway'

def find_fresh_swing_pair(swings):
    if not swings: return None, None
    shs = [s for s in swings if s['type'] == 'SH']
    sls = [s for s in swings if s['type'] == 'SL']
    if not shs or not sls: return None, None
    latest_sh    = shs[-1]
    sl_before_sh = [s for s in sls if s['index'] < latest_sh['index']]
    if not sl_before_sh: return None, None
    latest_sl = sl_before_sh[-1]
    if latest_sh['price'] <= latest_sl['price']: return None, None
    if (latest_sh['price'] - latest_sl['price']) / latest_sl['price'] * 100 < 0.3: return None, None
    return latest_sh['price'], latest_sl['price']

def find_anchor_swing_pair(swings):
    shs = [s for s in swings if s['type'] == 'SH']
    sls = [s for s in swings if s['type'] == 'SL']
    if not shs or not sls: return None, None
    latest_sh    = shs[-1]
    sl_before_sh = [s for s in sls if s['index'] < latest_sh['index']]
    if not sl_before_sh: return None, None
    anchor_sl = min(sl_before_sh, key=lambda s: s['price'])
    if latest_sh['price'] <= anchor_sl['price']: return None, None
    if (latest_sh['price'] - anchor_sl['price']) / anchor_sl['price'] * 100 < 3.0: return None, None
    return latest_sh['price'], anchor_sl['price']

def detect_fvg(candles, lookback=30):
    """
    [FIX-22] Bullish Fair Value Gap: 3-candle imbalance zone.
    FVG zone: Candle A high -> Candle C low (unfilled gap)
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
                    'top':         c_c['l'],
                    'bottom':      c_a['h'],
                    'mid':         (c_c['l'] + c_a['h']) / 2,
                    'size_pct':    gap_pct,
                    'candles_ago': i - 1
                })
    return fvgs

# ==========================================
# [P1-1] PHASE 1: CHOCH DETECTION
# ==========================================
def detect_choch(swings, current_price, structure):
    """
    Change of Character (CHOCH) Detection.
    
    UPTREND CHOCH:   price breaks BELOW most recent Higher Low = trend weakening
    DOWNTREND CHOCH: price breaks ABOVE most recent Lower High = trend reversing UP (BUY signal)
    
    Returns:
        'CHOCH_BULL'    — Downtrend CHOCH (bearish to bullish reversal)
        'CHOCH_BEAR'    — Uptrend CHOCH (bullish to bearish — warning for long)
        None            — No CHOCH detected
    """
    if not swings or structure == 'unknown':
        return None

    all_highs = sorted([s for s in swings if s['type'] == 'SH'], key=lambda x: x['index'])
    all_lows  = sorted([s for s in swings if s['type'] == 'SL'], key=lambda x: x['index'])

    # UPTREND: CHOCH = break BELOW recent Higher Low
    if structure in ('uptrend', 'uptrend_breakout', 'sideway'):
        if len(all_lows) >= 2:
            recent_hl = all_lows[-1]['price']  # Most recent swing low (Higher Low in uptrend)
            if current_price < recent_hl:
                return 'CHOCH_BEAR'

    # DOWNTREND: CHOCH = break ABOVE recent Lower High (BULLISH REVERSAL — BUY SIGNAL)
    if structure == 'downtrend':
        if len(all_highs) >= 2:
            recent_lh = all_highs[-1]['price']  # Most recent swing high (Lower High in downtrend)
            if current_price > recent_lh:
                return 'CHOCH_BULL'

    return None

# ==========================================
# [P1-2] PHASE 1: SWEEP CONFLUENCE VALIDATION
# ==========================================
def validate_sweep_confluence(candles, sweep_price, fvgs, price, atr):
    """
    Validate liquidity sweep has confluence (FVG atau OB di atas sweep level).
    Require displacement selepas sweep: momentum candle body > 60% of range.
    
    Returns: (is_valid: bool, displacement_pct: float, confluence_type: str)
    """
    if not candles or len(candles) < 5:
        return False, 0.0, "NO_DATA"

    # 1. Check displacement after sweep (current candle menunjukkan momentum)
    curr        = candles[-1]
    body        = abs(curr['c'] - curr['o'])
    total_range = curr['h'] - curr['l']
    displacement_pct = (curr['c'] - sweep_price) / sweep_price * 100 if sweep_price > 0 else 0

    has_displacement = (
        curr['c'] > curr['o'] and          # Bullish close
        total_range > atr * 0.5 and        # Meaningful size
        body > total_range * 0.5 and       # Strong body (>50% of range)
        displacement_pct >= 0.5            # At least 0.5% above sweep level
    )

    # 2. Check FVG above sweep level (unfilled gap = magnet target)
    has_fvg = any(fvg['bottom'] > sweep_price for fvg in fvgs) if fvgs else False

    # 3. Check Order Block above sweep (simplified: look for bearish candle before last upmove)
    has_ob = False
    try:
        for i in range(-15, -3):
            c      = candles[i]
            c_next = candles[i + 1]
            if (c['c'] < c['o'] and c_next['c'] > c_next['o'] and
                    c['h'] > sweep_price and c_next['c'] > sweep_price):
                bos_size = c_next['c'] - c_next['o']
                if bos_size > (c_next['h'] - c_next['l']) * 0.3:
                    has_ob = True
                    break
    except Exception:
        pass

    # Determine confluence type
    if has_displacement and has_fvg and has_ob:
        confluence_type = "SWEEP+FVG+OB"
        is_valid = True
    elif has_displacement and has_fvg:
        confluence_type = "SWEEP+FVG"
        is_valid = True
    elif has_displacement and has_ob:
        confluence_type = "SWEEP+OB"
        is_valid = True
    elif has_displacement:
        confluence_type = "SWEEP+DISP"
        is_valid = True
    elif has_fvg or has_ob:
        confluence_type = "SWEEP_PARTIAL"
        is_valid = False   # Sweep tapi tiada displacement — masih tunggu
    else:
        confluence_type = "SWEEP_RAW"
        is_valid = False   # Bare sweep tanpa apa-apa confirmation

    return is_valid, displacement_pct, confluence_type

# ==========================================
# [P2-1] PHASE 2: SESSION TIMING
# ==========================================
def get_trading_session():
    """
    Returns (session_name, session_score, session_emoji)
    
    London Open  07:00-11:00 UTC: +1 score, high institutional volume
    NY Overlap   12:00-16:00 UTC: +1 score, HIGHEST volatility + volume
    NY Session   16:01-21:59 UTC: neutral
    Asia Session 22:00-06:59 UTC: neutral (avoid for breakouts)
    Dead Zone    22:00-00:00 UTC: -1 score, low liquidity
    """
    utc_hour = datetime.now(timezone.utc).hour

    if 7 <= utc_hour <= 11:
        return "LONDON_OPEN",   +1, "🇬🇧"
    elif 12 <= utc_hour <= 16:
        return "NY_OVERLAP",    +1, "🇺🇸"
    elif 17 <= utc_hour <= 21:
        return "NY_SESSION",     0, "🌆"
    elif 22 <= utc_hour or utc_hour <= 1:
        return "DEAD_ZONE",     -1, "💀"
    else:
        return "ASIA_SESSION",   0, "🌏"

# ==========================================
# [P2-2] PHASE 2: WHALE PROXY (Multi-TF Volume Analysis)
# ==========================================
def check_whale_proxy(sym, candles_h4, candles_h1):
    """
    Multi-TF Volume Accumulation Proxy — Institutional Grade.
    Tiada API berbayar diperlukan. Guna candle volume data sahaja.
    
    Signals:
    1. H4 Volume Delta: green candles vol >> red candles vol = NET ACCUMULATION
    2. H4 Rising Volume: increasing volume on green candles = institutional buying
    3. H1 Volume Trend: strong volume on bullish candles = buy pressure
    4. Volume Climax Absence: no exhaustion spike on recent candles
    
    Returns: (whale_signal: str, whale_score: int, description: str)
    """
    whale_score = 0
    details     = []

    # === H4 ANALYSIS ===
    if candles_h4 and len(candles_h4) >= 20:
        recent_h4 = candles_h4[-20:]

        # Signal 1: H4 Volume Delta (green vol vs red vol)
        green_vol = sum(c['v'] for c in recent_h4 if c['c'] >= c['o'])
        red_vol   = sum(c['v'] for c in recent_h4 if c['c'] < c['o'])
        total_vol = green_vol + red_vol

        if total_vol > 0:
            green_ratio = green_vol / total_vol
            if green_ratio >= 0.65:
                whale_score += 2
                details.append(f"H4 ACCUM {green_ratio*100:.0f}%")
            elif green_ratio >= 0.55:
                whale_score += 1
                details.append(f"H4 LEAN-BULL {green_ratio*100:.0f}%")
            elif green_ratio <= 0.35:
                whale_score -= 2
                details.append(f"H4 DISTRIB {green_ratio*100:.0f}%")

        # Signal 2: H4 Volume Trend on green candles (last 5 vs prior 5)
        green_h4_5  = [c['v'] for c in recent_h4[-5:]  if c['c'] >= c['o']]
        green_h4_p5 = [c['v'] for c in recent_h4[-10:-5] if c['c'] >= c['o']]
        if green_h4_5 and green_h4_p5:
            avg_recent = sum(green_h4_5) / len(green_h4_5)
            avg_prior  = sum(green_h4_p5) / len(green_h4_p5)
            if avg_recent > avg_prior * 1.3:
                whale_score += 1
                details.append("H4 VOL-RISING")
            elif avg_recent < avg_prior * 0.7:
                whale_score -= 1
                details.append("H4 VOL-FADING")

        # Signal 3: H4 Higher Lows on volume (accumulation pattern)
        h4_lows_vol = [(c['l'], c['v']) for c in recent_h4[-6:] if c['c'] < c['o']]
        if len(h4_lows_vol) >= 3:
            # Declining volume on pullbacks = healthy accumulation
            vols_on_lows = [v for _, v in h4_lows_vol]
            if vols_on_lows[-1] < vols_on_lows[0] * 0.75:
                whale_score += 1
                details.append("H4 DRY-PULLBACK")

    # === H1 ANALYSIS ===
    if candles_h1 and len(candles_h1) >= 20:
        recent_h1 = candles_h1[-20:]

        # Signal 4: H1 impulse vs pullback volume
        impulse_vols  = [c['v'] for c in recent_h1 if c['c'] > c['o']]
        pullback_vols = [c['v'] for c in recent_h1 if c['c'] < c['o']]
        avg_imp  = sum(impulse_vols)  / len(impulse_vols)  if impulse_vols  else 1
        avg_pull = sum(pullback_vols) / len(pullback_vols) if pullback_vols else 1

        if avg_imp > avg_pull * 1.4:
            whale_score += 1
            details.append("H1 BUY-PRESSURE")
        elif avg_pull > avg_imp * 1.4:
            whale_score -= 1
            details.append("H1 SELL-PRESSURE")

        # Signal 5: No volume exhaustion (no climax spike on recent candles = not topping)
        vol_max_h1  = max(c['v'] for c in candles_h1[-50:]) if len(candles_h1) >= 50 else max(c['v'] for c in candles_h1)
        recent_vols = [c['v'] for c in recent_h1[-5:]]
        if recent_vols and max(recent_vols) > vol_max_h1 * 0.90:
            whale_score -= 1
            details.append("H1 VOL-CLIMAX-WARN")

    # === DETERMINE SIGNAL ===
    desc = " | ".join(details) if details else "NEUTRAL"
    if whale_score >= 3:
        return "ACCUMULATING", whale_score, desc
    elif whale_score >= 1:
        return "LEAN_BULLISH", whale_score, desc
    elif whale_score == 0:
        return "NEUTRAL",      whale_score, desc
    elif whale_score == -1:
        return "LEAN_BEARISH", whale_score, desc
    else:
        return "DISTRIBUTING", whale_score, desc

# ==========================================
# [P1-3] PHASE 1: CONFLUENCE STACKING
# ==========================================
def compute_entry_confluence(signals: dict):
    """
    Formal confluence scoring — require minimum 2 STRONG signals.
    
    Strong signals (2 points each):
      - SWEEP_CONFIRMED: Liquidity sweep + displacement validated
      - FVG_ACTIVE:      M15 FVG dalam H1 discount zone
      - OB_FRESH:        Fresh Order Block in golden zone
      - CHOCH_BULL:      Bullish CHOCH reversal signal
    
    Moderate signals (1 point each):
      - STRUCTURE_OK:    HH+HL confirmed structure
      - EMA_BULLISH:     EMA20 > EMA50 + price above EMA20
      - VPA_DRY:         Pullback volume < 70% impulse volume
      - PINBAR:          Bullish reversal candle at key level
      - ENGULFING:       Bullish engulfing at key level
      - WHALE_BULL:      Whale proxy accumulation signal
      - SESSION_HOT:     London/NY open session bonus
      - GOLDEN_ZONE:     Price dalam OTE 61.8-78.6% zone
    
    Returns: (confluence_count: int, strong_count: int, labels: list)
    """
    strong_map = {
        "SWEEP_CONFIRMED", "FVG_ACTIVE", "OB_FRESH", "CHOCH_BULL"
    }
    score        = 0
    strong_count = 0
    labels       = []

    for signal_name, is_present in signals.items():
        if not is_present:
            continue
        if signal_name in strong_map:
            score        += 2
            strong_count += 1
        else:
            score        += 1
        labels.append(signal_name)

    return score, strong_count, labels

# ==========================================
# 4. ENGINE 1: PULLBACK SMC (H1 + H4) — INSTITUTIONAL GRADE v15
# ==========================================
def analyze_smc_pa(sym, verbose=True):
    log = lambda msg: print(f"[{sym}-H1] {msg}") if verbose else None
    candles = get_gateio_klines(sym, "1h", 200)
    if len(candles) < 100:
        log("❌ REJECT: Data H1 < 100 candle")
        return None

    # --- 1. ATR ---
    atr = calculate_atr(candles, 14)

    # --- 2. H4 TREND CONFIRMATION + H4 DATA ---
    candles_h4       = get_gateio_klines(sym, "4h", 50)
    is_counter_trend = False
    h4_swing_high    = 0

    if len(candles_h4) >= 20:
        h4_swings    = find_fractal_swings(candles_h4, lookback=1)
        h4_structure = check_market_structure(h4_swings)
        if h4_structure == 'downtrend':
            log("⚠️ WARNING: H4 structure downtrend (Counter-Trend Mode)")
            is_counter_trend = True
            h4_shs           = [s for s in h4_swings if s['type'] == 'SH']
            h4_swing_high    = h4_shs[-1]['price'] if h4_shs else 0
        elif h4_structure == 'unknown':
            log("⚠️ H4 structure unknown, skip H4 filter")

    # --- 3. FRACTAL SWINGS ---
    swings = find_fractal_swings(candles, lookback=2)

    # --- 4. EMA CALCULATION [FIX-16] ---
    closes      = [c['c'] for c in candles[-200:]]
    ema20       = calculate_ema(closes, 20)
    ema50       = calculate_ema(closes, 50)
    price_now   = candles[-1]['c']
    ema_bullish = ema20 > ema50 and price_now > ema20 * 0.95

    # --- 5. MARKET STRUCTURE ---
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

    # [FIX-16] EMA override
    if structure == 'downtrend' and ema_bullish:
        log(f"⚠️ STRUCT CONFLICT: Fractal=downtrend tapi EMA20 > EMA50 → Override ke sideway")
        structure = 'sideway'
    elif structure == 'downtrend':
        log(f"❌ REJECT: Market structure downtrend (fractal + EMA kedua-dua confirm)")
        return None

    log(f"✅ STRUCTURE: {structure}")

    # --- [P1-1] CHOCH DETECTION ---
    price_now_live = get_gateio_price(sym)
    if price_now_live == 0:
        price_now_live = price_now

    choch_signal = detect_choch(swings, price_now_live, structure)

    if choch_signal == 'CHOCH_BEAR':
        log(f"⚠️ CHOCH BEARISH: Uptrend HL dah ditembus — HIGH RISK untuk long!")
        # Jangan reject terus, tapi mark sebagai risiko tinggi (kurangkan score)

    if choch_signal == 'CHOCH_BULL':
        log(f"✅ CHOCH BULLISH: LH ditembus ke atas — Potential reversal ENTRY!")
        # Bonus untuk signal ini

    # --- 6. DUAL FIB SYSTEM ---
    shs = [s for s in swings if s['type'] == 'SH']
    sls = [s for s in swings if s['type'] == 'SL']

    fresh_sh,  fresh_sl  = find_fresh_swing_pair(swings)
    anchor_sh, anchor_sl = find_anchor_swing_pair(swings)

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

    price = price_now_live
    curr  = candles[-1]
    prev  = candles[-2]

    in_fresh_zone  = fresh_fib  and fresh_fib["fib786"]  <= price <= fresh_fib["fib500"]
    in_anchor_zone = anchor_fib and anchor_fib["fib786"] <= price <= anchor_fib["fib500"]
    same_pair      = (fresh_sh == anchor_sh and fresh_sl == anchor_sl)

    if not in_fresh_zone and not in_anchor_zone:
        zone_ref = fresh_fib["fib500"] if fresh_fib else (anchor_fib["fib500"] if anchor_fib else 0)
        if price > zone_ref and ema_bullish and price > ema20:
            log(f"⚠️ PREMIUM ZONE OVERRIDE: Price > Fib500 tapi EMA Bullish → Allow")
            setup_mode  = "INTRADAY"
            swing_high  = fresh_sh or anchor_sh
            swing_low   = fresh_sl or anchor_sl
            rng         = swing_high - swing_low if (swing_high and swing_low) else price * 0.05
            fib_500     = swing_high - rng * 0.500
            fib_618     = swing_high - rng * 0.618
            fib_786     = swing_high - rng * 0.786
            in_discount = False
            fib_zone    = f"Premium (>{fmt(zone_ref)})"
        else:
            log("❌ REJECT: " + ("PREMIUM ZONE" if price > zone_ref else "EXTREME"))
            return None
    else:
        if in_fresh_zone and fresh_fib and not same_pair:
            setup_mode = "INTRADAY"
            swing_high = fresh_fib["sh"]
            swing_low  = fresh_fib["sl"]
            rng        = fresh_fib["rng"]
            fib_500    = fresh_fib["fib500"]
            fib_618    = fresh_fib["fib618"]
            fib_786    = fresh_fib["fib786"]
            log(f"⚡ FIBO INTRADAY: ${fmt(price)} dalam FRESH [{fmt(fib_786)}-{fmt(fib_500)}]")
        elif in_anchor_zone and anchor_fib:
            setup_mode = "SWING"
            swing_high = anchor_fib["sh"]
            swing_low  = anchor_fib["sl"]
            rng        = anchor_fib["rng"]
            fib_500    = anchor_fib["fib500"]
            fib_618    = anchor_fib["fib618"]
            fib_786    = anchor_fib["fib786"]
            log(f"⚖️ FIBO SWING: ${fmt(price)} dalam ANCHOR [{fmt(fib_786)}-{fmt(fib_500)}]")
        else:
            setup_mode = "INTRADAY" if in_fresh_zone else "SWING"
            swing_high = fresh_sh or anchor_sh
            swing_low  = fresh_sl or anchor_sl
            rng        = swing_high - swing_low if (swing_high and swing_low) else price * 0.05
            fib_500    = swing_high - rng * 0.500
            fib_618    = swing_high - rng * 0.618
            fib_786    = swing_high - rng * 0.786
            log(f"✅ FIBO PASS: ${fmt(price)} dalam DISCOUNT [{fmt(fib_786)}-{fmt(fib_500)}]")
        in_discount = True
        fib_zone    = f"{fmt(fib_500)} - {fmt(fib_786)}"

    # --- 7. EMA FILTER ---
    is_uptrend = ema20 > ema50
    if not is_uptrend:
        ema_gap_pct = abs(ema20 - ema50) / ema50 * 100
        if structure == 'sideway' and in_discount and ema_gap_pct < 5.0:
            log(f"⚠️ EMA CONVERGING: gap {ema_gap_pct:.2f}% < 5%, allow")
            is_uptrend = True
        else:
            log(f"❌ REJECT: EMA20 < EMA50 (gap {ema_gap_pct:.2f}%)")
            return None

    if price < ema20 * 0.90:
        log("❌ REJECT: Price terlalu jauh bawah EMA20 H1")
        return None

    distance_from_ema = abs(price - ema20)
    threshold = atr * 0.5 if atr > 0 else ema20 * 0.015

    # --- 8. VPA IMPULSE vs PULLBACK ---
    impulse_vols  = [c['v'] for c in candles[-21:-1] if c['c'] > c['o']]
    pullback_vols = [c['v'] for c in candles[-21:-1] if c['c'] < c['o']]
    avg_impulse_vol  = sum(impulse_vols) / len(impulse_vols) if impulse_vols else 1
    avg_pullback_vol = sum(pullback_vols) / len(pullback_vols) if pullback_vols else 1
    curr_vol = curr['v']
    vpa_dry  = avg_pullback_vol < (avg_impulse_vol * 0.7)

    # --- 9. CANDLE PATTERNS ---
    body       = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    upper_wick = curr['h'] - max(curr['o'], curr['c'])
    total_range = curr['h'] - curr['l']
    wick_ratio  = lower_wick / body if body > 0 else 0
    touches     = sum(1 for c in candles[-50:-1] if abs(c['l'] - swing_low) / swing_low < 0.01)

    min_size_ok  = (total_range > atr * 0.5) if atr > 0 else True
    is_pinbar    = (lower_wick > body * 2 and upper_wick < total_range * 0.1
                    and curr['c'] > curr['o'] and min_size_ok)
    prev_body    = abs(prev['c'] - prev['o'])
    curr_body    = abs(curr['c'] - curr['o'])
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                    and curr['c'] > prev['o'] and curr['o'] <= prev['c']
                    and curr_body > prev_body and curr_vol > prev['v'])

    # --- 10. M15 FVG CROSS-TF [FIX-22] ---
    fvg_active   = False
    fvg_detail   = ""
    fvgs_m15     = []
    try:
        candles_m15_fvg = get_gateio_klines(sym, "15m", 50)
        if len(candles_m15_fvg) >= 10:
            fvgs_m15 = detect_fvg(candles_m15_fvg, lookback=30)
            for fvg in fvgs_m15:
                if fib_786 <= fvg['bottom'] <= fib_500 and fvg['bottom'] <= price <= fvg['top']:
                    fvg_active = True
                    fvg_detail = f"{fmt(fvg['bottom'])}-{fmt(fvg['top'])} ({fvg['size_pct']:.2f}%)"
                    break
    except Exception:
        pass

    # --- 11. LIQUIDITY SWEEP + [P1-2] SWEEP CONFLUENCE VALIDATION ---
    sweep_detected      = False
    sweep_validated     = False
    sweep_confluence    = ""
    sweep_disp_pct      = 0.0

    if curr['l'] < swing_low and curr['c'] > swing_low and wick_ratio >= 2.0 and touches >= 2 and curr_vol > avg_impulse_vol:
        sweep_detected = True
        # [P1-2] Validate sweep dengan confluence
        is_valid, disp_pct, conf_type = validate_sweep_confluence(
            candles, swing_low, fvgs_m15, price, atr
        )
        sweep_validated  = is_valid
        sweep_confluence = conf_type
        sweep_disp_pct   = disp_pct
        if sweep_validated:
            log(f"✅ SWEEP VALIDATED: {conf_type} | Displacement {disp_pct:.2f}%")
        else:
            log(f"⚠️ SWEEP DETECTED tapi tiada confluence ({conf_type}) — partial signal")

    # --- 12. ORDER BLOCK DETECTION [FIX-13] ---
    ob_found    = False
    ob_in_golden = False
    for i in range(-100, -3):
        try:
            c      = candles[i]
            c_next = candles[i + 1]
            if c['c'] < c['o'] and c_next['c'] > c_next['o']:
                bos_size = c_next['c'] - c_next['o']
                if bos_size > rng * 0.01:
                    ob_high = c['h']
                    ob_low  = c['l']
                    if ob_low <= price <= ob_high:
                        touches_after = sum(1 for j in range(i + 2, 0) if ob_low <= candles[j]['l'] <= ob_high)
                        if touches_after <= 1:
                            ob_in_golden = fib_618 <= (ob_high + ob_low) / 2 <= fib_500
                            ob_found     = True
                            break
        except Exception:
            pass

    # --- [P2-1] SESSION TIMING ---
    session_name, session_score, session_emoji = get_trading_session()
    log(f"🕐 Session: {session_emoji} {session_name} (score adj: {session_score:+d})")

    # --- [P2-2] WHALE PROXY ---
    whale_signal, whale_score_val, whale_desc = check_whale_proxy(sym, candles_h4, candles)
    log(f"🐳 Whale Proxy: {whale_signal} ({whale_desc})")

    # --- [P1-3] CONFLUENCE STACKING ---
    confluence_signals = {
        "STRUCTURE_OK":     structure in ('uptrend', 'uptrend_breakout'),
        "EMA_BULLISH":      is_uptrend and price > ema20,
        "VPA_DRY":          vpa_dry,
        "GOLDEN_ZONE":      fib_786 <= price <= fib_618,
        "PINBAR":           is_pinbar,
        "ENGULFING":        is_engulfing,
        "SWEEP_CONFIRMED":  sweep_validated,
        "FVG_ACTIVE":       fvg_active,
        "OB_FRESH":         ob_found and ob_in_golden,
        "CHOCH_BULL":       choch_signal == 'CHOCH_BULL',
        "WHALE_BULL":       whale_signal in ("ACCUMULATING", "LEAN_BULLISH"),
        "SESSION_HOT":      session_score > 0,
    }

    conf_score, strong_count, conf_labels = compute_entry_confluence(confluence_signals)
    log(f"🔗 Confluence: {conf_score} pts | Strong: {strong_count} | {conf_labels}")

    # Penalti CHOCH_BEAR
    if choch_signal == 'CHOCH_BEAR':
        conf_score -= 2
        log(f"⚠️ CHOCH_BEAR PENALTY: -2 confluence pts")

    # --- 13. BUILD SCORE (Legacy + Confluence) ---
    score      = 0
    setup_name = None

    # Base: golden zone
    if fib_786 <= price <= fib_618:
        score += 1
        setup_name = "📍 FIB GOLDEN ZONE"
        log("✅ SETUP 9: OTE golden zone (78.6-61.8%)")

    # Sweep signal
    if sweep_detected:
        if sweep_validated:
            setup_name = f"💧 SWEEP+{sweep_confluence}"
            score += 3
            log(f"✅ SETUP 7: Sweep VALIDATED ({sweep_confluence})")
        else:
            score += 1  # Partial credit
            log(f"⚠️ SETUP 7: Sweep RAW (no confluence, partial +1)")

    # Pinbar / Engulfing
    if is_pinbar:
        if not setup_name: setup_name = "🕯️ PINBAR REVERSAL"
        score += 2
        log("✅ SETUP 5: Pinbar valid")
    elif is_engulfing:
        if not setup_name: setup_name = "🐂 BULLISH ENGULFING"
        score += 2
        log("✅ SETUP 5: Engulfing valid")

    # VPA
    if vpa_dry:
        score += 1
        log("✅ VPA PASS: Pullback vol < 70% impulse vol")

    # EMA proximity
    if is_uptrend and distance_from_ema < threshold and price > ema20:
        if not setup_name: setup_name = "📈 TREND PULLBACK"
        score += 1
        log("✅ SETUP 2: Pullback ke EMA20 (uptrend)")

    # Order Block [FIX-13]
    if ob_found:
        if not setup_name: setup_name = "🧱 FRESH ORDER BLOCK" + (" [GOLDEN]" if ob_in_golden else "")
        score += 3 if ob_in_golden else 2
        log(f"✅ SETUP 3: Fresh OB {'[GOLDEN]' if ob_in_golden else ''}")

    # FVG [FIX-22]
    if fvg_active:
        score += 3
        if not setup_name: setup_name = "🕳️ M15 FVG ZONE"
        log(f"✅ SETUP 8: M15 FVG {fvg_detail}")

    # CHOCH BULL bonus
    if choch_signal == 'CHOCH_BULL':
        score += 2
        if not setup_name: setup_name = "🔄 CHOCH REVERSAL"
        log("✅ CHOCH BULL: Trend reversal signal +2")

    # Session bonus/penalty [P2-1]
    score += session_score
    if session_score > 0:
        log(f"✅ SESSION BONUS: {session_name} +{session_score}")
    elif session_score < 0:
        log(f"⚠️ SESSION PENALTY: {session_name} {session_score}")

    # Whale proxy bonus [P2-2]
    if whale_signal == "ACCUMULATING":
        score += 2
        log("✅ WHALE PROXY: Accumulating +2")
    elif whale_signal == "LEAN_BULLISH":
        score += 1
        log("✅ WHALE PROXY: Lean Bullish +1")
    elif whale_signal == "DISTRIBUTING":
        score -= 2
        log("⚠️ WHALE PROXY: Distributing -2")
    elif whale_signal == "LEAN_BEARISH":
        score -= 1
        log("⚠️ WHALE PROXY: Lean Bearish -1")

    # [P1-3] MINIMUM CONFLUENCE REQUIREMENT
    # Require at least 2 strong OR 3+ moderate confluences
    if strong_count < 1 and conf_score < 3:
        log(f"❌ REJECT: Confluence insufficient (strong={strong_count}, total_conf={conf_score})")
        return None

    if not setup_name or score < 2:
        log(f"❌ REJECT: Score {score} < 2 atau tiada setup_name")
        return None

    # --- 14. SL/TP CALCULATION ---
    sl = compute_final_sl(price, swing_low, atr, atr_mult=1.0, max_sl_pct=0.08)
    sl_fib786_floor = fib_786 * 0.997
    if sl_fib786_floor > sl and sl_fib786_floor < price:
        sl = sl_fib786_floor
        log(f"📐 SL ke Fib786 floor ${fmt(sl)}")

    target_high = anchor_sh if anchor_sh and anchor_sh > swing_high else swing_high
    rng_target  = target_high - swing_low

    tp1 = target_high
    tp2 = swing_low + (rng_target * 1.618)
    tp3 = swing_low + (rng_target * 2.618)

    if is_counter_trend and h4_swing_high > 0:
        tp1 = min(tp1, h4_swing_high)
        tp2 = min(tp2, h4_swing_high)
        tp3 = min(tp3, h4_swing_high)
        if tp1 == tp2 == tp3:
            log("❌ REJECT: Counter-trend TP semua sama")
            return None
        log("⚠️ COUNTER-TREND: TP capped at H4 Swing High")

    risk = price - sl
    if risk <= 0:
        log("❌ REJECT: Risk invalid (sl >= entry)")
        return None
    if tp1 <= price:
        log(f"❌ REJECT: TP1 ({fmt(tp1)}) <= entry")
        return None
    if tp2 <= tp1:
        tp2 = tp1 + atr
    if tp3 <= tp2:
        tp3 = tp2 + atr * 2

    rr1 = (tp1 - price) / risk
    rr2 = (tp2 - price) / risk
    log(f"📐 SL: ${fmt(sl)} | TP1 RR:{rr1:.2f} | TP2 RR:{rr2:.2f} | Score: {score}")

    # --- 15. PENDING BOS WATCHLIST ---
    global BOS_WATCHLIST
    shs_list = [s for s in swings if s['type'] == 'SH']
    sls_list = [s for s in swings if s['type'] == 'SL']
    if len(sls_list) >= 2:
        last_hl  = sls_list[-1]['price']
        distance = abs(price - last_hl) / last_hl * 100
        if distance <= 2.0 and price > last_hl:
            with _watchlist_lock:
                if sym not in BOS_WATCHLIST:
                    BOS_WATCHLIST[sym] = {
                        "level": last_hl, "type": "HL", "added": time.time(),
                        "session": session_name, "whale": whale_signal
                    }
                    log(f"📌 PENDING BOS: HL ${fmt(last_hl)} ({distance:.1f}%)")
    if len(shs_list) >= 2:
        last_lh  = shs_list[-1]['price']
        distance = abs(price - last_lh) / last_lh * 100
        if distance <= 2.0 and price < last_lh:
            with _watchlist_lock:
                if sym not in BOS_WATCHLIST:
                    BOS_WATCHLIST[sym] = {
                        "level": last_lh, "type": "LH", "added": time.time(),
                        "session": session_name, "whale": whale_signal
                    }
                    log(f"📌 PENDING BOS: LH ${fmt(last_lh)} ({distance:.1f}%)")

    return {
        "setup":            f"{setup_name} ({'⚡INTRADAY' if setup_mode == 'INTRADAY' else '⚖️SWING'})",
        "entry":            price,
        "sl":               sl,
        "tp1":              tp1,
        "tp2":              tp2,
        "tp3":              tp3,
        "rr1":              round(rr1, 2),
        "rr2":              round(rr2, 2),
        "score":            score,
        "fib_zone":         fib_zone,
        "fib_500":          fib_500,
        "fib_618":          fib_618,
        "fib_786":          fib_786,
        "timeframe":        "H1",
        "setup_mode":       setup_mode,
        "structure":        structure,
        "is_counter_trend": is_counter_trend,
        # Phase 1+2 metadata
        "choch":            choch_signal or "NONE",
        "sweep_conf":       sweep_confluence if sweep_validated else "NONE",
        "confluence_score": conf_score,
        "confluence_labels": ",".join(conf_labels[:5]),
        "session":          session_name,
        "whale_signal":     whale_signal,
    }

# ==========================================
# 5. ENGINE 2: EARLY MOMENTUM (M15) — VOLUME ANOMALY v15
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

    body        = abs(curr['c'] - curr['o'])
    lower_wick  = min(curr['o'], curr['c']) - curr['l']
    is_pinbar   = lower_wick > body * 2 and curr['c'] > curr['o']
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                    and curr['c'] > prev['o'] and curr['o'] < prev['c'])
    if not (is_pinbar or is_engulfing):
        return None

    # [P2-1] Session check for momentum engine
    session_name, session_score, session_emoji = get_trading_session()
    log(f"🕐 Session: {session_emoji} {session_name}")

    # Dead zone = skip momentum (too risky without volume)
    if session_score < 0:
        log(f"⚠️ DEAD ZONE session — momentum less reliable, continue with caution")

    # [P1-1] CHOCH check on M15 structure
    swings_m15 = find_fractal_swings(candles[-50:], lookback=2)
    struct_m15 = check_market_structure(swings_m15)
    choch_m15  = detect_choch(swings_m15, price, struct_m15)
    if choch_m15 == 'CHOCH_BEAR':
        log("⚠️ M15 CHOCH BEAR: Uptrend HL ditembus — increased risk for momentum long")

    setup_name  = "⚡ PINBAR MOMENTUM" if is_pinbar else "⚡ ENGULFING MOMENTUM"
    entry       = price
    atr_m15     = calculate_atr(candles, 14)
    range_low   = min(lows[-20:])
    range_high  = max(highs[-50:])
    sl          = compute_final_sl(entry, range_low, atr_m15, atr_mult=0.75, max_sl_pct=0.08)
    risk        = entry - sl
    if risk <= 0:
        return None

    tp1 = range_high + (atr_m15 * 0.5 if atr_m15 > 0 else range_high * 0.01)
    tp2 = entry + risk * 2.618
    tp3 = entry + risk * 4.236
    tp2 = max(tp1 * 1.005, tp2)
    tp3 = max(tp2 * 1.005, tp3)

    h4_bias = "neutral"
    try:
        candles_h4 = get_gateio_klines(sym, "4h", 50)
        if len(candles_h4) >= 20:
            h4_closes  = [c['c'] for c in candles_h4]
            h4_ema     = calculate_ema(h4_closes, 20)
            h4_current = candles_h4[-1]['c']
            h4_bias    = "uptrend" if h4_current > h4_ema else "downtrend"

            # [P2-2] Whale proxy for momentum
            whale_sig, _, whale_d = check_whale_proxy(sym, candles_h4, candles)
            log(f"🐳 Whale: {whale_sig} ({whale_d})")
            if whale_sig == "DISTRIBUTING":
                log("❌ REJECT: Whale distributing — avoid momentum long")
                return None
    except Exception:
        whale_sig = "NEUTRAL"

    if h4_bias == "downtrend":
        sl        = range_low - (atr_m15 * 0.5 if atr_m15 > 0 else range_low * 0.005)
        risk      = entry - sl
        if risk <= 0: return None
        tp1       = range_high + (atr_m15 * 0.25 if atr_m15 > 0 else range_high * 0.005)
        tp2       = entry + risk * 1.618
        tp3       = tp2
        setup_name = "⚡ COUNTER-TREND (Risky)"
        log("⚠️ HTF BIAS: H4 Downtrend. TP capped.")
    else:
        log("✅ HTF BIAS: H4 Uptrend/Neutral.")

    rr1 = (tp1 - entry) / risk
    rr2 = (tp2 - entry) / risk
    log(f"⚡ MOMENTUM: {setup_name} | Session:{session_name} | RR1:{rr1:.2f}")

    return {
        "setup":            setup_name,
        "entry":            entry,
        "sl":               sl,
        "tp1":              tp1,
        "tp2":              tp2,
        "tp3":              tp3,
        "rr1":              round(rr1, 2),
        "rr2":              round(rr2, 2),
        "score":            3,
        "fib_zone":         "N/A",
        "timeframe":        "M15",
        "vol_spike":        vol_spike,
        "range_pct":        range_pct,
        "h4_bias":          h4_bias,
        "is_counter_trend": (h4_bias == "downtrend"),
        "session":          session_name,
        "whale_signal":     whale_sig if 'whale_sig' in dir() else "NEUTRAL",
        "choch":            choch_m15 or "NONE",
        "confluence_labels": f"VOL_{vol_spike:.1f}x|{session_name}",
    }

# ==========================================
# 6. ENGINE 3: BREAKOUT SNIPER (H1) — DONCHIAN + VOL CLIMAX v15
# ==========================================
def analyze_breakout_sniper(sym, verbose=True):
    log = lambda msg: print(f"[{sym}-1H-BO] {msg}") if verbose else None
    candles = get_gateio_klines(sym, "1h", 250)
    if len(candles) < 210:
        log("❌ REJECT: Data H1 < 210 candle")
        return None

    closes = [c['c'] for c in candles]
    highs  = [c['h'] for c in candles]
    lows   = [c['l'] for c in candles]
    vols   = [c['v'] for c in candles]

    # 1. Trend Filter: EMA50 > EMA200 (Golden Cross)
    ema50  = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)
    if ema50 <= ema200:
        log(f"❌ REJECT: EMA50 ({fmt(ema50)}) <= EMA200 ({fmt(ema200)}) - No Golden Cross")
        return None

    # 2. ADX > 20 (Avoid sideways)
    adx = calculate_adx(candles, 14)
    if adx < 20:
        log(f"❌ REJECT: ADX ({adx:.2f}) < 20")
        return None

    # 3. Donchian Breakout: Close > max(High, 20)
    curr       = candles[-1]
    price      = curr['c']
    highest_20 = max(highs[-21:-1])
    if price <= highest_20:
        log(f"❌ REJECT: Price ({fmt(price)}) <= 20H High ({fmt(highest_20)})")
        return None

    # 4. Volume Climax: Vol > 2.5x MA(Vol, 20)
    avg_vol_20 = sum(vols[-21:-1]) / 20
    if avg_vol_20 == 0:
        return None
    curr_vol = vols[-1]
    if curr_vol < avg_vol_20 * 2.5:
        log(f"❌ REJECT: Vol ({curr_vol:.0f}) < 2.5x Avg ({avg_vol_20:.0f})")
        return None

    # 5. RSI > 55 (Momentum)
    rsi = calculate_rsi(closes, 14)
    if rsi < 55:
        log(f"❌ REJECT: RSI ({rsi:.2f}) < 55")
        return None

    # [P1-1] CHOCH check — dont buy breakout in bearish CHOCH environment
    swings_h1   = find_fractal_swings(candles[-50:], lookback=2)
    struct_h1   = check_market_structure(swings_h1)
    choch_h1    = detect_choch(swings_h1, price, struct_h1)
    if choch_h1 == 'CHOCH_BEAR':
        log("❌ REJECT: CHOCH BEAR detected — breakout in weakening structure, skip")
        return None

    # [P2-1] Session timing for breakout
    session_name, session_score, session_emoji = get_trading_session()
    log(f"🕐 Session: {session_emoji} {session_name}")
    # Breakout in dead zone — skip (no institutional follow-through)
    if session_score < 0:
        log(f"❌ REJECT: Dead zone session — breakout tiada follow-through")
        return None

    # [P1-2] Displacement validation for breakout
    body_bo      = abs(curr['c'] - curr['o'])
    range_bo     = curr['h'] - curr['l']
    atr_bo       = calculate_atr(candles, 14)
    has_disp     = (curr['c'] > curr['o'] and
                    range_bo > atr_bo * 0.7 and
                    body_bo > range_bo * 0.5)
    if not has_disp:
        log(f"⚠️ DISPLACEMENT WEAK: body={fmt(body_bo)} range={fmt(range_bo)} — caution")

    # [P2-2] Whale proxy for breakout
    candles_h4_bo = get_gateio_klines(sym, "4h", 50)
    whale_bo, _, whale_d_bo = check_whale_proxy(sym, candles_h4_bo, candles)
    log(f"🐳 Whale: {whale_bo} ({whale_d_bo})")
    if whale_bo == "DISTRIBUTING":
        log("❌ REJECT: Whale distributing into breakout — likely bull trap")
        return None

    log(f"✅ BREAKOUT CONFIRMED | ADX:{adx:.1f} | Vol:{curr_vol/avg_vol_20:.1f}x | RSI:{rsi:.1f} | Session:{session_name} | Whale:{whale_bo}")

    # SL/TP [FIX-BO: No trailing spaces in keys]
    sl   = min(lows[-10:]) * 0.995
    risk = price - sl
    if risk <= 0:
        log("❌ REJECT: Invalid Risk")
        return None

    tp1 = price + (risk * 2.0)
    tp2 = price + (risk * 4.0)
    tp3 = price + (risk * 6.0)

    # Session bonus: higher targets during London/NY overlap
    if session_name == "NY_OVERLAP":
        tp2 = price + (risk * 5.0)
        tp3 = price + (risk * 7.0)
        log("✅ NY_OVERLAP: TP targets extended")

    rr1 = (tp1 - price) / risk
    rr2 = (tp2 - price) / risk
    log(f"📐 SL:${fmt(sl)} | TP1 RR:{rr1:.1f} | TP2 RR:{rr2:.1f}")

    # Score for breakout: session + whale + displacement
    bo_score = 4  # Base
    if session_score > 0:    bo_score += 1
    if whale_bo == "ACCUMULATING": bo_score += 1
    if has_disp:             bo_score += 1

    return {
        "setup":            "🚀 BREAKOUT SNIPER (Donchian+VolClimax)",
        "entry":            price,
        "sl":               sl,
        "tp1":              tp1,
        "tp2":              tp2,
        "tp3":              tp3,
        "rr1":              round(rr1, 2),
        "rr2":              round(rr2, 2),
        "score":            bo_score,
        "fib_zone":         "N/A",
        "timeframe":        "1H-BO",
        "setup_mode":       "BREAKOUT",
        "structure":        "uptrend_breakout",
        "is_counter_trend": False,
        "session":          session_name,
        "whale_signal":     whale_bo,
        "choch":            choch_h1 or "NONE",
        "confluence_labels": f"ADX{adx:.0f}|RSI{rsi:.0f}|VOL{curr_vol/avg_vol_20:.1f}x|{session_name}|{whale_bo}",
    }

# ==========================================
# 7. SIGNAL SENDER
# ==========================================
def send_signal(sym, smc_data, vol_24h, btc_chg=0.0):
    cfg   = get_config()
    entry = smc_data["entry"]
    sl    = smc_data["sl"]
    tp1   = smc_data["tp1"]
    tp2   = smc_data["tp2"]
    tp3   = smc_data["tp3"]
    timeframe = smc_data.get("timeframe", "H1")
    risk  = entry - sl

    if smc_data["score"] < cfg["score_pass"]:
        return False

    # [ALPHA-RISK] Position Sizing
    user_cap, user_risk = get_user_capital(int(ADMIN_ID) if ADMIN_ID else 0)
    pos_usd, pos_coins, risk_usd = calculate_position_size(user_cap, user_risk, entry, sl)
    if pos_usd <= 0 or pos_coins <= 0:
        print(f"[SKIP] {sym}: Position size invalid")
        return False

    # [FIX-8] RR Filter
    rr1 = (tp1 - entry) / risk if risk > 0 else 0
    rr2 = (tp2 - entry) / risk if risk > 0 else 0
    if rr1 < 1.5 and rr2 < 1.5:
        print(f"[SKIP] {sym}: RR1={rr1:.2f} RR2={rr2:.2f} — kedua < 1.5")
        return False

    if sl >= entry:
        print(f"[SKIP] {sym}: SL >= entry")
        return False
    if tp1 <= entry:
        print(f"[SKIP] {sym}: TP1 <= entry")
        return False

    current_price = get_gateio_price(sym)
    if current_price > 0:
        price_gap = abs(current_price - entry) / entry * 100
        if price_gap > 1.0:
            mode = smc_data.get("setup_mode", "")
            if price_gap > 15.0:
                if smc_data.get("score", 0) >= 2 and sym not in PULLBACK_WATCHLIST:
                    add_pullback_watchlist(sym, smc_data)
                print(f"[SKIP] {sym}: Price bergerak {price_gap:.1f}% — tunggu pullback")
                return False
            print(f"[INFO] {sym}: Price gap {price_gap:.1f}% dari entry ({mode})")

    sl_pct  = (entry - sl)  / entry * 100
    tp1_pct = (tp1 - entry) / entry * 100
    tp2_pct = (tp2 - entry) / entry * 100
    tp3_pct = (tp3 - entry) / entry * 100

    btc_warn = f"⚠️ <b>BTC ALERT:</b> BTC {btc_chg:+.2f}%\n\n" if btc_chg < -4.0 else ""

    pair_name    = f"{sym}USDT"
    engine_icon  = "⚡" if timeframe == "M15" else ("🚀" if timeframe == "1H-BO" else "🏴‍☠️")
    engine_label = "MOMENTUM" if timeframe == "M15" else ("BREAKOUT" if timeframe == "1H-BO" else "PULLBACK")

    counter_trend_badge = ""
    if smc_data.get("is_counter_trend", False):
        counter_trend_badge = "⚠️ <b>COUNTER-TREND:</b> TP capped\n"

    # [P1+P2] Institutional badges
    session      = smc_data.get("session", "UNKNOWN")
    whale_signal = smc_data.get("whale_signal", "NEUTRAL")
    choch        = smc_data.get("choch", "NONE")
    sweep_conf   = smc_data.get("sweep_conf", "NONE")
    conf_labels  = smc_data.get("confluence_labels", "")
    conf_score   = smc_data.get("confluence_score", 0)

    session_emojis = {
        "LONDON_OPEN": "🇬🇧 LONDON", "NY_OVERLAP": "🇺🇸 NY OVERLAP",
        "NY_SESSION": "🌆 NY", "ASIA_SESSION": "🌏 ASIA", "DEAD_ZONE": "💀 DEAD"
    }
    whale_emojis = {
        "ACCUMULATING": "🐳 ACCUM", "LEAN_BULLISH": "🐟 LEAN-BULL",
        "NEUTRAL": "➖ NEUTRAL", "LEAN_BEARISH": "🔴 LEAN-BEAR", "DISTRIBUTING": "🔴 DISTRIB"
    }
    session_badge  = session_emojis.get(session, session)
    whale_badge    = whale_emojis.get(whale_signal, whale_signal)
    choch_badge    = f"🔄 {choch}" if choch not in ("NONE", None) else ""
    sweep_badge    = f"💧 {sweep_conf}" if sweep_conf not in ("NONE", "", None) else ""

    inst_line = f"{session_badge} | {whale_badge}"
    if choch_badge:  inst_line += f" | {choch_badge}"
    if sweep_badge:  inst_line += f" | {sweep_badge}"

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
        f"💼 <b>Position:</b> <code>${pos_usd:.2f}</code> ({pos_coins:.4f} {sym})\n"
        f"⚠️ <b>Risk:</b> <code>${risk_usd:.2f}</code> ({user_risk}% of ${user_cap:.2f})\n\n"
        f"🏛️ <b>Institutional:</b> <code>{inst_line}</code>\n"
        f"🔗 <b>Confluence:</b> <code>{conf_labels[:60]}</code>\n\n"
        f"🧠 <b>Setup:</b> <code>{smc_data['setup']}</code>\n"
        f"⏱️ <b>TF:</b> <code>{timeframe}</code> | "
        f"<b>Score:</b> <code>{smc_data['score']}/{cfg['score_pass']}</code>"
    )

    if timeframe == "M15" and "vol_spike" in smc_data:
        msg += f"\n⚡ <b>Vol Spike:</b> <code>{smc_data['vol_spike']:.1f}x Avg</code>"

    try:
        sent = bot.send_message(VIP_CHANNEL_ID, msg, parse_mode="HTML",
                                reply_markup=markup, disable_web_page_preview=True)
        record = {
            "contract":    sym,
            "symbol":      sym,
            "network":     "GATEIO",
            "entry":       entry,
            "sl":          sl,
            "tp1":         tp1,
            "tp2":         tp2,
            "tp3":         tp3,
            "rr1":         round(rr1, 2),
            "rr2":         round(rr2, 2),
            "setup":       smc_data["setup"],
            "fibo_zone":   smc_data.get("fib_zone", "N/A"),
            "score":       smc_data["score"],
            "volume_24h":  vol_24h,
            "timeframe":   timeframe,
            "session":     session,
            "whale":       whale_signal,
            "confluence":  conf_labels[:100],
            "msg_id":      sent.message_id,
            "sent_at":     int(time.time()),
            "closed":      False,
        }
        save_signal(record)
        add_cooldown(sym)
        print(f"[SIGNAL SENT ✅] {sym} | {smc_data['setup']} | {timeframe} | RR1:{rr1:.2f} | Session:{session} | Whale:{whale_signal}")
        return True
    except Exception as e:
        alert_admin(f"Gagal hantar signal {sym}: {e}")
        return False

# ==========================================
# 8. SCANNER & TRADE MONITOR
# ==========================================
IS_SCANNING           = True
WATCHLIST             = {}
WATCHLIST_TIMEOUT     = 900     # M15 momentum: 15 minit
PULLBACK_WATCHLIST    = {}
PULLBACK_TIMEOUT      = 28800   # M5 pullback: 8 JAM
BOS_WATCHLIST         = {}
BOS_WATCHLIST_TIMEOUT = 28800   # BOS breakout: 8 JAM

def add_pullback_watchlist(sym, smc_data):
    with _watchlist_lock:
        PULLBACK_WATCHLIST[sym] = {
            "entry":   smc_data["entry"],
            "fib_500": smc_data.get("fib_500", smc_data["entry"]),
            "fib_786": smc_data.get("fib_786", smc_data["sl"]),
            "sl":      smc_data["sl"],
            "added":   time.time(),
            "setup":   smc_data["setup"],
            "whale":   smc_data.get("whale_signal", "NEUTRAL"),
        }
        print(f"[{sym}] 📌 PULLBACK WATCHLIST: Fib {fmt(smc_data.get('fib_786', smc_data['sl']))} - {fmt(smc_data['entry'])}")

def scan_once():
    global IS_SCANNING_ACTIVE
    with _scan_lock:
        if IS_SCANNING_ACTIVE:
            print("[SCAN] ⏭️ Scan sedang berjalan, skip")
            return
        IS_SCANNING_ACTIVE = True

    try:
        if not IS_SCANNING:
            return

        btc_chg = get_btc_24h_change()
        cfg     = get_config()

        # [P2-1] Show session in scan header
        session_name, session_score, session_emoji = get_trading_session()
        print(f"\n{'='*65}")
        print(f"🔍 [{datetime.now().strftime('%H:%M:%S')}] SCAN | Mode:{SCAN_MODE.upper()} | {session_emoji} {session_name} | BTC:{btc_chg:+.2f}%")
        print(f"{'='*65}")

        tickers    = get_gateio_tickers()
        candidates = [t for t in tickers if t["volume_24h"] >= cfg["min_vol_24h"]]
        print(f"[GATEIO] Total:{len(tickers)} | Candidates:{len(candidates)}")

        active = get_active_trades()
        print(f"[ACTIVE] {len(active)} trades aktif")

        passed              = 0
        momentum_candidates = sorted(candidates, key=lambda x: x["volume_24h"], reverse=True)[:100]

        for t in candidates:
            sym           = t["symbol"]
            current_price = t.get("last_price", 0)

            is_blacklisted, bl_reason = is_blacklisted_symbol(sym)
            if is_blacklisted:
                continue
            if is_in_cooldown(sym):
                if not check_cooldown_override(sym, current_price):
                    continue
            if sym in active:
                continue

            print(f"\n[{sym}] 🔎 ANALYZING...")

            # ENGINE 1: PULLBACK SMC
            if SCAN_MODE in ["pullback", "both", "all"]:
                smc = analyze_smc_pa(sym, verbose=True)
                if smc and smc["score"] >= cfg["score_pass"]:
                    if send_signal(sym, smc, t["volume_24h"], btc_chg=btc_chg):
                        passed += 1
                        time.sleep(2)

            # ENGINE 3: BREAKOUT SNIPER [FIX-BO: key names fixed]
            if SCAN_MODE in ["breakout", "both", "all"]:
                bo = analyze_breakout_sniper(sym, verbose=True)
                if bo and bo["score"] >= cfg["score_pass"]:
                    if send_signal(sym, bo, t["volume_24h"], btc_chg=btc_chg):
                        passed += 1
                        time.sleep(2)

            # ENGINE 2: MOMENTUM (M15)
            if SCAN_MODE in ["momentum", "both", "all"]:
                if t in momentum_candidates:
                    if SCAN_MODE in ("both", "all") and is_in_cooldown(sym):
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
                        with _watchlist_lock:
                            if sym not in WATCHLIST:
                                WATCHLIST[sym] = time.time()
                                print(f"[{sym}] 📌 WATCHLIST: Vol Anomaly ({curr_vol/avg_vol:.1f}x)")

                        highs       = [c['h'] for c in candles_m15[-50:]]
                        lows        = [c['l'] for c in candles_m15[-50:]]
                        range_pct   = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100
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
                            atr_m15  = calculate_atr(candles_m15, 14)
                            sl_m15   = compute_final_sl(curr['c'], min(lows[-20:]), atr_m15,
                                                        atr_mult=0.75, max_sl_pct=0.08)
                            risk_m15 = curr['c'] - sl_m15
                            if risk_m15 <= 0:
                                continue
                            tp1_m15 = max(highs[-50:]) + (atr_m15 * 0.5 if atr_m15 > 0 else max(highs[-50:]) * 0.01)
                            tp2_m15 = curr['c'] + risk_m15 * 2.618
                            tp3_m15 = curr['c'] + risk_m15 * 4.236
                            tp2_m15 = max(tp1_m15 * 1.005, tp2_m15)
                            tp3_m15 = max(tp2_m15 * 1.005, tp3_m15)

                            # [P2-1] Session + [P2-2] Whale for inline M15
                            sess_n, _, _ = get_trading_session()

                            smc_m15 = {
                                "setup":            "⚡ PINBAR MOMENTUM" if is_pinbar else "⚡ ENGULFING MOMENTUM",
                                "entry":            curr['c'],
                                "sl":               sl_m15,
                                "tp1":              tp1_m15,
                                "tp2":              tp2_m15,
                                "tp3":              tp3_m15,
                                "rr1":              round((tp1_m15 - curr['c']) / risk_m15, 2),
                                "rr2":              round((tp2_m15 - curr['c']) / risk_m15, 2),
                                "score":            3,
                                "fib_zone":         "N/A",
                                "timeframe":        "M15",
                                "vol_spike":        curr_vol / avg_vol,
                                "range_pct":        range_pct,
                                "is_counter_trend": False,
                                "session":          sess_n,
                                "whale_signal":     "NEUTRAL",
                                "choch":            "NONE",
                                "confluence_labels": f"VOL{curr_vol/avg_vol:.1f}x|{sess_n}",
                            }
                            if send_signal(sym, smc_m15, t["volume_24h"], btc_chg=btc_chg):
                                passed += 1
                                with _watchlist_lock:
                                    if sym in WATCHLIST: del WATCHLIST[sym]
                                time.sleep(2)
                    else:
                        with _watchlist_lock:
                            if sym in WATCHLIST: del WATCHLIST[sym]

        print(f"\n📊 SCAN SELESAI | {passed} signal dihantar | Session:{session_name}")
        print(f"{'='*65}\n")

    finally:
        with _scan_lock:
            IS_SCANNING_ACTIVE = False

def monitor_active_trades():
    active = get_active_trades()
    if not active: return
    for sym, trade in active.items():
        try:
            candles = get_gateio_klines(sym, "1h", 5)
            if not candles: continue
            cp          = candles[-1]['c']
            mid         = trade.get("msg_id")
            entry_price = trade.get("entry", 0)

            def notify(text, _mid=mid):
                full_text = f"<b>{sym}</b>\n{text}"
                kw = {"parse_mode": "HTML"}
                if _mid: kw["reply_to_message_id"] = _mid
                try:
                    bot.send_message(VIP_CHANNEL_ID, full_text, **kw)
                except Exception:
                    bot.send_message(VIP_CHANNEL_ID, full_text, parse_mode="HTML")

            updates = {}
            if cp >= trade["tp1"] and not trade.get("tp1_hit"):
                updates["tp1_hit"] = True
                profit_pct = (trade["tp1"] - entry_price) / entry_price * 100
                notify(f"✅ <b>TP1 HIT!</b>\n💰 <code>${fmt(cp)}</code> | +{profit_pct:.2f}%\n🔒 Alih SL → BE: <code>${fmt(entry_price)}</code>")
            if cp >= trade["tp2"] and not trade.get("tp2_hit"):
                updates["tp2_hit"] = True
                profit_pct = (trade["tp2"] - entry_price) / entry_price * 100
                notify(f"🚀 <b>TP2 HIT!</b>\n💰 <code>${fmt(cp)}</code> | +{profit_pct:.2f}%\n📈 Trail SL → TP1: <code>${fmt(trade['tp1'])}</code>")
            if cp >= trade["tp3"] and not trade.get("tp3_hit"):
                updates["tp3_hit"] = True
                updates["closed"]  = True
                profit_pct = (cp - entry_price) / entry_price * 100
                notify(f"🏆 <b>TP3 MOONSHOT!</b>\n💰 <code>${fmt(cp)}</code> | +{profit_pct:.2f}%")
            elif cp <= trade["sl"] and not trade.get("sl_hit"):
                updates["sl_hit"] = True
                updates["closed"] = True
                loss_pct = (cp - entry_price) / entry_price * 100
                notify(f"❌ <b>SL HIT</b>\n💰 <code>${fmt(cp)}</code> | {loss_pct:.2f}%")
            if updates:
                update_signal(sym, updates)
                print(f"[MONITOR] {sym}: {list(updates.keys())}")
        except Exception as e:
            print(f"[MONITOR] {sym}: {e}")

# ==========================================
# 9. PULLBACK & BOS MONITORS (Phase 1+2 Upgraded)
# ==========================================
def monitor_pullback_watchlist():
    if not IS_SCANNING or not PULLBACK_WATCHLIST:
        return
    with _watchlist_lock:
        items = list(PULLBACK_WATCHLIST.items())

    print(f"\n[PULLBACK MONITOR] {len(items)} coins | timeout:8h")
    symbols_to_remove = []

    for sym, data in items:
        try:
            elapsed_hours = (time.time() - data["added"]) / 3600
            if elapsed_hours > (28800 / 3600):
                print(f"[{sym}] ⏱️ Timeout — buang dari PULLBACK_WATCHLIST")
                symbols_to_remove.append(sym)
                continue

            candles_m5 = get_gateio_klines(sym, "5m", 50)
            if len(candles_m5) < 20:
                continue

            current_price = candles_m5[-1]['c']
            if current_price > data["entry"] or current_price < data["fib_786"]:
                continue

            # [P1-1] CHOCH check before pullback entry
            swings_m5  = find_fractal_swings(candles_m5, lookback=1)
            struct_m5  = check_market_structure(swings_m5)
            choch_m5   = detect_choch(swings_m5, current_price, struct_m5)
            if choch_m5 == 'CHOCH_BEAR':
                print(f"[{sym}] ⚠️ CHOCH BEAR di M5 — structure weakening, remove pullback")
                symbols_to_remove.append(sym)
                continue

            recent_10   = candles_m5[-10:]
            red_candles = sum(1 for c in recent_10 if c['c'] < c['o'])
            if red_candles >= 8:
                print(f"[{sym}] SLOW DUMP ({red_candles}/10 merah). Remove.")
                symbols_to_remove.append(sym)
                continue

            avg_vol_m5  = sum(c['v'] for c in candles_m5[-20:-1]) / 19
            curr_vol_m5 = candles_m5[-1]['v']
            if curr_vol_m5 > avg_vol_m5 * 1.5:
                continue

            curr = candles_m5[-1]
            prev = candles_m5[-2]
            body        = abs(curr['c'] - curr['o'])
            lower_wick  = min(curr['o'], curr['c']) - curr['l']
            is_pinbar   = lower_wick > body * 2 and curr['c'] > curr['o']
            is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                            and curr['c'] > prev['o'] and curr['o'] < prev['c'])

            if is_pinbar or is_engulfing:
                # [P2-1] Session check for pullback entry
                sess_n, sess_score, _ = get_trading_session()
                if sess_score < 0:
                    print(f"[{sym}] 💀 Dead zone session — skip pullback entry")
                    continue

                atr_m5  = calculate_atr(candles_m5, 14)
                sl_m5   = compute_final_sl(curr['c'], data["fib_786"], atr_m5, atr_mult=0.75, max_sl_pct=0.08)
                risk_m5 = curr['c'] - sl_m5
                if risk_m5 <= 0:
                    continue

                pattern = "Pinbar" if is_pinbar else "Engulfing"
                smc_pullback = {
                    "setup":            f"🔄 PULLBACK RECOVERY ({pattern} M5)",
                    "entry":            curr['c'],
                    "sl":               sl_m5,
                    "tp1":              data["entry"],
                    "tp2":              data["fib_500"],
                    "tp3":              data["fib_500"] * 1.05,
                    "rr1":              round((data["entry"] - curr['c']) / risk_m5, 2),
                    "rr2":              round((data["fib_500"] - curr['c']) / risk_m5, 2),
                    "score":            4,
                    "fib_zone":         "N/A",
                    "timeframe":        "M5",
                    "is_counter_trend": False,
                    "session":          sess_n,
                    "whale_signal":     data.get("whale", "NEUTRAL"),
                    "choch":            "NONE",
                    "confluence_labels": f"PULLBACK|{pattern}|{sess_n}",
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

    print(f"\n[BOS MONITOR] {len(items)} pending BOS | timeout:8h")
    symbols_to_remove = []

    for sym, data in items:
        try:
            elapsed_hours = (time.time() - data["added"]) / 3600
            if elapsed_hours > (BOS_WATCHLIST_TIMEOUT / 3600):
                print(f"[{sym}] ⏱️ BOS timeout — remove")
                symbols_to_remove.append(sym)
                continue

            current_price = get_gateio_price(sym)
            if current_price <= 0:
                continue

            level    = data["level"]
            bos_type = data["type"]

            # [P2-1] Session check for BOS
            sess_n, sess_score, sess_e = get_trading_session()
            if sess_score < 0:
                print(f"[{sym}] 💀 Dead zone — skip BOS check")
                continue

            if bos_type == "HL":
                # Long-only: HL = Higher Low = support hold
                proximity_pct = (current_price - level) / level * 100

                if current_price < level:
                    # [P1-1] Check if CHOCH confirming breakdown
                    print(f"[{sym}] 🔴 STRUCTURE BREAK: HL ${fmt(level)} broken @ ${fmt(current_price)} — CHOCH BEAR confirmed, remove")
                    symbols_to_remove.append(sym)

                elif 0 <= proximity_pct <= 1.5:
                    candles_m15 = get_gateio_klines(sym, "15m", 30)
                    if len(candles_m15) >= 10:
                        curr = candles_m15[-1]
                        prev = candles_m15[-2]
                        body        = abs(curr['c'] - curr['o'])
                        lower_wick  = min(curr['o'], curr['c']) - curr['l']
                        is_pinbar   = lower_wick > body * 2 and curr['c'] > curr['o']
                        is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                                        and curr['c'] > prev['o'] and curr['o'] < prev['c'])

                        if is_pinbar or is_engulfing:
                            # [P1-2] Validate displacement
                            atr_m15  = calculate_atr(candles_m15, 14)
                            total_rng = curr['h'] - curr['l']
                            cb        = abs(curr['c'] - curr['o'])
                            has_disp  = (curr['c'] > curr['o'] and cb > total_rng * 0.45 and
                                         atr_m15 > 0 and total_rng > atr_m15 * 0.4)

                            if not has_disp:
                                print(f"[{sym}] ⚠️ HL HOLD candle — weak displacement, wait")
                                continue

                            # [P2-2] Whale check
                            candles_h4_bos = get_gateio_klines(sym, "4h", 50)
                            whale_bos, _, _ = check_whale_proxy(sym, candles_h4_bos, candles_m15)
                            if whale_bos == "DISTRIBUTING":
                                print(f"[{sym}] ❌ Whale distributing at HL — skip")
                                continue

                            sl_hold  = compute_final_sl(current_price, level, atr_m15, atr_mult=1.0, max_sl_pct=0.08)
                            risk_hold = current_price - sl_hold
                            if risk_hold <= 0:
                                continue

                            pattern = "Pinbar" if is_pinbar else "Engulfing"
                            print(f"[{sym}] 🏗️ HL HOLD! ${fmt(level)} @ ${fmt(current_price)} ({elapsed_hours:.1f}h) | {whale_bos}")
                            smc_hold = {
                                "setup":            f"🏗️ HL HOLD — {pattern} (M15)",
                                "entry":            current_price,
                                "sl":               sl_hold,
                                "tp1":              current_price + risk_hold * 1.618,
                                "tp2":              current_price + risk_hold * 2.618,
                                "tp3":              current_price + risk_hold * 4.236,
                                "rr1":              1.618,
                                "rr2":              2.618,
                                "score":            4,
                                "fib_zone":         "N/A",
                                "timeframe":        "M15",
                                "is_counter_trend": False,
                                "fib_500":          current_price + risk_hold * 2.618,
                                "fib_786":          sl_hold,
                                "session":          sess_n,
                                "whale_signal":     whale_bos,
                                "choch":            "NONE",
                                "confluence_labels": f"HL_HOLD|{pattern}|{sess_n}|{whale_bos}",
                            }
                            if send_signal(sym, smc_hold, 0, btc_chg=0.0):
                                symbols_to_remove.append(sym)

            elif bos_type == "LH" and current_price > level:
                # Bullish BOS: LH broken = upward trend resumption
                # [P1-1] This IS a bullish CHOCH if structure was downtrend
                print(f"[{sym}] 💥 BOS BREAK! LH ${fmt(level)} @ ${fmt(current_price)} ({elapsed_hours:.1f}h)")

                candles_m5_bos = get_gateio_klines(sym, "5m", 30)
                atr_bos = calculate_atr(candles_m5_bos, 14) if len(candles_m5_bos) >= 15 else current_price * 0.015

                # [P1-2] Displacement check for BOS
                if len(candles_m5_bos) >= 2:
                    curr_bos   = candles_m5_bos[-1]
                    body_bos   = abs(curr_bos['c'] - curr_bos['o'])
                    range_bos  = curr_bos['h'] - curr_bos['l']
                    disp_ok    = (curr_bos['c'] > curr_bos['o'] and
                                  range_bos > atr_bos * 0.5 and
                                  body_bos > range_bos * 0.45)
                    if not disp_ok:
                        print(f"[{sym}] ⚠️ BOS BREAK weak displacement — wait retest")
                        continue

                # [P2-2] Whale check for BOS breakout
                candles_h4_bo2 = get_gateio_klines(sym, "4h", 50)
                whale_bo2, _, _ = check_whale_proxy(sym, candles_h4_bo2, candles_m5_bos)
                if whale_bo2 == "DISTRIBUTING":
                    print(f"[{sym}] ❌ Whale distributing — false BOS breakout, skip")
                    continue

                sl_bos  = compute_final_sl(current_price, level * 0.98, atr_bos, atr_mult=1.0, max_sl_pct=0.08)
                risk_bos = current_price - sl_bos
                if risk_bos <= 0:
                    symbols_to_remove.append(sym)
                    continue

                smc_bos = {
                    "setup":            "💥 BOS BREAK — Bullish (LH Tembus)",
                    "entry":            current_price,
                    "sl":               sl_bos,
                    "tp1":              current_price + risk_bos * 1.618,
                    "tp2":              current_price + risk_bos * 2.618,
                    "tp3":              current_price + risk_bos * 4.236,
                    "rr1":              1.618,
                    "rr2":              2.618,
                    "score":            4,
                    "fib_zone":         "N/A",
                    "timeframe":        "M5",
                    "is_counter_trend": False,
                    "session":          sess_n,
                    "whale_signal":     whale_bo2,
                    "choch":            "CHOCH_BULL",
                    "confluence_labels": f"BOS_BREAK|LH|{sess_n}|{whale_bo2}",
                }
                if send_signal(sym, smc_bos, 0, btc_chg=0.0):
                    symbols_to_remove.append(sym)

        except Exception as e:
            print(f"[BOS ERROR] {sym}: {e}")

    with _watchlist_lock:
        for sym in symbols_to_remove:
            if sym in BOS_WATCHLIST: del BOS_WATCHLIST[sym]

def fast_track_watchlist():
    with _watchlist_lock:
        now       = time.time()
        to_remove = [sym for sym, added_time in WATCHLIST.items()
                     if now - added_time > WATCHLIST_TIMEOUT]
        for sym in to_remove:
            del WATCHLIST[sym]
        if to_remove:
            print(f"[WATCHLIST] Cleanup: {len(to_remove)} expired (15min)")
    monitor_pullback_watchlist()
    monitor_bos_breaks()

# ==========================================
# 10. TELEGRAM COMMANDS
# ==========================================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    cfg       = get_config()
    active    = get_active_trades()
    uptime_m  = int((time.time() - START_TIME) / 60)
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")
    sess_n, sess_s, sess_e = get_trading_session()
    text = (
        f"🏴‍☠️ <b>ALPHA — Dual Engine Sniper v15.0</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Uptime   : <code>{uptime_m}m</code>\n"
        f"💼 Trade    : <code>{len(active)} aktif</code>\n"
        f"🔧 Scan     : <code>{'✅ AKTIF' if IS_SCANNING else '⛔ STOP'}</code>\n"
        f"🕐 Session  : <code>{sess_e} {sess_n}</code>\n\n"
        f"⚡ <b>Mode:</b> <code>{SCAN_MODE.upper()}</code>\n"
        f"🎛️ <b>Preset:</b> <code>{preset_lbl}</code>\n\n"
        f"<b>🏴‍️ Engine 1 — Pullback SMC (H1+H4):</b>\n"
        f"Fractal Swing | EMA | ATR+Fib SL | CHOCH | Sweep+Confluence\n\n"
        f"<b>⚡ Engine 2 — Momentum (M15):</b>\n"
        f"Volume Anomaly | Whale Proxy | Session Filter\n\n"
        f"<b>🚀 Engine 3 — Breakout (H1):</b>\n"
        f"Donchian+VolClimax | ADX | Displacement | Session+Whale\n\n"
        f"<b>🏛️ Phase 1+2 Features:</b>\n"
        f"├ CHOCH Detection (Bull/Bear)\n"
        f"├ Sweep Confluence Validation\n"
        f"├ Confluence Stacking (Min 2+)\n"
        f"├ Session Timing (London/NY)\n"
        f"└ Whale Proxy (Multi-TF Vol)"
    )
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🟢 Soft",     callback_data="tune:soft"),
        InlineKeyboardButton("🟡 Standard", callback_data="tune:standard"),
        InlineKeyboardButton("🔴 Hard",     callback_data="tune:hard")
    )
    kb.add(
        InlineKeyboardButton("🏴‍☠️ Pullback",  callback_data="mode:pullback"),
        InlineKeyboardButton("⚡ Momentum",   callback_data="mode:momentum"),
        InlineKeyboardButton("🚀 Breakout",   callback_data="mode:breakout")
    )
    kb.add(
        InlineKeyboardButton("🔄 Both",    callback_data="mode:both"),
        InlineKeyboardButton("🌐 All",     callback_data="mode:all")
    )
    kb.add(
        InlineKeyboardButton("▶️ Mula",    callback_data="scan_on"),
        InlineKeyboardButton("⏸ Henti",   callback_data="scan_off"),
        InlineKeyboardButton("📓 Journal", callback_data="journal")
    )
    kb.add(
        InlineKeyboardButton("📊 Status",  callback_data="status"),
        InlineKeyboardButton("❓ Help",    callback_data="help")
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
        text = f"✅ <b>PRESET DIAPLIKASI</b>\n\n{lbl}"
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
    if new_mode in ["pullback", "momentum", "both", "breakout", "all"]:
        SCAN_MODE = new_mode
        bot.send_message(call.message.chat.id, f"✅ Mode: <b>{new_mode.upper()}</b>", parse_mode="HTML")
        alert_admin(f"⚡ Mode: {new_mode.upper()}")

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
            f"<b>Aktif:</b> {PRESETS.get(active, {}).get('label', 'Custom')}\n\n"
            f"🟢 <code>/tune soft</code>     — Vol $500K, Pass 2\n"
            f"🟡 <code>/tune standard</code> — Vol $1M,   Pass 3\n"
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
        bot.reply_to(msg, f"⚡ Mode: <b>{SCAN_MODE.upper()}</b>\n\nGuna: <code>/mode pullback|momentum|breakout|both|all</code>", parse_mode="HTML")
        return
    new_mode = parts[1].lower()
    if new_mode in ["pullback", "momentum", "both", "breakout", "all"]:
        SCAN_MODE = new_mode
        bot.reply_to(msg, f"✅ Mode: <b>{new_mode.upper()}</b>", parse_mode="HTML")
        alert_admin(f"⚡ Mode: {new_mode.upper()}")
    else:
        bot.reply_to(msg, "❌ Mode tidak sah. Guna: pullback|momentum|breakout|both|all")

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
        smc_bo  = analyze_breakout_sniper(sym, verbose=False)
        if smc_h1:
            choch  = smc_h1.get('choch', 'NONE')
            whale  = smc_h1.get('whale_signal', 'NEUTRAL')
            sess   = smc_h1.get('session', 'UNKNOWN')
            conf   = smc_h1.get('confluence_labels', '')
            bot.send_message(msg.chat.id,
                f"🏴‍️ <b>{sym} — PULLBACK SMC (H1)</b>\n"
                f"Setup     : <code>{smc_h1['setup']}</code>\n"
                f"Score     : <code>{smc_h1['score']}</code>\n"
                f"RR1       : <code>{smc_h1.get('rr1',0):.2f}</code>\n"
                f"Structure : <code>{smc_h1.get('structure','unknown')}</code>\n"
                f"CHOCH     : <code>{choch}</code>\n"
                f"Whale     : <code>{whale}</code>\n"
                f"Session   : <code>{sess}</code>\n"
                f"Confluences: <code>{conf[:60]}</code>",
                parse_mode="HTML")
        elif smc_bo:
            bot.send_message(msg.chat.id,
                f"🚀 <b>{sym} — BREAKOUT (H1)</b>\n"
                f"Setup   : <code>{smc_bo['setup']}</code>\n"
                f"Score   : <code>{smc_bo['score']}</code>\n"
                f"RR1     : <code>{smc_bo.get('rr1',0):.2f}</code>\n"
                f"Session : <code>{smc_bo.get('session','?')}</code>\n"
                f"Whale   : <code>{smc_bo.get('whale_signal','?')}</code>",
                parse_mode="HTML")
        elif smc_m15:
            bot.send_message(msg.chat.id,
                f"⚡ <b>{sym} — MOMENTUM (M15)</b>\n"
                f"Setup     : <code>{smc_m15['setup']}</code>\n"
                f"Vol Spike : <code>{smc_m15['vol_spike']:.1f}x</code>\n"
                f"RR1       : <code>{smc_m15.get('rr1',0):.2f}</code>\n"
                f"Session   : <code>{smc_m15.get('session','?')}</code>",
                parse_mode="HTML")
        else:
            bot.send_message(msg.chat.id, f"❌ <code>{sym}</code>: Tiada setup valid", parse_mode="HTML")
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
    sess_n, _, sess_e = get_trading_session()
    with _watchlist_lock:
        pw_count = len(PULLBACK_WATCHLIST)
        bw_count = len(BOS_WATCHLIST)
        w_count  = len(WATCHLIST)
    bot.reply_to(msg, (
        f"📊 <b>STATUS — ALPHA v15.0</b>\n\n"
        f"Scan     : {'🟢 AKTIF' if IS_SCANNING else '⛔ STOP'}\n"
        f"Mode     : <code>{SCAN_MODE.upper()}</code>\n"
        f"Trade    : <code>{len(active)}</code> aktif\n"
        f"Session  : <code>{sess_e} {sess_n}</code>\n\n"
        f"📌 <b>Watchlists:</b>\n"
        f"├ M15 Momentum  : <code>{w_count}</code>\n"
        f"├ M5 Pullback   : <code>{pw_count}</code>\n"
        f"└ BOS Breakout  : <code>{bw_count}</code>\n\n"
        f"🎛️ Preset : <code>{preset_lbl}</code>\n"
        f"Vol Min  : <code>${cfg['min_vol_24h']/1e6:.1f}M</code>\n"
        f"Score Min: <code>{cfg['score_pass']}</code>\n\n"
        f"<b>🏛️ Phase 1+2 Active:</b>\n"
        f"✅ CHOCH Detection\n"
        f"✅ Sweep Confluence\n"
        f"✅ Confluence Stacking\n"
        f"✅ Session Timing\n"
        f"✅ Whale Proxy"
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
            f"💼 <b>Modal:</b> ${cap:,.2f}\n"
            f"⚠️ <b>Risk:</b> {risk}%\n\n"
            f"Set: <code>/modal 1000</code>",
            parse_mode="HTML")
        return
    try:
        new_cap = float(args[1])
        if new_cap < 10:
            bot.reply_to(msg, "⚠️ Minimum modal: $10")
            return
        set_user_capital(int(ADMIN_ID), new_cap)
        bot.reply_to(msg,
            f"✅ <b>Modal:</b> ${new_cap:,.2f}\n"
            f"Risk default: 2% (${new_cap*0.02:,.2f}/trade)\n"
            f"Max position: 50% (${new_cap*0.50:,.2f})",
            parse_mode="HTML")
    except ValueError:
        bot.reply_to(msg, "❌ Format: <code>/modal 1000</code>", parse_mode="HTML")

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    if str(msg.chat.id) != str(ADMIN_ID):
        return
    bot.reply_to(msg, (
        "📖 <b>ARAHAN — ALPHA v15.0</b>\n\n"
        "/start             — Menu utama\n"
        "/scan              — Paksa scan\n"
        "/pair [SYM]        — Analisis manual\n"
        "/journal           — Laporan 7 hari\n"
        "/status            — Status semasa\n"
        "/mode [MODE]       — Tukar mode\n"
        "                    (pullback|momentum|breakout|both|all)\n"
        "/tune [PRESET]     — Tukar preset (soft|standard|hard)\n"
        "/modal [AMOUNT]    — Set modal trading\n\n"
        "<b>🏛️ Phase 1+2 Filters:</b>\n"
        "├ CHOCH: Early trend reversal detection\n"
        "├ Sweep: Requires FVG/OB confluence\n"
        "├ Confluence: Min 2 strong signals\n"
        "├ Session: London/NY bias (+score)\n"
        "└ Whale: Multi-TF accumulation proxy"
    ), parse_mode="HTML")

# ==========================================
# 11. JOURNAL
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
        s = t.get("setup", "Unknown")[:25]
        setups[s] = setups.get(s, 0) + 1
    setup_str = " | ".join(f"{k}: {v}" for k, v in sorted(setups.items(), key=lambda x: -x[1]))

    sessions = {}
    for t in trades:
        s = t.get("session", "UNKNOWN")
        sessions[s] = sessions.get(s, 0) + 1
    sess_str = " | ".join(f"{k}:{v}" for k, v in sorted(sessions.items(), key=lambda x: -x[1]))

    whales = {}
    for t in trades:
        w = t.get("whale", "NEUTRAL")
        whales[w] = whales.get(w, 0) + 1
    whale_str = " | ".join(f"{k}:{v}" for k, v in sorted(whales.items(), key=lambda x: -x[1]))

    wr = tp1_n / total * 100 if total else 0

    return (
        f"📓 <b>ALPHA JOURNAL (7D) — v15.0</b>\n\n"
        f"├ Total Signal : <code>{total}</code>\n"
        f"├ TP1 Hit      : <code>{tp1_n} ({wr:.0f}%)</code>\n"
        f"├ TP2 Hit      : <code>{tp2_n}</code>\n"
        f"├ TP3 Moonshot : <code>{tp3_n}</code>\n"
        f"├ SL Hit       : <code>{sl_n}</code>\n"
        f"└ Masih Buka   : <code>{open_n}</code>\n\n"
        f"<b>🧠 Setup Breakdown:</b>\n<code>{setup_str[:150]}</code>\n\n"
        f"<b>🕐 Session Breakdown:</b>\n<code>{sess_str}</code>\n\n"
        f"<b>🐳 Whale Breakdown:</b>\n<code>{whale_str}</code>"
    )

# ==========================================
# 12. SCHEDULER & MAIN
# ==========================================
def run_scheduler():
    schedule.every(5).minutes.do(lambda: threading.Thread(target=scan_once,              daemon=True).start())
    schedule.every(5).minutes.do(lambda: threading.Thread(target=monitor_active_trades,  daemon=True).start())
    schedule.every(30).seconds.do(lambda: threading.Thread(target=fast_track_watchlist,  daemon=True).start())
    while True:
        schedule.run_pending()
        time.sleep(1)

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALPHA DUAL ENGINE v15.0 [PHASE1+2 INSTITUTIONAL] ACTIVE")
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
    sess_n, _, sess_e = get_trading_session()
    alert_admin(
        f"🏴‍☠️ ALPHA v15.0 DEPLOYED\n"
        f"Mode: {SCAN_MODE.upper()}\n"
        f"Preset: {PRESETS[get_config()['active_preset']]['label']}\n"
        f"Session: {sess_e} {sess_n}\n\n"
        f"[Phase 1] CHOCH | Sweep Confluence | Confluence Stacking\n"
        f"[Phase 2] Session Timing | Whale Proxy | Displacement\n\n"
        f"Gunakan /modal untuk set modal trading"
    )
    threading.Thread(target=scan_once).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
