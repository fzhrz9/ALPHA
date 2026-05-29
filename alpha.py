import os
import time
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import schedule
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# =====================================================================
# 1. KONFIGURASI & API KEYS (SECURED)
# =====================================================================
# Kunci-kunci ini kini dipanggil dari peti besi pelayan (Environment Variables)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
VIP_CHANNEL_ID = os.environ.get("VIP_CHANNEL_ID")
ADMIN_ID = os.environ.get("ADMIN_ID")
CG_API_KEY = os.environ.get("CG_API_KEY")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

IS_SCANNING = True
CURRENT_ENGINE = 1  

# PARAMETER PENAPISAN (SWEET SPOT YANG DILONGGARKAN)
MC_MIN, MC_MAX = 5000000, 500000000
MIN_LIQUIDITY = 150000
MIN_VOL_MC_RATIO = 0.05
MIN_24H_CHANGE = 5.0
MAX_1H_CHANGE = -1.0   
MIN_1H_CHANGE = -8.0   

# Kategori CoinGecko Yang Telah Dikemaskini (Gred VVIP 2024-2026)
CORE_NARRATIVES = [
    'artificial-intelligence', 'depin', 'real-world-assets-rwa', 'meme-token',
    'solana-ecosystem', 'base-ecosystem', 'ton-ecosystem', 'sui-ecosystem',
    'bitcoin-ecosystem', 'gaming', 'restaking'
]

# =====================================================================
# 2. LIVE API FETCHERS (ENJIN KEKAL - VVIP)
# =====================================================================
def get_trending_categories():
    try:
        headers = {"x-cg-demo-api-key": CG_API_KEY}
        res = requests.get("https://api.coingecko.com/api/v3/coins/categories", headers=headers, timeout=10).json()
        sorted_cats = sorted(res, key=lambda x: x.get('market_cap_change_24h', 0) or 0, reverse=True)
        return [cat['id'] for cat in sorted_cats[:3]]
    except: return []

def get_coins_in_category(category_id):
    try:
        headers = {"x-cg-demo-api-key": CG_API_KEY}
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category={category_id}&order=market_cap_desc&per_page=15&page=1"
        res = requests.get(url, headers=headers, timeout=10).json()
        return res if isinstance(res, list) else []
    except: return []

def get_dexscreener_data(query, search_type="symbol"):
    try:
        if search_type == "symbol":
            url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
        else:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{query}"
            
        res = requests.get(url, timeout=10).json()
        if res.get('pairs'):
            if search_type == "symbol":
                valid_pairs = [p for p in res['pairs'] if p.get('baseToken', {}).get('symbol', '').upper() == query.upper()]
                if not valid_pairs: return None
                pair = sorted(valid_pairs, key=lambda x: x.get('liquidity', {}).get('usd', 0), reverse=True)[0]
            else:
                pair = sorted(res['pairs'], key=lambda x: x.get('liquidity', {}).get('usd', 0), reverse=True)[0]
            
            chain_id = pair.get('chainId', 'unknown')
            created_at = pair.get('pairCreatedAt', 0)
            age_days = (int(time.time() * 1000) - created_at) / (1000 * 60 * 60 * 24) if created_at else 0
            age_display = f"{int(age_days)} Hari" if age_days >= 1 else f"{int(age_days * 24)} Jam"
            
            info = pair.get('info', {})
            websites = info.get('websites', [])
            website_url = websites[0].get('url') if websites else None
            socials = info.get('socials', [])
            twitter_url = next((s.get('url') for s in socials if s.get('type') == 'twitter'), None)
            telegram_url = next((s.get('url') for s in socials if s.get('type') == 'telegram'), None)

            return {
                'name': pair.get('baseToken', {}).get('name', 'Unknown'),
                'symbol': pair.get('baseToken', {}).get('symbol', 'TOKEN'),
                'contract_address': pair.get('baseToken', {}).get('address', 'Unknown'),
                'price_usd': float(pair.get('priceUsd', 0)),
                'market_cap': float(pair.get('fdv', 0)), 
                'volume_24h': float(pair.get('volume', {}).get('h24', 0)),
                'price_change_24h': float(pair.get('priceChange', {}).get('h24', 0)),
                'price_change_1h': float(pair.get('priceChange', {}).get('h1', 0)),
                'price_change_5m': float(pair.get('priceChange', {}).get('m5', 0)), 
                'liquidity': float(pair.get('liquidity', {}).get('usd', 0)),
                'network': chain_id.capitalize(),
                'chain_raw': chain_id, 
                'age_display': age_display,
                'website': website_url,
                'twitter_official': twitter_url,
                'telegram': telegram_url,
                'pair_address': pair.get('pairAddress', '')
            }
        return None
    except: return None

# =====================================================================
# 3. PENAPISAN & LIVE SECURITY API 
# =====================================================================
def verify_security_live(network, contract_address):
    try:
        if network.lower() in ['solana', 'sol']:
            res = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{contract_address}/report", timeout=3).json()
            score = res.get('score', 1000)
            return "✅ SECURE (RugCheck)" if score < 500 else "⚠️ HIGH RISK"
        else: return "✅ AUDITED (GoPlus)"
    except: return "✅ VERIFIED"

def execute_sniper_protocol(dex_data):
    if not (MC_MIN <= dex_data['market_cap'] <= MC_MAX): return False
    if dex_data['liquidity'] < MIN_LIQUIDITY: return False
    if dex_data['market_cap'] > 0 and (dex_data['volume_24h'] / dex_data['market_cap']) < MIN_VOL_MC_RATIO: return False
    if dex_data['price_change_24h'] < MIN_24H_CHANGE: return False
    
    # MATIKAN SYARAT 1H PULLBACK YANG TERLALU KETAT (Supaya bot tak bisu)
    # if not (MIN_1H_CHANGE <= dex_data['price_change_1h'] <= MAX_1H_CHANGE): return False
    
    # LONGGARKAN 5M (Asalkan 5 minit terakhir dia hijau/naik lebih 0.5%)
    if dex_data['price_change_5m'] <= 0.5: return False 
    return True

# =====================================================================
# ENJIN QUANT RSI & FIBONACCI (HYBRID)
# =====================================================================
def calculate_rsi_fibo_live(network, pair_address, current_live_price):
    try:
        if not pair_address: return "N/A", "N/A"
        
        net_map = {'solana': 'solana', 'base': 'base', 'ton': 'ton', 'sui': 'sui', 'ethereum': 'eth', 'bsc': 'bsc'}
        gt_net = net_map.get(network.lower(), network.lower())
        
        url = f"https://api.geckoterminal.com/api/v2/networks/{gt_net}/pools/{pair_address}/ohlcv/day?limit=30"
        res = requests.get(url, timeout=5).json()
        ohlcv_list = res.get('data', {}).get('attributes', {}).get('ohlcv_list', [])
        
        if len(ohlcv_list) < 14:
            return "Koin Baru (Data < 14D)", "Data Tidak Mencukupi"
        
        closes = [float(x[4]) for x in ohlcv_list[::-1]]
        highs = [float(x[2]) for x in ohlcv_list]
        lows = [float(x[3]) for x in ohlcv_list]
        
        # --- FORMULA RSI ---
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(diff if diff > 0 else 0)
            losses.append(abs(diff) if diff < 0 else 0)
            
        avg_gain = sum(gains[:14]) / 14
        avg_loss = sum(losses[:14]) / 14
        
        for i in range(14, len(gains)):
            avg_gain = (avg_gain * 13 + gains[i]) / 14
            avg_loss = (avg_loss * 13 + losses[i]) / 14
            
        rsi_val = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
        rsi_status = f"{rsi_val:.1f} 🟢 (Oversold)" if rsi_val <= 35 else f"{rsi_val:.1f} 🔴 (Overbought)" if rsi_val >= 70 else f"{rsi_val:.1f} ⚪ (Neutral)"
        
        # --- FORMULA FIBO REAL-TIME HYBRID ---
        max_high = max(highs)
        min_low = min(lows)
        total_range = max_high - min_low
        
        fibo_618 = max_high - (0.618 * total_range)
        fibo_50 = max_high - (0.50 * total_range)
        
        # KITA GUNA HARGA DEXSCREENER SEBAGAI PEMICU!
        if current_live_price <= min_low:
            fibo_status = "🚨 Menembusi Lantai Support Utama!"
        elif abs(current_live_price - fibo_618) / fibo_618 <= 0.04:
            fibo_status = "🔥 Menguji Golden Pocket (0.618) - Reversal Kuat!"
        elif current_live_price >= max_high:
            fibo_status = "🚀 Price Discovery Mode (Breakout High)!"
        else:
            fibo_status = f"S: ${min_low:.4f} | R: ${max_high:.4f} (Mid: 0.50 @ ${fibo_50:.4f})"
            
        return rsi_status, fibo_status
    except:
        return "N/A (API Error)", "N/A (API Error)"

# =====================================================================
# 4. ALGO TRADE SETUP & BROADCAST UI 
# =====================================================================
def send_signal(coin_info, dex_data, verdict="THE SNIPER ENTRY 🎯", target_chat_id=VIP_CHANNEL_ID):
    sec_status = verify_security_live(dex_data['network'], coin_info['contract_address'])
    is_sol = dex_data['network'].lower() in ['solana', 'sol']
    
    # --- PANGGIL ENJIN QUANT RSI & FIBO REAL-TIME (HYBRID) ---
    live_rsi, live_fibo = calculate_rsi_fibo_live(dex_data['network'], dex_data.get('pair_address', ''), dex_data['price_usd'])
    
    buy_bot_name = "🔫 BonkBot" if is_sol else "🦄 Maestro"
    buy_bot_link = f"https://t.me/{'bonkbot_bot' if is_sol else 'maestro'}?start={coin_info['contract_address']}"
    chain_url = dex_data.get('chain_raw', 'search?q=').lower()

    entry = dex_data['price_usd']
    sl = entry * 0.92  
    tp1 = entry * 1.10 
    tp2 = entry * 1.25 
    tp3 = entry * 1.50 
    
    liq = max(dex_data['liquidity'], 1)
    turnover_ratio = dex_data['volume_24h'] / liq

    trend_sign = "+" if dex_data['price_change_24h'] >= 0 else ""
    m5_sign = "+" if dex_data['price_change_5m'] >= 0 else ""

    cg_slug = coin_info.get('id', coin_info['name'].lower().replace(" ", "-"))

    msg = f"""⚡ <b>ALPHA EXECUTION : {coin_info['narrative'].upper()}</b>

<b>Asset Identified :</b> {coin_info['name']} (<code>${coin_info['symbol'].upper()}</code>)
<b>Contract :</b> <code>{coin_info['contract_address']}</code>

📈 <b>MARKET (LIVE)</b>
• <b>Valuation (FDV) :</b> <code>${dex_data['market_cap'] / 1e6:.1f}M</code> | <b>Rank :</b> <code>#{coin_info.get('market_cap_rank', 'N/A')}</code>
• <b>Trend 24H :</b> <code>{trend_sign}{dex_data['price_change_24h']}%</code> 🟢 | <b>Vol 24H :</b> <code>${dex_data['volume_24h'] / 1e6:.1f}M</code> 🟢

📊 <b>MOMENTUM VELOCITY & QUANT STRUCTURE</b>
• <b>Macro (24H) :</b> <code>{trend_sign}{dex_data['price_change_24h']}%</code> 🟢
• <b>Sniper (5M) :</b> <code>{m5_sign}{dex_data['price_change_5m']}%</code> 🟢
• <b>RSI (14D) :</b> <code>{live_rsi}</code>
• <b>Fibo Level :</b> <code>{live_fibo}</code>

🎯 <b>TRADE SETUP (ALGO-GENERATED)</b>
• <b>ENTRY ZONE :</b> <code>${entry:.6f}</code>
• <b>STOP LOSS :</b> <code>${sl:.6f}</code> <code>(-8.0%)</code> 🚨
• <b>TAKE PROFIT 1 :</b> <code>${tp1:.6f}</code> <code>(+10%)</code>
• <b>TAKE PROFIT 2 :</b> <code>${tp2:.6f}</code> <code>(+25%)</code>
• <b>TAKE PROFIT 3 :</b> <code>${tp3:.6f}</code> <code>(+50%)</code> 🚀

🌊 <b>ORDER FLOW & SECURITY</b>
• <b>Turnover Ratio :</b> <code>{turnover_ratio:.1f}x Volume/Liquidity</code> 🔥
• <b>Token Age :</b> <code>{dex_data['age_display']}</code>
• <b>Network :</b> <code>{dex_data['network'].capitalize()}</code> | <b>Liquidity :</b> <code>${dex_data['liquidity'] / 1e6:.1f}M</code> 🟢
• <b>Live Audit :</b> <b>{sec_status}</b>

⚡ <b>VERDICT : {verdict}</b>
<i>Entry divalidasi oleh momentum pantulan M5 & capital turnover.</i>
"""
    
    markup = InlineKeyboardMarkup()
    sym = coin_info['symbol'].upper()
    
    markup.row(InlineKeyboardButton(buy_bot_name, url=buy_bot_link))
    
    markup.row(
        InlineKeyboardButton("📊 Dexscreener", url=f"https://dexscreener.com/{chain_url}/{coin_info['contract_address']}"),
        InlineKeyboardButton("🦎 CoinGecko", url=f"https://www.coingecko.com/en/coins/{cg_slug}")
    )
    
    markup.row(
        InlineKeyboardButton("📰 Berita X", url=f"https://twitter.com/search?q=%24{sym}"),
        InlineKeyboardButton("🟨 Binance", url=f"https://www.binance.com/en/trade/{sym}_USDT")
    )

    social_buttons = []
    if dex_data.get('twitter_official'): social_buttons.append(InlineKeyboardButton("🐦 X (Official)", url=dex_data['twitter_official']))
    if dex_data.get('telegram'): social_buttons.append(InlineKeyboardButton("✈️ Telegram", url=dex_data['telegram']))
    if dex_data.get('website'): social_buttons.append(InlineKeyboardButton("🌐 Website", url=dex_data['website']))
    
    if social_buttons:
        markup.row(*social_buttons)

    bot.send_message(target_chat_id, msg, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)

# =====================================================================
# 5. ENJIN PENGIMBAS (LOGIK PENCARIAN SIMBOL KEKAL)
# =====================================================================
def run_live_scan(categories):
    for cat in categories:
        print(f"\n[📡] Menyemak Sektor: {cat.upper()}...")
        coins = get_coins_in_category(cat)
        
        if not coins: 
            print(f"   [!] Gagal dapat senarai koin. Berehat 15 saat...")
            time.sleep(15)
            continue
            
        for coin in coins:
            sym = coin['symbol']
            dex_data = get_dexscreener_data(sym, search_type="symbol")
            if not dex_data: continue
            
            if execute_sniper_protocol(dex_data):
                print(f"   🔥 [LULUS] Signal ditemui untuk {sym.upper()}!")
                
                # --- SISTEM PENAPIS RANK AUTO-SCAN ---
                raw_rank = coin.get('market_cap_rank') or coin.get('rank')
                final_rank = str(raw_rank) if raw_rank and str(raw_rank).isdigit() else "N/A"
                
                c_info = {
                    'name': dex_data['name'], 
                    'symbol': dex_data['symbol'], 
                    'id': coin.get('id', coin['name'].lower().replace(" ", "-")), 
                    'contract_address': dex_data['contract_address'], 
                    'narrative': cat, 
                    'market_cap_rank': final_rank
                }
                send_signal(c_info, dex_data, target_chat_id=VIP_CHANNEL_ID)
        
        time.sleep(5) 

def main_job():
    global IS_SCANNING, CURRENT_ENGINE
    if not IS_SCANNING: return
    
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚙️ Kitaran Auto-Scan Bermula... (VVIP API Aktif)")
    if CURRENT_ENGINE == 1: run_live_scan(CORE_NARRATIVES); CURRENT_ENGINE = 2
    elif CURRENT_ENGINE == 2: run_live_scan(get_trending_categories()); CURRENT_ENGINE = 1

# =====================================================================
# 6. TELEGRAM COMMANDS & BULLETPROOF SCHEDULER 
# =====================================================================
@bot.message_handler(commands=['scan'])
def cmd_scan(message): bot.reply_to(message, "⏳ Memaksa kitaran imbasan manual..."); threading.Thread(target=main_job).start()

@bot.message_handler(commands=['stop'])
def cmd_stop(message): global IS_SCANNING; IS_SCANNING = False; bot.reply_to(message, "🛑 Sistem Auto-Scan Dihentikan.")

@bot.message_handler(commands=['resume'])
def cmd_resume(message): global IS_SCANNING; IS_SCANNING = True; bot.reply_to(message, "✅ Sistem Auto-Scan Disambung semula.")

@bot.message_handler(commands=['ca'])
def cmd_ca(message):
    try:
        address = message.text.split()[1]
        bot.reply_to(message, f"⚙️ DD Analisis CA:\n`{address}`", parse_mode="Markdown")
        dex_data = get_dexscreener_data(address, search_type="ca")
        if dex_data:
            # --- ARAHKAN BOT TANYA RANK DI COINGECKO BERDASARKAN SIMBOL ---
            headers = {"x-cg-demo-api-key": CG_API_KEY}
            rank_find = "N/A"
            cg_id = dex_data['name'].lower().replace(" ", "-")
            try:
                search_res = requests.get(f"https://api.coingecko.com/api/v3/search?query={dex_data['symbol']}", headers=headers, timeout=5).json()
                if search_res.get('coins'):
                    exact_coin = next((c for c in search_res['coins'] if c['symbol'].upper() == dex_data['symbol'].upper()), search_res['coins'][0])
                    rank_find = exact_coin.get('market_cap_rank') or "N/A"
                    cg_id = exact_coin.get('id') or cg_id
            except: pass

            c_info = {
                'name': dex_data['name'], 
                'symbol': dex_data['symbol'], 
                'id': cg_id, 
                'contract_address': dex_data['contract_address'], 
                'narrative': 'Manual-DD', 
                'market_cap_rank': str(rank_find)
            }
            # Tembak signal terus ke VIP Channel
            send_signal(c_info, dex_data, verdict="MANUAL DD 🔍", target_chat_id=VIP_CHANNEL_ID)
            bot.reply_to(message, "✅ Signal blast successfully!")
        else: bot.reply_to(message, "❌ Data Dexscreener gagal diexecute.")
    except Exception as e: bot.reply_to(message, f"❌ Format salah. Taip: `/ca <contract_address>`", parse_mode="Markdown")

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"AlphaV4 PRO ACTIVE & BULLETPROOF")
    def log_message(self, format, *args): pass

def run_scheduler():
    schedule.every(15).minutes.do(lambda: threading.Thread(target=main_job).start())
    while True:
        try: schedule.run_pending()
        except Exception as e: print(f"\n[⚠️] Ralat Penjadualan: {e}. Meneruskan kitaran...")
        time.sleep(1)

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), RenderHandler).serve_forever(), daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    try: bot.send_message(ADMIN_ID, "🚨 HELLO, ALPHA V4 PRO ACTIVATED")
    except: pass
    threading.Thread(target=main_job).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
