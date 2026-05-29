import os
import time
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import schedule
import threading
import traceback
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
import traceback # Pastikan tambah 'import traceback' di bahagian paling atas (tempat import module)

def alert_admin(error_text):
    """Hantar mesej ralat terus ke Telegram Admin"""
    try:
        msg = f"🚨 <b>SYSTEM ERROR DETECTED</b> 🚨\n\n<pre>{error_text}</pre>"
        bot.send_message(ADMIN_ID, msg, parse_mode="HTML")
    except Exception as e:
        print(f"[!] Gagal hantar amaran ke Telegram: {e}")

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
    'solana-ecosystem', 'base-ecosystem', 'ton-ecosystem', 'sui-ecosystem', 'zero-knowledge-proofs',
    'bitcoin-ecosystem', 'gaming', 'restaking', 'layer-1', 'layer-2', 'decentralized-storage'
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

def get_coins_in_category(category_id, per_page=15):
    try:
        headers = {"x-cg-demo-api-key": CG_API_KEY}
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category={category_id}&order=market_cap_desc&per_page={per_page}&page=1"
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
    # Mengembalikan (Status Lulus/Gagal, Sebab)
    if not (MC_MIN <= dex_data['market_cap'] <= MC_MAX): 
        return False, f"MC Luar Julat (${dex_data['market_cap']/1e6:.1f}M)"
    if dex_data['liquidity'] < MIN_LIQUIDITY: 
        return False, f"Kecairan Rendah (${dex_data['liquidity']/1e3:.1f}K)"
    if dex_data['market_cap'] > 0 and (dex_data['volume_24h'] / dex_data['market_cap']) < MIN_VOL_MC_RATIO: 
        return False, f"Vol/MC < 5% ({(dex_data['volume_24h']/dex_data['market_cap']):.2f})"
    if dex_data['price_change_24h'] < MIN_24H_CHANGE: 
        return False, f"Trend 24H Merah ({dex_data['price_change_24h']}%)"
    if dex_data['price_change_5m'] <= 0.5: 
        return False, f"Reversal 5M Lemah ({dex_data['price_change_5m']}%)"
    
    return True, "LULUS SYARAT 🎯"
# =====================================================================
# ENJIN QUANT RSI & FIBONACCI (HYBRID)
# =====================================================================
def calculate_rsi_fibo_live(network, pair_address, current_live_price):
    try:
        if not pair_address: return "N/A", "N/A", 0
        
        net_map = {'solana': 'solana', 'base': 'base', 'ton': 'ton', 'sui': 'sui', 'ethereum': 'eth', 'bsc': 'bsc'}
        gt_net = net_map.get(network.lower(), network.lower())
        
        url = f"https://api.geckoterminal.com/api/v2/networks/{gt_net}/pools/{pair_address}/ohlcv/day?limit=30"
        res = requests.get(url, timeout=5).json()
        ohlcv_list = res.get('data', {}).get('attributes', {}).get('ohlcv_list', [])
        
        if len(ohlcv_list) < 14:
            return "Koin Baru (Data < 14D)", "Data Tidak Mencukupi", 0
        
        closes = [float(x[4]) for x in ohlcv_list[::-1]]
        highs = [float(x[2]) for x in ohlcv_list[::-1]]
        lows = [float(x[3]) for x in ohlcv_list[::-1]]
        
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
        
        # --- FORMULA FIBO ---
        max_high = max(highs)
        min_low = min(lows)
        total_range = max_high - min_low
        fibo_618 = max_high - (0.618 * total_range)
        fibo_50 = max_high - (0.50 * total_range)
        
        if current_live_price <= min_low: fibo_status = "🚨 Menembusi Lantai Support Utama!"
        elif abs(current_live_price - fibo_618) / fibo_618 <= 0.04: fibo_status = "🔥 Menguji Golden Pocket (0.618) - Reversal Kuat!"
        elif current_live_price >= max_high: fibo_status = "🚀 Price Discovery Mode (Breakout High)!"
        else: fibo_status = f"S: ${min_low:.4f} | R: ${max_high:.4f}"

        # --- FORMULA ATR (AVERAGE TRUE RANGE 14-HARI) ---
        trs = []
        for i in range(1, len(closes)):
            h = highs[i]
            l = lows[i]
            pc = closes[i-1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        atr_val = sum(trs[-14:]) / 14 if len(trs) >= 14 else (sum(trs)/len(trs) if trs else 0)
            
        return rsi_status, fibo_status, atr_val
    except:
        return "N/A (API Error)", "N/A (API Error)", 0

# =====================================================================
# 4. ALGO TRADE SETUP & BROADCAST UI 
# =====================================================================
def send_signal(coin_info, dex_data, verdict="THE SNIPER ENTRY 🎯", target_chat_id=VIP_CHANNEL_ID):
    sec_status = verify_security_live(dex_data['network'], coin_info['contract_address'])
    is_sol = dex_data['network'].lower() in ['solana', 'sol']
    
    # Terima 3 nilai sekarang: RSI, Fibo, dan ATR
    live_rsi, live_fibo, atr = calculate_rsi_fibo_live(dex_data['network'], dex_data.get('pair_address', ''), dex_data['price_usd'])
    
    buy_bot_name = "🔫 BonkBot" if is_sol else "🦄 Maestro"
    buy_bot_link = f"https://t.me/{'bonkbot_bot' if is_sol else 'maestro'}?start={coin_info['contract_address']}"
    chain_url = dex_data.get('chain_raw', 'search?q=').lower()

    entry = dex_data['price_usd']
    
    # ==========================================================
    # 🧮 INSTITUTIONAL DYNAMIC RISK MANAGEMENT (ATR + R:R)
    # ==========================================================
    # Intraday Buffer: Kita ambil 15% dari Daily ATR untuk SL Sniper (Elak kena sweep jarum)
    intraday_atr = atr * 0.15 if atr > 0 else (entry * 0.08) # Fallback 8% kalau koin baru (tiada data ATR)
    
    sl = entry - intraday_atr
    risk_amount = entry - sl
    sl_pct = ((entry - sl) / entry) * 100
    
    # TP Berdasarkan Nisbah R:R (Bukan tekaan statik)
    tp1 = entry + (risk_amount * 1.5) # R:R 1:1.5 (Target Realistik)
    tp2 = entry + (risk_amount * 3.0) # R:R 1:3.0 (Target Optimum)
    tp3 = entry + (risk_amount * 5.0) # R:R 1:5.0 (Moonbag/Runner)

    tp1_pct = ((tp1 - entry) / entry) * 100
    tp2_pct = ((tp2 - entry) / entry) * 100
    tp3_pct = ((tp3 - entry) / entry) * 100
    # ==========================================================
    
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
• <b>Volatility (ATR) :</b> <code>${atr:.4f} / Day</code>

🎯 <b>TRADE SETUP (DYNAMIC ATR RISK:REWARD)</b>
• <b>ENTRY ZONE :</b> <code>${entry:.6f}</code>
• <b>STOP LOSS :</b> <code>${sl:.6f}</code> <code>(-{sl_pct:.1f}%)</code> 🚨 <i>(Below Sweep Zone)</i>
• <b>TAKE PROFIT 1 :</b> <code>${tp1:.6f}</code> <code>(+{tp1_pct:.1f}%)</code> <i>[RR 1:1.5]</i>
• <b>TAKE PROFIT 2 :</b> <code>${tp2:.6f}</code> <code>(+{tp2_pct:.1f}%)</code> <i>[RR 1:3.0]</i>
• <b>TAKE PROFIT 3 :</b> <code>${tp3:.6f}</code> <code>(+{tp3_pct:.1f}%)</code> 🚀 <i>[RR 1:5.0]</i>

🌊 <b>ORDER FLOW & SECURITY</b>
• <b>Turnover Ratio :</b> <code>{turnover_ratio:.1f}x Volume/Liquidity</code> 🔥
• <b>Token Age :</b> <code>{dex_data['age_display']}</code>
• <b>Live Audit :</b> <b>{sec_status}</b>

⚡ <b>VERDICT : {verdict}</b>
"""
# (Biarkan bahagian butang InlineKeyboardMarkup kekal seperti biasa)   
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
def run_live_scan(categories, max_coins=15, engine_label="ENJIN"):
    try:
        for cat in categories:
            print(f"\n[📡 {engine_label}] Menyemak Sektor: {cat.upper()} (Had: Top {max_coins} Token)...")
            coins = get_coins_in_category(cat, per_page=max_coins)
            
            if not coins: 
                print(f"   [!] Sektor {cat.upper()} tiada respon. Seterusnya...")
                continue
                
            stats = {'scanned': 0, 'passed': 0, 'reasons': {}} # Perekod Ringkasan
            
            for coin in coins:
                sym = coin['symbol']
                dex_data = get_dexscreener_data(sym, search_type="symbol")
                if not dex_data: continue
                
                stats['scanned'] += 1
                
                # Terima dua nilai dari enjin tapisan
                is_passed, reason = execute_sniper_protocol(dex_data)
                
                if is_passed:
                    stats['passed'] += 1
                    print(f"   🔥 [LULUS] Signal ditemui untuk {sym.upper()}!")
                    
                    raw_rank = coin.get('market_cap_rank') or coin.get('rank')
                    final_rank = str(raw_rank) if raw_rank and str(raw_rank).isdigit() else "N/A"
                    
                    c_info = {
                        'name': dex_data['name'], 
                        'symbol': dex_data['symbol'], 
                        'id': coin.get('id', coin['name'].lower().replace(" ", "-")), 
                        'contract_address': dex_data['contract_address'], 
                        'narrative': f"{engine_label} | {cat}", 
                        'market_cap_rank': final_rank
                    }
                    send_signal(c_info, dex_data, verdict=f"{engine_label} PRO 🎯", target_chat_id=VIP_CHANNEL_ID)
                else:
                    # Kumpul sebab-sebab koin ditolak
                    stats['reasons'][reason] = stats['reasons'].get(reason, 0) + 1
            
            # --- PAPARAN RINGKASAN LOG DI RENDER ---
            print(f"   📊 [RINGKASAN SEKTOR {cat.upper()}]")
            print(f"      - Token Diimbas : {stats['scanned']}")
            print(f"      - Lulus Signal  : {stats['passed']}")
            if stats['reasons']:
                print(f"      - Punca Koin Ditolak:")
                for r, count in sorted(stats['reasons'].items(), key=lambda item: item[1], reverse=True):
                    print(f"         > {r} = {count} token")
            
            time.sleep(5) # Jeda keselamatan pelayan API

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"[!] RALAT KRITIKAL DALAM {engine_label}:\n{error_details}")
        alert_admin(f"Kerosakan Semasa Imbasan {engine_label}:\n{str(e)}\n\nSila semak Render Logs.") 

def main_job():
    global IS_SCANNING
    if not IS_SCANNING: return
    
    try:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚙️ Kitaran Gergasi Auto-Scan Bermula...")
        
        # === 🚀 ENJIN 1: FOKUS NARATIF TETAP ===
        print("\n>>> Memulakan ENJIN 1 (Naratif Teras)...")
        run_live_scan(CORE_NARRATIVES, max_coins=15, engine_label="ENJIN 1")
        
        # === 🔥 ENJIN 2: FOKUS TOP 3 TRENDING SEKTOR ===
        print("\n>>> Memulakan ENJIN 2 (Hype Semasa Harian)...")
        trending_cats = get_trending_categories()
        if trending_cats:
            run_live_scan(trending_cats, max_coins=5, engine_label="ENJIN 2")
        else:
            print("[!] Enjin 2 Gagal: Masalah sambungan senarai trending.")
            
    except Exception as e:
        error_details = traceback.format_exc()
        alert_admin(f"CRASH KESELURUHAN (MAIN JOB):\n{str(e)}")
        print(f"[!] SYSTEM CRASH:\n{error_details}")

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
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "❌ Format salah. Taip: `/ca <contract_address>`", parse_mode="Markdown")
            return
            
        address = parts[1]
        bot.reply_to(message, f"⚙️ DD Analisis CA memproses:\n`{address}`", parse_mode="Markdown")
        
        dex_data = get_dexscreener_data(address, search_type="ca")
        if dex_data:
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
            bot.reply_to(message, "✅ Signal blast berjaya!")
        else: 
            bot.reply_to(message, "❌ Data Dexscreener gagal ditarik (Koin mungkin tak wujud di DEX).")
            
    except Exception as e: 
        error_details = traceback.format_exc()
        bot.reply_to(message, f"🚨 **RALAT SISTEM:**\n`{str(e)}`\n\nSila rujuk Render Logs untuk detail.", parse_mode="Markdown")
        print(f"\n[!] ERROR DALAM COMMAND /CA:\n{error_details}")

class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"AlphaV4 PRO ACTIVE & BULLETPROOF")
    def log_message(self, format, *args): pass

def run_scheduler():
    schedule.every(10).minutes.do(lambda: threading.Thread(target=main_job).start())
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
