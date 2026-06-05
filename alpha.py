"""
ALPHA — Gate.io Spot SMC & Price Action Sniper (H1)
Edge: Buy the dip at Discount Zone (Fib 0.5-0.786), target Moonshot TP.
7 Setup Engine + VPA Filter + BTC Circuit Breaker + Verbose Logging.
Minimalist signal format. Fibonacci hidden from display.
+ Cooldown Override (second chance entry)
+ Stablecoin/Wrapped Token Blocklist
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

def alert_admin(text):
    try:
        bot.send_message(ADMIN_ID, f"🚨 <b>ALPHA SYSTEM</b>\n<pre>{str(text)[:800]}</pre>", parse_mode="HTML")
    except Exception: pass

# =================================================================
# 2. PRESETS & SUPABASE HELPERS
# =================================================================
PRESETS = {
    "soft":     {"min_vol_24h": 500_000,   "score_pass": 2, "label": "🟢 SOFT (Banyak Signal, Longgar)"},
    "standard": {"min_vol_24h": 1_000_000, "score_pass": 3, "label": "🟡 STANDARD (Balance)"},
    "hard":     {"min_vol_24h": 2_500_000, "score_pass": 4, "label": "🔴 HARD (Sniper Only, Ketat)"}
}

DEFAULT_CONFIG = {
    "min_vol_24h": 1_000_000,
    "score_pass": 3,
    "cooldown_hours": 24,
    "active_preset": "standard"
}

_config_cache = {}_config_loaded_at = 0

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
    except Exception as e:
        print(f"[CONFIG] set error: {e}")

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
    except Exception: return False

def check_cooldown_override(contract, current_price):
    """
    Cooldown Override — Reset cooldown jika harga turun >20% dari entry asal.
    Senario: Token pump (fakeout) kemudian dump balik ke sweet spot.
    Returns True jika override berjaya (cooldown di-reset).    """
    try:
        rows = sb.table("signals").select("entry").eq("contract", contract).execute().data
        if not rows:
            return False
        
        entry_price = rows[0].get("entry", 0)
        if entry_price <= 0:
            return False
        
        # Kira % penurunan dari entry asal
        drop_pct = (entry_price - current_price) / entry_price * 100
        
        # Jika turun >20%, reset cooldown (second chance entry)
        if drop_pct > 20:
            print(f"[OVERRIDE] {contract[:16]}... turun {drop_pct:.1f}% dari entry ${entry_price:.6f} — RESET COOLDOWN")
            sb.table("sent_pool").delete().eq("key", contract).execute()
            return True
        
        return False
    except Exception as e:
        print(f"[OVERRIDE ERROR] {e}")
        return False

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
# 3. HELPER & GATE.IO API + BTC CIRCUIT BREAKER + BLOCKLIST# =================================================================

# ── STABLECOIN & WRAPPED TOKEN BLOCKLIST ──────────────────────
STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FRAX", "LUSD", 
    "GUSD", "USDD", "FDUSD", "PYUSD", "USDK", "SUSD", "RSR",
    "EURS", "EURT", "UST", "ALUSD", "MIM", "CUSD", "CEUR",
    "USD", "USDT.BSC", "USDC.BSC", "USDC.BASE",
}

WRAPPED_TOKENS = {
    "WETH", "WBTC", "WBNB", "WSOL", "WMATIC", "WAVAX", "WFTM",
    "WROSE", "WONE", "WCRO", "WGLMR", "WMOVR", "WDEV",
    "BETH", "STETH", "RETH", "CBETH", "WBETH",
}

SYMBOL_BLACKLIST = STABLECOINS | WRAPPED_TOKENS

def is_blacklisted_symbol(sym):
    """Semak jika token adalah stablecoin atau wrapped token."""
    s = sym.upper().strip()
    
    # Direct match
    if s in SYMBOL_BLACKLIST:
        return True, f"Blacklisted: {s}"
    
    # Partial match (contoh: wUSDC, USDT.BSC)
    for blacklisted in SYMBOL_BLACKLIST:
        if blacklisted in s:
            return True, f"Blacklisted (partial): {s}"
    
    # Leveraged tokens (5L, 5S, 3L, 3S, 2L, 2S, UP, DOWN, BULL, BEAR)
    for suffix in ["5L", "5S", "3L", "3S", "2L", "2S", "1L", "1S", "UP", "DOWN", "BULL", "BEAR"]:
        if s.endswith(suffix):
            return True, f"Leveraged token: {s}"
    
    return False, None
# ── TAMAT BLOCKLIST ───────────────────────────────────────────

def fmt(val):
    if val == 0: return "0.00"
    if abs(val) < 0.000001: return f"{val:.10f}"
    if abs(val) < 0.001: return f"{val:.8f}"
    if abs(val) < 1.0: return f"{val:.6f}"
    if abs(val) < 1000: return f"{val:.4f}"
    return f"{val:,.2f}"

def get_btc_24h_change():
    """Ambil % perubahan BTC 24H dari Binance (Circuit Breaker)."""
    try:        r = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT",
            timeout=3
        ).json()
        return float(r.get("priceChangePercent", 0))
    except Exception:
        return 0.0

def get_gateio_tickers():
    """Ambil semua pair USDT di Gate.io beserta Volume 24H & Last Price"""
    try:
        r = requests.get("https://api.gateio.ws/api/v4/spot/tickers", timeout=10).json()
        pairs = []
        for t in r:
            if t['currency_pair'].endswith('_USDT'):
                sym = t['currency_pair'].replace('_USDT', '')
                vol = float(t.get('quote_volume', 0))
                last_price = float(t.get('last', 0))
                pairs.append({
                    "symbol": sym, 
                    "volume_24h": vol, 
                    "last_price": last_price,
                    "pair": t['currency_pair']
                })
        return pairs
    except Exception as e:
        print(f"[GATEIO TICKERS] Error: {e}")
        return []

def get_gateio_klines(sym, interval="1h", limit=50):
    """Ambil Candle H1 dari Gate.io"""
    url = f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={sym}_USDT&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=8).json()
        candles = []
        # Gate.io format: [timestamp, volume, close, highest, lowest, open, quote_volume]
        for k in reversed(r):
            candles.append({
                't': int(k[0]), 'o': float(k[5]), 'h': float(k[3]),
                'l': float(k[4]), 'c': float(k[2]), 'v': float(k[1])
            })
        return candles
    except Exception:
        return []

# =================================================================
# 4. ENJIN SMC + PRICE ACTION (H1 TIMEFRAME) — 7 SETUP ENGINE
# =================================================================
def analyze_smc_pa(candles, sym="?", verbose=True):
    """    Mengesan 7 Setup SMC/PA di H1.
    Syarat Wajib: Harga mesti di Discount Zone (Fib 0.5 - 0.786).
    Returns: dict dengan setup, entry, sl, tp1, tp2, tp3, score, fib_zone ATAU None
    """
    log = lambda msg: print(f"[{sym}] {msg}") if verbose else None

    if len(candles) < 30:
        log("❌ REJECT: Data candle < 30 (perlukan sejarah struktur)")
        return None

    highs = [c['h'] for c in candles[-30:]]
    lows = [c['l'] for c in candles[-30:]]
    closes = [c['c'] for c in candles[-30:]]
    volumes = [c['v'] for c in candles[-30:]]

    swing_high = max(highs)
    swing_low = min(lows)
    rng = swing_high - swing_low
    if rng <= 0:
        log("❌ REJECT: Range terlalu sempit (sideways mati)")
        return None

    # 1. Kira Fibonacci Discount Zone
    fib_500 = swing_high - (rng * 0.500)
    fib_786 = swing_high - (rng * 0.786)
    fib_zone = f"{fmt(fib_500)} - {fmt(fib_786)}"

    curr = candles[-1]
    prev = candles[-2]
    price = curr['c']

    # 2. ANTI-FOMO HARD FILTER: WAJIB di Discount Zone
    in_discount = fib_786 <= price <= fib_500
    if not in_discount:
        if price > fib_500:
            log(f"❌ REJECT: Price ${fmt(price)} di PREMIUM ZONE (atas Fib 0.5) — FOMO RISK")
        else:
            log(f"❌ REJECT: Price ${fmt(price)} di EXTREME (bawah Fib 0.786) — falling knife")
        return None
    log(f"✅ FIBO PASS: Price ${fmt(price)} dalam DISCOUNT ZONE ({fib_zone})")

    # 3. Trend Filter (EMA 20) — Elak catch falling knife
    ema20 = sum(closes[-20:]) / 20
    if price < ema20 * 0.90:
        log(f"❌ REJECT: Price terlalu jauh bawah EMA20 ({fmt(ema20)}) — downtrend kuat")
        return None

    # 4. VPA (Volume Price Analysis)
    avg_vol = sum(volumes[-20:]) / 20 if sum(volumes[-20:]) > 0 else 1
    curr_vol = curr['v']    vpa_dry = curr_vol < (avg_vol * 0.8)  # Volume mengecil = pullback sihat

    # 5. Kesan Setup (Scoring Matrix)
    setup_name = None
    score = 0

    # ─── SETUP 7: LIQUIDITY SWEEP (God Tier) ────────────────
    if curr['l'] < swing_low and curr['c'] > swing_low:
        setup_name = "💧 LIQUIDITY SWEEP (Turtle Soup)"
        score += 3
        log(f"✅ SETUP 7 DETECTED: Harga cucuk Swing Low ${fmt(swing_low)}, close semula atas (Whale trap retail)")

    # ─── SETUP 5: CANDLESTICK REVERSAL (Pinbar / Engulfing) ──
    body = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and
                    curr['c'] > prev['o'] and curr['o'] < prev['c'])

    if is_pinbar:
        if not setup_name: setup_name = "️ PINBAR REVERSAL"
        score += 2
        log("✅ SETUP 5 DETECTED: Pinbar dengan ekor panjang (buyer kuat)")
    elif is_engulfing:
        if not setup_name: setup_name = "🐂 BULLISH ENGULFING"
        score += 2
        log("✅ SETUP 5 DETECTED: Bullish Engulfing (buyer overpower seller)")

    # ─── SETUP 4: VPA CONFIRMATION ──────────────────────────
    if vpa_dry:
        score += 1
        log(f"✅ VPA PASS: Volume {curr_vol:,.0f} < 80% avg ({avg_vol:,.0f}) — tiada whale dump")
    else:
        log(f"⚠️  VPA WEAK: Volume tinggi ({curr_vol:,.0f}) — possible distribution")

    # ─── SETUP 2: TREND PULLBACK (Dekat EMA) ───────────────
    if abs(price - ema20) / ema20 < 0.015:
        if not setup_name: setup_name = "📈 TREND PULLBACK (HH/HL)"
        score += 1
        log("✅ SETUP 2 DETECTED: Pullback ke EMA20 (dynamic support)")

    # ─── SETUP 3: ORDER BLOCK (SMC) ─────────────────────────
    for i in range(-15, -3):
        try:
            c = candles[i]
            c_next = candles[i+1]
            if c['c'] < c['o'] and c_next['c'] > c_next['o']:
                bos_size = c_next['c'] - c_next['o']
                if bos_size > (rng * 0.08) and c['l'] <= price <= c['h']:
                    if not setup_name: setup_name = " ORDER BLOCK (SMC)"                    score += 1
                    log(f"✅ SETUP 3 DETECTED: Price dalam Order Block (${fmt(c['l'])}-${fmt(c['h'])})")
                    break
        except: pass

    # Gagal score minimum
    if not setup_name or score < 2:
        log(f"❌ REJECT: Tiada setup kukuh (Score: {score}, perlu ≥2 untuk proses)")
        return None

    # 6. Kira SL & TP (Moonshot)
    sl = min(curr['l'], swing_low) * 0.995  # 0.5% buffer bawah wick
    tp1 = swing_high
    tp2 = swing_low + (rng * 1.618)
    tp3 = swing_low + (rng * 2.618)

    risk = price - sl
    if risk <= 0:
        log("❌ REJECT: Risk invalid (SL >= Entry)")
        return None

    rr1 = (tp1 - price) / risk
    rr2 = (tp2 - price) / risk

    log(f"🎯 SETUP COMPLETE: {setup_name} | Score: {score} | RR: 1:{rr1:.1f}(TP1) 1:{rr2:.1f}(TP2)")

    return {
        "setup": setup_name,
        "entry": price, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": rr1, "rr2": rr2,
        "score": score,
        "fib_zone": fib_zone,
        "swing_high": swing_high,
        "swing_low": swing_low
    }

# =================================================================
# 5. SIGNAL GENERATOR (MINIMALIST + HIDDEN FIBO + BTC WARNING)
# =================================================================
def send_signal(sym, smc_data, vol_24h, btc_chg=0.0):
    cfg = get_config()
    if smc_data["score"] < cfg["score_pass"]:
        print(f"[{sym}] ❌ REJECT FINAL: Score {smc_data['score']}/{cfg['score_pass']} — bawah threshold preset {cfg['active_preset'].upper()}")
        return False

    entry = smc_data["entry"]
    sl = smc_data["sl"]
    tp1, tp2, tp3 = smc_data["tp1"], smc_data["tp2"], smc_data["tp3"]
    rr1, rr2 = smc_data["rr1"], smc_data["rr2"]
    # Kira % kerugian/keuntungan (minimalist display)
    sl_pct = (entry - sl) / entry * 100
    tp1_pct = (tp1 - entry) / entry * 100
    tp2_pct = (tp2 - entry) / entry * 100
    tp3_pct = (tp3 - entry) / entry * 100

    # BTC Circuit Breaker Warning
    btc_warn = ""
    if btc_chg < -4.0:
        btc_warn = f"⚠️ <b>BTC ALERT:</b> BTC {btc_chg:+.2f}% (Market Risk)\n\n"

    # Keyboard: Direct Gate.io & TradingView (Self Custody, USDT Ready)
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 Gate.io", url=f"https://www.gate.io/trade/{sym}_USDT"),
        InlineKeyboardButton("📈 TradingView", url=f"https://www.tradingview.com/chart/?symbol=GATEIO:{sym}USDT")
    )

    # MINIMALIST FORMAT — Fibonacci HIDDEN dari paparan
    msg = (
        f"🏴‍☠️ <b>ALPHA — {sym}</b>\n\n"
        f"{btc_warn}"
        f"💰 <b>Entry:</b> <code>${fmt(entry)}</code>\n"
        f"📊 <b>Vol24H:</b> <code>${vol_24h/1e6:.2f}M</code>\n\n"
        f"🛑 <b>SL:</b> <code>${fmt(sl)}</code> <i>(-{sl_pct:.1f}%)</i>\n"
        f"📈 <b>TP1:</b> <code>${fmt(tp1)}</code> <i>(+{tp1_pct:.1f}%)</i>\n"
        f"📈 <b>TP2:</b> <code>${fmt(tp2)}</code> <i>(+{tp2_pct:.1f}%)</i>\n"
        f"📈 <b>TP3:</b> <code>${fmt(tp3)}</code> <i>(+{tp3_pct:.1f}%)</i>\n\n"
        f"🧠 <b>Setup:</b> <code>{smc_data['setup']}</code>\n"
        f"🎯 <b>RR:</b> <code>1:{rr2:.1f}</code> | <b>Score:</b> <code>{smc_data['score']}/{cfg['score_pass']}</code>"
    )

    try:
        sent = bot.send_message(VIP_CHANNEL_ID, msg, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
        record = {
            "contract": sym, "symbol": sym, "network": "GATEIO",
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "rr1": round(rr1, 2), "rr2": round(rr2, 2),
            "setup": smc_data["setup"], "fibo_zone": smc_data["fib_zone"],
            "score": smc_data["score"], "volume_24h": vol_24h,
            "msg_id": sent.message_id, "sent_at": int(time.time())
        }
        save_signal(record)
        add_cooldown(sym)
        print(f"[SIGNAL SENT ✅] {sym} | {smc_data['setup']} | Score: {smc_data['score']}")
        return True
    except Exception as e:
        alert_admin(f"Gagal hantar signal {sym}: {e}")
        return False
# =================================================================
# 6. SCANNER & TRADE MONITOR (VERBOSE LOGGING)
# =================================================================
def scan_once():
    """Satu kitaran scan menggunakan Gate.io API."""
    if not IS_SCANNING:
        return
    
    # ── CIRCUIT BREAKER BTC ─────────────────────────────────────
    btc_chg = get_btc_24h_change()
    is_btc_bleeding = btc_chg < -4.0
    print(f"[BTC] 24H Change: {btc_chg:+.2f}% | Alert: {is_btc_bleeding}")
    # ── TAMAT CIRCUIT BREAKER ───────────────────────────────────

    cfg = get_config()
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")

    print(f"\n{'='*60}")
    print(f" [{datetime.now().strftime('%H:%M:%S')}] SCAN DIMULAKAN | Preset: {preset_lbl}")
    print(f"{'='*60}")

    tickers = get_gateio_tickers()
    print(f"[GATEIO] Total {len(tickers)} pairs USDT ditemui")

    # Safety Net: Tapis Volume 24H
    candidates = [t for t in tickers if t["volume_24h"] >= cfg["min_vol_24h"]]
    rejected_vol = len(tickers) - len(candidates)
    print(f"[SAFETY NET] {len(candidates)} pairs lulus Min Vol ${cfg['min_vol_24h']/1e6:.1f}M | {rejected_vol} ditolak (volume rendah / coin mati)")

    passed = 0
    skipped_reasons = {"cooldown": 0, "active": 0, "no_data": 0, "no_setup": 0, "score_low": 0, "blacklisted": 0}

    for t in candidates:
        sym = t["symbol"]
        current_price = t.get("last_price", 0)

        # ── BARU: SKIP STABLECOIN/WRAPPED/LEVERAGED TOKEN ─────
        is_blacklisted, bl_reason = is_blacklisted_symbol(sym)
        if is_blacklisted:
            skipped_reasons["blacklisted"] += 1
            continue
        # ── TAMAT SKIP STABLECOIN ─────────────────────────────

        # Semak cooldown dengan OVERRIDE (second chance entry)
        if is_in_cooldown(sym):
            if check_cooldown_override(sym, current_price):
                print(f"[{sym}] 🔄 OVERRIDE: Cooldown di-reset (harga turun >20% dari entry)")
                # Teruskan scan — jangan continue
            else:                skipped_reasons["cooldown"] += 1
                continue

        # Active trade check
        active = get_active_trades()
        if sym in active:
            skipped_reasons["active"] += 1
            continue

        # Ambil H1 Candles
        candles = get_gateio_klines(sym, "1h", 50)
        if len(candles) < 30:
            skipped_reasons["no_data"] += 1
            print(f"[{sym}] ❌ REJECT: Data candle tidak cukup ({len(candles)}/30)")
            continue

        print(f"\n[{sym}] 🔎 ANALYZING... Vol24H: ${t['volume_24h']/1e6:.2f}M")

        # Analisis SMC (verbose=True untuk log terperinci)
        smc = analyze_smc_pa(candles, sym, verbose=True)

        if not smc:
            skipped_reasons["no_setup"] += 1
            continue

        if smc["score"] < cfg["score_pass"]:
            skipped_reasons["score_low"] += 1
            continue

        # Hantar signal dengan BTC warning
        if send_signal(sym, smc, t["volume_24h"], btc_chg=btc_chg):
            passed += 1
            time.sleep(2)

        time.sleep(0.2)  # Rate limit Gate.io

    print(f"\n{'='*60}")
    print(f"📊 SCAN SELESAI | {passed} signal dihantar")
    print(f"⏭️  Skip reasons: Blacklisted={skipped_reasons['blacklisted']}, Cooldown={skipped_reasons['cooldown']}, Active={skipped_reasons['active']}, "
          f"NoData={skipped_reasons['no_data']}, NoSetup={skipped_reasons['no_setup']}, ScoreLow={skipped_reasons['score_low']}")
    print(f"{'='*60}\n")

def monitor_active_trades():
    active = get_active_trades()
    if not active: return

    for sym, trade in active.items():
        try:
            candles = get_gateio_klines(sym, "1h", 5)
            if not candles: continue            cp = candles[-1]['c']

            # Ambil msg_id dari signal asal
            mid = trade.get("msg_id")

            def notify(text):
                """Hantar sebagai REPLY ke signal asal."""
                kw = {"parse_mode": "HTML"}
                if mid:
                    kw["reply_to_message_id"] = mid
                try:
                    bot.send_message(VIP_CHANNEL_ID, text, **kw)
                except Exception as e:
                    # Fallback: hantar standalone jika reply gagal
                    print(f"[MONITOR] Reply gagal untuk {sym}: {e}")
                    bot.send_message(VIP_CHANNEL_ID, text, parse_mode="HTML")

            updates = {}

            if cp >= trade["tp1"] and not trade.get("tp1_hit"):
                updates["tp1_hit"] = True
                notify(
                    f"✅ <b>{sym} — TP1 HIT!</b>\n"
                    f"💰 Harga: <code>${fmt(cp)}</code>\n"
                    f" Alih SL → BE: <code>${fmt(trade['entry'])}</code>"
                )

            if cp >= trade["tp2"] and not trade.get("tp2_hit"):
                updates["tp2_hit"] = True
                notify(
                    f"🚀 <b>{sym} — TP2 HIT!</b>\n"
                    f"💰 Harga: <code>${fmt(cp)}</code>\n"
                    f"📈 Trail SL → TP1: <code>${fmt(trade['tp1'])}</code>"
                )

            if cp >= trade["tp3"] and not trade.get("tp3_hit"):
                updates["tp3_hit"] = True
                updates["closed"] = True
                profit_pct = (cp - trade["entry"]) / trade["entry"] * 100
                notify(
                    f" <b>{sym} — TP3 MOONSHOT!</b>\n"
                    f"💰 Tutup: <code>${fmt(cp)}</code>\n"
                    f"📊 Profit: <code>+{profit_pct:.1f}%</code>\n"
                    f"🎯 Trade ditutup dengan jayanya."
                )

            elif cp <= trade["sl"] and not trade.get("sl_hit"):
                updates["sl_hit"] = True
                updates["closed"] = True
                loss_pct = (cp - trade["entry"]) / trade["entry"] * 100                notify(
                    f"❌ <b>{sym} — SL HIT</b>\n"
                    f"💰 Tutup: <code>${fmt(cp)}</code>\n"
                    f"📉 Loss: <code>{loss_pct:.1f}%</code>\n"
                    f"️ Setup invalidated."
                )

            if updates:
                update_signal(sym, updates)
                print(f"[MONITOR] {sym}: {list(updates.keys())}")

        except Exception as e:
            print(f"[MONITOR] {sym}: {e}")

# =================================================================
# 7. TELEGRAM COMMANDS
# =================================================================
IS_SCANNING = True

@bot.message_handler(commands=["start", "menu"])
def cmd_start(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    cfg = get_config()
    active = get_active_trades()
    uptime_m = int((time.time() - START_TIME) / 60)
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")

    text = (
        f"🏴‍️ <b>ALPHA — Gate.io SMC Sniper</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Uptime   : <code>{uptime_m}m</code>\n"
        f"📊 Trade   : <code>{len(active)} aktif</code>\n"
        f"🔧 Scan    : <code>{'✅ AKTIF' if IS_SCANNING else '⛔ STOP'}</code>\n\n"
        f"🎛️ <b>Preset Aktif:</b>\n<code>{preset_lbl}</code>\n\n"
        f"<b>🛡️ Parameter Semasa:</b>\n"
        f"├ Min Vol 24H : <code>${cfg['min_vol_24h']/1e6:.1f}M</code>\n"
        f"├ Score Pass  : <code>{cfg['score_pass']}</code>\n"
        f"├ Cooldown    : <code>{cfg['cooldown_hours']}h</code>\n"
        f"└ Timeframe   : <code>H1 (Close Confirm)</code>\n\n"
        f"<b>🧠 Enjin Setup (7):</b>\n"
        f"1. Breakout Retest | 2. Trend Pullback\n"
        f"3. Order Block | 4. VPA | 5. Reversal\n"
        f"6. Bull Flag | 7. Liquidity Sweep ⭐"
    )
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🟢 Soft", callback_data="tune:soft"),
        InlineKeyboardButton("🟡 Standard", callback_data="tune:standard"),
        InlineKeyboardButton("🔴 Hard", callback_data="tune:hard")
    )    kb.add(
        InlineKeyboardButton("▶️ Mula", callback_data="scan_on"),
        InlineKeyboardButton(" Henti", callback_data="scan_off"),
        InlineKeyboardButton("📓 Journal", callback_data="journal")
    )
    kb.add(
        InlineKeyboardButton("📊 Status", callback_data="status"),
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
        text = (
            f"✅ <b>PRESET DIAPLIKASI</b>\n\n{lbl}\n\n"
            f"├ Min Vol : <code>${p['min_vol_24h']/1e6:.1f}M</code>\n"
            f"└ Pass    : <code>{p['score_pass']}/4</code>\n\n"
            f"<i>Scan seterusnya akan guna preset ini.</i>"
        )
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML")
        except: pass
        alert_admin(f"🎛️ Preset: {lbl}")

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
            f"🟢 <code>/tune soft</code> — Vol $500K, Pass 2\n"
            f"🟡 <code>/tune standard</code> — Vol $1M, Pass 3\n"
            f"🔴 <code>/tune hard</code> — Vol $2.5M, Pass 4"
        )
        bot.reply_to(msg, text, parse_mode="HTML")
        return
    ok, lbl = apply_preset(parts[1].lower())
    bot.reply_to(msg, f"✅ {lbl}" if ok else "❌ Preset tidak sah")

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
        candles = get_gateio_klines(sym, "1h", 50)
        if len(candles) < 30:
            bot.send_message(msg.chat.id, f" {sym}: Data candle tidak cukup", parse_mode="HTML")
            return
        smc = analyze_smc_pa(candles, sym, verbose=False)
        if smc:
            bot.send_message(msg.chat.id,
                f"✅ <b>{sym}</b>\nSetup: <code>{smc['setup']}</code>\nScore: <code>{smc['score']}</code>\nZone: <code>{smc['fib_zone']}</code>",
                parse_mode="HTML")
        else:
            bot.send_message(msg.chat.id, f"❌ {sym}: Tiada setup SMC di Discount Zone", parse_mode="HTML")
    threading.Thread(target=_do).start()

@bot.message_handler(commands=["scan"])
def cmd_scan(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    bot.reply_to(msg, "⚙️ Scan dipaksa...")
    threading.Thread(target=scan_once).start()

@bot.message_handler(commands=["status"])
def cmd_status(msg):    if str(msg.chat.id) != str(ADMIN_ID): return
    cfg = get_config()
    active = get_active_trades()
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")
    bot.reply_to(msg, (
        f"📊 <b>STATUS</b>\n"
        f"Scan  : {'🟢 AKTIF' if IS_SCANNING else '🔴 STOP'}\n"
        f"Trade : <code>{len(active)}</code> aktif\n\n"
        f"🎛️ Preset: <code>{preset_lbl}</code>\n"
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
        "/status         — Status semasa\n\n"
        "🎛️ <b>TUNE PRESET:</b>\n"
        "/tune           — Papar menu\n"
        "/tune soft      — Longgar\n"
        "/tune standard  — Balance\n"
        "/tune hard      — Ketat\n\n"
        "<i>Bot akan explain WHY signal lulus/gagal di Render Logs.</i>"
    ), parse_mode="HTML")

# =================================================================
# 8. JOURNAL
# =================================================================
def generate_journal():
    trades = get_signals_since(7)
    if not trades:
        return " <b>JOURNAL (7D)</b>\n\nTiada signal dalam 7 hari lepas."

    total = len(trades)
    tp1_n = sum(1 for t in trades if t.get("tp1_hit"))
    tp2_n = sum(1 for t in trades if t.get("tp2_hit"))
    tp3_n = sum(1 for t in trades if t.get("tp3_hit"))
    sl_n = sum(1 for t in trades if t.get("sl_hit"))
    open_n = sum(1 for t in trades if not t.get("closed"))
    # Setup breakdown
    setups = {}
    for t in trades:
        s = t.get("setup", "Unknown")
        setups[s] = setups.get(s, 0) + 1
    setup_str = " | ".join(f"{k}: {v}" for k, v in sorted(setups.items(), key=lambda x: -x[1]))

    wr = tp1_n / total * 100 if total else 0

    return (
        f" <b>ALPHA JOURNAL (7D)</b>\n\n"
        f"├ Total Signal : <code>{total}</code>\n"
        f"├ TP1 Hit      : <code>{tp1_n} ({wr:.0f}%)</code>\n"
        f"├ TP2 Hit      : <code>{tp2_n}</code>\n"
        f"├ TP3 Moonshot : <code>{tp3_n}</code>\n"
        f"├ SL Hit       : <code>{sl_n}</code>\n"
        f"└ Masih Buka   : <code>{open_n}</code>\n\n"
        f"<b>🧠 Setup Breakdown:</b>\n<code>{setup_str}</code>"
    )

# =================================================================
# 9. SCHEDULER & MAIN
# =================================================================
def run_scheduler():
    schedule.every(15).minutes.do(lambda: threading.Thread(target=scan_once).start())
    schedule.every(5).minutes.do(lambda: threading.Thread(target=monitor_active_trades).start())
    while True:
        schedule.run_pending()
        time.sleep(30)

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALPHA GATEIO SMC ACTIVE")
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
        "‍☠️ ALPHA Gate.io SMC Sniper DEPLOYED\n"        f"Preset: {PRESETS[get_config()['active_preset']]['label']}\n"
        "/tune untuk ubah preset\n"
        "📊 Monitor Render Logs untuk WHY pass/fail"
    )
    threading.Thread(target=scan_once).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
