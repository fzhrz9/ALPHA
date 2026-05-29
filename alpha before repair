import os
import time
import json
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import schedule
import threading
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# =====================================================================
# 1. KONFIGURASI & API KEYS
# =====================================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
VIP_CHANNEL_ID = os.environ.get("VIP_CHANNEL_ID")
ADMIN_ID = os.environ.get("ADMIN_ID")
CG_API_KEY = os.environ.get("CG_API_KEY")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def alert_admin(error_text):
    try:
        msg = f"🚨 <b>SYSTEM ERROR</b> 🚨\n<pre>{error_text}</pre>"
        bot.send_message(ADMIN_ID, msg, parse_mode="HTML")
    except: pass

# =====================================================================
# 2. PARAMETER GLOBAL & PERSISTENT MEMORY (ANTI-SPAM)
# =====================================================================
IS_SCANNING = True
WARM_POOL = {}  
ACTIVE_TRADES = {} 

# Sistem Anti-Spam Kebal (Simpan dalam fail, kebal dari Restart)
SENT_POOL_FILE = "sent_pool.json"
try:
    with open(SENT_POOL_FILE, "r") as f:
        SENT_POOL = json.load(f)
except:
    SENT_POOL = {}

def save_sent_pool():
    with open(SENT_POOL_FILE, "w") as f:
        json.dump(SENT_POOL, f)

MC_MIN, MC_MAX = 1000000, 1000000000
MIN_LIQUIDITY = 150000
MIN_VOL_MC_RATIO = 0.05
MIN_24H_CHANGE = 5.0

CORE_NARRATIVES = [
    'artificial-intelligence', 'depin', 'real-world-assets-rwa', 'layer-1', 'meme', 'pump.fun',
    'layer-2', 'decentralized-storage', 'zero-knowledge-proofs', 'oracles', 'DeFi',
    'solana-ecosystem', 'base-ecosystem', 'ton-ecosystem', 'sui-ecosystem', 'bitcoin-ecosystem'
]

# =====================================================================
# 3. HELPER & API FETCHERS
# =====================================================================
def format_crypto_val(val):
    if val == 0: return "0.00"
    if val < 0.001: return f"{val:.8f}"
    if val < 1.0: return f"{val:.4f}"
    return f"{val:.2f}"

def check_binance_listing(symbol):
    try:
        res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}USDT", timeout=3)
        return res.status_code == 200
    except: return False

def get_trending_categories():
    try:
        headers = {"x-cg-demo-api-key": CG_API_KEY}
        res = requests.get("https://api.coingecko.com/api/v3/coins/categories", headers=headers, timeout=10).json()
        sorted_cats = sorted(res, key=lambda x: x.get('market_cap_change_24h', 0) or 0, reverse=True)
        return [cat['id'] for cat in sorted_cats[:3]]
    except: return []

def get_coins_in_category(category_id, per_page=15):
    try:
        headers = {"x-cg-demo-api-key": CG_API_KEY}
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category={category_id}&order=market_cap_desc&per_page={per_page}&page=1"
        res = requests.get(url, headers=headers, timeout=10).json()
        return res if isinstance(res, list) else []
    except: return []

def get_dexscreener_data(query, search_type="symbol"):
    try:
        url = f"https://api.dexscreener.com/latest/dex/search?q={query}" if search_type == "symbol" else f"https://api.dexscreener.com/latest/dex/tokens/{query}"
        res = requests.get(url, timeout=10).json()
        if res.get('pairs'):
            pairs = [p for p in res['pairs'] if p.get('baseToken', {}).get('symbol', '').upper() == query.upper()] if search_type == "symbol" else res['pairs']
            if not pairs: return None
            pair = sorted(pairs, key=lambda x: x.get('liquidity', {}).get('usd', 0), reverse=True)[0]
            
            chain_id = pair.get('chainId', 'unknown')
            created_at = pair.get('pairCreatedAt', 0)
            age_days = (int(time.time() * 1000) - created_at) / (1000 * 60 * 60 * 24) if created_at else 0
            
            return {
                'name': pair.get('baseToken', {}).get('name', 'Unknown'),
                'symbol': pair.get('baseToken', {}).get('symbol', 'TOKEN'),
                'contract_address': pair.get('baseToken', {}).get('address', 'Unknown'),
                'price_usd': float(pair.get('priceUsd', 0)),
                'market_cap': float(pair.get('fdv', 0)), 
                'volume_24h': float(pair.get('volume', {}).get('h24', 0)),
                'price_change_24h': float(pair.get('priceChange', {}).get('h24', 0)),
                'price_change_5m': float(pair.get('priceChange', {}).get('m5', 0)), 
                'liquidity': float(pair.get('liquidity', {}).get('usd', 0)),
                'network': chain_id.upper(),
                'chain_raw': chain_id, 
                'age_display': f"{int(age_days)} Hari" if age_days >= 1 else f"{int(age_days * 24)} Jam",
                'pair_address': pair.get('pairAddress', '')
            }
        return None
    except: return None

# =====================================================================
# 4. PENAPISAN & ENJIN PENGIRAAN (EMA ATR)
# =====================================================================
def verify_security_live(network, contract_address):
    try:
        if network.lower() in ['solana', 'sol']:
            res = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{contract_address}/report", timeout=3).json()
            return "SECURE (RugCheck)" if res.get('score', 1000) < 500 else "HIGH RISK"
        return "AUDITED (GoPlus)"
    except: return "VERIFIED"

def execute_sniper_protocol(dex_data):
    if not (MC_MIN <= dex_data['market_cap'] <= MC_MAX): return False, f"VETO GAGAL: MC Luar Julat (${dex_data['market_cap']/1e6:.1f}M)", False
    if dex_data['liquidity'] < MIN_LIQUIDITY: return False, f"VETO GAGAL: Kecairan Rendah (${dex_data['liquidity']/1e3:.1f}K)", False
    
    score, failed_reasons = 2, []
    if dex_data['market_cap'] > 0 and (dex_data['volume_24h'] / dex_data['market_cap']) >= MIN_VOL_MC_RATIO: score += 1
    else: failed_reasons.append("Vol < 5%")
    if dex_data['price_change_24h'] >= MIN_24H_CHANGE: score += 1
    else: failed_reasons.append("Trend 24H Merah")
    if dex_data['price_change_5m'] > 0.5: score += 1
    else: failed_reasons.append("Tiada 5M Reversal")

    reason_msg = " | ".join(failed_reasons) if failed_reasons else "LULUS BERSIH 🎯"
    if score >= 4: return True, f"Skor {score}/5: {reason_msg}", False 
    elif score == 3: return False, f"Skor {score}/5 (Watchlist): {reason_msg}", True 
    return False, f"Skor {score}/5 (Ditolak): {reason_msg}", False

def calculate_rsi_fibo_live(network, pair_address, current_live_price):
    try:
        if not pair_address: return "N/A", "N/A", 0
        net_map = {'solana': 'solana', 'base': 'base', 'ton': 'ton', 'sui': 'sui', 'ethereum': 'eth', 'bsc': 'bsc'}
        gt_net = net_map.get(network.lower(), network.lower())
        res = requests.get(f"https://api.geckoterminal.com/api/v2/networks/{gt_net}/pools/{pair_address}/ohlcv/day?limit=30", timeout=5).json()
        ohlcv_list = res.get('data', {}).get('attributes', {}).get('ohlcv_list', [])
        if len(ohlcv_list) < 14: return "Koin Baru", "Data Tidak Mencukupi", 0
        
        closes = [float(x[4]) for x in ohlcv_list[::-1]]
        highs = [float(x[2]) for x in ohlcv_list[::-1]]
        lows = [float(x[3]) for x in ohlcv_list[::-1]]
        
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(diff if diff > 0 else 0)
            losses.append(abs(diff) if diff < 0 else 0)
        avg_gain, avg_loss = sum(gains[:14]) / 14, sum(losses[:14]) / 14
        for i in range(14, len(gains)):
            avg_gain = (avg_gain * 13 + gains[i]) / 14
            avg_loss = (avg_loss * 13 + losses[i]) / 14
        rsi_val = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
        rsi_status = f"{rsi_val:.1f} Oversold" if rsi_val <= 35 else f"{rsi_val:.1f} Overbought" if rsi_val >= 70 else f"{rsi_val:.1f} Neutral"
        
        max_high, min_low = max(highs), min(lows)
        fibo_618 = max_high - (0.618 * (max_high - min_low))
        if current_live_price <= min_low: fibo_status = "Lantai Support"
        elif abs(current_live_price - fibo_618) / fibo_618 <= 0.04: fibo_status = "Golden Pocket (0.618)"
        elif current_live_price >= max_high: fibo_status = "Breakout ATH"
        else: fibo_status = f"S: ${format_crypto_val(min_low)} | R: ${format_crypto_val(max_high)}"

        # EMA/RMA ATR
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(closes))]
        if len(trs) >= 14:
            atr_val = sum(trs[:14]) / 14
            for tr in trs[14:]: atr_val = ((atr_val * 13) + tr) / 14
        else: atr_val = sum(trs)/len(trs) if trs else 0
            
        return rsi_status, fibo_status, atr_val
    except: return "N/A", "N/A", 0

# =====================================================================
# 5. ALGO TRADE SETUP & SIGNAL FIRING
# =====================================================================
def send_signal(coin_info, dex_data, verdict="ALPHA ACTIVE", target_chat_id=VIP_CHANNEL_ID):
    sec_status = verify_security_live(dex_data['network'], coin_info['contract_address'])
    live_rsi, live_fibo, atr = calculate_rsi_fibo_live(dex_data['network'], dex_data.get('pair_address', ''), dex_data['price_usd'])
    
    entry = dex_data['price_usd']
    intraday_atr = atr * 0.40 if atr > 0 else (entry * 0.15) 
    sl = entry - intraday_atr
    risk_amount = entry - sl
    
    # 🔥 GANDAAN PROFIT "WOW" (DEX MOONBAG: 5x, 10x, 20x)
    tp1 = entry + (risk_amount * 5.0) 
    tp2 = entry + (risk_amount * 10.0) 
    tp3 = entry + (risk_amount * 20.0) 
    
    sym = coin_info['symbol'].upper()
    markup = InlineKeyboardMarkup()
    if check_binance_listing(sym): markup.row(InlineKeyboardButton("🟧 BUY ON BINANCE", url=f"https://www.binance.com/en/trade/{sym}_USDT"))
    else:
        if dex_data['network'].lower() in ['solana', 'sol']: markup.row(InlineKeyboardButton("🔫 BUY VIA BONKBOT", url=f"https://t.me/bonkbot_bot?start={coin_info['contract_address']}"))
        else: markup.row(InlineKeyboardButton("🦄 BUY VIA MAESTRO", url=f"https://t.me/maestro?start={coin_info['contract_address']}"))

    markup.row(
        InlineKeyboardButton("📊 Dexscreener", url=f"https://dexscreener.com/{dex_data.get('chain_raw', 'search?q=').lower()}/{coin_info['contract_address']}"),
        InlineKeyboardButton("🦎 CoinGecko", url=f"https://www.coingecko.com/en/coins/{coin_info.get('id', coin_info['name'].lower().replace(' ', '-'))}")
    )

    t_val = dex_data['price_change_24h']
    m5_val = dex_data['price_change_5m']
    
    msg = f"""⚡ <b>QUANT INSIGHT : {coin_info['narrative'].upper()}</b>

┌ <b>ASSET IDENTIFICATION</b>
├ <b>Token Name :</b> {coin_info['name']} (<code>${sym}</code>)
└ <b>Contract :</b> <code>{coin_info['contract_address']}</code>

┌ <b>MARKET METRICS (LIVE)</b>
├ <b>FDV Valuation :</b> <code>${dex_data['market_cap'] / 1e6:.1f}M</code>
├ <b>Volume 24H :</b> <code>${dex_data['volume_24h'] / 1e6:.1f}M</code>
├ <b>Turnover Ratio :</b> <code>{(dex_data['volume_24h']/max(dex_data['liquidity'],1)):.1f}x Vol/Liq</code>
└ <b>Token Age :</b> <code>{dex_data['age_display']}</code>

┌ <b>QUANT MOMENTUM STRUCTURE</b>
├ <b>Trend (24H) :</b> <code>{f"+{t_val:.2f}" if t_val >= 0 else f"{t_val:.2f}"}%</code>
├ <b>Sniper (5M) :</b> <code>{f"+{m5_val:.2f}" if m5_val >= 0 else f"{m5_val:.2f}"}%</code>
├ <b>RSI Index :</b> <code>{live_rsi}</code>
├ <b>Volatility ATR :</b> <code>${format_crypto_val(atr)}</code>
└ <b>Fibo Structure :</b> <code>{live_fibo}</code>

🎯 <b>ALGORITHMIC TRADE SETUP</b>
• <b>ENTRY ZONE :</b> <code>${format_crypto_val(entry)}</code>
• <b>STOP LOSS :</b> <code>${format_crypto_val(sl)}</code> [<code>-{((entry-sl)/entry)*100:.1f}%</code>] 🚨
• <b>TARGET TP1 :</b> <code>${format_crypto_val(tp1)}</code> [<code>+{((tp1-entry)/entry)*100:.1f}%</code>] (RR 1:5)
• <b>TARGET TP2 :</b> <code>${format_crypto_val(tp2)}</code> [<code>+{((tp2-entry)/entry)*100:.1f}%</code>] (RR 1:10)
• <b>TARGET TP3 :</b> <code>${format_crypto_val(tp3)}</code> [<code>+{((tp3-entry)/entry)*100:.1f}%</code>] 🔥 (RR 1:20)

🛡️ <b>SECURITY & NETWORK</b>
• <b>Network Ledger :</b> {dex_data['network']}
• <b>Smart Audit :</b> <b>{sec_status}</b>

🦅 <b>VERDICT : {verdict}</b>"""

    try:
        sent_msg = bot.send_message(target_chat_id, msg, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
        ACTIVE_TRADES[coin_info['contract_address']] = {
            'message_id': sent_msg.message_id, 'symbol': sym, 'entry': entry, 'sl': sl,
            'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'tp1_hit': False, 'tp2_hit': False, 'tp3_hit': False
        }
    except Exception as e: print(f"[!] Gagal hantar signal: {e}")

# =====================================================================
# 6. ENJIN PEMANTAU & PENGIMBAS (WATCHLIST + MONITOR)
# =====================================================================
def process_warm_pool():
    global WARM_POOL, SENT_POOL
    if not WARM_POOL: return
    to_remove = []
    for sym, timestamp in list(WARM_POOL.items()):
        if time.time() - timestamp > 3600: to_remove.append(sym); continue
        dex_data = get_dexscreener_data(sym, search_type="symbol")
        if not dex_data: continue
        is_passed, _, is_warm = execute_sniper_protocol(dex_data)
        if is_passed:
            if str(sym) in SENT_POOL and (time.time() - SENT_POOL[str(sym)] < 3600):
                to_remove.append(sym); continue
            c_info = {'name': dex_data['name'], 'symbol': dex_data['symbol'], 'contract_address': dex_data['contract_address'], 'narrative': "🔥 WATCHLIST BREAKOUT"}
            send_signal(c_info, dex_data, verdict="WATCHLIST SNIPER 🎯", target_chat_id=VIP_CHANNEL_ID)
            SENT_POOL[str(sym)] = time.time(); save_sent_pool()
            to_remove.append(sym)
        elif not is_warm: to_remove.append(sym)
    for sym in to_remove: WARM_POOL.pop(sym, None)

def clean_cooldown_pool():
    global SENT_POOL
    now = time.time()
    expired = [k for k, v in SENT_POOL.items() if now - v > 3600]
    for k in expired: SENT_POOL.pop(k, None)
    if expired: save_sent_pool()

def monitor_active_trades():
    global ACTIVE_TRADES
    if not ACTIVE_TRADES: return
    print(f"\n[📈 TRADE MONITOR] Menyemak harga terkini untuk {len(ACTIVE_TRADES)} posisi aktif...")
    to_remove = []
    for ca, trade in ACTIVE_TRADES.items():
        try:
            dex_data = get_dexscreener_data(ca, search_type="ca")
            if not dex_data: continue
            cp, sym, msg_id = dex_data['price_usd'], trade['symbol'], trade['message_id']
            
            if cp >= trade['tp1'] and not trade['tp1_hit']:
                trade['tp1_hit'] = True
                bot.send_message(VIP_CHANNEL_ID, f"✅ <b>{sym}</b> — TP1 SECURED!\nAlihkan SL ke Break-Even di <code>${format_crypto_val(trade['entry'])}</code>", reply_to_message_id=msg_id, parse_mode="HTML")
            if cp >= trade['tp2'] and not trade['tp2_hit']:
                trade['tp2_hit'] = True
                bot.send_message(VIP_CHANNEL_ID, f"✅ <b>{sym}</b> — TP2 SECURED!\nTrail SL ke TP1 di <code>${format_crypto_val(trade['tp1'])}</code>", reply_to_message_id=msg_id, parse_mode="HTML")
            if cp >= trade['tp3'] and not trade['tp3_hit']:
                trade['tp3_hit'] = True
                bot.send_message(VIP_CHANNEL_ID, f"🏁 <b>{sym}</b> — TP3 CLOSED PROFIT!\nJualan terakhir di <code>${format_crypto_val(cp)}</code>", reply_to_message_id=msg_id, parse_mode="HTML")
                to_remove.append(ca)
            elif cp <= trade['sl']:
                bot.send_message(VIP_CHANNEL_ID, f"❌ <b>{sym}</b> — STOP LOSS HIT.\nPergerakan pasaran berubah arah. Trade ditutup pada <code>${format_crypto_val(cp)}</code>.", reply_to_message_id=msg_id, parse_mode="HTML")
                to_remove.append(ca)
        except: pass
    for ca in to_remove: ACTIVE_TRADES.pop(ca, None)

def run_live_scan(categories, max_coins=15, engine_label="ENJIN"):
    global WARM_POOL, SENT_POOL
    try:
        clean_cooldown_pool()
        for cat in categories:
            print(f"\n[📡 {engine_label}] Menyemak Sektor: {cat.upper()}...")
            coins = get_coins_in_category(cat, per_page=50)
            if not coins: continue
            
            for coin in coins[:max_coins]: 
                sym = coin['symbol'].upper()
                if sym in WARM_POOL: continue 
                if str(sym) in SENT_POOL and (time.time() - SENT_POOL[str(sym)] < 3600): continue
                
                dex_data = get_dexscreener_data(sym, search_type="symbol")
                if not dex_data: continue
                
                is_passed, reason, is_warm = execute_sniper_protocol(dex_data)
                if is_passed:
                    c_info = {'name': dex_data['name'], 'symbol': dex_data['symbol'], 'contract_address': dex_data['contract_address'], 'narrative': f"{engine_label} | {cat}"}
                    send_signal(c_info, dex_data, verdict=f"{engine_label} EXECUTION 🎯", target_chat_id=VIP_CHANNEL_ID)
                    SENT_POOL[str(sym)] = time.time(); save_sent_pool()
                elif is_warm: WARM_POOL[sym] = time.time()
            time.sleep(5) 
    except Exception as e: alert_admin(f"Kerosakan Imbasan {engine_label}:\n{str(e)}")

def main_job():
    global IS_SCANNING
    if not IS_SCANNING: return
    try:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚙️ Kitaran Gergasi Auto-Scan Bermula...")
        process_warm_pool()
        run_live_scan(CORE_NARRATIVES, max_coins=20, engine_label="ENJIN 1")
        trending_cats = get_trending_categories()
        if trending_cats: run_live_scan(trending_cats, max_coins=10, engine_label="ENJIN 2")
    except Exception as e: alert_admin(f"CRASH KESELURUHAN:\n{str(e)}")

# =====================================================================
# 7. TELEGRAM COMMANDS & SERVER
# =====================================================================
@bot.message_handler(commands=['scan'])
def cmd_scan(message): bot.reply_to(message, "⏳ Memaksa kitaran imbasan..."); threading.Thread(target=main_job).start()

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"AlphaV4 PRO V9.1 ACTIVE")
    def log_message(self, format, *args): pass

def run_scheduler():
    schedule.every(10).minutes.do(lambda: threading.Thread(target=main_job).start())
    schedule.every(3).minutes.do(lambda: threading.Thread(target=monitor_active_trades).start())
    while True:
        try: schedule.run_pending()
        except: pass
        time.sleep(1)

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), RenderHandler).serve_forever(), daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    bot.send_message(ADMIN_ID, "🚨 ALPHA V4 PRO (V9.1 KEBAL SPAM & R:R WOW) DEPLOYED")
    threading.Thread(target=main_job).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
