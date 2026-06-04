"""
ALPHA — Gate.io Spot SMC & Price Action Sniper (H1)
Edge: Buy the dip at Discount Zone (Fib 0.5-0.786), target Moonshot TP.
7 Setup Engine + VPA Filter + Verbose Render Logging.
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
# 3. HELPER & GATE.IO API
# =================================================================
def fmt(val):
    if val == 0: return "0.00"
    if abs(val) < 0.000001: return f"{val:.10f}"
    if abs(val) < 0.001: return f"{val:.8f}"
    if abs(val) < 1.0: return f"{val:.6f}"
    if abs(val) < 1000: return f"{val:.4f}"
    return f"{val:,.2f}"

def get_gateio_tickers():
    """Ambil semua pair USDT di Gate.io beserta Volume 24H"""
    try:
        r = requests.get("https://api.gateio.ws/api/v4/spot/tickers", timeout=10).json()
        pairs = []
        for t in r:
            if t['currency_pair'].endswith('_USDT'):
                sym = t['currency_pair'].replace('_USDT', '')
                vol = float(t.get('quote_volume', 0))
                pairs.append({"symbol": sym, "volume_24h": vol, "pair": t['currency_pair']})
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
    """Mengesan 7 Setup SMC/PA di H1. Syarat Wajib: Fib Discount Zone."""
    log = lambda msg: print(f"[{sym}] {msg}") if verbose else None

    if len(candles) < 30:
        log("❌ REJECT: Data candle < 30")
        return None

    highs = [c['h'] for c in candles[-30:]]
    lows = [c['l'] for c in candles[-30:]]
    closes = [c['c'] for c in candles[-30:]]
    volumes = [c['v'] for c in candles[-30:]]

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
        if price > fib_500:
            log(f"❌ REJECT: Price ${fmt(price)} di PREMIUM ZONE (FOMO)")
        else:
            log(f"❌ REJECT: Price ${fmt(price)} di EXTREME (falling knife)")
        return None
    log(f"✅ FIBO PASS: Price ${fmt(price)} dalam DISCOUNT ({fib_zone})")

    ema20 = sum(closes[-20:]) / 20
    if price < ema20 * 0.90:
        log(f"❌ REJECT: Terlalu jauh bawah EMA20 ({fmt(ema20)})")
        return None

    avg_vol = sum(volumes[-20:]) / 20 if sum(volumes[-20:]) > 0 else 1
    curr_vol = curr['v']
    vpa_dry = curr_vol < (avg_vol * 0.8)

    setup_name = None
    score = 0

    # SETUP 7: LIQUIDITY SWEEP
    if curr['l'] < swing_low and curr['c'] > swing_low:
        setup_name = "💧 LIQUIDITY SWEEP (God Tier)"
        score += 3
        log(f"✅ SETUP 7: Sweep Swing Low ${fmt(swing_low)}")

    # SETUP 5: REVERSAL CANDLE
    body = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    is_pinbar = lower_wick > (body * 2) and curr['c'] > curr['o']
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o'] and
                    curr['c'] > prev['o'] and curr['o'] < prev['c'])

    if is_pinbar:
        if not setup_name: setup_name = "🕯️ PINBAR REVERSAL"
        score += 2
        log("✅ SETUP 5: Pinbar detected")
    elif is_engulfing:
        if not setup_name: setup_name = "🐂 BULLISH ENGULFING"
        score += 2
        log("✅ SETUP 5: Bullish Engulfing detected")

    # SETUP 4: VPA
    if vpa_dry:
        score += 1
        log(f"✅ VPA PASS: Vol {curr_vol:,.0f} < 80% avg")
    else:
        log(f"⚠️  VPA WEAK: Vol tinggi ({curr_vol:,.0f})")

    # SETUP 2: TREND PULLBACK
    if abs(price - ema20) / ema20 < 0.015:
        if not setup_name: setup_name = "📈 TREND PULLBACK (HH/HL)"
        score += 1
        log("✅ SETUP 2: Pullback ke EMA20")

    # SETUP 3: ORDER BLOCK
    for i in range(-15, -3):
        try:
            c = candles[i]
            c_next = candles[i+1]
            if c['c'] < c['o'] and c_next['c'] > c_next['o']:
                bos_size = c_next['c'] - c_next['o']
                if bos_size > (rng * 0.08) and c['l'] <= price <= c['h']:
                    if not setup_name: setup_name = "🧱 ORDER BLOCK (SMC)"
                    score += 1
                    log(f"✅ SETUP 3: Price dalam OB")
                    break
        except: pass

    if not setup_name or score < 2:
        log(f"❌ REJECT: Tiada setup kukuh (Score: {score})")
        return None

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

    log(f"🎯 COMPLETE: {setup_name} | Score: {score} | RR: 1:{rr2:.1f}")

    return {
        "setup": setup_name,
        "entry": price, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": rr1, "rr2": rr2,
        "score": score,
        "fib_zone": fib_zone
    }

# =================================================================
# 5. SIGNAL GENERATOR
# =================================================================
def send_signal(sym, smc_data, vol_24h):
    cfg = get_config()
    if smc_data["score"] < cfg["score_pass"]:
        print(f"[{sym}] ❌ REJECT FINAL: Score {smc_data['score']}/{cfg['score_pass']}")
        return False

    entry = smc_data["entry"]
    sl = smc_data["sl"]
    tp1, tp2, tp3 = smc_data["tp1"], smc_data["tp2"], smc_data["tp3"]
    rr1, rr2 = smc_data["rr1"], smc_data["rr2"]

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 Gate.io", url=f"https://www.gate.io/trade/{sym}_USDT"),
        InlineKeyboardButton("📈 TradingView", url=f"https://www.tradingview.com/chart/?symbol=GATEIO:{sym}USDT")
    )

    msg = (
        f"🏴‍☠️ <b>ALPHA SMC SNIPER — {sym}/USDT</b>\n\n"
        f"┌ <b>STRUKTUR SMC (H1)</b>\n"
        f"├ Setup : <code>{smc_data['setup']}</code>\n"
        f"├ Zone  : <code>DISCOUNT ({smc_data['fib_zone']})</code>\n"
        f"├ Skor  : <code>{smc_data['score']}/{cfg['score_pass']} Confluence</code>\n"
        f"└ Vol24H: <code>${vol_24h/1e6:.2f}M</code>\n\n"
        f"🎯 <b>TRADE SETUP (MOONSHOT)</b>\n"
        f"• ENTRY : <code>${fmt(entry)}</code>\n"
        f"• SL    : <code>${fmt(sl)}</code> [{(entry-sl)/entry*100:.1f}%]\n"
        f"• TP1   : <code>${fmt(tp1)}</code> (Swing High | RR 1:{rr1:.1f})\n"
        f"• TP2   : <code>${fmt(tp2)}</code> (Fib 1.618 | RR 1:{rr2:.1f})\n"
        f"• TP3   : <code>${fmt(tp3)}</code> (Fib 2.618 | 🌕 Moonbag)\n\n"
        f"🦅 <i>Edge: Whale trap retail SL di Discount Zone.</i>"
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
# 6. SCANNER & TRADE MONITOR
# =================================================================
def scan_once():
    if not IS_SCANNING: return
    cfg = get_config()
    preset_lbl = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")

    print(f"\n{'='*60}")
    print(f"🔍 [{datetime.now().strftime('%H:%M:%S')}] SCAN | Preset: {preset_lbl}")
    print(f"{'='*60}")

    tickers = get_gateio_tickers()
    print(f"[GATEIO] Total {len(tickers)} pairs USDT")

    candidates = [t for t in tickers if t["volume_24h"] >= cfg["min_vol_24h"]]
    rejected_vol = len(tickers) - len(candidates)
    print(f"[SAFETY NET] {len(candidates)} lulus Min Vol ${cfg['min_vol_24h']/1e6:.1f}M | {rejected_vol} ditolak")

    passed = 0
    skipped = {"cooldown": 0, "active": 0, "no_data": 0, "no_setup": 0, "score_low": 0}

    for t in candidates:
        sym = t["symbol"]

        if is_in_cooldown(sym):
            skipped["cooldown"] += 1
            continue

        if sym in get_active_trades():
            skipped["active"] += 1
            continue

        candles = get_gateio_klines(sym, "1h", 50)
        if len(candles) < 30:
            skipped["no_data"] += 1
            continue

        print(f"\n[{sym}] 🔎 ANALYZING... Vol24H: ${t['volume_24h']/1e6:.2f}M")
        smc = analyze_smc_pa(candles, sym, verbose=True)

        if not smc:
            skipped["no_setup"] += 1
            continue

        if smc["score"] < cfg["score_pass"]:
            skipped["score_low"] += 1
            continue

        if send_signal(sym, smc, t["volume_24h"]):
            passed += 1
            time.sleep(2)

        time.sleep(0.2)

    print(f"\n{'='*60}")
    print(f"📊 SELESAI | {passed} signal dihantar")
    print(f"⏭️  Skip: Cooldown={skipped['cooldown']}, Active={skipped['active']}, "
          f"NoData={skipped['no_data']}, NoSetup={skipped['no_setup']}, ScoreLow={skipped['score_low']}")
    print(f"{'='*60}\n")

def monitor_active_trades():
    active = get_active_trades()
    if not active: return

    for sym, trade in active.items():
        try:
            candles = get_gateio_klines(sym, "1h", 5)
            if not candles: continue
            cp = candles[-1]['c']

            updates = {}
            if cp >= trade["tp1"] and not trade.get("tp1_hit"):
                updates["tp1_hit"] = True
                bot.send_message(VIP_CHANNEL_ID, f"✅ <b>{sym}</b> TP1! SL → BE: <code>${fmt(trade['entry'])}</code>", parse_mode="HTML")

            if cp >= trade["tp2"] and not trade.get("tp2_hit"):
                updates["tp2_hit"] = True
                bot.send_message(VIP_CHANNEL_ID, f"🚀 <b>{sym}</b> TP2! Trail SL → TP1", parse_mode="HTML")

            if cp >= trade["tp3"] and not trade.get("tp3_hit"):
                updates["tp3_hit"] = True
                updates["closed"] = True
                bot.send_message(VIP_CHANNEL_ID, f"🏆 <b>{sym}</b> TP3 MOONSHOT!", parse_mode="HTML")

            elif cp <= trade["sl"] and not trade.get("sl_hit"):
                updates["sl_hit"] = True
                updates["closed"] = True
                bot.send_message(VIP_CHANNEL_ID, f"❌ <b>{sym}</b> SL HIT.", parse_mode="HTML")

            if updates: update_signal(sym, updates)
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
        f"🏴‍☠️ <b>ALPHA — Gate.io SMC Sniper</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Uptime   : <code>{uptime_m}m</code>\n"
        f"📊 Trade   : <code>{len(active)} aktif</code>\n"
        f"🔧 Scan    : <code>{'✅ AKTIF' if IS_SCANNING else '⛔ STOP'}</code>\n\n"
        f"🎛️ <b>Preset:</b> <code>{preset_lbl}</code>\n\n"
        f"<b>🛡️ Parameter:</b>\n"
        f"├ Min Vol 24H : <code>${cfg['min_vol_24h']/1e6:.1f}M</code>\n"
        f"├ Score Pass  : <code>{cfg['score_pass']}</code>\n"
        f"└ Timeframe   : <code>H1 (Close Confirm)</code>"
    )
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🟢 Soft", callback_data="tune:soft"),
        InlineKeyboardButton("🟡 Standard", callback_data="tune:standard"),
        InlineKeyboardButton("🔴 Hard", callback_data="tune:hard")
    )
    kb.add(
        InlineKeyboardButton("▶️ Mula", callback_data="scan_on"),
        InlineKeyboardButton("⏸ Henti", callback_data="scan_off"),
        InlineKeyboardButton("📓 Journal", callback_data="journal")
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
        text = f"✅ <b>PRESET DIAPLIKASI</b>\n\n{lbl}\n\n├ Min Vol: <code>${p['min_vol_24h']/1e6:.1f}M</code>\n└ Pass: <code>{p['score_pass']}</code>"
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML")
        except: pass
        alert_admin(f"🎛️ Preset: {lbl}")

@bot.callback_query_handler(func=lambda c: c.data in ["scan_on", "scan_off", "journal"])
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
        bot.reply_to(msg, "❌ /pair [SYMBOL]")
        return
    sym = parts[1].upper()
    bot.reply_to(msg, f"🔍 Analyzing <code>{sym}</code>...", parse_mode="HTML")

    def _do():
        candles = get_gateio_klines(sym, "1h", 50)
        if len(candles) < 30:
            bot.send_message(msg.chat.id, f"❌ {sym}: Data < 30 candles", parse_mode="HTML")
            return
        smc = analyze_smc_pa(candles, sym, verbose=False)
        if smc:
            bot.send_message(msg.chat.id,
                f"✅ <b>{sym}</b>\nSetup: <code>{smc['setup']}</code>\nScore: <code>{smc['score']}</code>",
                parse_mode="HTML")
        else:
            bot.send_message(msg.chat.id, f"❌ {sym}: No SMC setup in Discount Zone", parse_mode="HTML")
    threading.Thread(target=_do).start()

@bot.message_handler(commands=["scan"])
def cmd_scan(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    bot.reply_to(msg, "⚙️ Scan dipaksa...")
    threading.Thread(target=scan_once).start()

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
        "/scan           — Paksa scan\n"
        "/pair [SYM]     — Analisis manual\n"
        "/journal        — Laporan 7 hari\n\n"
        "🎛️ <b>TUNE:</b>\n"
        "/tune soft | standard | hard"
    ), parse_mode="HTML")

def generate_journal():
    trades = get_signals_since(7)
    if not trades:
        return "📓 <b>JOURNAL (7D)</b>\n\nTiada signal dalam 7 hari."

    total = len(trades)
    tp1_n = sum(1 for t in trades if t.get("tp1_hit"))
    tp2_n = sum(1 for t in trades if t.get("tp2_hit"))
    tp3_n = sum(1 for t in trades if t.get("tp3_hit"))
    sl_n = sum(1 for t in trades if t.get("sl_hit"))
    open_n = sum(1 for t in trades if not t.get("closed"))
    wr = tp1_n / total * 100 if total else 0

    setups = {}
    for t in trades:
        s = t.get("setup", "Unknown")
        setups[s] = setups.get(s, 0) + 1
    setup_str = " | ".join(f"{k}: {v}" for k, v in sorted(setups.items(), key=lambda x: -x[1]))

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
# 8. SCHEDULER & MAIN
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
        "🏴‍☠️ ALPHA Gate.io SMC Sniper DEPLOYED\n"
        f"Preset: {PRESETS[get_config()['active_preset']]['label']}\n"
        "/tune untuk ubah preset"
    )
    threading.Thread(target=scan_once).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)