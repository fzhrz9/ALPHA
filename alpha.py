"""
ALPHA — Pre-CEX Listing Scout
==============================
Niche: Token $5M–$50M MC, ada di DEX, BELUM listing di CEX besar.
Edge: Masuk sebelum Binance/OKX/Bybit listing. Keluar bila Nova7 pick up.

Sumber discovery:
  - DexScreener /token-boosts (realtime, percuma, unlimited)
  - CoinGecko /search/trending (1 call per refresh — bukan loop kategori)

Persistence:
  - Supabase PostgreSQL (percuma, kekal merentas restart)
  - Tiada local JSON untuk data kritikal

Rate limit strategy:
  - DexScreener: unlimited → boleh panggil bebas
  - CoinGecko: hanya 1 endpoint, ~6 calls/hari → selamat
  - GeckoTerminal: ~30 req/min → delay 2s antara panggilan
  - GoPlus: ~5 req/s → delay 0.3s
  - CEX checks: Binance/OKX/Bybit public API, no auth
"""

import os, time, json, requests, threading, traceback, schedule
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from supabase import create_client, Client

# ================================================================
# 1. KONFIGURASI
# ================================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
VIP_CHANNEL_ID     = os.environ.get("VIP_CHANNEL_ID")
ADMIN_ID           = os.environ.get("ADMIN_ID")
CG_API_KEY         = os.environ.get("CG_API_KEY", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL")
SUPABASE_KEY       = os.environ.get("SUPABASE_KEY")  # anon/public key

bot       = TeleBot(TELEGRAM_BOT_TOKEN)
START_TIME = time.time()

# Supabase client — persistent storage
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def alert_admin(text):
    try:
        bot.send_message(
            ADMIN_ID,
            f"🚨 <b>ALPHA SYSTEM</b>\n<pre>{str(text)[:800]}</pre>",
            parse_mode="HTML"
        )
    except Exception:
        pass

# ================================================================
# 2. SUPABASE HELPERS
# Schema SQL (jalankan sekali di Supabase SQL Editor):
#
# CREATE TABLE signals (
#   id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
#   contract   TEXT UNIQUE NOT NULL,
#   symbol     TEXT, name TEXT, network TEXT,
#   entry      FLOAT, sl FLOAT, tp1 FLOAT, tp2 FLOAT, tp3 FLOAT,
#   rr1 FLOAT, rr2 FLOAT, rr3 FLOAT,
#   atr FLOAT, rsi TEXT, fibo TEXT,
#   mc FLOAT, liquidity FLOAT, volume_24h FLOAT,
#   narrative TEXT, verdict TEXT,
#   tp1_hit BOOL DEFAULT false, tp2_hit BOOL DEFAULT false,
#   tp3_hit BOOL DEFAULT false, sl_hit BOOL DEFAULT false,
#   closed    BOOL DEFAULT false,
#   msg_id    BIGINT,
#   sent_at   BIGINT DEFAULT extract(epoch from now())
# );
#
# CREATE TABLE sent_pool (
#   key      TEXT PRIMARY KEY,
#   sent_at  BIGINT
# );
#
# CREATE TABLE config (
#   key   TEXT PRIMARY KEY,
#   value TEXT
# );
# ================================================================

DEFAULT_CONFIG = {
    "mc_min":            5_000_000,    # $5M
    "mc_max":           50_000_000,    # $50M
    "liq_min":             500_000,    # $500K
    "vol_mc_ratio_min":      0.10,     # 10% — token mesti aktif
    "change_24h_min":         3.0,     # +3%
    "atr_sl_mult":            1.5,     # SL = ATR × 1.5
    "cooldown_hours":          24,     # 24 jam sebelum token sama boleh signal semula
    "min_token_age_days":       7,     # token mesti wujud > 7 hari
    "score_pass":               3,     # lulus jika >= 3 dari 4
}

_config_cache = {}
_config_loaded_at = 0

def get_config():
    """Muatkan config dari Supabase, cache 5 minit."""
    global _config_cache, _config_loaded_at
    if _config_cache and time.time() - _config_loaded_at < 300:
        return _config_cache
    try:
        rows = sb.table("config").select("key, value").execute().data
        cfg  = DEFAULT_CONFIG.copy()
        for row in rows:
            k, v = row["key"], row["value"]
            if k in cfg:
                cfg[k] = type(DEFAULT_CONFIG[k])(v)
        _config_cache     = cfg
        _config_loaded_at = time.time()
        return cfg
    except Exception:
        return _config_cache or DEFAULT_CONFIG.copy()

def set_config(key, value):
    try:
        sb.table("config").upsert({"key": key, "value": str(value)}).execute()
        _config_cache[key] = type(DEFAULT_CONFIG.get(key, value))(value)
    except Exception as e:
        print(f"[CONFIG] set error: {e}")

def is_in_cooldown(contract):
    """Semak sama ada token dalam cooldown (dari Supabase sent_pool)."""
    try:
        cfg     = get_config()
        cutoff  = int(time.time()) - int(cfg["cooldown_hours"] * 3600)
        rows    = sb.table("sent_pool").select("sent_at").eq("key", contract).execute().data
        if not rows:
            return False
        return rows[0]["sent_at"] > cutoff
    except Exception:
        return False

def add_cooldown(contract):
    try:
        sb.table("sent_pool").upsert({"key": contract, "sent_at": int(time.time())}).execute()
    except Exception as e:
        print(f"[COOLDOWN] add error: {e}")

def is_active_trade(contract):
    try:
        rows = sb.table("signals").select("closed").eq("contract", contract).eq("closed", False).execute().data
        return bool(rows)
    except Exception:
        return False

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
        rows   = sb.table("signals").select("*").gte("sent_at", cutoff).execute().data
        return rows
    except Exception:
        return []

# ================================================================
# 3. HELPER UMUM
# ================================================================
def fmt(val):
    if val == 0: return "0.00"
    if abs(val) < 0.000001: return f"{val:.10f}"
    if abs(val) < 0.001:    return f"{val:.8f}"
    if abs(val) < 1.0:      return f"{val:.6f}"
    if abs(val) < 1000:     return f"{val:.4f}"
    return f"{val:,.2f}"

# ================================================================
# 4. API FETCHERS
# ================================================================

# ── DexScreener ──────────────────────────────────────────────────
def get_ds_boosted_top(limit=30):
    """Token teratas yang sedang diboosted di DexScreener (realtime)."""
    try:
        r = requests.get(
            "https://api.dexscreener.com/token-boosts/top/v1",
            timeout=8
        ).json()
        return r[:limit] if isinstance(r, list) else []
    except Exception:
        return []

def get_ds_boosted_latest(limit=20):
    """Token terbaru yang mula diboosted (isyarat awal)."""
    try:
        r = requests.get(
            "https://api.dexscreener.com/token-boosts/latest/v1",
            timeout=8
        ).json()
        return r[:limit] if isinstance(r, list) else []
    except Exception:
        return []

def get_dex_by_ca(chain, ca):
    """Ambil data DEX untuk satu token via contract address."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
        r   = requests.get(url, timeout=8).json()
        pairs = r.get("pairs") or []
        if not pairs:
            return None
        # Ambil pair terliquid dalam chain yang betul
        filtered = [p for p in pairs if p.get("chainId", "").lower() == chain.lower()]
        if not filtered:
            filtered = pairs
        pair = sorted(
            filtered,
            key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0),
            reverse=True
        )[0]
        return _parse_pair(pair)
    except Exception:
        return None

def get_dex_by_sym(symbol):
    """Cari token berdasarkan simbol — ambil pair paling liquid."""
    try:
        url   = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
        r     = requests.get(url, timeout=8).json()
        pairs = r.get("pairs") or []
        # Tapis kepada simbol yang tepat
        pairs = [
            p for p in pairs
            if p.get("baseToken", {}).get("symbol", "").upper() == symbol.upper()
        ]
        if not pairs:
            return None
        pair = sorted(
            pairs,
            key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0),
            reverse=True
        )[0]
        return _parse_pair(pair)
    except Exception:
        return None

def _parse_pair(pair):
    created  = pair.get("pairCreatedAt", 0) or 0
    age_days = (time.time() * 1000 - created) / 86_400_000 if created else 0
    return {
        "name":         pair.get("baseToken", {}).get("name", "?"),
        "symbol":       pair.get("baseToken", {}).get("symbol", "?"),
        "contract":     pair.get("baseToken", {}).get("address", ""),
        "price_usd":    float(pair.get("priceUsd", 0) or 0),
        "fdv":          float(pair.get("fdv", 0) or 0),
        "volume_24h":   float(pair.get("volume", {}).get("h24", 0) or 0),
        "change_24h":   float(pair.get("priceChange", {}).get("h24", 0) or 0),
        "change_5m":    float(pair.get("priceChange", {}).get("m5", 0) or 0),
        "liquidity":    float(pair.get("liquidity", {}).get("usd", 0) or 0),
        "network":      pair.get("chainId", "?").upper(),
        "chain_raw":    pair.get("chainId", "?").lower(),
        "pair_address": pair.get("pairAddress", ""),
        "age_days":     age_days,
        "age_display":  f"{int(age_days)}d" if age_days >= 1 else f"{int(age_days*24)}h",
    }

# ── CoinGecko (minimal calls) ─────────────────────────────────────
def get_cg_trending():
    """
    Ambil 7 trending coins dari CoinGecko.
    1 API call per refresh — bukan loop kategori.
    """
    try:
        h = {"x-cg-demo-api-key": CG_API_KEY} if CG_API_KEY else {}
        r = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            headers=h, timeout=8
        ).json()
        coins = r.get("coins") or []
        return [c["item"]["symbol"].upper() for c in coins[:7]]
    except Exception:
        return []

def verify_cg_listed(symbol):
    """
    Semak sama ada token sudah listed di CoinGecko.
    CEX listing biasanya mensyaratkan ini.
    Hemat API: hanya dipanggil untuk calon yang lulus pre-filter.
    """
    try:
        h   = {"x-cg-demo-api-key": CG_API_KEY} if CG_API_KEY else {}
        url = f"https://api.coingecko.com/api/v3/search?query={symbol}"
        r   = requests.get(url, headers=h, timeout=8).json()
        coins = r.get("coins") or []
        # Tepat cari simbol
        match = [c for c in coins if c.get("symbol", "").upper() == symbol.upper()]
        return bool(match)
    except Exception:
        return True  # jika gagal semak, anggap listed (elak false negative)

# ── CEX Checks (percuma, tanpa API key) ──────────────────────────
def check_binance(sym):
    try:
        r = requests.get(
            f"https://api.binance.com/api/v3/ticker/price?symbol={sym}USDT",
            timeout=3
        )
        return r.status_code == 200
    except Exception:
        return False

def check_okx(sym):
    try:
        r = requests.get(
            f"https://www.okx.com/api/v5/market/ticker?instId={sym}-USDT",
            timeout=3
        ).json()
        return bool(r.get("data"))
    except Exception:
        return False

def check_bybit(sym):
    try:
        r = requests.get(
            f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}USDT",
            timeout=3
        ).json()
        return bool((r.get("result") or {}).get("list"))
    except Exception:
        return False

def check_coinbase(sym):
    try:
        r = requests.get(
            f"https://api.exchange.coinbase.com/products/{sym}-USD",
            timeout=3
        )
        return r.status_code == 200
    except Exception:
        return False

def check_gateio(sym):
    try:
        r = requests.get(
            f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={sym}_USDT",
            timeout=3
        ).json()
        return bool(r)
    except Exception:
        return False

def check_all_cex(symbol):
    """
    Semak semua CEX major secara serentak (threading).
    Returns: dict dengan status setiap CEX dan sama ada listed_on_any.
    """
    results = {}
    threads = []

    checks = {
        "binance":  check_binance,
        "okx":      check_okx,
        "bybit":    check_bybit,
        "coinbase": check_coinbase,
        "gateio":   check_gateio,
    }

    def _check(name, fn, sym):
        try:
            results[name] = fn(sym)
        except Exception:
            results[name] = False

    for name, fn in checks.items():
        t = threading.Thread(target=_check, args=(name, fn, symbol), daemon=True)
        t.start()
        threads.append(t)

    # Tunggu semua selesai (max 5 saat)
    for t in threads:
        t.join(timeout=5)

    # Hanya Binance, OKX, Bybit dikira "major" — gate.io boleh overlap
    major_listed = any([results.get("binance"), results.get("okx"), results.get("bybit"), results.get("coinbase")])
    results["listed_on_any"] = major_listed

    listed_names = [k for k, v in results.items() if v and k != "listed_on_any"]
    results["label"] = ", ".join(listed_names) if listed_names else "Tiada listing major"
    return results

# ── Teknikal H4 (GeckoTerminal) ───────────────────────────────────
def get_technicals_h4(network, pair_address):
    """RSI(14) + ATR(14) Wilder + Fibonacci dari H4 candles."""
    try:
        if not pair_address:
            return "N/A", "N/A", 0, 0, 0

        net_map = {
            "solana": "solana", "base": "base", "bsc": "bsc",
            "ethereum": "eth", "eth": "eth", "arbitrum": "arbitrum",
        }
        gt_net = net_map.get(network.lower(), network.lower())

        url = (
            f"https://api.geckoterminal.com/api/v2/networks/{gt_net}"
            f"/pools/{pair_address}/ohlcv/hour?aggregate=4&limit=60"
        )
        time.sleep(1.5)  # hormat rate limit GeckoTerminal
        res   = requests.get(url, timeout=8).json()
        ohlcv = res.get("data", {}).get("attributes", {}).get("ohlcv_list", [])

        if len(ohlcv) < 14:
            return "Data Terhad", "Data Terhad", 0, 0, 0

        candles = list(reversed(ohlcv))
        closes  = [float(c[4]) for c in candles]
        highs   = [float(c[2]) for c in candles]
        lows    = [float(c[3]) for c in candles]

        # RSI(14) Wilder
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0.0))
            losses.append(max(-d, 0.0))
        avg_g = sum(gains[:14]) / 14
        avg_l = sum(losses[:14]) / 14
        for i in range(14, len(gains)):
            avg_g = (avg_g * 13 + gains[i])  / 14
            avg_l = (avg_l * 13 + losses[i]) / 14
        rsi = 100.0 if avg_l == 0 else 100 - (100 / (1 + avg_g / avg_l))
        rsi_lbl = (
            f"{rsi:.1f} 🔴 Overbought" if rsi >= 70
            else f"{rsi:.1f} 🟢 Oversold"  if rsi <= 35
            else f"{rsi:.1f} ⚪ Neutral"
        )

        # ATR(14) Wilder
        trs = [
            max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            for i in range(1, len(closes))
        ]
        atr = sum(trs[:14]) / 14
        for t in trs[14:]:
            atr = (atr * 13 + t) / 14

        # Fibonacci dari 20 candle terakhir
        n          = min(20, len(candles))
        swing_high = max(highs[-n:])
        swing_low  = min(lows[-n:])
        price      = closes[-1]
        rng        = swing_high - swing_low

        if rng > 0:
            f618 = swing_high - 0.618 * rng
            f786 = swing_high - 0.786 * rng
            f382 = swing_high - 0.382 * rng

            def near(a, b):
                return abs(a-b) / max(b, 1e-12) <= 0.04

            if price >= swing_high:   fibo_lbl = f"Breakout ATH ({fmt(swing_high)})"
            elif near(price, f618):   fibo_lbl = f"Golden 0.618 ({fmt(f618)})"
            elif near(price, f786):   fibo_lbl = f"Zon 0.786 ({fmt(f786)})"
            elif near(price, f382):   fibo_lbl = f"Zon 0.382 ({fmt(f382)})"
            elif price <= swing_low:  fibo_lbl = f"Lantai Support ({fmt(swing_low)})"
            else:                     fibo_lbl = f"S: {fmt(swing_low)} | R: {fmt(swing_high)}"
        else:
            fibo_lbl = "Range sempit"
            swing_high, swing_low = price * 1.20, price * 0.85

        return rsi_lbl, fibo_lbl, atr, swing_high, swing_low

    except Exception:
        return "N/A", "N/A", 0, 0, 0

# ── Security (GoPlus + RugCheck) ──────────────────────────────────
GOPLUS_CHAINS = {
    "bsc": "56", "base": "8453", "ethereum": "1", "eth": "1",
    "polygon": "137", "arbitrum": "42161",
}

def verify_security(network, contract):
    net = network.lower()
    try:
        if net in ("solana", "sol"):
            time.sleep(0.3)
            r     = requests.get(
                f"https://api.rugcheck.xyz/v1/tokens/{contract}/report",
                timeout=6
            ).json()
            score = r.get("score", 9999)
            if score < 300:  return True,  f"✅ SELAMAT (RugCheck: {score})"
            if score < 700:  return True,  f"⚠️ Notis (RugCheck: {score})"
            return False, f"🚨 BAHAYA (RugCheck: {score})"

        chain_id = GOPLUS_CHAINS.get(net)
        if not chain_id:
            return True, f"⚠️ Rantai '{net}' tiada semakan"

        time.sleep(0.3)
        r      = requests.get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
            f"?contract_addresses={contract}",
            timeout=7
        ).json()
        result = (r.get("result") or {}).get(contract.lower(), {})
        if not result:
            return True, "⚠️ Tiada rekod GoPlus"

        if result.get("is_honeypot") == "1":
            return False, "🚨 HONEYPOT!"

        buy_tax  = float(result.get("buy_tax",  0) or 0)
        sell_tax = float(result.get("sell_tax", 0) or 0)
        if buy_tax  > 15: return False, f"🚨 Buy Tax {buy_tax:.0f}%"
        if sell_tax > 15: return False, f"🚨 Sell Tax {sell_tax:.0f}%"

        notices = []
        if result.get("is_proxy")    == "1": notices.append("Proxy")
        if result.get("is_mintable") == "1": notices.append("Mintable")
        if 0 < buy_tax  <= 15: notices.append(f"Buy Tax {buy_tax:.0f}%")
        if 0 < sell_tax <= 15: notices.append(f"Sell Tax {sell_tax:.0f}%")

        if notices:
            return True, f"⚠️ Notis: {', '.join(notices)}"
        return True, "✅ SELAMAT (GoPlus)"

    except Exception as e:
        return True, f"⚠️ Semakan gagal"

# ── Binance listing check (utk info signal) ──────────────────────
def check_binance_listed(sym):
    return check_binance(sym)

# ================================================================
# 5. PRE-CEX FILTER
# ================================================================
def pre_filter(dex):
    """
    4 kriteria teras Pre-CEX Scout.
    Returns (passed, reason, score)
    """
    cfg = get_config()

    # Veto: MC di luar julat Pre-CEX
    if not (cfg["mc_min"] <= dex["fdv"] <= cfg["mc_max"]):
        return False, f"MC ${dex['fdv']/1e6:.1f}M luar julat Pre-CEX", 0

    # Veto: kecairan tidak mencukupi
    if dex["liquidity"] < cfg["liq_min"]:
        return False, f"Kecairan ${dex['liquidity']/1e3:.0f}K terlalu rendah", 0

    # Veto: token terlalu baru
    if dex["age_days"] < cfg["min_token_age_days"]:
        return False, f"Token hanya {dex['age_days']:.1f} hari — terlalu muda", 0

    score, fails = 0, []

    # Kriteria 1: Vol/MC — permintaan organik
    vol_mc = dex["volume_24h"] / max(dex["fdv"], 1)
    if vol_mc >= cfg["vol_mc_ratio_min"]:
        score += 1
    else:
        fails.append(f"Vol/MC {vol_mc*100:.1f}%")

    # Kriteria 2: Momentum positif 24H
    if dex["change_24h"] >= cfg["change_24h_min"]:
        score += 1
    else:
        fails.append(f"24H {dex['change_24h']:.1f}%")

    # Kriteria 3: Isipadu bermakna (> $500K = serious project)
    if dex["volume_24h"] >= cfg["liq_min"]:
        score += 1
    else:
        fails.append(f"Vol24H ${dex['volume_24h']/1e3:.0f}K")

    # Kriteria 4: Kecairan kukuh (2× minimum)
    if dex["liquidity"] >= cfg["liq_min"] * 2:
        score += 1
    else:
        fails.append("Kecairan sederhana")

    reason = " | ".join(fails) if fails else "BERSIH"
    if score >= cfg["score_pass"]:
        return True, f"Skor {score}/4: {reason}", score
    return False, f"Skor {score}/4: {reason}", score

# ================================================================
# 6. SIGNAL GENERATOR
# ================================================================
def send_signal(dex, cex_status, rsi_lbl, fibo_lbl, atr, swing_high, swing_low,
                sec_label, narrative, target=None):
    """
    Hantar signal Pre-CEX ke channel.
    SL = ATR × multiplier | TP = Fibonacci extensions
    """
    if target is None:
        target = VIP_CHANNEL_ID

    entry    = dex["price_usd"]
    cfg      = get_config()
    atr_mult = cfg["atr_sl_mult"]

    # SL
    if atr > 0:
        sl = entry - (atr * atr_mult)
    else:
        sl = entry * 0.82
    sl = max(sl, entry * 0.55)
    sl = max(sl, 0.0)
    risk = entry - sl if entry > sl else entry * 0.18

    # TP dari Fibonacci extension
    if swing_high > 0 and swing_low > 0 and swing_high > swing_low:
        rng = swing_high - swing_low
        tp1 = swing_high
        tp2 = swing_low + rng * 1.618
        tp3 = swing_low + rng * 2.618
    else:
        tp1 = entry * 1.25
        tp2 = entry * 1.60
        tp3 = entry * 2.20

    tp1 = max(tp1, entry * 1.10)
    tp2 = max(tp2, entry * 1.35)
    tp3 = max(tp3, entry * 2.00)

    rr1 = (tp1 - entry) / risk if risk > 0 else 0
    rr2 = (tp2 - entry) / risk if risk > 0 else 0
    rr3 = (tp3 - entry) / risk if risk > 0 else 0

    sym       = dex["symbol"].upper()
    t24       = dex["change_24h"]
    turn      = dex["volume_24h"] / max(dex["liquidity"], 1)
    not_on    = cex_status["label"]

    # Keyboard
    markup = InlineKeyboardMarkup()
    if dex["chain_raw"] in ("solana", "sol"):
        markup.row(InlineKeyboardButton(
            "🔫 BONKBOT", url=f"https://t.me/bonkbot_bot?start={dex['contract']}"
        ))
    else:
        markup.row(InlineKeyboardButton(
            "🦄 MAESTRO", url=f"https://t.me/maestro?start={dex['contract']}"
        ))
    markup.row(
        InlineKeyboardButton(
            "📊 DexScreener",
            url=f"https://dexscreener.com/{dex['chain_raw']}/{dex['contract']}"
        ),
        InlineKeyboardButton(
            "🔍 GoPlus",
            url=f"https://gopluslabs.io/token-security/{dex['chain_raw']}/{dex['contract']}"
        ),
    )

    msg = (
        f"🏴‍☠️ <b>PRE-CEX SCOUT — {narrative.upper()}</b>\n\n"
        f"┌ <b>ASET</b>\n"
        f"├ Token  : {dex['name']} (<code>${sym}</code>)\n"
        f"└ CA     : <code>{dex['contract']}</code>\n\n"
        f"┌ <b>METRIK PASARAN (LIVE)</b>\n"
        f"├ FDV      : <code>${dex['fdv']/1e6:.2f}M</code>\n"
        f"├ Vol 24H  : <code>${dex['volume_24h']/1e3:.0f}K</code>\n"
        f"├ Turnover : <code>{turn:.1f}x Vol/Liq</code>\n"
        f"└ Umur     : <code>{dex['age_display']}</code>\n\n"
        f"┌ <b>STRUKTUR TEKNIKAL (H4)</b>\n"
        f"├ Trend 24H : <code>{f'+{t24:.2f}' if t24>=0 else f'{t24:.2f}'}%</code>\n"
        f"├ RSI (H4)  : <code>{rsi_lbl}</code>\n"
        f"├ ATR (H4)  : <code>${fmt(atr)}</code>\n"
        f"└ Fibonacci  : <code>{fibo_lbl}</code>\n\n"
        f"┌ <b>STATUS CEX</b>\n"
        f"├ Network   : {dex['network']}\n"
        f"├ Belum di  : <b>Binance | OKX | Bybit | Coinbase</b>\n"
        f"├ Gate.io   : {'✅ Ada' if cex_status.get('gateio') else '❌ Tiada'}\n"
        f"└ Audit     : {sec_label}\n\n"
        f"🎯 <b>TRADE SETUP</b>\n"
        f"• ENTRY : <code>${fmt(entry)}</code>\n"
        f"• SL    : <code>${fmt(sl)}</code>  [{(entry-sl)/entry*100:.1f}%]  ATR×{atr_mult}\n"
        f"• TP1   : <code>${fmt(tp1)}</code>  [+{(tp1-entry)/entry*100:.1f}%]  RR 1:{rr1:.1f}  Fib S.High\n"
        f"• TP2   : <code>${fmt(tp2)}</code>  [+{(tp2-entry)/entry*100:.1f}%]  RR 1:{rr2:.1f}  Fib 1.618\n"
        f"• TP3   : <code>${fmt(tp3)}</code>  [+{(tp3-entry)/entry*100:.1f}%]  RR 1:{rr3:.1f}  Fib 2.618\n\n"
        f"🦅 <b>THESIS: Token ini belum listing di CEX major.\n"
        f"Edge kita: masuk sebelum pengumuman listing.</b>"
    )

    try:
        sent = bot.send_message(
            target, msg,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True
        )
        record = {
            "contract":   dex["contract"],
            "symbol":     sym,
            "name":       dex["name"],
            "network":    dex["network"],
            "entry":      entry, "sl": sl,
            "tp1": tp1,   "tp2": tp2,    "tp3": tp3,
            "rr1":        round(rr1, 2), "rr2": round(rr2, 2), "rr3": round(rr3, 2),
            "atr":        atr,   "rsi": rsi_lbl,  "fibo": fibo_lbl,
            "mc":         dex["fdv"], "liquidity": dex["liquidity"],
            "volume_24h": dex["volume_24h"],
            "narrative":  narrative,
            "msg_id":     sent.message_id,
            "sent_at":    int(time.time()),
        }
        save_signal(record)
        add_cooldown(dex["contract"])
        print(f"[SIGNAL] {sym} | ${dex['fdv']/1e6:.1f}M | {narrative}")

    except Exception as e:
        alert_admin(f"Gagal hantar signal {sym}: {e}")

# ================================================================
# 7. TRADE MONITOR
# ================================================================
def monitor_active_trades():
    """Semak TP/SL untuk semua trade aktif dari Supabase."""
    active = get_active_trades()
    if not active:
        return

    to_close = []
    for ca, trade in active.items():
        try:
            dex = get_dex_by_ca(trade.get("network", "").lower(), ca)
            if not dex:
                continue

            cp  = dex["price_usd"]
            sym = trade["symbol"]
            mid = trade.get("msg_id")

            def notify(text, _mid=mid):
                kw = {"parse_mode": "HTML"}
                if _mid:
                    kw["reply_to_message_id"] = _mid
                bot.send_message(VIP_CHANNEL_ID, text, **kw)

            updates = {}
            if cp >= trade["tp1"] and not trade.get("tp1_hit"):
                updates["tp1_hit"] = True
                notify(f"✅ <b>{sym}</b> TP1!\nAlih SL → BE: <code>${fmt(trade['entry'])}</code>")

            if cp >= trade["tp2"] and not trade.get("tp2_hit"):
                updates["tp2_hit"] = True
                notify(f"🚀 <b>{sym}</b> TP2!\nTrail SL → TP1: <code>${fmt(trade['tp1'])}</code>")

            if cp >= trade["tp3"] and not trade.get("tp3_hit"):
                updates["tp3_hit"]  = True
                updates["closed"]   = True
                to_close.append(ca)
                notify(f"🏆 <b>{sym}</b> TP3 MOONSHOT!\nTutup di <code>${fmt(cp)}</code>")

            elif cp <= trade["sl"] and not trade.get("sl_hit"):
                updates["sl_hit"] = True
                updates["closed"] = True
                to_close.append(ca)
                notify(f"❌ <b>{sym}</b> SL HIT.\nTrade ditutup: <code>${fmt(cp)}</code>")

            if updates:
                update_signal(ca, updates)

        except Exception as e:
            print(f"[MONITOR] {ca}: {e}")

    if to_close:
        print(f"[MONITOR] Ditutup: {to_close}")

# ================================================================
# 8. SCANNER — Pre-CEX Discovery
# ================================================================
def scan_once():
    """
    Satu kitaran scan menggunakan DexScreener boosted + CG trending.
    Tiada loop CoinGecko kategori. Tiada 4-jam lag.
    """
    if not IS_SCANNING:
        return

    candidates = {}  # ca → {chain, ca}

    # Source 1: DexScreener top boosted (up to 30)
    for item in get_ds_boosted_top(30):
        ca    = item.get("tokenAddress", "")
        chain = item.get("chainId", "")
        if ca and chain:
            candidates[ca] = {"ca": ca, "chain": chain}
    print(f"[SCAN] Boosted top: {len(candidates)} token")

    # Source 2: DexScreener latest boosted (up to 20)
    for item in get_ds_boosted_latest(20):
        ca    = item.get("tokenAddress", "")
        chain = item.get("chainId", "")
        if ca and chain and ca not in candidates:
            candidates[ca] = {"ca": ca, "chain": chain}
    print(f"[SCAN] Boosted latest: {len(candidates)} total")

    # Source 3: CoinGecko trending (7 coins, 1 API call)
    trending_syms = get_cg_trending()
    for sym in trending_syms:
        dex = get_dex_by_sym(sym)
        if dex and dex["contract"] not in candidates:
            candidates[dex["contract"]] = {"ca": dex["contract"], "chain": dex["chain_raw"], "_dex": dex}
    print(f"[SCAN] + CG trending: {len(candidates)} total")

    passed = 0
    for ca, meta in candidates.items():
        try:
            # Ambil data DEX jika belum ada
            if "_dex" in meta:
                dex = meta["_dex"]
            else:
                time.sleep(0.2)
                dex = get_dex_by_ca(meta["chain"], ca)

            if not dex or not dex.get("contract"):
                continue

            # Pre-filter awal (cepat, tanpa API tambahan)
            ok, reason, _ = pre_filter(dex)
            if not ok:
                continue

            # Semak cooldown (Supabase)
            if is_in_cooldown(ca):
                continue

            # Semak trade aktif
            if is_active_trade(ca):
                continue

            # CEX check (serentak, < 5 saat)
            cex = check_all_cex(dex["symbol"])
            if cex["listed_on_any"]:
                print(f"[SKIP] {dex['symbol']} — sudah di CEX major")
                continue

            # CoinGecko listing verification (1 API call)
            if not verify_cg_listed(dex["symbol"]):
                print(f"[SKIP] {dex['symbol']} — tiada rekod CoinGecko")
                continue

            # Teknikal H4
            rsi_lbl, fibo_lbl, atr, sh, sl_fib = get_technicals_h4(
                dex["network"], dex["pair_address"]
            )

            # Skip jika RSI overbought (beli di puncak = risiko tinggi)
            if "Overbought" in rsi_lbl:
                print(f"[SKIP] {dex['symbol']} — RSI overbought")
                continue

            # Security
            is_safe, sec_label = verify_security(dex["network"], dex["contract"])
            if not is_safe:
                alert_admin(f"🚨 Security GAGAL: {dex['symbol']} — {sec_label}")
                continue

            # Narrative label
            if cex.get("gateio"):
                narrative = "PRE-BINANCE (Gate.io listed)"
            else:
                narrative = "PRE-CEX SCOUT"

            send_signal(dex, cex, rsi_lbl, fibo_lbl, atr, sh, sl_fib, sec_label, narrative)
            passed += 1
            time.sleep(1)  # throttle antara signal

        except Exception as e:
            print(f"[SCAN ERROR] {ca}: {e}")
            continue

    print(f"[SCAN] Selesai. {passed} signal dihantar dari {len(candidates)} calon.")

# ================================================================
# 9. JOURNAL — dari Supabase
# ================================================================
def generate_journal(label="Mingguan", days=7):
    trades = get_signals_since(days)

    if not trades:
        return f"📓 <b>ALPHA JOURNAL {label.upper()}</b>\n\nTiada signal dalam {days} hari lepas."

    total  = len(trades)
    tp1_n  = sum(1 for t in trades if t.get("tp1_hit"))
    tp2_n  = sum(1 for t in trades if t.get("tp2_hit"))
    tp3_n  = sum(1 for t in trades if t.get("tp3_hit"))
    sl_n   = sum(1 for t in trades if t.get("sl_hit"))
    open_n = sum(1 for t in trades if not t.get("closed"))
    wr     = tp1_n / total * 100 if total else 0

    nets = {}
    for t in trades:
        n = t.get("network", "?")
        nets[n] = nets.get(n, 0) + 1
    net_str = " | ".join(f"{k}:{v}" for k, v in sorted(nets.items(), key=lambda x: -x[1]))

    best = max(
        trades,
        key=lambda t: t.get("tp3_hit",0)*3 + t.get("tp2_hit",0)*2 + t.get("tp1_hit",0),
        default=None
    )
    losers = [t for t in trades if t.get("sl_hit")]

    lines = [
        f"📓 <b>ALPHA JOURNAL {label.upper()}</b>",
        f"📅 {datetime.fromtimestamp(time.time()-days*86400).strftime('%d/%m')} – "
        f"{datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "<b>📊 RINGKASAN</b>",
        f"├ Total Signal : <code>{total}</code>",
        f"├ TP1 Secured  : <code>{tp1_n} ({wr:.0f}%)</code>",
        f"├ TP2 Secured  : <code>{tp2_n} ({tp2_n/total*100:.0f}%)</code>",
        f"├ TP3 Moonshot : <code>{tp3_n} ({tp3_n/total*100:.0f}%)</code>",
        f"├ SL Hit       : <code>{sl_n} ({sl_n/total*100:.0f}%)</code>",
        f"└ Masih Buka   : <code>{open_n}</code>",
        "",
        f"<b>🌐 RANGKAIAN</b>",
        f"<code>{net_str}</code>",
    ]

    if best:
        tier = ("TP3 🏆" if best.get("tp3_hit")
                else "TP2 🚀" if best.get("tp2_hit")
                else "TP1 ✅" if best.get("tp1_hit")
                else "Open ⏳")
        lines += ["", f"<b>⭐ TERBAIK:</b> {best['symbol']} → {tier}"]

    if losers:
        syms = ", ".join(t["symbol"] for t in losers[:5])
        lines += [f"<b>❌ SL HIT:</b> {syms}"]

    lines += [
        "",
        "<b>📈 PRESTASI</b>",
        f"TP1: {wr:.0f}%  "
        f"TP2: {tp2_n/total*100:.0f}%  "
        f"TP3: {tp3_n/total*100:.0f}%  "
        f"SL: {sl_n/total*100:.0f}%",
    ]
    return "\n".join(lines)

def send_weekly_journal():
    report = generate_journal("Mingguan", days=7)
    try:
        bot.send_message(ADMIN_ID, report, parse_mode="HTML")
        bot.send_message(VIP_CHANNEL_ID, report, parse_mode="HTML")
    except Exception as e:
        alert_admin(f"Gagal hantar journal: {e}")

# ================================================================
# 10. TELEGRAM COMMANDS
# ================================================================
IS_SCANNING  = True
_force_cache = {}

@bot.message_handler(commands=["start", "menu"])
def cmd_start(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    cfg      = get_config()
    active   = get_active_trades()
    uptime_m = int((time.time() - START_TIME) / 60)

    text = (
        f"🏴‍☠️ <b>ALPHA — Pre-CEX Scout</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Uptime     : <code>{uptime_m}m</code>\n"
        f"📊 Trade Aktif: <code>{len(active)}</code>\n"
        f"🔧 Scan       : <code>{'✅ AKTIF' if IS_SCANNING else '⛔ STOP'}</code>\n\n"
        f"<b>Filter Pre-CEX:</b>\n"
        f"MC    : ${cfg['mc_min']/1e6:.0f}M – ${cfg['mc_max']/1e6:.0f}M\n"
        f"Liq   : ${cfg['liq_min']/1e3:.0f}K\n"
        f"Umur  : >{cfg['min_token_age_days']}d\n"
        f"ATR×  : {cfg['atr_sl_mult']}\n"
    )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Status",      callback_data="status"),
        InlineKeyboardButton("📓 Journal",     callback_data="journal"),
        InlineKeyboardButton("🔧 Edit Filter", callback_data="edit_filter"),
        InlineKeyboardButton("🎯 Force Pair",  callback_data="help_pair"),
        InlineKeyboardButton("▶️ Mula",        callback_data="scan_on"),
        InlineKeyboardButton("⏸ Henti",        callback_data="scan_off"),
    )
    bot.send_message(str(msg.chat.id), text, parse_mode="HTML", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("force_signal:"))
def cb_force_signal(call):
    if str(call.message.chat.id) != str(ADMIN_ID): return
    bot.answer_callback_query(call.id)
    ca = call.data.split(":", 2)[1]
    if ca in _force_cache:
        item = _force_cache.pop(ca)
        def _do():
            send_signal(
                item["dex"], item["cex"], item["rsi"], item["fibo"],
                item["atr"], item["sh"], item["sl_fib"], item["sec"],
                "🎯 FORCE PAIR"
            )
        threading.Thread(target=_do).start()
        bot.send_message(call.message.chat.id,
                         f"📡 Signal <b>{item['dex']['symbol']}</b> dihantar!",
                         parse_mode="HTML")
    else:
        bot.send_message(call.message.chat.id, "⚠️ Cache tamat. /pair semula.")

@bot.callback_query_handler(func=lambda c: c.data == "cancel_force")
def cb_cancel(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "❌ Dibatalkan.")

@bot.callback_query_handler(func=lambda c: True)
def cb_main(call):
    global IS_SCANNING
    cid = str(call.message.chat.id)
    if cid != str(ADMIN_ID): return
    bot.answer_callback_query(call.id)
    d = call.data

    if d == "status":
        cfg    = get_config()
        active = get_active_trades()
        text = (
            f"📊 <b>STATUS</b>\n"
            f"Scan : {'🟢' if IS_SCANNING else '🔴'}\n"
            f"Trade aktif: {len(active)}\n\n"
            f"MC   : ${cfg['mc_min']/1e6:.0f}M–${cfg['mc_max']/1e6:.0f}M\n"
            f"Liq  : ${cfg['liq_min']/1e3:.0f}K\n"
            f"Umur : >{cfg['min_token_age_days']}d\n"
            f"ATR× : {cfg['atr_sl_mult']}\n"
            f"Pass : {cfg['score_pass']}/4"
        )
        bot.send_message(cid, text, parse_mode="HTML")

    elif d == "journal":
        bot.send_message(cid, generate_journal("Terkini", days=7), parse_mode="HTML")

    elif d == "edit_filter":
        bot.send_message(cid, (
            "🔧 <b>EDIT FILTER</b>\n\n"
            "<code>/setmc [min_M] [max_M]</code> → /setmc 5 50\n"
            "<code>/setliq [K]</code>            → /setliq 500\n"
            "<code>/setage [hari]</code>          → /setage 7\n"
            "<code>/setatr [x]</code>             → /setatr 1.5\n"
            "<code>/setpass [1-4]</code>          → /setpass 3"
        ), parse_mode="HTML")

    elif d == "help_pair":
        bot.send_message(cid, (
            "🎯 <b>FORCE PAIR</b>\n\n"
            "Taip: <code>/pair [CA]</code>\n\n"
            "Contoh:\n"
            "<code>/pair 0x1234abc...</code>\n"
            "<code>/pair EPjFWdd5A... (Solana)</code>"
        ), parse_mode="HTML")

    elif d == "scan_on":
        IS_SCANNING = True
        bot.send_message(cid, "▶️ Scan AKTIF.")
        threading.Thread(target=scan_once).start()

    elif d == "scan_off":
        IS_SCANNING = False
        bot.send_message(cid, "⏸ Scan BERHENTI.")

# ── Filter commands ───────────────────────────────────────────────
@bot.message_handler(commands=["setmc"])
def cmd_setmc(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    try:
        p = msg.text.split()
        set_config("mc_min", float(p[1]) * 1e6)
        set_config("mc_max", float(p[2]) * 1e6)
        bot.reply_to(msg, f"✅ MC: ${float(p[1]):.0f}M – ${float(p[2]):.0f}M")
    except Exception:
        bot.reply_to(msg, "❌ /setmc [min_M] [max_M]  →  /setmc 5 50")

@bot.message_handler(commands=["setliq"])
def cmd_setliq(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    try:
        val = float(msg.text.split()[1]) * 1000
        set_config("liq_min", val)
        bot.reply_to(msg, f"✅ Liq min: ${val/1e3:.0f}K")
    except Exception:
        bot.reply_to(msg, "❌ /setliq [K]  →  /setliq 500")

@bot.message_handler(commands=["setage"])
def cmd_setage(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    try:
        val = int(msg.text.split()[1])
        set_config("min_token_age_days", val)
        bot.reply_to(msg, f"✅ Umur min token: {val} hari")
    except Exception:
        bot.reply_to(msg, "❌ /setage [hari]  →  /setage 7")

@bot.message_handler(commands=["setatr"])
def cmd_setatr(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    try:
        val = float(msg.text.split()[1])
        set_config("atr_sl_mult", val)
        bot.reply_to(msg, f"✅ ATR multiplier: {val}x")
    except Exception:
        bot.reply_to(msg, "❌ /setatr [x]  →  /setatr 1.5")

@bot.message_handler(commands=["setpass"])
def cmd_setpass(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    try:
        val = int(msg.text.split()[1])
        if not 1 <= val <= 4: raise ValueError
        set_config("score_pass", val)
        bot.reply_to(msg, f"✅ Skor minimum lulus: {val}/4")
    except Exception:
        bot.reply_to(msg, "❌ /setpass [1-4]  →  /setpass 3")

# ── Force pair (analisis manual) ─────────────────────────────────
@bot.message_handler(commands=["pair"])
def cmd_pair(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "❌ /pair [CA]")
        return
    query = parts[1].strip()
    bot.reply_to(msg, f"🔍 Menganalisa <code>{query[:20]}...</code>", parse_mode="HTML")

    def _do():
        try:
            # Cuba cari sebagai CA (panjang > 20 char) atau simbol
            if len(query) > 20:
                # Cuba detect chain: SOL = base58 (44 char), EVM = 0x...
                chain = "solana" if not query.startswith("0x") else "bsc"
                dex = get_dex_by_ca(chain, query)
                if not dex:
                    # Cuba chain lain
                    for c in ["base", "ethereum"]:
                        dex = get_dex_by_ca(c, query)
                        if dex: break
            else:
                dex = get_dex_by_sym(query)

            if not dex:
                bot.send_message(msg.chat.id, f"❌ Tiada data DEX untuk <code>{query}</code>", parse_mode="HTML")
                return

            ok, reason, score = pre_filter(dex)
            cex = check_all_cex(dex["symbol"])
            rsi_lbl, fibo_lbl, atr, sh, sl_fib = get_technicals_h4(dex["network"], dex["pair_address"])
            is_safe, sec_label = verify_security(dex["network"], dex["contract"])

            text = (
                f"🔍 <b>ANALISIS: {dex['symbol'].upper()}</b>\n\n"
                f"MC      : ${dex['fdv']/1e6:.2f}M\n"
                f"Liq     : ${dex['liquidity']/1e3:.0f}K\n"
                f"Vol 24H : ${dex['volume_24h']/1e3:.0f}K\n"
                f"Umur    : {dex['age_display']}\n"
                f"24H     : {dex['change_24h']:+.2f}%\n"
                f"RSI (H4): {rsi_lbl}\n"
                f"Fibo    : {fibo_lbl}\n"
                f"ATR     : ${fmt(atr)}\n"
                f"Security: {sec_label}\n\n"
                f"CEX Major: {'🚨 SUDAH LISTED' if cex['listed_on_any'] else '✅ BELUM LISTED'}\n"
                f"  {cex['label']}\n\n"
                f"Pre-filter: <b>{reason}</b> (Skor {score}/4)\n"
            )
            kb = InlineKeyboardMarkup()
            kb.row(
                InlineKeyboardButton("✅ PAKSA SIGNAL", callback_data=f"force_signal:{dex['contract']}"),
                InlineKeyboardButton("❌ Batal",         callback_data="cancel_force"),
            )
            _force_cache[dex["contract"]] = {
                "dex": dex, "cex": cex, "rsi": rsi_lbl, "fibo": fibo_lbl,
                "atr": atr, "sh": sh, "sl_fib": sl_fib, "sec": sec_label
            }
            bot.send_message(msg.chat.id, text, parse_mode="HTML", reply_markup=kb)

        except Exception as e:
            bot.send_message(msg.chat.id, f"❌ Error: {e}")

    threading.Thread(target=_do).start()

@bot.message_handler(commands=["journal"])
def cmd_journal(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    parts = msg.text.split()
    days  = int(parts[1]) if len(parts) > 1 else 7
    bot.reply_to(msg, generate_journal(f"Manual ({days}d)", days=days), parse_mode="HTML")

@bot.message_handler(commands=["scan"])
def cmd_scan(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    bot.reply_to(msg, "⚙️ Scan dipaksa...")
    threading.Thread(target=scan_once).start()

@bot.message_handler(commands=["status"])
def cmd_status(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    cfg    = get_config()
    active = get_active_trades()
    bot.reply_to(msg, (
        f"📊 <b>STATUS</b>\n"
        f"Scan  : {'🟢' if IS_SCANNING else '🔴'}\n"
        f"Trade : {len(active)} aktif\n\n"
        f"MC   : ${cfg['mc_min']/1e6:.0f}M–${cfg['mc_max']/1e6:.0f}M\n"
        f"Liq  : ${cfg['liq_min']/1e3:.0f}K\n"
        f"ATR× : {cfg['atr_sl_mult']}\n"
        f"Pass : {cfg['score_pass']}/4"
    ), parse_mode="HTML")

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    if str(msg.chat.id) != str(ADMIN_ID): return
    bot.reply_to(msg, (
        "📖 <b>ARAHAN</b>\n\n"
        "/start          — Menu utama\n"
        "/scan           — Paksa kitaran scan\n"
        "/pair [CA]      — Analisis + paksa signal token\n"
        "/journal [hari] — Jana laporan\n"
        "/status         — Status semasa\n\n"
        "<b>Edit Filter:</b>\n"
        "/setmc [min] [max] — MC (M$)\n"
        "/setliq [K]        — Kecairan min\n"
        "/setage [hari]     — Umur min token\n"
        "/setatr [x]        — Pengganda ATR SL\n"
        "/setpass [1-4]     — Skor minimum lulus"
    ), parse_mode="HTML")

# ================================================================
# 11. SCHEDULER & MAIN
# ================================================================
def run_scheduler():
    schedule.every(3).minutes.do(
        lambda: threading.Thread(target=scan_once).start()
    )
    schedule.every(3).minutes.do(
        lambda: threading.Thread(target=monitor_active_trades).start()
    )
    schedule.every().sunday.at("21:00").do(
        lambda: threading.Thread(target=send_weekly_journal).start()
    )
    while True:
        try:
            schedule.run_pending()
        except Exception:
            pass
        time.sleep(30)

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALPHA PRE-CEX ACTIVE")
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
    def log_message(self, *args):
        pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", port), RenderHandler).serve_forever(),
        daemon=True
    ).start()
    threading.Thread(target=run_scheduler, daemon=True).start()

    # Scan pertama selepas 10 saat (bagi masa bot ready)
    time.sleep(10)
    alert_admin(
        "🏴‍☠️ ALPHA Pre-CEX Scout DEPLOYED\n"
        f"Filter: MC ${get_config()['mc_min']/1e6:.0f}M–"
        f"${get_config()['mc_max']/1e6:.0f}M | "
        f"Liq ${get_config()['liq_min']/1e3:.0f}K\n"
        "/start untuk menu"
    )
    threading.Thread(target=scan_once).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
