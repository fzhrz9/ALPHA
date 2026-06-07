"""
ALPHA — Gate.io Dual Engine Sniper (BALANCED PREMIUM UI)
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
SCAN_MODE = os.environ.get("SCAN_MODE", "both").lower()

def alert_admin(text):
    try:
        bot.send_message(ADMIN_ID, f"🚨 <b>ALPHA SYSTEM</b>\n<pre>{str(text)[:800]}</pre>", parse_mode="HTML")
    except Exception:
        pass

# =================================================================
# 2. PRESETS & SUPABASE HELPERS
# =================================================================
PRESETS = {
    "soft": {"min_vol_24h": 300_000, "score_pass": 2, "label": "🟢 SOFT"},
    "standard": {"min_vol_24h": 1_000_000, "score_pass": 3, "label": "🟡 STANDARD"},
    "hard": {"min_vol_24h": 2_500_000, "score_pass": 4, "label": "🔴 HARD"}
}

DEFAULT_CONFIG = {
    "min_vol_24h": 300_000,
    "score_pass": 2,
    "cooldown_hours": 24,
    "active_preset": "soft"
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
# 3. HELPER & GATE.IO API + BLOCKLIST
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
# 4. MATH HELPERS (INSTITUTIONAL GRADE)
# =================================================================
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
        c = candles[i]
        prev = candles[i - 1]
        tr = max(c['h'] - c['l'], abs(c['h'] - prev['c']), abs(c['l'] - prev['c']))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def find_fractal_swings(candles, lookback=2):
    swings = []
    n = len(candles)
    for i in range(lookback, n - lookback):
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
    if len(swings) < 2:
        return 'unknown'
    recent_swings = swings[-4:]
    highs = [s for s in recent_swings if s['type'] == 'SH']
    lows = [s for s in recent_swings if s['type'] == 'SL']
    if len(highs) < 2 or len(lows) < 2:
        last_swing = recent_swings[-1]
        prev_swing = recent_swings[-2] if len(recent_swings) >= 2 else None
        if last_swing['type'] == 'SH' and prev_swing and prev_swing['type'] == 'SH':
            return 'uptrend' if last_swing['price'] > prev_swing['price'] else 'downtrend'
        elif last_swing['type'] == 'SL' and prev_swing and prev_swing['type'] == 'SL':
            return 'downtrend' if last_swing['price'] < prev_swing['price'] else 'uptrend'
        return 'sideway'
    highs.sort(key=lambda x: x['index'])
    lows.sort(key=lambda x: x['index'])
    is_higher_high = highs[-1]['price'] > highs[-2]['price']
    is_higher_low = lows[-1]['price'] > lows[-2]['price']
    is_lower_high = highs[-1]['price'] < highs[-2]['price']
    is_lower_low = lows[-1]['price'] < lows[-2]['price']
    if is_higher_high and is_higher_low:
        return 'uptrend'
    elif is_lower_high and is_lower_low:
        return 'downtrend'
    elif is_higher_high and not is_lower_low:
        return 'uptrend_breakout'
    else:
        return 'sideway'

# =================================================================
# 5. PREMIUM UI HELPERS (MOCKUP BARU - CLEAN & COPYABLE)
# =================================================================
def calculate_grade(score, max_score=4):
    percentage = int((score / max_score) * 100)
    if percentage >= 90:
        return "S", percentage
    elif percentage >= 80:
        return "A+", percentage
    elif percentage >= 70:
        return "B+", percentage
    elif percentage >= 60:
        return "C+", percentage
    else:
        return "D", percentage

def build_htf_context(smc_data):
    tf_map = {
        'uptrend': '🟢 Up',
        'downtrend': '🔴 Down',
        'sideway': '🟡 Side',
        'uptrend_breakout': '🟢 Break',
        'unknown': '⚪ ?'
    }
    h4_struct = smc_data.get('h4_structure', 'unknown')
    h1_struct = smc_data.get('structure', 'unknown')
    timeframe = smc_data.get('timeframe', 'H1')
    
    if timeframe == 'M15':
        h4_bias = smc_data.get('h4_bias', 'neutral')
        if h4_bias == 'uptrend':
            h4_display = '🟢 Up'
        elif h4_bias == 'downtrend':
            h4_display = '🔴 Down'
        else:
            h4_display = '🟡 Side'
    else:
        h4_display = tf_map.get(h4_struct, '⚪ ?')
        
    h1_display = tf_map.get(h1_struct, '⚪ ?')
    
    if timeframe == 'M15':
        m15_display = '🔴 Break'
    else:
        m15_display = '🟡 Range'
    
    return f"H4 {h4_display} │ H1 {h1_display} │ M15 {m15_display}"

def build_confluence(smc_data):
    lines = []
    timeframe = smc_data.get('timeframe', 'H1')
    
    if 'structure' in smc_data and timeframe == 'H1':
        struct = smc_data['structure']
        if struct in ['uptrend', 'uptrend_breakout']:
            lines.append("• Structure: Uptrend (HH/HL)")
        elif struct == 'sideway':
            lines.append("• Structure: Sideway")
    
    if smc_data.get('fib_zone') != 'N/A' and timeframe == 'H1':
        lines.append("• Fibonacci: Discount Zone")
    
    if 'vpa_dry' in smc_data:
        if smc_data['vpa_dry']:
            lines.append("• VPA: Dry (Pullback vol <70%)")
        else:
            lines.append("• VPA: Weak")
    
    if 'vol_spike' in smc_data:
        lines.append(f"• Volume: {smc_data['vol_spike']:.1f}x Spike")
    
    if 'range_pct' in smc_data:
        range_val = smc_data['range_pct']
        if range_val <= 5:
            lines.append(f"• Accumulation: Range {range_val:.1f}% (Tight)")
        else:
            lines.append(f"• Accumulation: Range {range_val:.1f}%")
    
    setup = smc_data.get('setup', 'Unknown')
    if 'Pinbar' in setup:
        lines.append("• Setup: Pinbar Reversal")
    elif 'Engulfing' in setup:
        lines.append("• Setup: Engulfing Candle")
    elif 'Sweep' in setup:
        lines.append("• Setup: Liquidity Sweep")
    elif 'Order Block' in setup:
        lines.append("• Setup: Fresh Order Block")
    elif 'Pullback' in setup:
        lines.append("• Setup: Trend Pullback")
    elif 'Momentum' in setup:
        lines.append("• Setup: Momentum Breakout")
    else:
        lines.append(f"• Setup: {setup}")
    
    return "\n   ".join(lines[:5])

def build_balanced_signal(sym, smc_data, vol_24h, btc_chg):
    """
    Build balanced premium signal message - MOCKUP BARU (Clean & Copyable)
    Semua nombor dalam <code> untuk copy cepat
    """
    # === HEADER ===
    grade, grade_pct = calculate_grade(smc_data['score'])
    timeframe = smc_data.get('timeframe', 'H1')
    setup = smc_data.get('setup', 'Unknown')
    
    # Icon based on grade dan setup type
    if grade in ['C+', 'D'] or 'Counter' in setup:
        header_icon = '⚠️'
    elif timeframe == 'M15':
        header_icon = '⚡'
    elif grade == 'S':
        header_icon = '💎'
    else:
        header_icon = '🏴‍☠️'
    
    # === HTF CONTEXT ===
    htf_context = build_htf_context(smc_data)
    
    # === ENTRY DETAILS ===
    entry = smc_data['entry']
    sl = smc_data['sl']
    tp1 = smc_data['tp1']
    tp2 = smc_data['tp2']
    tp3 = smc_data['tp3']
    
    risk_pct = (entry - sl) / entry * 100
    tp1_pct = (tp1 - entry) / entry * 100
    tp2_pct = (tp2 - entry) / entry * 100
    tp3_pct = (tp3 - entry) / entry * 100
    
    # RR calculation
    risk = entry - sl
    rr1 = (tp1 - entry) / risk if risk > 0 else 0
    rr2 = (tp2 - entry) / risk if risk > 0 else 0
    rr3 = (tp3 - entry) / risk if risk > 0 else 0
    
    # === CONFLUENCE ===
    confluence = build_confluence(smc_data)
    
    # === WARNING SECTION (untuk counter-trend) ===
    warning_section = ""
    if smc_data.get('h4_bias') == 'downtrend' or 'Counter' in setup:
        warning_section = f"\n\n⚠️ <b>RISK WARNING:</b>\n• H4 Downtrend (Counter-Trend)\n• SL Tightened\n• TP3 Capped"
    
    # === BTC STATUS ===
    if btc_chg > -2:
        btc_icon = '🟢'
    elif btc_chg < -4:
        btc_icon = '🔴'
    else:
        btc_icon = '🟡'
    
    # === VOLUME STATUS ===
    vol_m = vol_24h / 1e6
    if vol_m > 1:
        vol_icon = '🟢'
    elif vol_m > 0.5:
        vol_icon = '🟡'
    else:
        vol_icon = '🔴'
    
    # === FOOTER WARNING ===
    footer_warning = ""
    if grade in ['C+', 'D']:
        footer_warning = "\n\n⚠️ <b>REDUCED POSITION SIZE RECOMMENDED</b>"
    elif grade == 'S':
        footer_warning = "\n\n💎 <b>PREMIUM SETUP - FULL POSITION OK</b>"
    
    # === BUILD MESSAGE (MOCKUP BARU - CLEAN & COPYABLE) ===
    pair_name = f"{sym}USDT"
    
    msg = f"""{header_icon} <b>{sym}/USDT — {setup}</b>
⏱ <code>{timeframe}</code> | Grade: <code>{grade} ({grade_pct}%)</code>

📊 <b>HTF:</b> {htf_context}

━━━━━━━━━━━━━━━━━━━━━━

💰 <b>Entry:</b> <code>{fmt(entry)}</code>
🛑 <b>SL:</b> <code>{fmt(sl)}</code> <i>(-{risk_pct:.1f}%)</i>

🎯 <b>TP1:</b> <code>{fmt(tp1)}</code> <i>(+{tp1_pct:.1f}%) [RR 1:{rr1:.1f}]</i>
🎯 <b>TP2:</b> <code>{fmt(tp2)}</code> <i>(+{tp2_pct:.1f}%) [RR 1:{rr2:.1f}]</i>
🎯 <b>TP3:</b> <code>{fmt(tp3)}</code> <i>(+{tp3_pct:.1f}%) [RR 1:{rr3:.1f}]</i>

━━━━━━━━━━━━━━━━━━━━━━

📈 <b>Confluence:</b>
{confluence}{warning_section}

📊 Vol: <code>${vol_m:.2f}M</code> {vol_icon} │ BTC: <code>{btc_chg:+.2f}%</code> {btc_icon} │ Risk: <code>{risk_pct:.1f}%</code>{footer_warning}"""
    
    return msg

# =================================================================
# 6. ENGINE 1: PULLBACK SMC (H1 + H4)
# =================================================================
def analyze_smc_pa(sym, verbose=True):
    log = lambda msg: print(f"[{sym}-H1] {msg}") if verbose else None
    candles = get_gateio_klines(sym, "1h", 200)
    if len(candles) < 100:
        log("❌ REJECT: Data H1 < 100 candle")
        return None

    candles_h4 = get_gateio_klines(sym, "4h", 50)
    h4_structure = 'unknown'
    if len(candles_h4) >= 20:
        h4_swings = find_fractal_swings(candles_h4, lookback=1)
        h4_structure = check_market_structure(h4_swings)
        if h4_structure == 'downtrend':
            log("❌ REJECT: H4 structure downtrend (LH/LL)")
            return None
        elif h4_structure == 'unknown':
            log("⚠️ H4 structure unknown, skip H4 filter")

    swings = find_fractal_swings(candles, lookback=2)
    if len(swings) >= 4:
        structure = check_market_structure(swings)
    else:
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
        log("❌ REJECT: Market structure downtrend (LH/LL)")
        return None
    log(f"✅ STRUCTURE: {structure}")

    shs = [s for s in swings if s['type'] == 'SH']
    sls = [s for s in swings if s['type'] == 'SL']
    swing_high = shs[-1]['price'] if shs else max(c['h'] for c in candles[-200:])
    swing_low = sls[-1]['price'] if sls else min(c['l'] for c in candles[-200:])
    rng = swing_high - swing_low
    if rng <= 0:
        log("❌ REJECT: Range terlalu sempit")
        return None

    fib_500 = swing_high - (rng * 0.500)
    fib_786 = swing_high - (rng * 0.786)
    fib_zone = f"{fmt(fib_500)} - {fmt(fib_786)}"
    curr = candles[-1]
    price = curr['c']

    in_discount = fib_786 <= price <= fib_500
    if not in_discount:
        if price > fib_500:
            log("❌ REJECT: PREMIUM ZONE")
        else:
            log("❌ REJECT: EXTREME (falling knife)")
        return None
    log(f"✅ FIBO PASS: Price ${fmt(price)} dalam DISCOUNT ZONE ({fib_zone})")

    closes = [c['c'] for c in candles[-200:]]
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    is_uptrend = ema20 > ema50
    if not is_uptrend:
        log("❌ REJECT: EMA20 < EMA50 (bukan uptrend)")
        return None

    impulse_vols = [c['v'] for c in candles[-20:] if c['c'] > c['o']]
    avg_impulse_vol = sum(impulse_vols) / len(impulse_vols) if impulse_vols else 1
    pullback_vols = [c['v'] for c in candles[-20:] if c['c'] < c['o']]
    avg_pullback_vol = sum(pullback_vols) / len(pullback_vols) if pullback_vols else 1
    vpa_dry = avg_pullback_vol < (avg_impulse_vol * 0.7)

    setup_name = None
    score = 0

    body = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    wick_ratio = lower_wick / body if body > 0 else 0
    touches = sum(1 for c in candles[-50:-1] if abs(c['l'] - swing_low) / swing_low < 0.01)

    if (curr['l'] < swing_low and curr['c'] > swing_low and wick_ratio >= 2.0 and touches >= 2 and curr['v'] > avg_impulse_vol):
        setup_name = "💧 LIQUIDITY SWEEP"
        score += 3
        log(f"✅ SETUP 7: Sweep ({touches} touches, wick {wick_ratio:.1f}x)")

    total_range = curr['h'] - curr['l']
    upper_wick = curr['h'] - max(curr['o'], curr['c'])
    is_pinbar = (lower_wick > (body * 2) and upper_wick < (total_range * 0.1) and curr['c'] > curr['o'])
    prev = candles[-2]
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and curr['c'] > prev['o'] and curr['o'] <= prev['c'] and body > abs(prev['c'] - prev['o']))

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

    if vpa_dry:
        score += 1
        log("✅ VPA PASS: Pullback vol < 70% impulse vol")
    else:
        log("⚠️ VPA WEAK (Optional - tidak reject)")

    atr = calculate_atr(candles, 14)
    distance_from_ema = abs(price - ema20)
    threshold = atr * 0.5 if atr > 0 else ema20 * 0.015
    if (is_uptrend and distance_from_ema < threshold and price > ema20):
        if not setup_name:
            setup_name = "📈 TREND PULLBACK"
        score += 1
        log("✅ SETUP 2: Pullback ke EMA20 (uptrend)")

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
                        touches_after = sum(1 for j in range(i + 2, 0) if ob_low <= candles[j]['l'] <= ob_high)
                        if touches_after <= 1:
                            if not setup_name:
                                setup_name = "🧱 FRESH ORDER BLOCK"
                            score += 2
                            log("✅ SETUP 3: Fresh OB detected")
                            break
        except Exception:
            pass

    if score < 2:
        log(f"❌ REJECT: Score {score} < 2")
        return None
    if not setup_name:
        setup_name = "📊 MIXED SETUP"

    major_swing_low = min(c['l'] for c in candles[-200:])
    sl_candidate = major_swing_low * 0.995
    min_sl_distance = price * 0.05
    sl = min(sl_candidate, price - min_sl_distance)
    if (price - sl) < (price * 0.05):
        sl = price * 0.95

    tp1 = swing_high
    tp2 = swing_low + (rng * 1.618)
    tp3 = swing_low + (rng * 2.618)
    risk = price - sl
    if risk <= 0:
        sl = price * 0.98
        risk = price - sl

    log(f"✅ SETUP COMPLETE: {setup_name} | Score: {score}")

    return {
        "setup": setup_name, "entry": price, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": (tp1 - price) / risk, "rr2": (tp2 - price) / risk, "score": score,
        "fib_zone": fib_zone, "timeframe": "H1",
        "structure": structure, "h4_structure": h4_structure, "vpa_dry": vpa_dry
    }

# =================================================================
# 7. ENGINE 2: EARLY MOMENTUM (M15)
# =================================================================
def analyze_early_momentum(sym, verbose=True):
    log = lambda msg: print(f"[{sym}-M15] {msg}") if verbose else None
    candles = get_gateio_klines(sym, "15m", 100)
    if len(candles) < 50:
        log("❌ REJECT: Data M15 < 50")
        return None

    avg_vol = sum(c['v'] for c in candles[-20:-1]) / 19 if len(candles) >= 20 else 1
    curr_vol = candles[-1]['v']
    if curr_vol < (avg_vol * 2.5):
        return None
    vol_spike = curr_vol / avg_vol

    highs = [c['h'] for c in candles[-50:]]
    lows = [c['l'] for c in candles[-50:]]
    range_pct = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100
    if range_pct > 8:
        return None

    curr = candles[-1]
    prev = candles[-2]
    price = curr['c']
    body = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and curr['c'] > prev['o'] and curr['o'] < prev['c'])
    if not (is_pinbar or is_engulfing):
        return None

    setup_name = "⚡ PINBAR MOMENTUM" if is_pinbar else "⚡ ENGULFING MOMENTUM"
    entry = price
    sl = min(lows[-20:]) * 0.99
    tp1 = max(highs[-50:]) * 1.02
    tp2 = entry + (entry - sl) * 3
    tp3 = entry + (entry - sl) * 5

    h4_bias = "neutral"
    candles_h4 = get_gateio_klines(sym, "4h", 50)
    if len(candles_h4) >= 20:
        h4_ema = calculate_ema([c['c'] for c in candles_h4[-20:]], 20)
        if candles_h4[-1]['c'] > h4_ema:
            h4_bias = "uptrend"
        else:
            h4_bias = "downtrend"

    if h4_bias == "downtrend":
        sl = min(lows[-20:]) * 0.995
        tp3 = tp2
        setup_name = "⚡ COUNTER-TREND (Risky)"

    risk = entry - sl
    if risk <= 0:
        return None

    log(f"⚡ MOMENTUM SETUP: {setup_name} | H4 Bias: {h4_bias}")

    return {
        "setup": setup_name, "entry": entry, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": 0, "rr2": 0, "score": 3,
        "fib_zone": "N/A", "timeframe": "M15",
        "vol_spike": vol_spike, "range_pct": range_pct, "h4_bias": h4_bias
    }

# =================================================================
# 8. SIGNAL GENERATOR (BALANCED PREMIUM FORMAT)
# =================================================================
IS_SCANNING = True
WATCHLIST = {}
PULLBACK_WATCHLIST = {}
BOS_WATCHLIST = {}

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
        candles_atr = get_gateio_klines(sym, "1h", 20)
        threshold = 2.0
        if len(candles_atr) >= 15:
            trs = []
            for i in range(-14, 0):
                c = candles_atr[i]
                prev = candles_atr[i-1]
                tr = max(c['h'] - c['l'], abs(c['h'] - prev['c']), abs(c['l'] - prev['c']))
                trs.append(tr)
            atr = sum(trs) / len(trs)
            threshold = max(2.0, (atr / entry) * 100 * 2.0)
        
        if price_gap > threshold:
            print(f"[SKIP] {sym}: Harga dah bergerak {price_gap:.1f}%")
            if smc_data["score"] >= 2 and sym not in PULLBACK_WATCHLIST:
                PULLBACK_WATCHLIST[sym] = {"entry": entry, "sl": sl, "added": time.time()}
            return False

    # BUILD PREMIUM MESSAGE (MOCKUP BARU)
    msg = build_balanced_signal(sym, smc_data, vol_24h, btc_chg)

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 Gate.io", url=f"https://www.gate.io/trade/{sym}_USDT"),
        InlineKeyboardButton("📈 TradingView", url=f"https://www.tradingview.com/chart/?symbol=GATEIO:{sym}USDT")
    )

    try:
        sent = bot.send_message(VIP_CHANNEL_ID, msg, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
        record = {
            "contract": sym, "symbol": sym, "network": "GATEIO",
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "rr1": round(smc_data.get("rr1", 0), 2), "rr2": round(smc_data.get("rr2", 0), 2),
            "setup": smc_data["setup"], "fibo_zone": smc_data.get("fib_zone", "N/A"),
            "score": smc_data["score"], "volume_24h": vol_24h,
            "timeframe": timeframe, "msg_id": sent.message_id, "sent_at": int(time.time())
        }
        save_signal(record)
        add_cooldown(sym)
        print(f"[SIGNAL SENT ✅] {sym} | {smc_data['setup']} | {timeframe}")
        return True
    except Exception as e:
        alert_admin(f"Gagal hantar signal {sym}: {e}")
        return False

# =================================================================
# 9. SCANNER & MONITORS
# =================================================================
def scan_once():
    if not IS_SCANNING:
        return
    btc_chg = get_btc_24h_change()
    cfg = get_config()
    print(f"\n{'='*60}\n🔍 [{datetime.now().strftime('%H:%M:%S')}] SCAN | Mode: {SCAN_MODE.upper()}\n{'='*60}")
    tickers = get_gateio_tickers()
    candidates = [t for t in tickers if t["volume_24h"] >= cfg["min_vol_24h"]]
    momentum_candidates = sorted(candidates, key=lambda x: x["volume_24h"], reverse=True)[:100]
    for t in candidates:
        sym = t["symbol"]
        current_price = t.get("last_price", 0)
        if is_blacklisted_symbol(sym)[0]:
            continue
        if is_in_cooldown(sym) and not check_cooldown_override(sym, current_price):
            continue
        if sym in get_active_trades():
            continue
        print(f"\n[{sym}] 🔎 ANALYZING...")
        if SCAN_MODE in ["pullback", "both"]:
            smc = analyze_smc_pa(sym, verbose=True)
            if smc and smc["score"] >= cfg["score_pass"]:
                if send_signal(sym, smc, t["volume_24h"], btc_chg=btc_chg):
                    time.sleep(2)
        if SCAN_MODE in ["momentum", "both"]:
            if t in momentum_candidates:
                if SCAN_MODE == "both" and is_in_cooldown(sym):
                    continue
                candles_h1 = get_gateio_klines(sym, "1h", 50)
                if len(candles_h1) >= 20:
                    h1_ema20 = calculate_ema([c['c'] for c in candles_h1[-20:]], 20)
                    if candles_h1[-1]['c'] < h1_ema20 * 0.95:
                        continue
                candles_m15 = get_gateio_klines(sym, "15m", 100)
                if len(candles_m15) < 50:
                    continue
                curr = candles_m15[-1]
                avg_vol = sum(c['v'] for c in candles_m15[-20:-1]) / 19
                curr_vol = curr['v']
                if curr_vol >= (avg_vol * 2.5):
                    if sym not in WATCHLIST:
                        WATCHLIST[sym] = time.time()
                    highs = [c['h'] for c in candles_m15[-50:]]
                    lows = [c['l'] for c in candles_m15[-50:]]
                    range_pct = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100
                    range_high = max(highs)
                    range_low = min(lows)
                    total_range = range_high - range_low
                    if curr['c'] < (range_low + (total_range * 0.60)):
                        if sym in WATCHLIST:
                            del WATCHLIST[sym]
                        continue
                    recent_highs = [c['h'] for c in candles_m15[-20:-1]]
                    local_lh = max(recent_highs) if recent_highs else range_high
                    if curr['c'] <= local_lh and curr_vol/avg_vol < 4.0:
                        if sym in WATCHLIST:
                            del WATCHLIST[sym]
                        continue
                    prev = candles_m15[-2]
                    body = abs(curr['c'] - curr['o'])
                    lower_wick = min(curr['o'], curr['c']) - curr['l']
                    is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
                    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and curr['c'] > prev['o'] and curr['o'] < prev['c'])
                    if range_pct <= 8 and (is_pinbar or is_engulfing):
                        smc_m15 = analyze_early_momentum(sym, verbose=False)
                        if smc_m15:
                            if send_signal(sym, smc_m15, t["volume_24h"], btc_chg=btc_chg):
                                if sym in WATCHLIST:
                                    del WATCHLIST[sym]
                                time.sleep(2)
                else:
                    if sym in WATCHLIST:
                        del WATCHLIST[sym]

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
                except:
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
                notify(f"🏆 <b>{sym} — TP3 MOONSHOT!</b>\n💰 Tutup: <code>${fmt(cp)}</code>")
            elif cp <= trade["sl"] and not trade.get("sl_hit"):
                updates["sl_hit"] = True
                updates["closed"] = True
                notify(f"❌ <b>{sym} — SL HIT</b>\n💰 Tutup: <code>${fmt(cp)}</code>")
            if updates:
                update_signal(sym, updates)
        except Exception as e:
            print(f"[MONITOR] {sym}: {e}")

def fast_track_watchlist():
    if not IS_SCANNING or not WATCHLIST:
        return
    symbols_to_remove = []
    for sym, added_time in list(WATCHLIST.items()):
        if time.time() - added_time > 600:
            symbols_to_remove.append(sym)
            continue
        try:
            candles = get_gateio_klines(sym, "15m", 50)
            if len(candles) < 20:
                continue
            curr = candles[-1]
            prev = candles[-2]
            body = abs(curr['c'] - curr['o'])
            lower_wick = min(curr['o'], curr['c']) - curr['l']
            is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
            is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and curr['c'] > prev['o'] and curr['o'] < prev['c'])
            if is_pinbar or is_engulfing:
                smc = analyze_early_momentum(sym, verbose=False)
                if smc and send_signal(sym, smc, 0, btc_chg=0.0):
                    symbols_to_remove.append(sym)
        except:
            pass
    for sym in symbols_to_remove:
        if sym in WATCHLIST:
            del WATCHLIST[sym]

def monitor_pullback_watchlist():
    if not IS_SCANNING or not PULLBACK_WATCHLIST:
        return
    symbols_to_remove = []
    for sym, data in list(PULLBACK_WATCHLIST.items()):
        try:
            if time.time() - data["added"] > 1200:
                symbols_to_remove.append(sym)
                continue
            candles_m5 = get_gateio_klines(sym, "5m", 50)
            if len(candles_m5) < 20:
                continue
            current_price = candles_m5[-1]['c']
            if current_price > data["entry"] or current_price < data["sl"]:
                continue
            recent_10 = candles_m5[-10:]
            red_candles = sum(1 for c in recent_10 if c['c'] < c['o'])
            if red_candles >= 8:
                symbols_to_remove.append(sym)
                continue
            curr = candles_m5[-1]
            prev = candles_m5[-2]
            body = abs(curr['c'] - curr['o'])
            lower_wick = min(curr['o'], curr['c']) - curr['l']
            is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
            is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and curr['c'] > prev['o'] and curr['o'] < prev['c'])
            if is_pinbar or is_engulfing:
                smc_pullback = {
                    "setup": "🔄 PULLBACK RECOVERY (M5)", "entry": curr['c'],
                    "sl": data["sl"] * 0.99, "tp1": data["entry"], "tp2": data["entry"] * 1.05, "tp3": data["entry"] * 1.10,
                    "rr1": 2.0, "rr2": 4.0, "score": 4, "fib_zone": "N/A", "timeframe": "M5"
                }
                if send_signal(sym, smc_pullback, 0, btc_chg=0.0):
                    symbols_to_remove.append(sym)
        except:
            pass
    for sym in symbols_to_remove:
        if sym in PULLBACK_WATCHLIST:
            del PULLBACK_WATCHLIST[sym]

# =================================================================
# 10. TELEGRAM COMMANDS
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
        f"🏴‍☠️ <b>ALPHA — Dual Engine Sniper (BALANCED PREMIUM)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Uptime   : <code>{uptime_m}m</code>\n"
        f"📊 Trade   : <code>{len(active)} aktif</code>\n"
        f"🔧 Scan    : <code>{'✅ AKTIF' if IS_SCANNING else '⛔ STOP'}</code>\n\n"
        f"⚡ <b>Scan Mode:</b> <code>{SCAN_MODE.upper()}</code>\n"
        f"🎛️ <b>Preset:</b> <code>{preset_lbl}</code>"
    )
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(InlineKeyboardButton("🟢 Soft", callback_data="tune:soft"), InlineKeyboardButton("🟡 Standard", callback_data="tune:standard"), InlineKeyboardButton("🔴 Hard", callback_data="tune:hard"))
    kb.add(InlineKeyboardButton("🏴‍☠️ Pullback", callback_data="mode:pullback"), InlineKeyboardButton("⚡ Momentum", callback_data="mode:momentum"), InlineKeyboardButton("🔄 Both", callback_data="mode:both"))
    kb.add(InlineKeyboardButton("▶️ Mula", callback_data="scan_on"), InlineKeyboardButton("⏸ Henti", callback_data="scan_off"), InlineKeyboardButton("📓 Journal", callback_data="journal"))
    bot.send_message(msg.chat.id, text, parse_mode="HTML", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tune:"))
def cb_tune(call):
    if str(call.message.chat.id) != str(ADMIN_ID):
        return
    bot.answer_callback_query(call.id)
    preset = call.data.split(":")[1]
    ok, lbl = apply_preset(preset)
    if ok:
        bot.send_message(call.message.chat.id, f"✅ Preset ditukar ke: <b>{lbl}</b>", parse_mode="HTML")

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

@bot.callback_query_handler(func=lambda c: c.data in ["scan_on", "scan_off", "journal"])
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
        trades = get_signals_since(7)
        if not trades:
            bot.send_message(call.message.chat.id, "Tiada data 7 hari.")
        else:
            bot.send_message(call.message.chat.id, f"Total: {len(trades)} | TP1: {sum(1 for t in trades if t.get('tp1_hit'))}", parse_mode="HTML")

# =================================================================
# 11. SCHEDULER & MAIN
# =================================================================
def run_scheduler():
    schedule.every(5).minutes.do(lambda: threading.Thread(target=scan_once).start())
    schedule.every(5).minutes.do(lambda: threading.Thread(target=monitor_active_trades).start())
    schedule.every(30).seconds.do(lambda: threading.Thread(target=fast_track_watchlist).start())
    schedule.every(30).seconds.do(lambda: threading.Thread(target=monitor_pullback_watchlist).start())
    while True:
        schedule.run_pending()
        time.sleep(1)

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALPHA BALANCED PREMIUM ACTIVE")
    def log_message(self, *args):
        pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", port), RenderHandler).serve_forever(), daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    time.sleep(5)
    alert_admin(f"🏴‍☠️ ALPHA BALANCED PREMIUM DEPLOYED\nMode: {SCAN_MODE.upper()}")
    threading.Thread(target=scan_once).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)