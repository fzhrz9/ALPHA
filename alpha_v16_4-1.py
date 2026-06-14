"""
ALPHA — Gate.io Triple Engine Sniper v16.4 JOURNAL-JUJUR

v16.4 JOURNAL TELUS (jawab: profitable atau tidak?):
  Masalah lama: counter TP/SL/BE double-count trade sama (satu trade boleh
  ada sl_hit + tp1_hit serentak dari bug wick) → WR & jumlah MUSTAHIL
  (TP1 36 + SL 38 = 74 'closed' walhal hanya 41 closed). Session/Whale
  papar "None: 82" sebab kolum belum di-migrate.

  Baharu: realized_r() kira R SEBENAR setiap trade (model skala 50/30/20,
  SL→BE), outcome MUTUALLY EXCLUSIVE ikut keutamaan (TP3>TP2>TP1>BE>SL>Open).
  Metrik sebenar: Expectancy R/trade, Profit Factor, equity $. Verdict jujur
  ikut sampel (≥30 baru sahkan). Session/Whale degrade elok + nota migration.

================== v16.3 BASE ==================

v16.3 UI PREMIUM MINIMAL:
  GRED sistem (💎A+ / 🟢A / 🟡B / 🟠C) — satu huruf gabung syarat+EV+risiko,
  ganti Quality bar & Score mentah yang mengelirukan (0/10 + Score -2).
  Checklist syarat 1-baris: ✅Zon ✅EMA ✅Candle ⬜Whale ⬜Sesi.
  Baris amaran risiko HANYA bila wujud: 💀 Sesi mati · 🐳 Whale agih · 🔄 Retak.
  Kad signal 12 baris (dulu ~20). Baris kosong 🔗 dibuang. /pair design sama.

================== v16.2 BASE ==================

v16.2 SISTEM 5 SYARAT (≥3/5 lulus = signal):
  Falsafah baru: TIADA lagi rantaian hard-reject bersiri (AND logic) yang
  memerlukan SEMUA filter lulus. Setiap engine kini nilai 5 SYARAT TERAS dan
  hantar signal jika ≥3/5 lulus (preset hard: 4/5). Hard reject dikekalkan
  HANYA untuk bahaya sebenar: downtrend mati, falling knife >18% bawah EMA,
  trend bearish kuat (ADX≥25 -DI), breakout tanpa volume (<1.3x), CLV negatif.

  E1 PULLBACK : Zon Fib+Struktur | Trend EMA | Trigger Candle | Smart Money | Sesi+Selamat
  E2 MOMENTUM : Vol Kuat | Akumulasi | Trigger Candle | Bias H4/Whale | Sesi+Selamat
  E3 BREAKOUT : Golden Cross | ADX Directional | Volume Climax | RSI Momentum | Sesi+Disp
                (dead zone: perlu 4/5 — fakeout risk tinggi)

  Kalibrasi whale dari log production: A/D slope -0.08..-0.25 = LEAN_BEARISH
  (bukan DISTRIBUTING), DISTRIBUTING hanya score ≤ -3. EMA distance 10-18%
  bawah = syarat gagal sahaja (deep discount swing masih boleh layak).

  UI: Checklist ✅/⬜ 5 syarat dipaparkan dalam setiap signal & /pair.
  P(win) kini ambil kira syarat_pass. RRnet floor 1.3→1.2.

================== v16.1 BASE ==================

v16.1 EMERGENCY FIXES (punca spam BE-EXIT + zero signal):
  [RC-1] update_signal RESILIENT — 3-tier fallback (full→core→closed-only).
         Punca spam: kolum be_hit/p_win tiada dalam Supabase → SELURUH update
         gagal → closed tak pernah saved → notify ulang tiap 5 min.
  [RC-2] should_notify() in-memory dedupe — JAMINAN tiada spam walau DB gagal
  [RC-3] Monitor auto-close trade blacklisted legacy (USDC stablecoin dll)
  [RC-4] Scan loop per-symbol try/except — 1 error TIDAK bunuh seluruh scan
         (Punca zero signal: 1 exception = scan mati setiap cycle)
  [RC-5] Auto-close trade zombie > 7 hari
  [RC-6] save_signal resilient — fallback ke kolum legacy jika schema lama
  [RC-7] REJECT_STATS + /diag — nampak KENAPA tiada signal (transparency)
  [RC-8] MAX_SCAN_SYMBOLS=150 top-volume — scan siap dalam masa munasabah

⚠️ WAJIB: Jalankan SQL ini dalam Supabase SQL Editor (sekali sahaja):
─────────────────────────────────────────────────────────────────
ALTER TABLE signals ADD COLUMN IF NOT EXISTS be_hit     BOOLEAN DEFAULT FALSE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS tp3_hit    BOOLEAN DEFAULT FALSE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS p_win      DOUBLE PRECISION;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS ev         DOUBLE PRECISION;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS session    TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS whale      TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS confluence TEXT;
UPDATE signals SET closed = TRUE WHERE closed = FALSE;  -- bersihkan legacy
─────────────────────────────────────────────────────────────────
(Bot tetap berfungsi TANPA SQL ini berkat fallback, tapi data lebih lengkap dengannya)

================== v16.0 BASE ==================
Engine 1: Pullback SMC (H1+H4) | Engine 2: Momentum (M15) | Engine 3: Breakout (H1)
Mode: /mode pullback | momentum | breakout | both | all

v16.0: DEBUG + SECURITY + REAL-MATH OPTIMIZATION
=================================================
[DEBUG FIXES — 13]
  [BUG-1]  Premium override None crash → hard guard: reject jika tiada fib pair
  [BUG-2]  whale_sig dir() hack → initialize "NEUTRAL" sebelum try
  [BUG-3]  touches division → None/zero guard + ATR-scaled tolerance
  [BUG-4]  PULLBACK_WATCHLIST read dalam send_signal → guna _watchlist_lock
  [BUG-5]  ATR → Wilder RMA sebenar: ATR_t = (ATR_{t-1}·(n-1) + TR_t)/n
  [BUG-6]  CHOCH dalam SIDEWAY dibuang — compression break = normal, bukan reversal
  [BUG-7]  CHOCH + ATR buffer (0.25·ATR) — elak wick noise trigger
  [BUG-8]  ADX + Wilder directional rule: ADX≥18 DAN +DI > -DI
  [BUG-9]  Volume anomaly → Z-score statistik: z=(v-μ)/σ, z≥2.0 (95.4 percentile)
  [BUG-10] Fee-adjusted RR: RR_net=(TP-E-fees)/((E-SL)+fees), Gate.io 0.2%/side
  [BUG-11] Session docstring/boundary diselaraskan
  [BUG-12] Whale dry-pullback → least-squares regression slope (bukan first/last)
  [BUG-13] Monitor guna candle HIGH/LOW + SL-first conservative rule

[SECURITY FIXES — 6]
  [SEC-1] Callback auth → call.from_user.id (bukan chat.id) — halang button hijack
  [SEC-2] sanitize_symbol() — alphanumeric only sebelum URL/HTML
  [SEC-3] alert_admin → html.escape + truncate, tiada token leak
  [SEC-4] /modal bound: $10 ≤ modal ≤ $10,000,000
  [SEC-5] Polling restart wrapper — auto-recover dari network crash
  [SEC-6] api_get() — retry 3x + exponential backoff + 429 handling

[REAL-MATH OPTIMIZATION — Low Threshold + High Accuracy]
  Falsafah: TURUNKAN threshold individu (lebih banyak calon lepas),
  tapi GATE dengan Expected Value — hanya entry jika expectancy positif.
  ─────────────────────────────────────────────────────────────────
  [OPT-1] Volume Z-score ≥ 2.0 ATAU ratio ≥ 2.5 (dulu: fixed 3.0x)
  [OPT-2] RSI ≥ 50 + slope > 0 (dulu: ≥55) — derivative momentum confirm
  [OPT-3] ADX ≥ 18 + (+DI > -DI) (dulu: ≥20 tanpa arah)
  [OPT-4] Vol climax ≥ 2.0x + z ≥ 1.5 (dulu: 2.5x)
  [OPT-5] Sweep wick ≥ 1.5 + close_pos ≥ 0.6 + CLV ≥ 0.2 (dulu: wick ≥2.0)
  [OPT-6] Fib zone ± 0.25·ATR tolerance band (dulu: binary in/out)
  [OPT-7] CLV/Chaikin A/D whale proxy: CLV=((C-L)-(H-C))/(H-L), A/D=Σ CLV·V
  [OPT-8] P(win) logistic: p = 0.32 + 0.46/(1+e^{-0.55(s-5)}) — calibrated estimate
  [OPT-9] EV gate: EV = p·RR_net - (1-p) ≥ 0.15 — positive expectancy WAJIB
  [OPT-10] Preset pass diturunkan: soft 2 / standard 2 / hard 3 (EV gate jaga kualiti)

[UI OPTIMUM]
  Quality bar ▰▰▰▰▱, P(win)%, EV dalam R, net-RR selepas fees,
  institutional line padat, journal per-engine/session/whale win-rate.
"""

import os, time, html, math, re as _re
import requests, threading, schedule
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

FEE_PER_SIDE       = 0.002   # Gate.io spot taker 0.2% — [BUG-10]
MIN_EV             = 0.10    # [OPT-9] EV gate (v16.1: 0.15→0.10, recalibrate dari journal)

_watchlist_lock    = threading.Lock()
_scan_lock         = threading.Lock()
IS_SCANNING_ACTIVE = False

# ── [v16.1] Resilience & Observability ──
NOTIFIED       = {}      # [RC-2] dedupe notifikasi (sym:event → ts)
CLOSED_LOCAL   = set()   # [RC-1] fallback closed-state dalam memori
REJECT_STATS   = {}      # [RC-7] kiraan sebab reject per scan
LAST_SCAN_INFO = {}      # [RC-7] heartbeat scan terakhir
MAX_SCAN_SYMBOLS = 150   # [RC-8] had simbol per scan (top volume)

def should_notify(sym, event, ttl=7 * 86400):
    """[RC-2] Dedupe — JAMIN setiap event (sym, jenis) dinotify SEKALI sahaja,
    walaupun DB write gagal. Lapisan pertahanan kedua selepas DB."""
    key = f"{sym}:{event}"
    now = time.time()
    if now - NOTIFIED.get(key, 0) < ttl:
        return False
    NOTIFIED[key] = now
    return True

def bump(reason):
    """[RC-7] Kira sebab reject untuk diagnostik /diag."""
    REJECT_STATS[reason] = REJECT_STATS.get(reason, 0) + 1

def safe_html(text, limit=700):
    """[SEC-3] Escape HTML + truncate — elak token/path leak & parse break."""
    return html.escape(str(text)[:limit])

def sanitize_symbol(sym):
    """[SEC-2] Alphanumeric+underscore sahaja — elak URL/HTML injection."""
    return _re.sub(r'[^A-Z0-9_]', '', str(sym).upper())[:20]

def alert_admin(text):
    try:
        bot.send_message(ADMIN_ID, f"🚨 <b>ALPHA SYSTEM</b>\n<pre>{safe_html(text)}</pre>", parse_mode="HTML")
    except Exception:
        pass

def is_admin_user(user_id):
    """[SEC-1] Semak identiti PENGGUNA yang klik, bukan chat lokasi mesej."""
    return str(user_id) == str(ADMIN_ID)

# ==========================================
# 2. PRESETS & SUPABASE
# ==========================================
# [OPT-10] Threshold pass DITURUNKAN — EV gate (OPT-9) jaga kualiti
PRESETS = {
    "soft":     {"min_vol_24h": 500_000,   "score_pass": 2, "min_syarat": 3, "label": "🟢 SOFT"},
    "standard": {"min_vol_24h": 1_000_000, "score_pass": 2, "min_syarat": 3, "label": "🟡 STANDARD"},
    "hard":     {"min_vol_24h": 2_500_000, "score_pass": 3, "min_syarat": 4, "label": "🔴 HARD"}
}

DEFAULT_CONFIG = {
    "min_vol_24h":    1_000_000,
    "score_pass":     2,
    "cooldown_hours": 24,
    "active_preset":  "standard",
    "min_ev":         MIN_EV,
    "min_syarat":     3,      # [v16.2] minimum syarat lulus daripada 5
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
        _config_cache, _config_loaded_at = cfg, time.time()
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
    set_config("min_vol_24h",   p["min_vol_24h"])
    set_config("score_pass",    p["score_pass"])
    set_config("min_syarat",    p["min_syarat"])
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
        if not rows: return False
        entry_price = rows[0].get("entry", 0)
        if entry_price <= 0: return False
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

# [RC-6] Kolum yang PASTI wujud dalam schema legacy v14.x
LEGACY_RECORD_KEYS = {"contract", "symbol", "network", "entry", "sl", "tp1", "tp2", "tp3",
                      "rr1", "rr2", "setup", "fibo_zone", "score", "volume_24h",
                      "timeframe", "msg_id", "sent_at", "closed"}
LEGACY_UPDATE_KEYS = {"closed", "tp1_hit", "tp2_hit", "tp3_hit", "sl_hit"}

def save_signal(record: dict):
    """[RC-6] Resilient: cuba penuh → fallback kolum legacy. Trade MESTI masuk DB
    supaya monitor nampak — kalau tidak, signal dihantar tapi tak dimonitor."""
    try:
        sb.table("signals").upsert(record, on_conflict="contract").execute()
        return True
    except Exception as e:
        print(f"[SIGNAL SAVE] full fail ({type(e).__name__}) — retry legacy cols")
    try:
        core = {k: v for k, v in record.items() if k in LEGACY_RECORD_KEYS}
        sb.table("signals").upsert(core, on_conflict="contract").execute()
        return True
    except Exception as e:
        print(f"[SIGNAL SAVE] core fail: {e}")
        return False

def update_signal(contract, fields: dict):
    """[RC-1] Resilient 3-tier: full → legacy-cols → closed-only → in-memory.
    PUNCA SPAM v16.0: 1 kolum hilang (be_hit) = SELURUH update gagal
    = closed tak saved = monitor notify berulang setiap 5 minit."""
    try:
        sb.table("signals").update(fields).eq("contract", contract).execute()
        return True
    except Exception as e:
        print(f"[SIGNAL UPDATE] full fail ({type(e).__name__}) — retry legacy cols")
    core = {k: v for k, v in fields.items() if k in LEGACY_UPDATE_KEYS}
    if core:
        try:
            sb.table("signals").update(core).eq("contract", contract).execute()
            return True
        except Exception as e:
            print(f"[SIGNAL UPDATE] core fail: {e}")
    if fields.get("closed"):
        try:
            sb.table("signals").update({"closed": True}).eq("contract", contract).execute()
            return True
        except Exception as e:
            print(f"[SIGNAL UPDATE] closed-only fail: {e}")
            CLOSED_LOCAL.add(contract)   # [RC-1] JAMINAN loop berhenti
    return False

def get_active_trades():
    try:
        rows = sb.table("signals").select("*").eq("closed", False).execute().data
        return {r["contract"]: r for r in rows if r["contract"] not in CLOSED_LOCAL}
    except Exception:
        return {}

def get_signals_since(days=7):
    try:
        cutoff = int(time.time()) - days * 86400
        return sb.table("signals").select("*").gte("sent_at", cutoff).execute().data
    except Exception:
        return []

# ==========================================
# [ALPHA-RISK] PENGURUSAN RISIKO
# ==========================================
def set_user_capital(user_id, capital, risk_pct=2.0):
    try:
        sb.table("user_profiles").upsert({
            "user_id": user_id, "capital": capital,
            "risk_pct": risk_pct, "updated": int(time.time())
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
    risk_usd      = capital * (risk_pct / 100.0)
    risk_distance = entry - sl
    if risk_distance <= 0:
        return 0, 0, 0
    position_usd = risk_usd / (risk_distance / entry)
    position_usd = min(position_usd, capital, capital * 0.50)
    position_coins  = position_usd / entry
    actual_risk_usd = position_coins * risk_distance
    return position_usd, position_coins, actual_risk_usd

def compute_final_sl(entry, structure_low, atr, atr_mult=1.5, max_sl_pct=0.08):
    sl_atr       = entry - (atr_mult * atr) if atr > 0 else entry * 0.98
    sl_structure = structure_low * 0.995
    sl_raw       = min(sl_atr, sl_structure)
    sl_floor     = entry * (1.0 - max_sl_pct)
    return max(sl_raw, sl_floor)

# ==========================================
# 3. API (RESILIENT) + BLOCKLIST + MATH SEBENAR
# ==========================================
STABLECOINS      = {"USDT","USDC","BUSD","DAI","TUSD","USDP","FRAX","LUSD","GUSD",
                    "USDD","FDUSD","PYUSD","USDK","SUSD","RSR","EURS","EURT","UST",
                    "ALUSD","MIM","CUSD","CEUR","XAUT","PAXG"}
WRAPPED_TOKENS   = {"WETH","WBTC","WBNB","WSOL","WMATIC","WAVAX","WFTM","BETH","STETH","RETH","CBETH"}
SYMBOL_BLACKLIST = STABLECOINS | WRAPPED_TOKENS

def is_blacklisted_symbol(sym):
    s = sanitize_symbol(sym)
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

def api_get(url, timeout=8, retries=3):
    """[SEC-6] GET dengan retry + exponential backoff + 429 handling."""
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"[API 429] backoff {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                print(f"[API FAIL] {url[:60]}: {e}")
                return None
            time.sleep(0.5 * (2 ** attempt))
    return None

def get_btc_24h_change():
    r = api_get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=3, retries=2)
    try:
        return float(r.get("priceChangePercent", 0)) if r else 0.0
    except Exception:
        return 0.0

def get_gateio_tickers():
    r = api_get("https://api.gateio.ws/api/v4/spot/tickers", timeout=10)
    if not r: return []
    pairs = []
    try:
        for t in r:
            if t['currency_pair'].endswith('_USDT'):
                sym = sanitize_symbol(t['currency_pair'].replace('_USDT', ''))
                if not sym: continue
                pairs.append({
                    "symbol": sym,
                    "volume_24h": float(t.get('quote_volume', 0)),
                    "last_price": float(t.get('last', 0)),
                    "pair": t['currency_pair']
                })
    except Exception as e:
        print(f"[TICKERS] parse error: {e}")
    return pairs

def get_gateio_price(sym):
    sym = sanitize_symbol(sym)
    r = api_get(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={sym}_USDT", timeout=3, retries=2)
    try:
        return float(r[0].get('last', 0)) if r else 0
    except Exception:
        return 0

def get_gateio_klines(sym, interval="1h", limit=200):
    sym = sanitize_symbol(sym)
    url = f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={sym}_USDT&interval={interval}&limit={limit}"
    r = api_get(url)
    if not r: return []
    candles = []
    try:
        for k in reversed(r):
            candles.append({
                't': int(k[0]), 'o': float(k[5]), 'h': float(k[3]),
                'l': float(k[4]), 'c': float(k[2]), 'v': float(k[1])
            })
    except Exception:
        return []
    return candles

# ─────────────────────────────────────────────
# MATH SEBENAR — Statistik + Wilder + Chaikin
# ─────────────────────────────────────────────
def calculate_ema(data, period):
    if len(data) < period:
        return sum(data) / len(data) if data else 0
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_atr(candles, period=14):
    """
    [BUG-5] Wilder RMA sebenar (bukan SMA):
      ATR_t = (ATR_{t-1}·(n-1) + TR_t) / n
    TR = max(H-L, |H-C_prev|, |L-C_prev|)
    """
    if len(candles) < period + 1:
        return 0
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i-1]
        trs.append(max(c['h'] - c['l'], abs(c['h'] - p['c']), abs(c['l'] - p['c'])))
    atr = sum(trs[:period]) / period          # seed = SMA pertama
    for tr in trs[period:]:                   # Wilder smoothing
        atr = (atr * (period - 1) + tr) / period
    return atr

def calculate_rsi(closes, period=14):
    """Wilder RSI standard."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i-1]
        gains.append(max(0, ch))
        losses.append(max(0, -ch))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calculate_adx(candles, period=14):
    """
    [BUG-8] Wilder ADX LENGKAP — return (adx, +DI, -DI).
    Rule sebenar Wilder: trend valid jika ADX ≥ threshold DAN +DI > -DI (bullish).
    """
    if len(candles) < period * 2:
        return 0.0, 0.0, 0.0
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(candles)):
        hd = candles[i]['h'] - candles[i-1]['h']
        ld = candles[i-1]['l'] - candles[i]['l']
        plus_dm.append(max(0, hd) if hd > ld else 0)
        minus_dm.append(max(0, ld) if ld > hd else 0)
        trs.append(max(candles[i]['h'] - candles[i]['l'],
                       abs(candles[i]['h'] - candles[i-1]['c']),
                       abs(candles[i]['l'] - candles[i-1]['c'])))
    def smooth(data, p):
        s = [sum(data[:p])]
        for i in range(p, len(data)):
            s.append(s[-1] - (s[-1] / p) + data[i])
        return s
    sp, sm, st = smooth(plus_dm, period), smooth(minus_dm, period), smooth(trs, period)
    dx_values, last_pdi, last_mdi = [], 0.0, 0.0
    for i in range(len(st)):
        if st[i] == 0:
            dx_values.append(0)
            continue
        pdi = 100 * (sp[i] / st[i])
        mdi = 100 * (sm[i] / st[i])
        last_pdi, last_mdi = pdi, mdi
        di_sum = pdi + mdi
        dx_values.append(100 * abs(pdi - mdi) / di_sum if di_sum > 0 else 0)
    if len(dx_values) < period:
        return 0.0, last_pdi, last_mdi
    adx = sum(dx_values[:period]) / period
    for i in range(period, len(dx_values)):
        adx = (adx * (period - 1) + dx_values[i]) / period
    return adx, last_pdi, last_mdi

def volume_zscore(vols, lookback=20):
    """
    [BUG-9/OPT-1] Z-score statistik: z = (v - μ) / σ
    z ≥ 2.0 = 95.4 percentile (anomaly signifikan, bukan magic number).
    """
    if len(vols) < lookback + 1:
        return 0.0
    sample = vols[-(lookback + 1):-1]
    mu  = sum(sample) / len(sample)
    var = sum((v - mu) ** 2 for v in sample) / len(sample)
    sd  = var ** 0.5
    if sd <= 0:
        return 0.0
    return (vols[-1] - mu) / sd

def clv(candle):
    """
    [OPT-7] Close Location Value (Chaikin):
      CLV = ((C-L) - (H-C)) / (H-L)  ∈ [-1, +1]
    +1 = close di high (buyer kawal penuh), -1 = close di low.
    """
    rng = candle['h'] - candle['l']
    if rng <= 0:
        return 0.0
    return ((candle['c'] - candle['l']) - (candle['h'] - candle['c'])) / rng

def close_position(candle):
    """Lokasi close dalam range [0..1]. ≥0.6 = close kuat di upper 40%."""
    rng = candle['h'] - candle['l']
    if rng <= 0:
        return 0.5
    return (candle['c'] - candle['l']) / rng

def linreg_slope(values):
    """
    [BUG-12] Least-squares slope: β = Σ(x-x̄)(y-ȳ) / Σ(x-x̄)²
    Trend sebenar siri data — bukan banding first vs last.
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    num = sum((xs[i] - mx) * (values[i] - my) for i in range(n))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0

def chaikin_ad_slope(candles, lookback=20):
    """
    [OPT-7] Chaikin Accumulation/Distribution:
      A/D_t = A/D_{t-1} + CLV_t × V_t
    Slope positif = net institutional accumulation (money flow masuk).
    """
    if len(candles) < lookback:
        return 0.0
    ad, series = 0.0, []
    for c in candles[-lookback:]:
        ad += clv(c) * c['v']
        series.append(ad)
    # Normalize slope dengan purata volume supaya cross-coin comparable
    avg_v = sum(c['v'] for c in candles[-lookback:]) / lookback
    raw_slope = linreg_slope(series)
    return raw_slope / avg_v if avg_v > 0 else 0.0

def estimate_win_probability(conf_score, strong_count):
    """
    [OPT-8] Logistic mapping confluence → P(win):
      p = p_min + (p_max - p_min) / (1 + e^{-k(s - s₀)})
    Kalibrasi: p_min=0.32 (base rate setup rawak), p_max=0.78 (ceiling realistik),
    s₀=5 (midpoint), k=0.55 (steepness). s = conf_score + 0.5·strong_count.
    NOTA: Ini ESTIMATE berkalibrasi, bukan jaminan — laraskan dari journal data.
    """
    s = conf_score + strong_count * 0.5
    p = 0.32 + (0.78 - 0.32) / (1 + math.exp(-0.55 * (s - 5.0)))
    return round(p, 3)

def net_rr(entry, sl, tp):
    """
    [BUG-10] RR selepas fees (round-trip):
      RR_net = (TP - E - E·f_rt) / ((E - SL) + E·f_rt),  f_rt = 2 × 0.2% = 0.4%
    """
    fee_rt = entry * FEE_PER_SIDE * 2
    reward = (tp - entry) - fee_rt
    risk   = (entry - sl) + fee_rt
    if risk <= 0:
        return 0.0
    return reward / risk

def expected_value(p_win, rr_net):
    """
    [OPT-9] Expectancy per 1R dirisikokan:
      EV = p·RR_net - (1 - p)·1
    EV ≥ +0.15R diperlukan — secara matematik, sistem profitable jangka panjang.
    """
    return p_win * rr_net - (1.0 - p_win)

def find_fractal_swings(candles, lookback=2):
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
    if   is_hh and is_hl:        return 'uptrend'
    elif is_lh and is_ll:        return 'downtrend'
    elif is_hh and not is_ll:    return 'uptrend_breakout'
    elif is_hl and is_lh:        return 'sideway'
    elif is_hl and not is_lh:    return 'uptrend'
    else:                        return 'sideway'

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
    fvgs = []
    n = len(candles)
    for i in range(2, min(lookback, n - 1)):
        c_a, c_b, c_c = candles[-(i + 1)], candles[-i], candles[-(i - 1)]
        if c_b['c'] > c_b['o'] and c_c['l'] > c_a['h']:
            gap_pct = (c_c['l'] - c_a['h']) / c_b['c'] * 100
            if gap_pct >= 0.3:
                fvgs.append({
                    'top': c_c['l'], 'bottom': c_a['h'],
                    'mid': (c_c['l'] + c_a['h']) / 2,
                    'size_pct': gap_pct, 'candles_ago': i - 1
                })
    return fvgs

# ==========================================
# [P1-1] CHOCH — FIXED (BUG-6, BUG-7)
# ==========================================
def detect_choch(swings, current_price, structure, atr=0.0):
    """
    Change of Character dengan ATR buffer (0.25·ATR) — elak wick noise.
    [BUG-6] SIDEWAY dikecualikan: break dalam compression = normal.
    
    UPTREND:   close < recent_HL - buffer → CHOCH_BEAR (warning)
    DOWNTREND: close > recent_LH + buffer → CHOCH_BULL (reversal entry)
    """
    if not swings or structure in ('unknown', 'sideway'):
        return None
    buffer = atr * 0.25 if atr > 0 else 0.0
    all_highs = sorted([s for s in swings if s['type'] == 'SH'], key=lambda x: x['index'])
    all_lows  = sorted([s for s in swings if s['type'] == 'SL'], key=lambda x: x['index'])

    if structure in ('uptrend', 'uptrend_breakout'):
        if len(all_lows) >= 2:
            recent_hl = all_lows[-1]['price']
            if current_price < recent_hl - buffer:
                return 'CHOCH_BEAR'
    if structure == 'downtrend':
        if len(all_highs) >= 2:
            recent_lh = all_highs[-1]['price']
            if current_price > recent_lh + buffer:
                return 'CHOCH_BULL'
    return None

# ==========================================
# [P1-2] SWEEP CONFLUENCE — OPTIMIZED (OPT-5)
# ==========================================
def validate_sweep_confluence(candles, sweep_price, fvgs, price, atr):
    """
    Sweep valid jika:
    1. Displacement: close > sweep + 0.5%, body kuat, range bermakna
    2. CLV ≥ 0.2 (Chaikin — buyer kawal candle)
    3. close_pos ≥ 0.6 (close di upper 40% range)
    + FVG/OB di atas sweep sebagai target confluence.
    """
    if not candles or len(candles) < 5:
        return False, 0.0, "NO_DATA"
    curr  = candles[-1]
    body  = abs(curr['c'] - curr['o'])
    rng   = curr['h'] - curr['l']
    disp_pct = (curr['c'] - sweep_price) / sweep_price * 100 if sweep_price > 0 else 0
    c_clv = clv(curr)
    c_pos = close_position(curr)

    has_displacement = (
        curr['c'] > curr['o'] and
        rng > atr * 0.5 and
        body > rng * 0.5 and
        disp_pct >= 0.5 and
        c_clv >= 0.2 and          # [OPT-5] buyer control
        c_pos >= 0.6              # [OPT-5] strong close location
    )
    has_fvg = any(f['bottom'] > sweep_price for f in fvgs) if fvgs else False

    has_ob = False
    try:
        for i in range(-15, -3):
            c, cn = candles[i], candles[i + 1]
            if (c['c'] < c['o'] and cn['c'] > cn['o'] and
                    c['h'] > sweep_price and cn['c'] > sweep_price):
                if (cn['c'] - cn['o']) > (cn['h'] - cn['l']) * 0.3:
                    has_ob = True
                    break
    except Exception:
        pass

    if has_displacement and has_fvg and has_ob:
        return True, disp_pct, "SWEEP+FVG+OB"
    if has_displacement and has_fvg:
        return True, disp_pct, "SWEEP+FVG"
    if has_displacement and has_ob:
        return True, disp_pct, "SWEEP+OB"
    if has_displacement:
        return True, disp_pct, "SWEEP+DISP"
    if has_fvg or has_ob:
        return False, disp_pct, "SWEEP_PARTIAL"
    return False, disp_pct, "SWEEP_RAW"

# ==========================================
# [P2-1] SESSION TIMING — FIXED (BUG-11)
# ==========================================
def get_trading_session():
    """
    London Open  07:00–11:59 UTC: +1 (institutional volume masuk)
    NY Overlap   12:00–16:59 UTC: +1 (volatiliti + volume tertinggi)
    NY Session   17:00–21:59 UTC:  0
    Dead Zone    22:00–01:59 UTC: -1 (likuiditi nipis, fakeout tinggi)
    Asia Session 02:00–06:59 UTC:  0
    """
    h = datetime.now(timezone.utc).hour
    if 7 <= h <= 11:
        return "LONDON_OPEN", +1, "🇬🇧"
    elif 12 <= h <= 16:
        return "NY_OVERLAP",  +1, "🇺🇸"
    elif 17 <= h <= 21:
        return "NY_SESSION",   0, "🌆"
    elif h >= 22 or h <= 1:
        return "DEAD_ZONE",   -1, "💀"
    else:
        return "ASIA_SESSION", 0, "🌏"

# ==========================================
# [P2-2] WHALE PROXY — CLV/CHAIKIN UPGRADE (OPT-7, BUG-12)
# ==========================================
def check_whale_proxy(sym, candles_h4, candles_h1):
    """
    Multi-TF money-flow proxy guna formula institutional sebenar:
    1. H4 Volume Delta (green vs red ratio)
    2. H4 Chaikin A/D slope (Σ CLV·V regression) — [OPT-7]
    3. H4 dry pullback — regression slope volume merah [BUG-12]
    4. H1 impulse vs pullback pressure
    5. H1 volume exhaustion guard
    """
    whale_score = 0
    details     = []

    if candles_h4 and len(candles_h4) >= 20:
        recent_h4 = candles_h4[-20:]

        # 1. Volume Delta
        green_vol = sum(c['v'] for c in recent_h4 if c['c'] >= c['o'])
        red_vol   = sum(c['v'] for c in recent_h4 if c['c'] < c['o'])
        total_vol = green_vol + red_vol
        if total_vol > 0:
            gr = green_vol / total_vol
            if gr >= 0.65:
                whale_score += 2; details.append(f"H4 ACCUM {gr*100:.0f}%")
            elif gr >= 0.55:
                whale_score += 1; details.append(f"H4 LEAN-BULL {gr*100:.0f}%")
            elif gr <= 0.35:
                whale_score -= 2; details.append(f"H4 DISTRIB {gr*100:.0f}%")

        # 2. [OPT-7] Chaikin A/D slope — money flow regression
        # [v16.2] Threshold dikalibrasi dari data sebenar (log: -0.10..-0.42 biasa
        # dalam pasaran drift — bukan distribution sebenar)
        ad_slope = chaikin_ad_slope(recent_h4, lookback=20)
        if ad_slope > 0.10:
            whale_score += 2; details.append(f"A/D+ {ad_slope:.2f}")
        elif ad_slope > 0.03:
            whale_score += 1; details.append(f"A/D~ {ad_slope:.2f}")
        elif ad_slope < -0.25:
            whale_score -= 2; details.append(f"A/D-- {ad_slope:.2f}")
        elif ad_slope < -0.08:
            whale_score -= 1; details.append(f"A/D- {ad_slope:.2f}")

        # 3. [BUG-12] Dry pullback — regression slope volume candle merah
        red_vols = [c['v'] for c in recent_h4[-8:] if c['c'] < c['o']]
        if len(red_vols) >= 3:
            rv_slope = linreg_slope(red_vols)
            rv_mean  = sum(red_vols) / len(red_vols)
            if rv_mean > 0 and rv_slope / rv_mean < -0.05:   # selling vol menyusut
                whale_score += 1; details.append("DRY-PULLBACK")

    if candles_h1 and len(candles_h1) >= 20:
        recent_h1 = candles_h1[-20:]

        # 4. Impulse vs pullback pressure
        imp = [c['v'] for c in recent_h1 if c['c'] > c['o']]
        pul = [c['v'] for c in recent_h1 if c['c'] < c['o']]
        ai = sum(imp) / len(imp) if imp else 1
        ap = sum(pul) / len(pul) if pul else 1
        if ai > ap * 1.4:
            whale_score += 1; details.append("H1 BUY-PRES")
        elif ap > ai * 1.4:
            whale_score -= 1; details.append("H1 SELL-PRES")

        # 5. Exhaustion guard
        vmax = max(c['v'] for c in candles_h1[-50:]) if len(candles_h1) >= 50 else max(c['v'] for c in candles_h1)
        if max(c['v'] for c in recent_h1[-5:]) > vmax * 0.90:
            whale_score -= 1; details.append("VOL-CLIMAX-WARN")

    desc = " | ".join(details) if details else "NEUTRAL"
    if whale_score >= 3:    return "ACCUMULATING", whale_score, desc
    if whale_score >= 1:    return "LEAN_BULLISH", whale_score, desc
    if whale_score == 0:    return "NEUTRAL",      whale_score, desc
    if whale_score >= -2:   return "LEAN_BEARISH", whale_score, desc
    return "DISTRIBUTING", whale_score, desc   # [v16.2] hanya ≤ -3 (bukti kukuh)

# ==========================================
# [P1-3] CONFLUENCE STACKING
# ==========================================
def compute_entry_confluence(signals: dict):
    strong_map = {"SWEEP_CONFIRMED", "FVG_ACTIVE", "OB_FRESH", "CHOCH_BULL"}
    score, strong_count, labels = 0, 0, []
    for name, present in signals.items():
        if not present:
            continue
        if name in strong_map:
            score += 2; strong_count += 1
        else:
            score += 1
        labels.append(name)
    return score, strong_count, labels

# [v16.3-UI] Label pendek syarat untuk paparan 1-baris
SYARAT_SHORT = {
    "Zon Fib+Struktur": "Zon", "Trend EMA": "EMA", "Trigger Candle": "Candle",
    "Smart Money": "Whale", "Sesi+Selamat": "Sesi",
    "Vol Kuat": "Vol", "Akumulasi": "Akum", "Bias H4/Whale": "H4",
    "Golden Cross": "GC", "ADX Directional": "ADX", "Volume Climax": "Vol",
    "RSI Momentum": "RSI", "Sesi+Disp": "Sesi",
}

def compute_grade(syarat_pass, ev, whale, choch, dead_zone):
    """
    [v16.3-UI] GRED premium — satu huruf gabungkan syarat + EV + risiko.
    💎A+ = elit | 🟢A = kuat | 🟡B = sederhana | 🟠C = marginal (sah tapi berhati-hati)
    """
    pts = syarat_pass
    if ev >= 0.50:   pts += 2
    elif ev >= 0.25: pts += 1
    if whale in ("ACCUMULATING", "LEAN_BULLISH"): pts += 1
    if whale == "DISTRIBUTING": pts -= 2
    if choch == "CHOCH_BEAR":   pts -= 2
    if dead_zone:               pts -= 1
    if pts >= 8: return "A+", "💎"
    if pts >= 6: return "A",  "🟢"
    if pts >= 4: return "B",  "🟡"
    return "C", "🟠"

def quality_bar(score, max_score=10):
    filled = max(0, min(max_score, int(round(score))))
    return "▰" * filled + "▱" * (max_score - filled)

# ==========================================
# 4. ENGINE 1: PULLBACK SMC (H1+H4) v16
# ==========================================
def analyze_smc_pa(sym, verbose=True):
    log = lambda msg: print(f"[{sym}-H1] {msg}") if verbose else None
    candles = get_gateio_klines(sym, "1h", 200)
    if len(candles) < 100:
        log("❌ REJECT: Data H1 < 100")
        return None

    atr = calculate_atr(candles, 14)

    candles_h4       = get_gateio_klines(sym, "4h", 50)
    is_counter_trend = False
    h4_swing_high    = 0
    if len(candles_h4) >= 20:
        h4_swings    = find_fractal_swings(candles_h4, lookback=1)
        h4_structure = check_market_structure(h4_swings)
        if h4_structure == 'downtrend':
            log("⚠️ H4 downtrend (Counter-Trend Mode)")
            is_counter_trend = True
            h4_shs = [s for s in h4_swings if s['type'] == 'SH']
            h4_swing_high = h4_shs[-1]['price'] if h4_shs else 0

    swings = find_fractal_swings(candles, lookback=2)

    closes      = [c['c'] for c in candles[-200:]]
    ema20       = calculate_ema(closes, 20)
    ema50       = calculate_ema(closes, 50)
    price_now   = candles[-1]['c']
    ema_bullish = ema20 > ema50 and price_now > ema20 * 0.95

    if len(swings) >= 4:
        structure = check_market_structure(swings)
    else:
        if ema20 > ema50 and price_now > ema20:
            structure = 'uptrend'
        elif ema20 < ema50 and price_now < ema20:
            structure = 'downtrend'
        else:
            structure = 'unknown'

    if structure == 'downtrend' and ema_bullish:
        log("⚠️ STRUCT CONFLICT → sideway")
        structure = 'sideway'
    elif structure == 'downtrend':
        log("❌ REJECT: downtrend (fractal+EMA confirm)")
        return None
    log(f"✅ STRUCTURE: {structure}")

    price_live = get_gateio_price(sym)
    price      = price_live if price_live > 0 else price_now

    # [BUG-7] CHOCH dengan ATR buffer
    choch_signal = detect_choch(swings, price, structure, atr=atr)
    if choch_signal == 'CHOCH_BEAR':
        log("⚠️ CHOCH BEAR: HL ditembus (>0.25·ATR) — risiko tinggi")
    if choch_signal == 'CHOCH_BULL':
        log("✅ CHOCH BULL: LH ditembus — reversal entry")

    fresh_sh,  fresh_sl  = find_fresh_swing_pair(swings)
    anchor_sh, anchor_sl = find_anchor_swing_pair(swings)

    # [BUG-1] HARD GUARD — tiada fib pair langsung = reject
    if (not fresh_sh or not fresh_sl) and (not anchor_sh or not anchor_sl):
        log("❌ REJECT: Tiada fib pair valid (fresh & anchor kosong)")
        return None

    def calc_fibs(sh, sl):
        if not sh or not sl or sh <= sl:
            return None
        r = sh - sl
        return {"sh": sh, "sl": sl, "rng": r,
                "fib500": sh - r*0.500, "fib618": sh - r*0.618, "fib786": sh - r*0.786}

    fresh_fib  = calc_fibs(fresh_sh,  fresh_sl)
    anchor_fib = calc_fibs(anchor_sh, anchor_sl)

    curr, prev = candles[-1], candles[-2]
    tol = atr * 0.25   # [OPT-6] Fib tolerance band

    def in_zone(fib):
        return fib and (fib["fib786"] - tol) <= price <= (fib["fib500"] + tol)

    def in_core(fib):
        return fib and fib["fib786"] <= price <= fib["fib500"]

    in_fresh_zone  = in_zone(fresh_fib)
    in_anchor_zone = in_zone(anchor_fib)
    same_pair      = (fresh_sh == anchor_sh and fresh_sl == anchor_sl)

    if not in_fresh_zone and not in_anchor_zone:
        zone_ref = fresh_fib["fib500"] if fresh_fib else anchor_fib["fib500"]
        if price > zone_ref and ema_bullish and price > ema20:
            log("⚠️ PREMIUM OVERRIDE: EMA bullish → allow")
            setup_mode = "INTRADAY"
            base = fresh_fib or anchor_fib       # [BUG-1] dijamin wujud
            swing_high, swing_low = base["sh"], base["sl"]
            rng     = base["rng"]
            fib_500, fib_618, fib_786 = base["fib500"], base["fib618"], base["fib786"]
            in_discount = False
            fib_zone    = f"Premium (>{fmt(zone_ref)})"
        else:
            log("❌ REJECT: " + ("PREMIUM" if price > zone_ref else "EXTREME"))
            return None
    else:
        if in_fresh_zone and fresh_fib and not same_pair:
            setup_mode = "INTRADAY"; base = fresh_fib
            log(f"⚡ FIBO INTRADAY: ${fmt(price)} dalam FRESH±tol")
        elif in_anchor_zone and anchor_fib:
            setup_mode = "SWING"; base = anchor_fib
            log(f"⚖️ FIBO SWING: ${fmt(price)} dalam ANCHOR±tol")
        else:
            setup_mode = "INTRADAY" if in_fresh_zone else "SWING"
            base = fresh_fib or anchor_fib
            log(f"✅ FIBO PASS (band ±{fmt(tol)})")
        swing_high, swing_low = base["sh"], base["sl"]
        rng     = base["rng"]
        fib_500, fib_618, fib_786 = base["fib500"], base["fib618"], base["fib786"]
        in_discount = True
        fib_zone    = f"{fmt(fib_500)} - {fmt(fib_786)}"

    # [v16.2] EMA jadi SYARAT, bukan hard reject — hard floor hanya untuk trend mati
    is_uptrend = ema20 > ema50
    ema_ok = is_uptrend
    if not is_uptrend:
        ema_gap_pct = abs(ema20 - ema50) / ema50 * 100
        if ema_gap_pct < 6.0:
            log(f"⚠️ EMA CONVERGING {ema_gap_pct:.2f}% — syarat trend separa lulus")
            ema_ok = True
            is_uptrend = True
        elif ema_gap_pct > 12.0:
            log(f"❌ REJECT: EMA bearish gap {ema_gap_pct:.2f}% — trend mati")
            return None
        else:
            log(f"⚠️ EMA gap {ema_gap_pct:.2f}% — syarat trend GAGAL, nilai syarat lain")

    # Hard floor: hanya falling knife sebenar (>18% bawah EMA20)
    price_near_ema = price >= ema20 * 0.90
    if price < ema20 * 0.82:
        log("❌ REJECT: Price >18% bawah EMA20 (falling knife)")
        return None
    if not price_near_ema:
        log("⚠️ Price 10-18% bawah EMA20 — syarat trend gagal (deep discount)")

    distance_from_ema = abs(price - ema20)
    threshold = atr * 0.5 if atr > 0 else ema20 * 0.015

    impulse_vols  = [c['v'] for c in candles[-21:-1] if c['c'] > c['o']]
    pullback_vols = [c['v'] for c in candles[-21:-1] if c['c'] < c['o']]
    avg_impulse_vol  = sum(impulse_vols) / len(impulse_vols) if impulse_vols else 1
    avg_pullback_vol = sum(pullback_vols) / len(pullback_vols) if pullback_vols else 1
    curr_vol = curr['v']
    vpa_dry  = avg_pullback_vol < (avg_impulse_vol * 0.7)

    body        = abs(curr['c'] - curr['o'])
    lower_wick  = min(curr['o'], curr['c']) - curr['l']
    upper_wick  = curr['h'] - max(curr['o'], curr['c'])
    total_range = curr['h'] - curr['l']
    wick_ratio  = lower_wick / body if body > 0 else 0

    # [BUG-3] ATR-scaled touches + zero guard
    if swing_low and swing_low > 0:
        touch_tol = max(atr * 0.35, swing_low * 0.002)
        touches   = sum(1 for c in candles[-50:-1] if abs(c['l'] - swing_low) <= touch_tol)
    else:
        touches = 0

    min_size_ok  = (total_range > atr * 0.5) if atr > 0 else True
    is_pinbar    = (lower_wick > body * 2 and upper_wick < total_range * 0.1
                    and curr['c'] > curr['o'] and min_size_ok)
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                    and curr['c'] > prev['o'] and curr['o'] <= prev['c']
                    and body > abs(prev['c'] - prev['o']) and curr_vol > prev['v'])

    fvg_active, fvg_detail, fvgs_m15 = False, "", []
    try:
        candles_m15_fvg = get_gateio_klines(sym, "15m", 50)
        if len(candles_m15_fvg) >= 10:
            fvgs_m15 = detect_fvg(candles_m15_fvg, lookback=30)
            for fvg in fvgs_m15:
                if fib_786 <= fvg['bottom'] <= fib_500 and fvg['bottom'] <= price <= fvg['top']:
                    fvg_active = True
                    fvg_detail = f"{fmt(fvg['bottom'])}-{fmt(fvg['top'])}"
                    break
    except Exception:
        pass

    # [OPT-5] Sweep — threshold rendah + CLV/close_pos compensate
    sweep_detected = sweep_validated = False
    sweep_confluence = ""
    vols_h1 = [c['v'] for c in candles]
    v_z     = volume_zscore(vols_h1, lookback=20)

    if (curr['l'] < swing_low and curr['c'] > swing_low and
            wick_ratio >= 1.5 and touches >= 2 and
            (curr_vol > avg_impulse_vol or v_z >= 1.0)):
        sweep_detected = True
        is_valid, disp_pct, conf_type = validate_sweep_confluence(candles, swing_low, fvgs_m15, price, atr)
        sweep_validated, sweep_confluence = is_valid, conf_type
        log(f"{'✅ SWEEP VALIDATED' if is_valid else '⚠️ SWEEP partial'}: {conf_type} disp {disp_pct:.2f}%")

    ob_found = ob_in_golden = False
    for i in range(-100, -3):
        try:
            c, cn = candles[i], candles[i + 1]
            if c['c'] < c['o'] and cn['c'] > cn['o']:
                if (cn['c'] - cn['o']) > rng * 0.01:
                    ob_high, ob_low = c['h'], c['l']
                    if ob_low <= price <= ob_high:
                        touches_after = sum(1 for j in range(i + 2, 0) if ob_low <= candles[j]['l'] <= ob_high)
                        if touches_after <= 1:
                            ob_in_golden = fib_618 <= (ob_high + ob_low) / 2 <= fib_500
                            ob_found = True
                            break
        except Exception:
            pass

    session_name, session_score, session_emoji = get_trading_session()
    whale_signal, whale_score_val, whale_desc  = check_whale_proxy(sym, candles_h4, candles)
    log(f"🕐 {session_emoji}{session_name} | 🐳 {whale_signal} ({whale_desc})")

    confluence_signals = {
        "STRUCTURE_OK":    structure in ('uptrend', 'uptrend_breakout'),
        "EMA_BULLISH":     is_uptrend and price > ema20,
        "VPA_DRY":         vpa_dry,
        "GOLDEN_ZONE":     in_core(fresh_fib if setup_mode == "INTRADAY" else anchor_fib) and fib_786 <= price <= fib_618,
        "PINBAR":          is_pinbar,
        "ENGULFING":       is_engulfing,
        "SWEEP_CONFIRMED": sweep_validated,
        "FVG_ACTIVE":      fvg_active,
        "OB_FRESH":        ob_found and ob_in_golden,
        "CHOCH_BULL":      choch_signal == 'CHOCH_BULL',
        "WHALE_BULL":      whale_signal in ("ACCUMULATING", "LEAN_BULLISH"),
        "SESSION_HOT":     session_score > 0,
    }
    conf_score, strong_count, conf_labels = compute_entry_confluence(confluence_signals)
    if choch_signal == 'CHOCH_BEAR':
        conf_score -= 2
        log("⚠️ CHOCH_BEAR: -2 confluence")
    log(f"🔗 Confluence {conf_score} | Strong {strong_count} | {conf_labels}")

    score, setup_name = 0, None
    if fib_786 <= price <= fib_618:
        score += 1; setup_name = "📍 FIB GOLDEN ZONE"
    if sweep_detected:
        if sweep_validated:
            setup_name = f"💧 {sweep_confluence}"; score += 3
        else:
            score += 1
    if is_pinbar:
        setup_name = setup_name or "🕯️ PINBAR REVERSAL"; score += 2
    elif is_engulfing:
        setup_name = setup_name or "🐂 BULLISH ENGULFING"; score += 2
    if vpa_dry:
        score += 1
    if is_uptrend and distance_from_ema < threshold and price > ema20:
        setup_name = setup_name or "📈 TREND PULLBACK"; score += 1
    if ob_found:
        setup_name = setup_name or ("🧱 ORDER BLOCK" + (" [GOLDEN]" if ob_in_golden else ""))
        score += 3 if ob_in_golden else 2
    if fvg_active:
        score += 3; setup_name = setup_name or "🕳️ M15 FVG ZONE"
    if choch_signal == 'CHOCH_BULL':
        score += 2; setup_name = setup_name or "🔄 CHOCH REVERSAL"
    score += session_score
    if whale_signal == "ACCUMULATING":   score += 2
    elif whale_signal == "LEAN_BULLISH": score += 1
    elif whale_signal == "DISTRIBUTING": score -= 2
    elif whale_signal == "LEAN_BEARISH": score -= 1

    # ════ [v16.2] SISTEM 5 SYARAT — signal jika ≥ min_syarat (default 3/5) ════
    trigger_ok  = is_pinbar or is_engulfing or sweep_detected or clv(curr) >= 0.3
    smart_money = (whale_signal in ("ACCUMULATING", "LEAN_BULLISH") or vpa_dry or
                   sweep_validated or fvg_active or (ob_found and ob_in_golden) or
                   choch_signal == 'CHOCH_BULL')
    market_safe = (session_score >= 0 and choch_signal != 'CHOCH_BEAR' and
                   whale_signal != 'DISTRIBUTING')
    syarat = {
        "Zon Fib+Struktur": in_discount and structure in ('uptrend', 'uptrend_breakout', 'sideway'),
        "Trend EMA":        ema_ok and price_near_ema,
        "Trigger Candle":   trigger_ok,
        "Smart Money":      smart_money,
        "Sesi+Selamat":     market_safe,
    }
    syarat_pass = sum(1 for v in syarat.values() if v)
    log(f"📋 SYARAT {syarat_pass}/5: " + " ".join(f"{'✅' if v else '❌'}{k}" for k, v in syarat.items()))
    if syarat_pass < 3:
        log(f"❌ REJECT: Syarat {syarat_pass}/5 < 3")
        return None
    if not setup_name:
        setup_name = "📍 ZONE ENTRY"

    sl = compute_final_sl(price, swing_low, atr, atr_mult=1.0, max_sl_pct=0.08)
    sl_floor = fib_786 * 0.997
    if sl_floor > sl and sl_floor < price:
        sl = sl_floor

    target_high = anchor_sh if anchor_sh and anchor_sh > swing_high else swing_high
    rng_target  = target_high - swing_low
    tp1 = target_high
    tp2 = swing_low + rng_target * 1.618
    tp3 = swing_low + rng_target * 2.618

    if is_counter_trend and h4_swing_high > 0:
        tp1, tp2, tp3 = min(tp1, h4_swing_high), min(tp2, h4_swing_high), min(tp3, h4_swing_high)
        if tp1 == tp2 == tp3:
            log("❌ REJECT: Counter-trend TP flat")
            return None

    risk = price - sl
    if risk <= 0 or tp1 <= price:
        log("❌ REJECT: Risk/TP invalid")
        return None
    if tp2 <= tp1: tp2 = tp1 + atr
    if tp3 <= tp2: tp3 = tp2 + atr * 2

    # [OPT-8/9] P(win) + EV
    p_win   = estimate_win_probability(conf_score + 1.2 * (syarat_pass - 3), strong_count)
    rr1_net = net_rr(price, sl, tp1)
    rr2_net = net_rr(price, sl, tp2)
    ev      = expected_value(p_win, max(rr1_net, rr2_net * 0.6))
    log(f"📐 SL ${fmt(sl)} | RR1net {rr1_net:.2f} | P(win) {p_win*100:.0f}% | EV {ev:+.2f}R")

    sls_list = [s for s in swings if s['type'] == 'SL']
    shs_list = [s for s in swings if s['type'] == 'SH']
    if len(sls_list) >= 2:
        last_hl = sls_list[-1]['price']
        d = abs(price - last_hl) / last_hl * 100
        if d <= 2.0 and price > last_hl:
            with _watchlist_lock:
                if sym not in BOS_WATCHLIST:
                    BOS_WATCHLIST[sym] = {"level": last_hl, "type": "HL", "added": time.time(),
                                          "session": session_name, "whale": whale_signal}
    if len(shs_list) >= 2:
        last_lh = shs_list[-1]['price']
        d = abs(price - last_lh) / last_lh * 100
        if d <= 2.0 and price < last_lh:
            with _watchlist_lock:
                if sym not in BOS_WATCHLIST:
                    BOS_WATCHLIST[sym] = {"level": last_lh, "type": "LH", "added": time.time(),
                                          "session": session_name, "whale": whale_signal}

    return {
        "setup": f"{setup_name} ({'⚡INTRADAY' if setup_mode == 'INTRADAY' else '⚖️SWING'})",
        "entry": price, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": round((tp1 - price) / risk, 2), "rr2": round((tp2 - price) / risk, 2),
        "rr1_net": round(rr1_net, 2), "rr2_net": round(rr2_net, 2),
        "p_win": p_win, "ev": round(ev, 2),
        "score": score, "fib_zone": fib_zone,
        "fib_500": fib_500, "fib_618": fib_618, "fib_786": fib_786,
        "timeframe": "H1", "setup_mode": setup_mode, "structure": structure,
        "is_counter_trend": is_counter_trend,
        "choch": choch_signal or "NONE",
        "sweep_conf": sweep_confluence if sweep_validated else "NONE",
        "confluence_score": conf_score, "strong_count": strong_count,
        "syarat_pass": syarat_pass, "syarat": syarat,
        "confluence_labels": ",".join(conf_labels[:6]),
        "fvg_detail": fvg_detail,
        "session": session_name, "whale_signal": whale_signal,
    }

# ==========================================
# 5. ENGINE 2: MOMENTUM (M15) v16
# ==========================================
def analyze_early_momentum(sym, verbose=True):
    log = lambda msg: print(f"[{sym}-M15] {msg}") if verbose else None
    candles = get_gateio_klines(sym, "15m", 100)
    if len(candles) < 50:
        return None

    vols     = [c['v'] for c in candles]
    avg_vol  = sum(vols[-20:-1]) / 19
    curr_vol = vols[-1]
    v_z      = volume_zscore(vols, lookback=20)

    # [v16.2] Trigger asas dilonggarkan: z ≥ 1.8 ATAU ratio ≥ 2.5
    vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 0
    if not (v_z >= 1.8 or vol_ratio >= 2.5):
        return None
    log(f"✅ VOL ANOMALY: z={v_z:.2f} | ratio={vol_ratio:.1f}x")

    highs = [c['h'] for c in candles[-50:]]
    lows  = [c['l'] for c in candles[-50:]]
    range_pct = (max(highs) - min(lows)) / min(lows) * 100 if min(lows) > 0 else 100
    range_ok  = range_pct <= 7        # [v16.2] syarat (dulu hard 5%)
    if range_pct > 12:                # hard floor: bukan akumulasi langsung
        return None
    log(f"{'✅' if range_ok else '⚠️'} RANGE: {range_pct:.1f}%")

    curr, prev = candles[-1], candles[-2]
    price = curr['c']
    body       = abs(curr['c'] - curr['o'])
    lower_wick = min(curr['o'], curr['c']) - curr['l']
    is_pinbar   = lower_wick > body * 2 and curr['c'] > curr['o']
    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                    and curr['c'] > prev['o'] and curr['o'] < prev['c'])
    # [v16.2] Pattern jadi syarat — CLV kuat (≥0.4) boleh ganti pattern
    c_clv      = clv(curr)
    pattern_ok = (is_pinbar or is_engulfing) or c_clv >= 0.4
    if c_clv < 0:
        log("❌ REJECT: CLV negatif — seller kawal candle")
        return None

    session_name, session_score, session_emoji = get_trading_session()
    atr_m15 = calculate_atr(candles, 14)

    swings_m15 = find_fractal_swings(candles[-50:], lookback=2)
    struct_m15 = check_market_structure(swings_m15)
    choch_m15  = detect_choch(swings_m15, price, struct_m15, atr=atr_m15)
    if choch_m15 == 'CHOCH_BEAR':
        log("⚠️ M15 CHOCH BEAR — risiko naik")

    setup_name = ("⚡ PINBAR MOMENTUM" if is_pinbar else
                  ("⚡ ENGULFING MOMENTUM" if is_engulfing else "⚡ CLV MOMENTUM"))
    entry      = price
    range_low  = min(lows[-20:])
    range_high = max(highs[-50:])
    sl   = compute_final_sl(entry, range_low, atr_m15, atr_mult=0.75, max_sl_pct=0.08)
    risk = entry - sl
    if risk <= 0:
        return None

    tp1 = range_high + (atr_m15 * 0.5 if atr_m15 > 0 else range_high * 0.01)
    tp2 = entry + risk * 2.618
    tp3 = entry + risk * 4.236
    tp2 = max(tp1 * 1.005, tp2)
    tp3 = max(tp2 * 1.005, tp3)

    # [BUG-2] Initialize SEBELUM try — tiada lagi dir() hack
    whale_sig = "NEUTRAL"
    h4_bias   = "neutral"
    try:
        candles_h4 = get_gateio_klines(sym, "4h", 50)
        if len(candles_h4) >= 20:
            h4_ema  = calculate_ema([c['c'] for c in candles_h4], 20)
            h4_bias = "uptrend" if candles_h4[-1]['c'] > h4_ema else "downtrend"
            whale_sig, _, whale_d = check_whale_proxy(sym, candles_h4, candles)
            log(f"🐳 {whale_sig} ({whale_d})")
            if whale_sig == "DISTRIBUTING":
                log("❌ REJECT: Whale distributing")
                return None
    except Exception:
        pass

    if h4_bias == "downtrend":
        sl   = range_low - (atr_m15 * 0.5 if atr_m15 > 0 else range_low * 0.005)
        risk = entry - sl
        if risk <= 0: return None
        tp1 = range_high + (atr_m15 * 0.25 if atr_m15 > 0 else range_high * 0.005)
        tp2 = entry + risk * 1.618
        tp3 = tp2
        setup_name = "⚡ COUNTER-TREND (Risky)"

    # ════ [v16.2] 5 SYARAT MOMENTUM — ≥3/5 ════
    syarat = {
        "Vol Kuat":      v_z >= 2.0 or vol_ratio >= 3.0,
        "Akumulasi":     range_ok,
        "Trigger Candle": pattern_ok,
        "Bias H4/Whale": (h4_bias != "downtrend") or whale_sig in ("ACCUMULATING", "LEAN_BULLISH"),
        "Sesi+Selamat":  session_score >= 0 and choch_m15 != 'CHOCH_BEAR',
    }
    syarat_pass = sum(1 for v in syarat.values() if v)
    log(f"📋 SYARAT {syarat_pass}/5: " + " ".join(f"{'✅' if v else '❌'}{k}" for k, v in syarat.items()))
    if syarat_pass < 3:
        log(f"❌ REJECT: Syarat {syarat_pass}/5 < 3")
        return None

    # Confluence + EV untuk momentum
    conf = {
        "VOL_ANOMALY": True, "PINBAR": is_pinbar, "ENGULFING": is_engulfing,
        "WHALE_BULL": whale_sig in ("ACCUMULATING", "LEAN_BULLISH"),
        "SESSION_HOT": session_score > 0, "STRUCTURE_OK": struct_m15 in ('uptrend', 'uptrend_breakout'),
        "CHOCH_BULL": choch_m15 == 'CHOCH_BULL',
    }
    conf_score, strong_count, conf_labels = compute_entry_confluence(conf)
    if choch_m15 == 'CHOCH_BEAR':
        conf_score -= 2
    p_win   = estimate_win_probability(conf_score + 1.2 * (syarat_pass - 3), strong_count)
    rr1_net = net_rr(entry, sl, tp1)
    ev      = expected_value(p_win, rr1_net)
    log(f"⚡ {setup_name} | P(win) {p_win*100:.0f}% | EV {ev:+.2f}R")

    return {
        "setup": setup_name, "entry": entry, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": round((tp1 - entry) / risk, 2), "rr2": round((tp2 - entry) / risk, 2),
        "rr1_net": round(rr1_net, 2), "rr2_net": round(net_rr(entry, sl, tp2), 2),
        "p_win": p_win, "ev": round(ev, 2),
        "score": 3, "fib_zone": "N/A", "timeframe": "M15",
        "vol_spike": vol_ratio, "vol_z": round(v_z, 2), "range_pct": range_pct,
        "h4_bias": h4_bias, "is_counter_trend": (h4_bias == "downtrend"),
        "session": session_name, "whale_signal": whale_sig,
        "choch": choch_m15 or "NONE",
        "confluence_score": conf_score, "strong_count": strong_count,
        "syarat_pass": syarat_pass, "syarat": syarat,
        "confluence_labels": ",".join(conf_labels[:5]),
    }

# ==========================================
# 6. ENGINE 3: BREAKOUT SNIPER (H1) v16
# ==========================================
def analyze_breakout_sniper(sym, verbose=True):
    log = lambda msg: print(f"[{sym}-1H-BO] {msg}") if verbose else None
    candles = get_gateio_klines(sym, "1h", 250)
    if len(candles) < 210:
        return None

    closes = [c['c'] for c in candles]
    highs  = [c['h'] for c in candles]
    lows   = [c['l'] for c in candles]
    vols   = [c['v'] for c in candles]

    # [v16.2] Golden Cross & ADX jadi SYARAT — bukan hard reject
    ema50, ema200 = calculate_ema(closes, 50), calculate_ema(closes, 200)
    golden_cross = ema50 > ema200

    adx, plus_di, minus_di = calculate_adx(candles, 14)
    adx_ok = adx >= 15 and plus_di > minus_di
    # Hard reject HANYA bila trend bearish KUAT terbukti
    if plus_di <= minus_di and adx >= 25:
        log(f"❌ REJECT: Trend bearish KUAT (ADX {adx:.1f}, -DI {minus_di:.1f} dominan)")
        return None

    curr  = candles[-1]
    price = curr['c']
    highest_20 = max(highs[-21:-1])
    if price <= highest_20:
        return None

    # [OPT-4] Vol climax 2.0x + z ≥ 1.5 (dulu 2.5x sahaja)
    avg_vol_20 = sum(vols[-21:-1]) / 20
    if avg_vol_20 == 0:
        return None
    vol_ratio = vols[-1] / avg_vol_20
    v_z       = volume_zscore(vols, lookback=20)
    vol_ok    = (vol_ratio >= 2.0 and v_z >= 1.2) or v_z >= 2.0
    # Hard floor: breakout TANPA volume langsung = fakeout klasik
    if vol_ratio < 1.3:
        log(f"❌ REJECT: Vol {vol_ratio:.1f}x < 1.3x — breakout tanpa peserta")
        return None

    # [v16.2] RSI jadi syarat — hard floor hanya RSI < 40 (momentum mati)
    rsi_now  = calculate_rsi(closes, 14)
    rsi_prev = calculate_rsi(closes[:-1], 14)
    rsi_slope = rsi_now - rsi_prev
    rsi_ok    = rsi_now >= 50 and rsi_slope > 0
    if rsi_now < 40:
        log(f"❌ REJECT: RSI {rsi_now:.1f} < 40 — breakout tanpa momentum")
        return None

    atr_bo = calculate_atr(candles, 14)
    swings_h1 = find_fractal_swings(candles[-50:], lookback=2)
    struct_h1 = check_market_structure(swings_h1)
    choch_h1  = detect_choch(swings_h1, price, struct_h1, atr=atr_bo)
    if choch_h1 == 'CHOCH_BEAR':
        log("❌ REJECT: CHOCH BEAR — breakout dalam struktur lemah")
        return None

    session_name, session_score, session_emoji = get_trading_session()
    dead_zone = session_score < 0   # [v16.2] dead zone = perlu 4/5 (bukan auto-reject)

    # Displacement + CLV
    body_bo  = abs(curr['c'] - curr['o'])
    range_bo = curr['h'] - curr['l']
    has_disp = (curr['c'] > curr['o'] and range_bo > atr_bo * 0.7 and
                body_bo > range_bo * 0.5 and clv(curr) >= 0.2)
    if not has_disp:
        log("⚠️ Displacement lemah — caution")

    candles_h4_bo = get_gateio_klines(sym, "4h", 50)
    whale_bo, _, whale_d_bo = check_whale_proxy(sym, candles_h4_bo, candles)
    if whale_bo == "DISTRIBUTING":
        log("❌ REJECT: Whale distributing — bull trap")
        return None

    # ════ [v16.2] 5 SYARAT BREAKOUT — ≥3/5 (dead zone: ≥4/5) ════
    syarat = {
        "Golden Cross":   golden_cross,
        "ADX Directional": adx_ok,
        "Volume Climax":  vol_ok,
        "RSI Momentum":   rsi_ok,
        "Sesi+Disp":      (not dead_zone) and has_disp,
    }
    syarat_pass = sum(1 for v in syarat.values() if v)
    need = 4 if dead_zone else 3
    log(f"📋 SYARAT {syarat_pass}/5 (perlu {need}): " + " ".join(f"{'✅' if v else '❌'}{k}" for k, v in syarat.items()))
    if syarat_pass < need:
        log(f"❌ REJECT: Syarat {syarat_pass}/5 < {need}")
        return None

    log(f"✅ BREAKOUT | ADX {adx:.1f} | Vol {vol_ratio:.1f}x z{v_z:.1f} | RSI {rsi_now:.1f} | {session_emoji}{session_name} | 🐳{whale_bo}")

    sl   = min(lows[-10:]) * 0.995
    risk = price - sl
    if risk <= 0:
        return None
    tp1 = price + risk * 2.0
    tp2 = price + risk * 4.0
    tp3 = price + risk * 6.0
    if session_name == "NY_OVERLAP":
        tp2, tp3 = price + risk * 5.0, price + risk * 7.0

    conf = {
        "BREAKOUT_DONCHIAN": True, "VOL_CLIMAX": True,
        "ADX_DIRECTIONAL": True, "RSI_MOMENTUM": True,
        "DISPLACEMENT": has_disp,
        "WHALE_BULL": whale_bo in ("ACCUMULATING", "LEAN_BULLISH"),
        "SESSION_HOT": session_score > 0,
    }
    conf_score, strong_count, conf_labels = compute_entry_confluence(conf)
    p_win   = estimate_win_probability(conf_score + 1.2 * (syarat_pass - 3), strong_count)
    rr1_net = net_rr(price, sl, tp1)
    ev      = expected_value(p_win, rr1_net)

    bo_score = 4
    if session_score > 0:            bo_score += 1
    if whale_bo == "ACCUMULATING":   bo_score += 1
    if has_disp:                     bo_score += 1

    return {
        "setup": "🚀 BREAKOUT SNIPER (Donchian+VolClimax)",
        "entry": price, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": round((tp1 - price) / risk, 2), "rr2": round((tp2 - price) / risk, 2),
        "rr1_net": round(rr1_net, 2), "rr2_net": round(net_rr(price, sl, tp2), 2),
        "p_win": p_win, "ev": round(ev, 2),
        "score": bo_score, "fib_zone": "N/A", "timeframe": "1H-BO",
        "syarat_pass": syarat_pass, "syarat": syarat,
        "setup_mode": "BREAKOUT", "structure": "uptrend_breakout",
        "is_counter_trend": False,
        "session": session_name, "whale_signal": whale_bo,
        "choch": choch_h1 or "NONE",
        "confluence_score": conf_score, "strong_count": strong_count,
        "confluence_labels": f"ADX{adx:.0f}+DI|RSI{rsi_now:.0f}↑|VOLz{v_z:.1f}|{session_name}",
    }

# ==========================================
# 7. SIGNAL SENDER — EV GATE + UI OPTIMUM
# ==========================================
def send_signal(sym, smc_data, vol_24h, btc_chg=0.0):
    cfg   = get_config()
    sym   = sanitize_symbol(sym)
    entry, sl = smc_data["entry"], smc_data["sl"]
    tp1, tp2, tp3 = smc_data["tp1"], smc_data["tp2"], smc_data["tp3"]
    timeframe = smc_data.get("timeframe", "H1")
    risk  = entry - sl

    # [v16.2] GATE UTAMA = SYARAT (≥3/5 default), score jadi info sahaja
    min_syarat = int(cfg.get("min_syarat", 3))
    if smc_data.get("syarat_pass", 3) < min_syarat:
        bump("SYARAT_KURANG")
        return False

    # ── [OPT-9] EXPECTED VALUE GATE ──
    p_win   = smc_data.get("p_win", estimate_win_probability(
                smc_data.get("confluence_score", 3), smc_data.get("strong_count", 0)))
    rr1_net = smc_data.get("rr1_net", net_rr(entry, sl, tp1))
    rr2_net = smc_data.get("rr2_net", net_rr(entry, sl, tp2))
    ev      = smc_data.get("ev", expected_value(p_win, max(rr1_net, rr2_net * 0.6)))
    min_ev  = float(cfg.get("min_ev", MIN_EV))
    if ev < min_ev:
        bump("EV_GATE")
        print(f"[SKIP] {sym}: EV {ev:+.2f}R < {min_ev:+.2f}R — expectancy negatif/lemah")
        return False

    user_cap, user_risk = get_user_capital(int(ADMIN_ID) if ADMIN_ID else 0)
    pos_usd, pos_coins, risk_usd = calculate_position_size(user_cap, user_risk, entry, sl)
    if pos_usd <= 0 or pos_coins <= 0:
        return False

    # [BUG-10] Filter guna NET RR (selepas fees)
    if rr1_net < 1.2 and rr2_net < 1.2:
        bump("RRNET_LOW")
        print(f"[SKIP] {sym}: RRnet1={rr1_net:.2f} RRnet2={rr2_net:.2f} < 1.2")
        return False
    if sl >= entry or tp1 <= entry:
        return False

    current_price = get_gateio_price(sym)
    if current_price > 0:
        price_gap = abs(current_price - entry) / entry * 100
        if price_gap > 15.0:
            with _watchlist_lock:                       # [BUG-4]
                not_in_wl = sym not in PULLBACK_WATCHLIST
            if smc_data.get("score", 0) >= 2 and not_in_wl:
                add_pullback_watchlist(sym, smc_data)
            bump("GAP_TUNGGU_PULLBACK")
            print(f"[SKIP] {sym}: gap {price_gap:.1f}% — tunggu pullback")
            return False

    sl_pct  = (entry - sl)  / entry * 100
    tp1_pct = (tp1 - entry) / entry * 100
    tp2_pct = (tp2 - entry) / entry * 100
    tp3_pct = (tp3 - entry) / entry * 100
    rr1 = (tp1 - entry) / risk
    rr2 = (tp2 - entry) / risk

    btc_warn = f"⚠️ <b>BTC ALERT:</b> {btc_chg:+.2f}%\n" if btc_chg < -4.0 else ""

    engine_icon  = "⚡" if timeframe == "M15" else ("🚀" if timeframe == "1H-BO" else "🏴‍☠️")
    engine_label = "MOMENTUM" if timeframe == "M15" else ("BREAKOUT" if timeframe == "1H-BO" else "PULLBACK")

    session      = smc_data.get("session", "?")
    whale_signal = smc_data.get("whale_signal", "NEUTRAL")
    choch        = smc_data.get("choch", "NONE")
    conf_labels  = smc_data.get("confluence_labels", "")

    se = {"LONDON_OPEN": "🇬🇧 LDN", "NY_OVERLAP": "🇺🇸 NY-OVL", "NY_SESSION": "🌆 NY",
          "ASIA_SESSION": "🌏 ASIA", "DEAD_ZONE": "💀 DEAD"}.get(session, session)
    we = {"ACCUMULATING": "🐳 ACCUM", "LEAN_BULLISH": "🐟 L-BULL", "NEUTRAL": "➖ NEUT",
          "LEAN_BEARISH": "🔴 L-BEAR", "DISTRIBUTING": "🔴 DISTRIB"}.get(whale_signal, whale_signal)
    # [v16.3] Whale bullish dipaparkan di setup line; bearish dah masuk warns
    we_pos = f" · {we}" if whale_signal in ("ACCUMULATING", "LEAN_BULLISH") else ""

    # ════ [v16.3] PREMIUM MINIMAL CARD ════
    # Hierarki: (1) GRED sekilas pandang (2) harga tindakan (3) konteks padat
    sy_pass = smc_data.get("syarat_pass", 3)
    grade, g_emoji = compute_grade(sy_pass, ev, whale_signal, choch,
                                   session == "DEAD_ZONE")

    # Checklist syarat 1-baris (label pendek)
    sy_map = smc_data.get("syarat", {})
    sy_line = ""
    if sy_map:
        sy_line = "📋 " + " ".join(
            f"{'✅' if v else '⬜'}{SYARAT_SHORT.get(k, k[:5])}"
            for k, v in sy_map.items()) + "\n"

    # Amaran risiko — HANYA bila wujud (jujur tapi tak bising)
    warns = []
    if session == "DEAD_ZONE":            warns.append("💀 Sesi mati")
    if whale_signal == "DISTRIBUTING":    warns.append("🐳 Whale agih")
    elif whale_signal == "LEAN_BEARISH":  warns.append("🐻 Whale lemah")
    if choch == "CHOCH_BEAR":             warns.append("🔄 Struktur retak")
    if smc_data.get("is_counter_trend"):  warns.append("↩️ Lawan H4")
    risk_line = f"⚠️ <i>{' · '.join(warns)}</i>\n" if warns else ""

    setup_short = safe_html(smc_data['setup'], 38)
    if timeframe == "M15" and "vol_spike" in smc_data:
        setup_short += f" · ⚡{smc_data['vol_spike']:.1f}x"

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 Gate.io",     url=f"https://www.gate.io/trade/{sym}_USDT"),
        InlineKeyboardButton("📈 TradingView", url=f"https://www.tradingview.com/chart/?symbol=GATEIO:{sym}USDT")
    )

    msg = (
        f"{engine_icon} <b>ALPHA {engine_label}</b> — <b>{sym}/USDT</b> · {timeframe}\n"
        f"{g_emoji} <b>GRED {grade}</b>  ·  📋 {sy_pass}/5  ·  🎲 <b>{p_win*100:.0f}%</b>  ·  EV <b>{ev:+.2f}R</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{btc_warn}"
        f"💰 Entry  <code>${fmt(entry)}</code>\n"
        f"🛑 SL     <code>${fmt(sl)}</code>  <i>−{sl_pct:.1f}%</i>\n"
        f"🎯 TP1    <code>${fmt(tp1)}</code>  <i>+{tp1_pct:.1f}% · RR{rr1:.1f}</i>\n"
        f"🎯 TP2    <code>${fmt(tp2)}</code>  <i>+{tp2_pct:.1f}%</i>\n"
        f"🎯 TP3    <code>${fmt(tp3)}</code>  <i>+{tp3_pct:.1f}%</i>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{sy_line}"
        f"{risk_line}"
        f"🧠 {setup_short} · {se}{we_pos}\n"
        f"💼 <code>${pos_usd:.2f}</code> · Risk <code>${risk_usd:.2f}</code> ({user_risk}%) · RRnet <b>{rr1_net:.2f}</b>"
    )

    try:
        sent = bot.send_message(VIP_CHANNEL_ID, msg, parse_mode="HTML",
                                reply_markup=markup, disable_web_page_preview=True)
        record = {
            "contract": sym, "symbol": sym, "network": "GATEIO",
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "rr1": round(rr1, 2), "rr2": round(rr2, 2),
            "setup": smc_data["setup"], "fibo_zone": smc_data.get("fib_zone", "N/A"),
            "score": smc_data["score"], "volume_24h": vol_24h, "timeframe": timeframe,
            "session": session, "whale": whale_signal,
            "confluence": conf_labels[:100],
            "p_win": p_win, "ev": ev, "syarat_pass": smc_data.get("syarat_pass", 0),
            "msg_id": sent.message_id, "sent_at": int(time.time()), "closed": False,
        }
        save_signal(record)
        add_cooldown(sym)
        print(f"[SIGNAL ✅] {sym} | {smc_data['setup']} | EV {ev:+.2f}R | P {p_win*100:.0f}%")
        return True
    except Exception as e:
        alert_admin(f"Gagal hantar {sym}: {e}")
        return False

# ==========================================
# 8. SCANNER & MONITOR
# ==========================================
IS_SCANNING           = True
WATCHLIST             = {}
WATCHLIST_TIMEOUT     = 900
PULLBACK_WATCHLIST    = {}
PULLBACK_TIMEOUT      = 28800
BOS_WATCHLIST         = {}
BOS_WATCHLIST_TIMEOUT = 28800

def add_pullback_watchlist(sym, smc_data):
    with _watchlist_lock:
        PULLBACK_WATCHLIST[sym] = {
            "entry": smc_data["entry"],
            "fib_500": smc_data.get("fib_500", smc_data["entry"]),
            "fib_786": smc_data.get("fib_786", smc_data["sl"]),
            "sl": smc_data["sl"], "added": time.time(),
            "setup": smc_data["setup"],
            "whale": smc_data.get("whale_signal", "NEUTRAL"),
        }
        print(f"[{sym}] 📌 PULLBACK WL: {fmt(smc_data.get('fib_786', smc_data['sl']))}-{fmt(smc_data['entry'])}")

def scan_once():
    global IS_SCANNING_ACTIVE, LAST_SCAN_INFO
    with _scan_lock:
        if IS_SCANNING_ACTIVE:
            print("[SCAN] skip — sedang berjalan")
            return
        IS_SCANNING_ACTIVE = True
    t0 = time.time()
    try:
        if not IS_SCANNING:
            return
        REJECT_STATS.clear()
        btc_chg = get_btc_24h_change()
        cfg     = get_config()
        sess_n, _, sess_e = get_trading_session()
        print(f"\n{'='*65}\n🔍 [{datetime.now().strftime('%H:%M:%S')}] SCAN | {SCAN_MODE.upper()} | {sess_e}{sess_n} | BTC {btc_chg:+.2f}%\n{'='*65}")

        tickers    = get_gateio_tickers()
        candidates = [t for t in tickers if t["volume_24h"] >= cfg["min_vol_24h"]]
        # [RC-8] Top-N by volume — scan siap < 5 min, fokus likuiditi terbaik
        candidates.sort(key=lambda x: x["volume_24h"], reverse=True)
        candidates = candidates[:MAX_SCAN_SYMBOLS]
        momentum_candidates = candidates[:100]
        print(f"[GATEIO] {len(tickers)} pairs → {len(candidates)} candidates (top-{MAX_SCAN_SYMBOLS} vol)")

        active = get_active_trades()
        passed = scanned = errors = 0

        for t in candidates:
            sym = t["symbol"]
            current_price = t.get("last_price", 0)
            bl, _ = is_blacklisted_symbol(sym)
            if bl:
                bump("BLACKLIST"); continue
            if is_in_cooldown(sym) and not check_cooldown_override(sym, current_price):
                bump("COOLDOWN_24H"); continue
            if sym in active:
                bump("TRADE_AKTIF"); continue
            scanned += 1
            print(f"\n[{sym}] 🔎 ...")

            # [RC-4] PER-SYMBOL ISOLATION — 1 symbol error ≠ scan mati
            if SCAN_MODE in ["pullback", "both", "all"]:
                try:
                    smc = analyze_smc_pa(sym, verbose=True)
                    if smc:
                        if send_signal(sym, smc, t["volume_24h"], btc_chg=btc_chg):
                            passed += 1; time.sleep(2)
                    elif smc is None:
                        bump("E1_NO_SETUP")
                except Exception as e:
                    errors += 1; bump("E1_ERROR")
                    print(f"[E1 ERR] {sym}: {type(e).__name__}: {e}")

            if SCAN_MODE in ["breakout", "both", "all"]:
                try:
                    bo = analyze_breakout_sniper(sym, verbose=True)
                    if bo:
                        if send_signal(sym, bo, t["volume_24h"], btc_chg=btc_chg):
                            passed += 1; time.sleep(2)
                    elif bo is None:
                        bump("E3_NO_SETUP")
                except Exception as e:
                    errors += 1; bump("E3_ERROR")
                    print(f"[E3 ERR] {sym}: {type(e).__name__}: {e}")

            if SCAN_MODE in ["momentum", "both", "all"]:
                try:
                    if t in momentum_candidates:
                        candles_m15 = get_gateio_klines(sym, "15m", 100)
                        if len(candles_m15) < 50:
                            continue
                        curr = candles_m15[-1]
                        if (time.time() - curr['t']) < (15 * 60 * 0.90):
                            bump("M15_CANDLE_MUDA"); continue
                        vols_m  = [c['v'] for c in candles_m15]
                        avg_vol = sum(vols_m[-20:-1]) / 19
                        v_z     = volume_zscore(vols_m, 20)
                        ratio   = curr['v'] / avg_vol if avg_vol > 0 else 0
                        if v_z >= 2.0 or ratio >= 2.5:        # [OPT-1]
                            with _watchlist_lock:
                                if sym not in WATCHLIST:
                                    WATCHLIST[sym] = time.time()
                                    print(f"[{sym}] 📌 WL: z={v_z:.2f} ratio={ratio:.1f}x")
                            res = analyze_early_momentum(sym, verbose=True)
                            if res and send_signal(sym, res, t["volume_24h"], btc_chg=btc_chg):
                                passed += 1
                                with _watchlist_lock:
                                    if sym in WATCHLIST: del WATCHLIST[sym]
                                time.sleep(2)
                            elif res is None:
                                bump("E2_NO_SETUP")
                        else:
                            with _watchlist_lock:
                                if sym in WATCHLIST: del WATCHLIST[sym]
                except Exception as e:
                    errors += 1; bump("E2_ERROR")
                    print(f"[E2 ERR] {sym}: {type(e).__name__}: {e}")

        dur = time.time() - t0
        top_rejects = dict(sorted(REJECT_STATS.items(), key=lambda x: -x[1])[:8])
        LAST_SCAN_INFO = {
            "ts": int(time.time()), "dur": round(dur, 1),
            "cands": len(candidates), "scanned": scanned,
            "signals": passed, "errors": errors,
            "rejects": top_rejects, "session": sess_n,
        }
        print(f"\n📊 SCAN {dur:.0f}s | dianalisa {scanned} | signal {passed} | err {errors}")
        print(f"📋 Rejects: {top_rejects}\n{'='*65}\n")
    finally:
        with _scan_lock:
            IS_SCANNING_ACTIVE = False


def monitor_active_trades():
    """[BUG-13] HIGH/LOW + SL-first | [v16.1] dedupe + auto-cleanup + resilient close."""
    active = get_active_trades()
    if not active:
        return
    now = int(time.time())
    for sym, trade in active.items():
        try:
            # [RC-3] Legacy blacklist cleanup — USDC/stablecoin dari versi lama
            bl, bl_reason = is_blacklisted_symbol(sym)
            if bl:
                update_signal(sym, {"closed": True})
                CLOSED_LOCAL.add(sym)
                print(f"[MONITOR] {sym}: AUTO-CLOSED ({bl_reason}) — legacy blacklisted")
                continue

            # [RC-5] Zombie cleanup — trade > 7 hari tanpa resolusi
            sent_at = trade.get("sent_at") or 0
            if sent_at and (now - int(sent_at)) > 7 * 86400:
                update_signal(sym, {"closed": True})
                CLOSED_LOCAL.add(sym)
                if should_notify(sym, "stale"):
                    try:
                        bot.send_message(VIP_CHANNEL_ID,
                            f"<b>{sym}</b>\n⏳ <b>AUTO-CLOSE</b> — trade melebihi 7 hari",
                            parse_mode="HTML")
                    except Exception:
                        pass
                continue

            candles = get_gateio_klines(sym, "1h", 5)
            if not candles:
                continue
            c   = candles[-1]
            hi, lo, cp = c['h'], c['l'], c['c']
            mid = trade.get("msg_id")
            entry_price = trade.get("entry", 0)
            if entry_price <= 0:        # [AUDIT] guard DB record rosak
                update_signal(sym, {"closed": True})
                CLOSED_LOCAL.add(sym)
                continue

            def notify(event, text, _mid=mid, _sym=sym):
                """[RC-2] Notify SEKALI sahaja per (sym, event) — walau DB gagal."""
                if not should_notify(_sym, event):
                    return
                full = f"<b>{_sym}</b>\n{text}"
                kw = {"parse_mode": "HTML"}
                if _mid:
                    kw["reply_to_message_id"] = _mid
                try:
                    bot.send_message(VIP_CHANNEL_ID, full, **kw)
                except Exception:
                    try:
                        bot.send_message(VIP_CHANNEL_ID, full, parse_mode="HTML")
                    except Exception:
                        pass

            updates = {}
            sl_touched = lo <= trade["sl"]
            # Conservative: jika candle sama sentuh SL dan TP, anggap SL dulu
            if sl_touched and not trade.get("sl_hit") and not trade.get("tp1_hit"):
                updates["sl_hit"] = True
                updates["closed"] = True
                loss_pct = (trade["sl"] - entry_price) / entry_price * 100
                notify("sl", f"❌ <b>SL HIT</b>\n💰 <code>${fmt(trade['sl'])}</code> | {loss_pct:.2f}%\n<i>(candle low ${fmt(lo)})</i>")
            else:
                if hi >= trade["tp1"] and not trade.get("tp1_hit"):
                    updates["tp1_hit"] = True
                    p = (trade["tp1"] - entry_price) / entry_price * 100
                    notify("tp1", f"✅ <b>TP1 HIT!</b>\n💰 <code>${fmt(trade['tp1'])}</code> | +{p:.2f}%\n🔒 SL → BE <code>${fmt(entry_price)}</code>")
                if hi >= trade["tp2"] and not trade.get("tp2_hit"):
                    updates["tp2_hit"] = True
                    p = (trade["tp2"] - entry_price) / entry_price * 100
                    notify("tp2", f"🚀 <b>TP2 HIT!</b>\n💰 <code>${fmt(trade['tp2'])}</code> | +{p:.2f}%\n📈 Trail SL → TP1")
                if hi >= trade["tp3"] and not trade.get("tp3_hit"):
                    updates["tp3_hit"] = True
                    updates["closed"]  = True
                    p = (trade["tp3"] - entry_price) / entry_price * 100
                    notify("tp3", f"🏆 <b>TP3 MOONSHOT!</b>\n💰 <code>${fmt(trade['tp3'])}</code> | +{p:.2f}%")
                # Breakeven exit selepas TP1 (sekali sahaja, dedupe dijamin)
                tp1_done = trade.get("tp1_hit") or updates.get("tp1_hit")
                if tp1_done and cp <= entry_price and not trade.get("closed") and not trade.get("be_hit") and not updates.get("tp3_hit"):
                    updates["be_hit"] = True
                    updates["closed"] = True
                    notify("be", f"🔒 <b>BREAKEVEN EXIT</b>\n💰 <code>${fmt(cp)}</code> (selepas TP1)")
            if updates:
                ok = update_signal(sym, updates)
                if not ok and updates.get("closed"):
                    CLOSED_LOCAL.add(sym)   # [RC-1] benteng terakhir anti-spam
                print(f"[MONITOR] {sym}: {list(updates.keys())} db={'OK' if ok else 'LOCAL-FALLBACK'}")
        except Exception as e:
            print(f"[MONITOR] {sym}: {type(e).__name__}: {e}")


# ==========================================
# 9. PULLBACK & BOS MONITORS
# ==========================================
def monitor_pullback_watchlist():
    if not IS_SCANNING or not PULLBACK_WATCHLIST:
        return
    with _watchlist_lock:
        items = list(PULLBACK_WATCHLIST.items())
    print(f"\n[PULLBACK MON] {len(items)} coins")
    remove = []
    for sym, data in items:
        try:
            if (time.time() - data["added"]) / 3600 > 8:
                remove.append(sym); continue
            candles_m5 = get_gateio_klines(sym, "5m", 50)
            if len(candles_m5) < 20: continue
            cp = candles_m5[-1]['c']
            if cp > data["entry"] or cp < data["fib_786"]:
                continue

            # Whale guard dari stored data (tiada extra API call)
            if data.get("whale") == "DISTRIBUTING":
                remove.append(sym); continue

            atr_m5    = calculate_atr(candles_m5, 14)
            swings_m5 = find_fractal_swings(candles_m5, lookback=1)
            struct_m5 = check_market_structure(swings_m5)
            if detect_choch(swings_m5, cp, struct_m5, atr=atr_m5) == 'CHOCH_BEAR':
                print(f"[{sym}] CHOCH BEAR M5 — remove")
                remove.append(sym); continue

            recent_10 = candles_m5[-10:]
            if sum(1 for c in recent_10 if c['c'] < c['o']) >= 8:
                print(f"[{sym}] SLOW DUMP — remove")
                remove.append(sym); continue

            avg_vol_m5 = sum(c['v'] for c in candles_m5[-20:-1]) / 19
            if candles_m5[-1]['v'] > avg_vol_m5 * 1.5:
                continue

            curr, prev = candles_m5[-1], candles_m5[-2]
            body       = abs(curr['c'] - curr['o'])
            lower_wick = min(curr['o'], curr['c']) - curr['l']
            is_pinbar   = lower_wick > body * 2 and curr['c'] > curr['o']
            is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                            and curr['c'] > prev['o'] and curr['o'] < prev['c'])
            if not (is_pinbar or is_engulfing):
                continue
            if clv(curr) < 0.1:        # [OPT-5]
                continue

            sess_n, sess_s, _ = get_trading_session()
            if sess_s < 0:
                continue

            sl_m5   = compute_final_sl(curr['c'], data["fib_786"], atr_m5, atr_mult=0.75, max_sl_pct=0.08)
            risk_m5 = curr['c'] - sl_m5
            if risk_m5 <= 0:
                continue
            pattern = "Pinbar" if is_pinbar else "Engulfing"
            p_win = estimate_win_probability(5, 1)   # pullback recovery base
            tp1_p, tp2_p = data["entry"], data["fib_500"]
            ev_p = expected_value(p_win, net_rr(curr['c'], sl_m5, tp1_p))
            sig = {
                "setup": f"🔄 PULLBACK RECOVERY ({pattern} M5)",
                "entry": curr['c'], "sl": sl_m5,
                "tp1": tp1_p, "tp2": tp2_p, "tp3": data["fib_500"] * 1.05,
                "rr1": round((tp1_p - curr['c']) / risk_m5, 2),
                "rr2": round((tp2_p - curr['c']) / risk_m5, 2),
                "rr1_net": round(net_rr(curr['c'], sl_m5, tp1_p), 2),
                "rr2_net": round(net_rr(curr['c'], sl_m5, tp2_p), 2),
                "p_win": p_win, "ev": round(ev_p, 2),
                "score": 4, "fib_zone": "N/A", "timeframe": "M5",
                "is_counter_trend": False, "session": sess_n,
                "whale_signal": data.get("whale", "NEUTRAL"), "choch": "NONE",
                "confluence_score": 5, "strong_count": 1,
                "confluence_labels": f"PULLBACK|{pattern}|CLV+|{sess_n}",
            }
            if send_signal(sym, sig, 0, btc_chg=0.0):
                remove.append(sym)
                print(f"[{sym}] 🚀 PULLBACK TRIGGERED!")
        except Exception as e:
            print(f"[PULLBACK ERR] {sym}: {e}")
    with _watchlist_lock:
        for sym in remove:
            if sym in PULLBACK_WATCHLIST: del PULLBACK_WATCHLIST[sym]

def monitor_bos_breaks():
    if not IS_SCANNING or not BOS_WATCHLIST:
        return
    with _watchlist_lock:
        items = list(BOS_WATCHLIST.items())
    print(f"\n[BOS MON] {len(items)} pending")
    remove = []
    for sym, data in items:
        try:
            if (time.time() - data["added"]) / 3600 > (BOS_WATCHLIST_TIMEOUT / 3600):
                remove.append(sym); continue
            cp = get_gateio_price(sym)
            if cp <= 0: continue
            level, bos_type = data["level"], data["type"]
            sess_n, sess_s, _ = get_trading_session()
            if sess_s < 0:
                continue

            if bos_type == "HL":
                prox = (cp - level) / level * 100
                if cp < level:
                    print(f"[{sym}] 🔴 HL broken — remove")
                    remove.append(sym)
                elif 0 <= prox <= 1.5:
                    candles_m15 = get_gateio_klines(sym, "15m", 30)
                    if len(candles_m15) < 10: continue
                    curr, prev = candles_m15[-1], candles_m15[-2]
                    body       = abs(curr['c'] - curr['o'])
                    lower_wick = min(curr['o'], curr['c']) - curr['l']
                    is_pinbar   = lower_wick > body * 2 and curr['c'] > curr['o']
                    is_engulfing = (curr['c'] > curr['o'] and prev['c'] < prev['o']
                                    and curr['c'] > prev['o'] and curr['o'] < prev['c'])
                    if not (is_pinbar or is_engulfing):
                        continue
                    atr_m15 = calculate_atr(candles_m15, 14)
                    rng_c   = curr['h'] - curr['l']
                    has_disp = (curr['c'] > curr['o'] and body > rng_c * 0.45 and
                                atr_m15 > 0 and rng_c > atr_m15 * 0.4 and clv(curr) >= 0.1)
                    if not has_disp:
                        continue
                    if data.get("whale") == "DISTRIBUTING":
                        continue
                    sl_h  = compute_final_sl(cp, level, atr_m15, atr_mult=1.0, max_sl_pct=0.08)
                    rk    = cp - sl_h
                    if rk <= 0: continue
                    pattern = "Pinbar" if is_pinbar else "Engulfing"
                    p_win = estimate_win_probability(5, 1)
                    tp1_h = cp + rk * 1.618
                    sig = {
                        "setup": f"🏗️ HL HOLD — {pattern} (M15)",
                        "entry": cp, "sl": sl_h,
                        "tp1": tp1_h, "tp2": cp + rk * 2.618, "tp3": cp + rk * 4.236,
                        "rr1": 1.618, "rr2": 2.618,
                        "rr1_net": round(net_rr(cp, sl_h, tp1_h), 2),
                        "rr2_net": round(net_rr(cp, sl_h, cp + rk * 2.618), 2),
                        "p_win": p_win,
                        "ev": round(expected_value(p_win, net_rr(cp, sl_h, tp1_h)), 2),
                        "score": 4, "fib_zone": "N/A", "timeframe": "M15",
                        "is_counter_trend": False,
                        "fib_500": cp + rk * 2.618, "fib_786": sl_h,
                        "session": sess_n, "whale_signal": data.get("whale", "NEUTRAL"),
                        "choch": "NONE",
                        "confluence_score": 5, "strong_count": 1,
                        "confluence_labels": f"HL_HOLD|{pattern}|{sess_n}",
                    }
                    if send_signal(sym, sig, 0, btc_chg=0.0):
                        remove.append(sym)

            elif bos_type == "LH" and cp > level:
                print(f"[{sym}] 💥 BOS BREAK LH ${fmt(level)}")
                candles_m5b = get_gateio_klines(sym, "5m", 30)
                atr_b = calculate_atr(candles_m5b, 14) if len(candles_m5b) >= 15 else cp * 0.015
                if len(candles_m5b) >= 2:
                    cb = candles_m5b[-1]
                    bb, rb = abs(cb['c'] - cb['o']), cb['h'] - cb['l']
                    if not (cb['c'] > cb['o'] and rb > atr_b * 0.5 and
                            bb > rb * 0.45 and clv(cb) >= 0.1):
                        continue
                if data.get("whale") == "DISTRIBUTING":
                    continue
                sl_b = compute_final_sl(cp, level * 0.98, atr_b, atr_mult=1.0, max_sl_pct=0.08)
                rk   = cp - sl_b
                if rk <= 0:
                    remove.append(sym); continue
                p_win = estimate_win_probability(6, 1)   # CHOCH_BULL strong
                tp1_b = cp + rk * 1.618
                sig = {
                    "setup": "💥 BOS BREAK — Bullish (LH Tembus)",
                    "entry": cp, "sl": sl_b,
                    "tp1": tp1_b, "tp2": cp + rk * 2.618, "tp3": cp + rk * 4.236,
                    "rr1": 1.618, "rr2": 2.618,
                    "rr1_net": round(net_rr(cp, sl_b, tp1_b), 2),
                    "rr2_net": round(net_rr(cp, sl_b, cp + rk * 2.618), 2),
                    "p_win": p_win,
                    "ev": round(expected_value(p_win, net_rr(cp, sl_b, tp1_b)), 2),
                    "score": 4, "fib_zone": "N/A", "timeframe": "M5",
                    "is_counter_trend": False, "session": sess_n,
                    "whale_signal": data.get("whale", "NEUTRAL"), "choch": "CHOCH_BULL",
                    "confluence_score": 6, "strong_count": 1,
                    "confluence_labels": f"BOS_BREAK|LH|DISP|{sess_n}",
                }
                if send_signal(sym, sig, 0, btc_chg=0.0):
                    remove.append(sym)
        except Exception as e:
            print(f"[BOS ERR] {sym}: {e}")
    with _watchlist_lock:
        for sym in remove:
            if sym in BOS_WATCHLIST: del BOS_WATCHLIST[sym]

def fast_track_watchlist():
    with _watchlist_lock:
        now = time.time()
        expired = [s for s, t in WATCHLIST.items() if now - t > WATCHLIST_TIMEOUT]
        for s in expired: del WATCHLIST[s]
    monitor_pullback_watchlist()
    monitor_bos_breaks()

# ==========================================
# 10. TELEGRAM COMMANDS — [SEC-1] from_user auth
# ==========================================
@bot.message_handler(commands=["start", "menu"])
def cmd_start(msg):
    if not is_admin_user(msg.from_user.id):
        return
    cfg    = get_config()
    active = get_active_trades()
    uptime = int((time.time() - START_TIME) / 60)
    lbl    = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")
    sess_n, _, sess_e = get_trading_session()
    text = (
        f"🏴‍☠️ <b>ALPHA v16.4 JOURNAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ <code>{uptime}m</code> · 💼 <code>{len(active)} aktif</code> · "
        f"{'🟢 SCAN' if IS_SCANNING else '⛔ STOP'}\n"
        f"🕐 <code>{sess_e} {sess_n}</code> · ⚡ <code>{SCAN_MODE.upper()}</code> · {lbl}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏴‍️ <b>E1 Pullback</b>: SMC+Fib+Sweep+CHOCH\n"
        f"⚡ <b>E2 Momentum</b>: Vol Z-score+CLV\n"
        f"🚀 <b>E3 Breakout</b>: Donchian+ADX(+DI)+RSI↑\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧮 <b>Math Engine:</b>\n"
        f"├ Wilder ATR/RSI/ADX (RMA sebenar)\n"
        f"├ Volume Z-score (statistik σ)\n"
        f"├ CLV/Chaikin A/D (money flow)\n"
        f"├ P(win) logistic + EV gate ≥{MIN_EV}R\n"
        f"└ Fee-adjusted RR (0.4% round-trip)"
    )
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(InlineKeyboardButton("🟢 Soft", callback_data="tune:soft"),
           InlineKeyboardButton("🟡 Std",  callback_data="tune:standard"),
           InlineKeyboardButton("🔴 Hard", callback_data="tune:hard"))
    kb.add(InlineKeyboardButton("🏴‍☠️ Pullback", callback_data="mode:pullback"),
           InlineKeyboardButton("⚡ Momentum",  callback_data="mode:momentum"),
           InlineKeyboardButton("🚀 Breakout",  callback_data="mode:breakout"))
    kb.add(InlineKeyboardButton("🔄 Both", callback_data="mode:both"),
           InlineKeyboardButton("🌐 All",  callback_data="mode:all"))
    kb.add(InlineKeyboardButton("▶️ Mula",   callback_data="scan_on"),
           InlineKeyboardButton("⏸ Henti",  callback_data="scan_off"),
           InlineKeyboardButton("📓 Journal", callback_data="journal"))
    kb.add(InlineKeyboardButton("📊 Status", callback_data="status"),
           InlineKeyboardButton("❓ Help",   callback_data="help"))
    bot.send_message(msg.chat.id, text, parse_mode="HTML", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tune:"))
def cb_tune(call):
    if not is_admin_user(call.from_user.id):       # [SEC-1]
        bot.answer_callback_query(call.id, "⛔ Unauthorized")
        return
    bot.answer_callback_query(call.id)
    ok, lbl = apply_preset(call.data.split(":")[1])
    if ok:
        try:
            bot.edit_message_text(f"✅ <b>PRESET</b>: {lbl}", call.message.chat.id,
                                  call.message.message_id, parse_mode="HTML")
        except Exception:
            pass
        alert_admin(f"🎛️ Preset: {lbl}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("mode:"))
def cb_mode(call):
    global SCAN_MODE
    if not is_admin_user(call.from_user.id):       # [SEC-1]
        bot.answer_callback_query(call.id, "⛔ Unauthorized")
        return
    bot.answer_callback_query(call.id)
    nm = call.data.split(":")[1]
    if nm in ["pullback", "momentum", "both", "breakout", "all"]:
        SCAN_MODE = nm
        bot.send_message(call.message.chat.id, f"✅ Mode: <b>{nm.upper()}</b>", parse_mode="HTML")

@bot.callback_query_handler(func=lambda c: c.data in ["scan_on", "scan_off", "journal", "status", "help"])
def cb_actions(call):
    global IS_SCANNING
    if not is_admin_user(call.from_user.id):       # [SEC-1]
        bot.answer_callback_query(call.id, "⛔ Unauthorized")
        return
    bot.answer_callback_query(call.id)
    if call.data == "scan_on":
        IS_SCANNING = True
        bot.send_message(call.message.chat.id, "▶️ Scan AKTIF.")
        threading.Thread(target=scan_once, daemon=True).start()
    elif call.data == "scan_off":
        IS_SCANNING = False
        bot.send_message(call.message.chat.id, "⏸ Scan BERHENTI.")
    elif call.data == "journal":
        bot.send_message(call.message.chat.id, generate_journal(), parse_mode="HTML")
    elif call.data == "status":
        _send_status(call.message.chat.id)
    elif call.data == "help":
        _send_help(call.message.chat.id)

@bot.message_handler(commands=["tune"])
def cmd_tune(msg):
    if not is_admin_user(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        a = get_config().get("active_preset", "standard")
        bot.reply_to(msg, (
            f"🎛️ <b>TUNE</b> — Aktif: {PRESETS.get(a, {}).get('label', '?')}\n\n"
            f"🟢 <code>/tune soft</code> — $500K, syarat 3/5\n"
            f"🟡 <code>/tune standard</code> — $1M, syarat 3/5\n"
            f"🔴 <code>/tune hard</code> — $2.5M, syarat 4/5\n\n"
            f"<i>Threshold rendah + EV gate ≥{MIN_EV}R jaga kualiti</i>"
        ), parse_mode="HTML")
        return
    ok, lbl = apply_preset(parts[1].lower())
    bot.reply_to(msg, f"✅ {lbl}" if ok else "❌ Preset tidak sah")

@bot.message_handler(commands=["mode"])
def cmd_mode(msg):
    global SCAN_MODE
    if not is_admin_user(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, f"⚡ Mode: <b>{SCAN_MODE.upper()}</b>\n<code>/mode pullback|momentum|breakout|both|all</code>", parse_mode="HTML")
        return
    nm = parts[1].lower()
    if nm in ["pullback", "momentum", "both", "breakout", "all"]:
        SCAN_MODE = nm
        bot.reply_to(msg, f"✅ Mode: <b>{nm.upper()}</b>", parse_mode="HTML")
    else:
        bot.reply_to(msg, "❌ Tidak sah")

@bot.message_handler(commands=["pair"])
def cmd_pair(msg):
    if not is_admin_user(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "/pair [SYMBOL]")
        return
    sym = sanitize_symbol(parts[1])               # [SEC-2]
    if not sym:
        bot.reply_to(msg, "❌ Symbol tidak sah")
        return
    bot.reply_to(msg, f"🔍 Analisa <code>{sym}</code>...", parse_mode="HTML")
    def _do():
        r = analyze_smc_pa(sym, verbose=False)
        eng = "🏴‍️ PULLBACK (H1)"
        if not r:
            r = analyze_breakout_sniper(sym, verbose=False)
            eng = "🚀 BREAKOUT (H1)"
        if not r:
            r = analyze_early_momentum(sym, verbose=False)
            eng = "⚡ MOMENTUM (M15)"
        if r:
            sy = r.get('syarat', {})
            sy_ln = " ".join(f"{'✅' if v else '⬜'}{SYARAT_SHORT.get(k, k[:5])}"
                             for k, v in sy.items()) if sy else "—"
            grade, g_emoji = compute_grade(
                r.get('syarat_pass', 3), r.get('ev', 0),
                r.get('whale_signal', 'NEUTRAL'), r.get('choch', 'NONE'),
                r.get('session') == "DEAD_ZONE")
            bot.send_message(msg.chat.id, (
                f"<b>{sym}/USDT</b> — {eng}\n"
                f"{g_emoji} <b>GRED {grade}</b> · 📋 {r.get('syarat_pass', 0)}/5 · "
                f"🎲 <b>{r.get('p_win', 0)*100:.0f}%</b> · EV <b>{r.get('ev', 0):+.2f}R</b>\n"
                f"📋 {sy_ln}\n"
                f"💰 <code>${fmt(r['entry'])}</code> → 🛑 <code>${fmt(r['sl'])}</code> · RRnet <b>{r.get('rr1_net', 0):.2f}</b>\n"
                f"🧠 {safe_html(r['setup'], 40)}\n"
                f"🏛️ {r.get('session', '?')} · 🐳 {r.get('whale_signal', '?')}"
            ), parse_mode="HTML")
        else:
            bot.send_message(msg.chat.id, f"❌ <code>{sym}</code>: Tiada setup valid", parse_mode="HTML")
    threading.Thread(target=_do, daemon=True).start()

@bot.message_handler(commands=["scan"])
def cmd_scan(msg):
    if not is_admin_user(msg.from_user.id):
        return
    bot.reply_to(msg, "⚙️ Scan dipaksa...")
    threading.Thread(target=scan_once, daemon=True).start()

def _send_status(chat_id):
    cfg    = get_config()
    active = get_active_trades()
    lbl    = PRESETS.get(cfg.get("active_preset", "standard"), {}).get("label", "Custom")
    sess_n, _, sess_e = get_trading_session()
    with _watchlist_lock:
        pw, bw, w = len(PULLBACK_WATCHLIST), len(BOS_WATCHLIST), len(WATCHLIST)
    bot.send_message(chat_id, (
        f"📊 <b>STATUS v16.0</b>\n"
        f"Scan {'🟢' if IS_SCANNING else '⛔'} · <code>{SCAN_MODE.upper()}</code> · {lbl}\n"
        f"🕐 <code>{sess_e}{sess_n}</code> · 💼 <code>{len(active)}</code> aktif\n\n"
        f"📌 WL: M15 <code>{w}</code> · M5 <code>{pw}</code> · BOS <code>{bw}</code>\n"
        f"Vol ≥ <code>${cfg['min_vol_24h']/1e6:.1f}M</code> · Syarat ≥ <code>{cfg.get('min_syarat', 3)}/5</code> · EV ≥ <code>{cfg.get('min_ev', MIN_EV)}R</code>\n"
        f"🩺 Scan terakhir: <code>{(str(int((time.time()-LAST_SCAN_INFO['ts'])/60))+'m lalu · '+str(LAST_SCAN_INFO['signals'])+' signal') if LAST_SCAN_INFO else 'belum ada'}</code> · /diag"
    ), parse_mode="HTML")

@bot.message_handler(commands=["status"])
def cmd_status(msg):
    if not is_admin_user(msg.from_user.id):
        return
    _send_status(msg.chat.id)

@bot.message_handler(commands=["diag"])
def cmd_diag(msg):
    """[RC-7] Diagnostik — jawab soalan 'mana signal?' dengan data."""
    if not is_admin_user(msg.from_user.id):
        return
    i = LAST_SCAN_INFO
    if not i:
        bot.reply_to(msg, "⏳ Belum ada scan selesai sejak restart. Cuba /scan dulu.")
        return
    age = int((time.time() - i["ts"]) / 60)
    rej_lines = "\n".join(f"├ {k}: {v}" for k, v in i.get("rejects", {}).items()) or "├ —"
    bot.reply_to(msg, (
        f"🩺 <b>DIAGNOSTIK SCAN TERAKHIR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ <code>{age}m</code> lalu · tempoh <code>{i['dur']}s</code> · <code>{i['session']}</code>\n"
        f"🎯 Candidates <code>{i['cands']}</code> · Dianalisa <code>{i['scanned']}</code>\n"
        f"📡 Signal <b>{i['signals']}</b> · Errors <code>{i['errors']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📋 Sebab reject:</b>\n<code>{safe_html(rej_lines, 500)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 Local-closed: <code>{len(CLOSED_LOCAL)}</code> · Dedupe: <code>{len(NOTIFIED)}</code>\n"
        f"<i>COOLDOWN tinggi = normal selepas banyak signal.\n"
        f"E*_ERROR tinggi = masalah API/data.\n"
        f"EV_GATE tinggi = setup wujud tapi expectancy lemah.</i>"
    ), parse_mode="HTML")

@bot.message_handler(commands=["journal"])
def cmd_journal(msg):
    if not is_admin_user(msg.from_user.id):
        return
    bot.reply_to(msg, generate_journal(), parse_mode="HTML")

@bot.message_handler(commands=["modal"])
def cmd_modal(msg):
    if not is_admin_user(msg.from_user.id):
        return
    args = msg.text.split()
    if len(args) < 2:
        cap, risk = get_user_capital(int(ADMIN_ID))
        bot.reply_to(msg, f"💼 ${cap:,.2f} · Risk {risk}%\nSet: <code>/modal 1000</code>", parse_mode="HTML")
        return
    try:
        new_cap = float(args[1])
        # [SEC-4] Bound validation
        if not (10 <= new_cap <= 10_000_000):
            bot.reply_to(msg, "⚠️ Modal mesti $10 – $10,000,000")
            return
        set_user_capital(int(ADMIN_ID), new_cap)
        bot.reply_to(msg, (
            f"✅ Modal: <b>${new_cap:,.2f}</b>\n"
            f"Risk 2%/trade = ${new_cap*0.02:,.2f}\n"
            f"Max position 50% = ${new_cap*0.50:,.2f}"
        ), parse_mode="HTML")
    except (ValueError, OverflowError):
        bot.reply_to(msg, "❌ Format: <code>/modal 1000</code>", parse_mode="HTML")

def _send_help(chat_id):
    bot.send_message(chat_id, (
        "📖 <b>ALPHA v16.4</b>\n\n"
        "/start — Menu\n/scan — Paksa scan\n/pair [SYM] — Analisis\n"
        "/journal — Laporan 7D\n/status — Status\n/diag — Diagnostik scan (kenapa tiada signal)\n"
        "/mode [M] — pullback|momentum|breakout|both|all\n"
        "/tune [P] — soft|standard|hard\n/modal [N] — Set modal\n\n"
        "<b>🧮 EV Gate:</b> Entry hanya jika\n"
        "EV = P(win)·RRnet − (1−P) ≥ +0.15R"
    ), parse_mode="HTML")

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    if not is_admin_user(msg.from_user.id):
        return
    _send_help(msg.chat.id)

# ==========================================
# 11. JOURNAL — Win-rate per dimensi
# ==========================================
def realized_r(trade):
    """
    [v16.4] Kira R SEBENAR setiap trade ikut model skala: 50% TP1 / 30% TP2 / 20% TP3,
    SL→BE selepas TP1. Outcome mutually exclusive ikut keutamaan (atasi bug double-count).
    Pulangan: (R_value, bucket_label)
    """
    entry = trade.get("entry", 0) or 0
    sl    = trade.get("sl", 0) or 0
    tp1   = trade.get("tp1", 0) or 0
    tp2   = trade.get("tp2", 0) or 0
    tp3   = trade.get("tp3", 0) or 0
    risk  = entry - sl
    if risk <= 0:
        return 0.0, "INVALID"
    rr1 = trade.get("rr1") or ((tp1 - entry) / risk if tp1 else 0)
    rr2 = trade.get("rr2") or ((tp2 - entry) / risk if tp2 else 0)
    rr3 = (tp3 - entry) / risk if tp3 else rr2 * 1.6

    # Keutamaan: hasil terjauh menang (TP3 > TP2 > TP1/BE > SL > Open)
    if trade.get("tp3_hit"):
        return 0.5*rr1 + 0.3*rr2 + 0.2*rr3, "TP3"
    if trade.get("tp2_hit"):
        return 0.5*rr1 + 0.3*rr2, "TP2"          # baki 20% diandai BE
    if trade.get("be_hit"):
        return 0.5*rr1, "BE"                      # 50% di TP1, baki balik BE
    if trade.get("tp1_hit"):
        return 0.5*rr1, "TP1"                     # sama: ambil separa di TP1
    if trade.get("sl_hit"):
        return -1.0, "SL"
    return None, "OPEN"

def generate_journal():
    trades = get_signals_since(7)
    if not trades:
        return "📓 <b>JOURNAL (7D)</b>\n\nTiada signal lagi."

    FEE_R = (FEE_PER_SIDE * 2) / 0.03   # anggaran fee dalam R (SL dist ~3%)
    buckets = {"TP3": 0, "TP2": 0, "TP1": 0, "BE": 0, "SL": 0, "OPEN": 0, "INVALID": 0}
    closed_R = []          # R bersih setiap trade tertutup
    win_R, loss_R = 0.0, 0.0
    eng_stat  = {}         # engine → [R_total, n_closed, n_win]

    for t in trades:
        r, bucket = realized_r(t)
        buckets[bucket] = buckets.get(bucket, 0) + 1
        tf = str(t.get("timeframe", "?"))
        eng_stat.setdefault(tf, [0.0, 0, 0])
        if r is not None and bucket not in ("OPEN", "INVALID"):
            r_net = r - FEE_R
            closed_R.append(r_net)
            if r_net > 0: win_R += r_net
            else:         loss_R += -r_net
            eng_stat[tf][0] += r_net
            eng_stat[tf][1] += 1
            if r_net > 0: eng_stat[tf][2] += 1

    n_closed = len(closed_R)
    total_R  = sum(closed_R)
    exp_R    = total_R / n_closed if n_closed else 0
    n_win    = sum(1 for r in closed_R if r > 0)
    wr       = n_win / n_closed * 100 if n_closed else 0
    pf       = win_R / loss_R if loss_R > 0 else (99.9 if win_R > 0 else 0)

    # Verdict jujur berdasar ekspektasi + saiz sampel
    if n_closed < 30:
        verdict = f"⚠️ SAMPEL KECIL ({n_closed}) — belum boleh sahkan"
    elif exp_R >= 0.15 and pf >= 1.3:
        verdict = f"🟢 PROFITABLE — +{exp_R:.2f}R/trade"
    elif exp_R >= 0.0:
        verdict = f"🟡 MARGINAL — {exp_R:+.2f}R/trade (nyaris breakeven)"
    else:
        verdict = f"🔴 RUGI — {exp_R:+.2f}R/trade"

    # Anggaran wang (guna modal admin)
    cap, risk_pct = get_user_capital(int(ADMIN_ID) if ADMIN_ID else 0)
    risk_usd = cap * (risk_pct / 100.0)
    pnl_usd  = total_R * risk_usd

    out = (
        f"📓 <b>ALPHA JOURNAL (7D)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{verdict}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Closed <b>{n_closed}</b> · Open <code>{buckets['OPEN']}</code> · WR <b>{wr:.0f}%</b>\n"
        f"💰 Jumlah <b>{total_R:+.1f}R</b> (≈${pnl_usd:+,.0f}) · Avg <b>{exp_R:+.2f}R</b>\n"
        f"⚖️ Profit Factor <b>{pf:.2f}</b> <i>(menang/kalah)</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Taburan hasil:</b>\n"
        f"🏆 TP3 <code>{buckets['TP3']}</code> · 🚀 TP2 <code>{buckets['TP2']}</code> · "
        f"✅ TP1 <code>{buckets['TP1']}</code> · 🔒 BE <code>{buckets['BE']}</code> · "
        f"❌ SL <code>{buckets['SL']}</code>\n"
    )

    # Per-engine dengan R sebenar
    out += "━━━━━━━━━━━━━━━━━━━━\n<b>⏱ Engine (R sebenar):</b>\n"
    eng_lines = []
    for tf, (rt, nc, nw) in sorted(eng_stat.items(), key=lambda x: -x[1][0]):
        if nc == 0:
            continue
        ewr = nw / nc * 100
        avg = rt / nc
        eng_lines.append(f"{tf}: {nc} trade · {rt:+.1f}R · avg {avg:+.2f}R · WR {ewr:.0f}%")
    out += f"<code>{safe_html(chr(10).join(eng_lines) or '—', 350)}</code>\n"

    # Session & Whale — graceful (kesan migration belum jalan)
    def dim_breakdown(key):
        d = {}
        for t in trades:
            r, bucket = realized_r(t)
            if r is None or bucket in ("OPEN", "INVALID"):
                continue
            k = t.get(key)
            k = str(k) if k not in (None, "", "None") else "∅"
            d.setdefault(k, [0.0, 0, 0])
            d[k][0] += r - FEE_R
            d[k][1] += 1
            if (r - FEE_R) > 0: d[k][2] += 1
        return d

    sess = dim_breakdown("session")
    only_null = len(sess) == 1 and "∅" in sess
    if only_null:
        out += ("━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ <b>Session/Whale: data kosong</b>\n"
                "<i>Jalankan SQL migration (header fail) — kolum belum wujud, "
                "jadi trade lama tak rekod session/whale. Trade BARU selepas migration "
                "akan terisi.</i>\n")
    else:
        out += "━━━━━━━━━━━━━━━━━━━━\n<b>🕐 Session (R):</b>\n"
        sl_lines = [f"{k}: {n} · {rt:+.1f}R · WR {w/n*100:.0f}%"
                    for k, (rt, n, w) in sorted(sess.items(), key=lambda x: -x[1][0]) if n]
        out += f"<code>{safe_html(chr(10).join(sl_lines), 300)}</code>\n"
        whale = dim_breakdown("whale")
        out += "\n<b>🐳 Whale (R):</b>\n"
        wl_lines = [f"{k}: {n} · {rt:+.1f}R · WR {w/n*100:.0f}%"
                    for k, (rt, n, w) in sorted(whale.items(), key=lambda x: -x[1][0]) if n]
        out += f"<code>{safe_html(chr(10).join(wl_lines), 300)}</code>\n"

    out += (f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Model: skala 50/30/20, SL→BE selepas TP1, tolak fee {FEE_R:.2f}R. "
            f"R = untung relatif kpd risiko 1 trade.</i>")
    return out


# ==========================================
# 12. SCHEDULER & MAIN — [SEC-5] restart wrapper
# ==========================================
def run_scheduler():
    schedule.every(5).minutes.do(lambda: threading.Thread(target=scan_once,             daemon=True).start())
    schedule.every(5).minutes.do(lambda: threading.Thread(target=monitor_active_trades, daemon=True).start())
    schedule.every(30).seconds.do(lambda: threading.Thread(target=fast_track_watchlist, daemon=True).start())
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"[SCHEDULER ERR] {e}")
        time.sleep(1)

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALPHA v16.4 JOURNAL ACTIVE")
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
        f"🏴‍☠️ ALPHA v16.4 JOURNAL DEPLOYED\n"
        f"Mode: {SCAN_MODE.upper()} | Session: {sess_e}{sess_n}\n\n"
        f"[DEBUG] 13 bugs fixed\n"
        f"[SECURITY] 6 issues patched\n"
        f"[MATH] Wilder RMA · Z-score · CLV/Chaikin · EV gate {MIN_EV}R\n"
        f"[UI] Quality bar · P(win) · Net-RR · /diag\n"
        f"[v16.1] Anti-spam dedupe · DB resilient · Scan isolation\n"
        f"⚠️ Jalankan SQL migration dalam header fail (sekali)"
    )
    threading.Thread(target=scan_once, daemon=True).start()
    # [SEC-5] Auto-restart polling — bot tak mati senyap
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"[POLLING CRASH] {e} — restart 10s")
            time.sleep(10)
