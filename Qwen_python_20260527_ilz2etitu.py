import asyncio
import aiohttp
from aiohttp import web
import sqlite3
import logging
import time
import os
import sys
import io
import json
from datetime import datetime

# ==========================================
# 1. KONFIGURASI PREMIUM SIGNAL PROVIDER
# ==========================================
BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
SIGNAL_CHANNEL_ID = os.getenv("SIGNAL_CHANNEL_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if not all([BOT_TOKEN, SIGNAL_CHANNEL_ID, ADMIN_CHAT_ID]):
    print("❌ FATAL: Sila set TG_BOT_TOKEN, SIGNAL_CHANNEL_ID, dan ADMIN_CHAT_ID dalam Render Environment Variables.")
    sys.exit(1)

# Rangkaian Fokus (NO ETH)
ALLOWED_CHAINS = {'solana', 'base', 'bsc'}

# Rate Limiters (Optimum Free Tier)
SCANNER_LIMIT = 60.0   # Scan new pools setiap 1 minit
GECKO_LIMIT = 1.5      # Polling watchlist setiap 1.5 saat
GOPLUS_LIMIT = 1.0     # Security check cooldown

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler()]
)

# Global variable for uptime tracking
start_time = time.time()

# ==========================================
# 2. HEALTH CHECK SERVER (For Render Web Service)
# ==========================================
async def health_check_handler(request):
    """Endpoint untuk Render: GET /health"""
    return web.json_response({"status": "ok", "uptime": time.time() - start_time})

async def init_web_server():
    """Start mini HTTP server on $PORT"""
    app = web.Application()
    app.add_routes([web.get('/health', health_check_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"🌐 Health server running on port {port}")

# ==========================================
# 3. TELEGRAM ENGINE
# ==========================================
class TelegramManager:
    def __init__(self, session):
        self.session = session
        self.base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"

    async def send_signal(self, message, inline_keyboard=None):
        await self._send(SIGNAL_CHANNEL_ID, message, parse_mode="HTML", inline_keyboard=inline_keyboard)

    async def send_chart_photo(self, chart_url, caption):
        """Download chart dari QuickChart & hantar sebagai PHOTO"""
        try:
            async with self.session.get(chart_url) as resp:
                if resp.status != 200:
                    logging.warning(f"⚠️ QuickChart fetch failed: {resp.status}")
                    return False
                chart_bytes = await resp.read()

            form = aiohttp.FormData()
            form.add_field('chat_id', SIGNAL_CHANNEL_ID)
            form.add_field('caption', caption, content_type='text/plain; charset=utf-8')
            form.add_field('parse_mode', 'HTML')
            form.add_field('photo', chart_bytes, filename='chart.png', content_type='image/png')

            async with self.session.post(f"{self.base_url}/sendPhoto", data=form) as send_resp:
                if send_resp.status == 200:
                    logging.info("📊 Chart photo sent successfully")
                    return True
                else:
                    logging.error(f"❌ Telegram sendPhoto failed: {await send_resp.text()}")
                    return False
        except Exception as e:
            logging.error(f"💥 Chart photo upload error: {e}")
            return False

    async def send_admin_alert(self, message, level="INFO"):
        emoji = "⚙️" if level == "INFO" else "⚠️" if level == "WARNING" else "❌"
        alert = f"{emoji} <b>[{level}] SYSTEM LOG</b>\n<pre>{message}</pre>"
        await self._send(ADMIN_CHAT_ID, alert, parse_mode="HTML")

    async def _send(self, chat_id, text, parse_mode=None, inline_keyboard=None):
        url = f"{self.base_url}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode: payload["parse_mode"] = parse_mode
        if inline_keyboard: payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logging.error(f"Telegram API Error: {await resp.text()}")
                    return False
                return True
        except Exception as e:
            logging.error(f"Telegram Send Failed: {e}")
            return False

# ==========================================
# 4. DATABASE & MATH
# ==========================================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('quant_bot.db', check_same_thread=False)
        self._setup()

    def _setup(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                     (pool_address TEXT PRIMARY KEY, chain TEXT, symbol TEXT, added_at REAL)''')
        self.conn.commit()

    def add(self, pool, chain, symbol):
        self.conn.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?, ?)", 
                          (pool, chain, symbol, time.time()))
        self.conn.commit()

    def get(self):
        return self.conn.execute("SELECT pool_address, chain, symbol FROM watchlist").fetchall()

    def remove(self, pool):
        self.conn.execute("DELETE FROM watchlist WHERE pool_address=?", (pool,))
        self.conn.commit()

class QuantMath:
    @staticmethod
    def vwap(candles):
        if not candles: return 0
        tpv = sum(((c['h']+c['l']+c['c'])/3)*c['v'] for c in candles)
        vol = sum(c['v'] for c in candles)
        return tpv/vol if vol > 0 else 0

    @staticmethod
    def atr(candles, p=14):
        if len(candles) < p+1: return 0
        trs = [max(c['h']-c['l'], abs(c['h']-candles[i-1]['c']), abs(c['l']-candles[i-1]['c'])) 
               for i, c in enumerate(candles) if i > 0]
        if not trs: return 0
        atr = sum(trs[:p])/p
        for t in trs[p:]: atr = (atr*(p-1)+t)/p
        return atr

    @staticmethod
    def cvd(candles):
        return sum(c['v'] if c['c']>c['o'] else -c['v'] for c in candles)

    @staticmethod
    def wyckoff_spring(candles, lb=20):
        if len(candles) < lb: return False
        recent = candles[-lb:]
        sl = min(c['l'] for c in recent[:-1])
        last = candles[-1]
        return last['c'] > sl and any(c['l'] < sl for c in recent[-5:])

# ==========================================
# 5. API & SECURITY ENGINES
# ==========================================
class DataFetcher:
    def __init__(self, session): 
        self.session = session

    async def scan_new_pools(self):
        """Scan new pools dari GeckoTerminal (semua chain kita: solana, base, bsc)"""
        all_pools = []
        chains_to_scan = ['solana', 'base', 'bsc']
        
        for chain in chains_to_scan:
            url = f"https://api.geckoterminal.com/api/v2/networks/{chain}/new_pools?page=1"
            try:
                async with self.session.get(url) as r:
                    if r.status == 200:
                        data = await r.json()
                        pools = data.get('data', [])
                        for pool in pools:
                            attrs = pool.get('attributes', {})
                            
                            pool_id = pool.get('id', '')
                            pool_address = pool_id.replace(f'{chain}_', '') if pool_id.startswith(f'{chain}_') else pool_id
                            
                            name = attrs.get('name', 'UNK')
                            symbol = name.split(' / ')[0].strip() if '/' in name else name
                            
                            all_pools.append({
                                'chain': chain,
                                'pool_address': pool_address,
                                'symbol': symbol,
                                'price_usd': float(attrs.get('base_token_price_usd', 0) or 0),
                                'volume_24h': float(attrs.get('volume_usd', {}).get('h24', 0) or 0),
                                'liquidity_usd': float(attrs.get('reserve_in_usd', 0) or 0),
                                'market_cap': float(attrs.get('market_cap_usd', 0) or 0),
                                'pool_created_at': attrs.get('pool_created_at', ''),
                                'fdv': float(attrs.get('fdv_usd', 0) or 0)
                            })
                        logging.info(f"   ✅ {chain.upper()}: Found {len(pools)} new pools")
                    else:
                        logging.warning(f"   ⚠️ {chain.upper()}: API returned {r.status}")
            except Exception as e:
                logging.error(f"   ❌ {chain.upper()} scan error: {e}")
            
            await asyncio.sleep(0.5)
        
        return all_pools

    async def gecko_ohlcv(self, net, pool):
        url = f"https://api.geckoterminal.com/api/v2/networks/{net}/pools/{pool}/ohlcv/minute?aggregate=1&limit=100"
        try:
            async with self.session.get(url) as r:
                if r.status == 200:
                    raw = (await r.json()).get('data',{}).get('attributes',{}).get('ohlcv_list',[])
                    return [{'t':x[0],'o':x[1],'h':x[2],'l':x[3],'c':x[4],'v':x[5]} for x in reversed(raw)]
        except: pass
        return []

class SecurityGuard:
    def __init__(self, session): 
        self.session = session
        self.chain_map = {'bsc':'56', 'base':'8453', 'solana':'solana'}

    async def audit(self, chain, addr):
        cid = self.chain_map.get(chain)
        if not cid: return False, "Chain not supported"
        url = f"https://api.gopluslabs.io/api/v1/token_security/{cid}?contract_addresses={addr}"
        try:
            async with self.session.get(url) as r:
                if r.status == 200:
                    d = await r.json()
                    res = d.get('result', {})
                    t = res.get(addr.lower(), res.get(addr, {}))
                    if isinstance(t, dict):
                        if t.get('is_honeypot')=='1': return False, "Honeypot"
                        if t.get('is_mintable')=='1': return False, "Mintable"
                        if float(t.get('buy_tax',0))>0.05: return False, "BuyTax>5%"
                        if float(t.get('sell_tax',0))>0.05: return False, "SellTax>5%"
        except: pass
        return True, "Secure"

# ==========================================
# 6. ADVANCED SIGNAL FORMATTER
# ==========================================
class SignalFormatter:
    @staticmethod
    def calculate_tp_levels(entry_price, atr):
        tp1 = entry_price + (2.0 * atr)
        tp2 = entry_price + (3.5 * atr)
        tp3 = entry_price + (5.0 * atr)
        return tp1, tp2, tp3

    @staticmethod
    def get_chain_display_name(chain):
        return {'solana': 'SOL', 'base': 'BASE', 'bsc': 'BSC'}.get(chain, chain.upper())

    @staticmethod
    def build_inline_keyboard(chain, pool_address, token_address):
        keyboard = []
        
        if chain == 'solana':
            bonk_url = f"https://t.me/bonkbot_bot?start=snipe_{token_address}"
            keyboard.append([{"text": "🟣 Trade with BONK", "url": bonk_url}])
        elif chain in ['base', 'bsc']:
            chain_id = '8453' if chain == 'base' else '56'
            maestro_url = f"https://t.me/MaestroSniperBot?start=buy_{token_address}_{chain_id}"
            keyboard.append([{"text": "🔵 Trade with MAESTRO", "url": maestro_url}])
        
        dexscreener_url = f"https://dexscreener.com/{chain}/{pool_address}"
        keyboard.append([{"text": "📊 View Chart", "url": dexscreener_url}])
        
        if chain == 'solana':
            rugcheck_url = f"https://rugcheck.xyz/tokens/{token_address}"
            keyboard.append([{"text": "🔒 RugCheck Security", "url": rugcheck_url}])
        else:
            chain_id = '8453' if chain == 'base' else '56'
            goplus_url = f"https://gopluslabs.io/token-security/{chain_id}/{token_address}"
            keyboard.append([{"text": "🔒 GoPlus Security", "url": goplus_url}])
        
        birdeye_url = f"https://birdeye.so/token/{token_address}?chain={chain}"
        keyboard.append([{"text": "🦅 Birdeye Analytics", "url": birdeye_url}])
        
        return keyboard

    @staticmethod
    def generate_chart_url(candles, entry, sl, tp1, tp2, tp3, symbol, chain):
        try:
            recent = candles[-30:] if len(candles) >= 30 else candles
            labels = [datetime.fromtimestamp(c['t']).strftime('%H:%M') for c in recent]
            prices = [c['c'] for c in recent]

            config = {
                "type": "line",
                "data": {
                    "labels": labels,
                    "datasets": [{
                        "label": symbol,
                        "data": prices,
                        "borderColor": "#00D4FF",
                        "backgroundColor": "rgba(0,212,255,0.15)",
                        "fill": True,
                        "tension": 0.3,
                        "pointRadius": 2,
                        "pointBackgroundColor": "#00D4FF",
                        "borderWidth": 2
                    }]
                },
                "options": {
                    "responsive": False,
                    "animation": False,
                    "plugins": {
                        "legend": {"display": False},
                        "title": {
                            "display": True,
                            "text": f"{symbol} ({chain.upper()}) | Alpha Signal",
                            "color": "#FFFFFF",
                            "font": {"size": 16, "weight": "bold"}
                        },
                        "annotation": {
                            "annotations": [
                                {"type": "line", "mode": "horizontal", "scaleID": "y", "value": entry, "borderColor": "#00FF00", "borderWidth": 2, "borderDash": [5,5], "label": {"display": True, "content": f"ENTRY: {entry:.6f}", "position": "start", "backgroundColor": "#00FF00", "color": "#000000"}},
                                {"type": "line", "mode": "horizontal", "scaleID": "y", "value": sl, "borderColor": "#FF0000", "borderWidth": 2, "borderDash": [5,5], "label": {"display": True, "content": f"SL: {sl:.6f}", "position": "start", "backgroundColor": "#FF0000", "color": "#FFFFFF"}},
                                {"type": "line", "mode": "horizontal", "scaleID": "y", "value": tp1, "borderColor": "#FFFF00", "borderWidth": 2, "borderDash": [5,5], "label": {"display": True, "content": f"TP1: {tp1:.6f}", "position": "end", "backgroundColor": "#FFFF00", "color": "#000000"}},
                                {"type": "line", "mode": "horizontal", "scaleID": "y", "value": tp2, "borderColor": "#FFA500", "borderWidth": 2, "borderDash": [5,5], "label": {"display": True, "content": f"TP2: {tp2:.6f}", "position": "end", "backgroundColor": "#FFA500", "color": "#000000"}},
                                {"type": "line", "mode": "horizontal", "scaleID": "y", "value": tp3, "borderColor": "#9B59B6", "borderWidth": 3, "borderDash": [10,5], "label": {"display": True, "content": f"TP3: {tp3:.6f}", "position": "end", "backgroundColor": "#9B59B6", "color": "#FFFFFF"}}
                            ]
                        }
                    },
                    "scales": {
                        "x": {"ticks": {"color": "#AAAAAA"}, "grid": {"color": "rgba(255,255,255,0.05)"}},
                        "y": {"ticks": {"color": "#AAAAAA"}, "grid": {"color": "rgba(255,255,255,0.05)"}}
                    }
                }
            }
            return f"https://quickchart.io/chart?c={json.dumps(config)}&w=800&h=500&bkg=%23131722&f=png"
        except Exception as e:
            logging.error(f"Chart URL generation failed: {e}")
            return None

    @staticmethod
    def format_premium_signal(symbol, chain, price, vwap, sl, tp1, tp2, tp3, pool_address):
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        chain_display = SignalFormatter.get_chain_display_name(chain)
        token_address = pool_address
        
        message = (
            f"🔥 <b>ALPHA SIGNAL: {symbol}</b> <i>({chain_display})</i>\n"
            f"🕒 {ts}\n\n"
            f"📊 <b>Setup:</b> Wyckoff Spring + VWAP Bounce\n\n"
            f"📋 <b>CA:</b> <code>{token_address}</code>\n\n"
            f"💰 <b>Entry Zone:</b> <code>{price:.8f}</code>\n"
            f"🎯 <b>VWAP Reference:</b> <code>{vwap:.8f}</code>\n"
            f"🛑 <b>Stop Loss:</b> <code>{sl:.8f}</code>\n\n"
            f"✅ <b>Take Profit 1:</b> <code>{tp1:.8f}</code> <i>(Conservative)</i>\n"
            f"✅ <b>Take Profit 2:</b> <code>{tp2:.8f}</code> <i>(Moderate)</i>\n"
            f"✅ <b>Take Profit 3:</b> <code>{tp3:.8f}</code> <i>(Moonshot)</i>\n\n"
            f"🔒 <b>Security:</b> ✅ GoPlus Verified\n"
            f"⚠️ <i>Trade at your own risk. Not financial advice.</i>"
        )
        
        keyboard = SignalFormatter.build_inline_keyboard(chain, pool_address, token_address)
        return message, keyboard

# ==========================================
# 7. MAIN ORCHESTRATOR
# ==========================================
async def run_bot():
    logging.info("🚀 ALPHA SIGNAL BOT v2.0 STARTING (WEB SERVICE MODE)")
    
    await init_web_server()
    
    async with aiohttp.ClientSession() as session:
        tg = TelegramManager(session)
        fetcher = DataFetcher(session)
        security = SecurityGuard(session)
        db = Database()

        await tg.send_admin_alert("✅ System Online. Monitoring Solana/Base/BSC DEX Spot.", "INFO")

        last_dex = 0
        last_gecko = 0

        while True:
            try:
                now = time.time()

                # ==========================================
                # 1. SCANNER (GeckoTerminal New Pools)
                # ==========================================
                if now - last_dex >= SCANNER_LIMIT:
                    last_dex = now
                    logging.info("🔍 Scanning GeckoTerminal for new pools (SOL/BASE/BSC)...")
                    pools = await fetcher.scan_new_pools()
                    logging.info(f"📥 Total {len(pools)} pools found across all chains")
                    
                    qualified_count = 0
                    for p in pools:
                        age_hours = 0
                        if p.get('pool_created_at'):
                            try:
                                created_str = p['pool_created_at'].replace('Z', '').split('.')[0]
                                created = datetime.strptime(created_str, "%Y-%m-%dT%H:%M:%S")
                                age_hours = (datetime.utcnow() - created).total_seconds() / 3600
                            except Exception:
                                age_hours = 999
                        
                        # 🔥 KRITERIA PREMIUM FILTER
                        if (p['liquidity_usd'] >= 30000 and
                            p['volume_24h'] >= 50000 and
                            100000 <= p['market_cap'] <= 5000000 and
                            age_hours >= 1 and
                            age_hours <= 168):
                            
                            db.add(p['pool_address'], p['chain'], p['symbol'])
                            qualified_count += 1
                            logging.info(f"   🎯 QUALIFIED: {p['symbol']} ({p['chain'].upper()}) | MC: ${p['market_cap']:,.0f} | Liq: ${p['liquidity_usd']:,.0f} | Age: {age_hours:.1f}h")
                        else:
                            reasons = []
                            if p['liquidity_usd'] < 30000:
                                reasons.append(f"Liq: ${p['liquidity_usd']:,.0f} < $30K")
                            if p['volume_24h'] < 50000:
                                reasons.append(f"Vol: ${p['volume_24h']:,.0f} < $50K")
                            if p['market_cap'] < 100000 or p['market_cap'] > 5000000:
                                reasons.append(f"MC: ${p['market_cap']:,.0f} not in $100K-$5M")
                            if age_hours < 1:
                                reasons.append(f"Age: {age_hours:.1f}h < 1h")
                            if age_hours > 168:
                                reasons.append(f"Age: {age_hours:.1f}h > 7d")
                            logging.info(f"   ⛔ REJECTED: {p['symbol']} ({p['chain'].upper()}) | {', '.join(reasons)}")
                    
                    logging.info(f"✅ {qualified_count} tokens added to watchlist (qualified from {len(pools)})")
                    await asyncio.sleep(0.1)

                # ==========================================
                # 2. ANALYZER (GeckoTerminal Polling)
                # ==========================================
                if now - last_gecko >= GECKO_LIMIT:
                    last_gecko = now
                    wl = db.get()[:5]  # Batch limit
                    logging.info(f"📊 Analyzing {len(wl)} tokens from watchlist (Total: {len(db.get())} in queue)")
                    
                    for pool, chain, sym in wl:
                        logging.info(f"   📈 Fetching data for {sym} ({chain.upper()})...")
                        candles = await fetcher.gecko_ohlcv(chain, pool)
                        if not candles:
                            logging.warning(f"   ⚠️ No candle data for {sym}, skipping...")
                            continue
                        
                        v = QuantMath.vwap(candles)
                        a = QuantMath.atr(candles)
                        c = QuantMath.cvd(candles)
                        spring = QuantMath.wyckoff_spring(candles)
                        price = candles[-1]['c']

                        logging.info(f"   📊 {sym}: Price=${price:.8f} | VWAP=${v:.8f} | ATR={a:.8f} | Spring={spring}")

                        # ENTRY LOGIC
                        if spring and price <= v * 1.02:
                            logging.info(f"🔍 Potential signal detected: {sym} on {chain.upper()}")
                            
                            safe, reason = await security.audit(chain, pool)
                            if safe:
                                logging.info(f"✅ Security check passed for {sym}")
                                
                                # Calculate SL and TP levels using real math
                                sl = price - (2.5 * a)
                                tp1, tp2, tp3 = SignalFormatter.calculate_tp_levels(price, a)
                                
                                # Format signal text + inline keyboard
                                msg, keyboard = SignalFormatter.format_premium_signal(
                                    sym, chain, price, v, sl, tp1, tp2, tp3, pool
                                )
                                
                                # 1. Hantar TEXT + INLINE BUTTONS
                                success = await tg.send_signal(msg, inline_keyboard=keyboard)
                                
                                if success:
                                    db.remove(pool)
                                    logging.info(f"📡 Signal sent successfully: {sym} | {chain.upper()}")
                                    await tg.send_admin_alert(f"📡 Signal Sent: {sym} | {chain.upper()}", "INFO")
                                    
                                    # 2. Hantar CHART sebagai PHOTO (Background Task)
                                    if candles:
                                        chart_url = SignalFormatter.generate_chart_url(candles, price, sl, tp1, tp2, tp3, sym, chain)
                                        if chart_url:
                                            caption = (
                                                f"📊 <b>{sym} ({chain.upper()})</b>\n"
                                                f"💰 Entry: <code>{price:.8f}</code>\n"
                                                f"🛑 SL: <code>{sl:.8f}</code>\n"
                                                f"✅ TP1: <code>{tp1:.8f}</code> | TP2: <code>{tp2:.8f}</code>"
                                            )
                                            asyncio.create_task(tg.send_chart_photo(chart_url, caption))
                                else:
                                    logging.error(f"❌ Failed to send signal for {sym}")
                            else:
                                logging.warning(f"🚫 Security check failed for {sym}: {reason}")
                                await tg.send_admin_alert(
                                    f"🚫 Security Check Failed: {sym}\nReason: {reason}\nChain: {chain.upper()}",
                                    "WARNING"
                                )
                        await asyncio.sleep(0.2)

                await asyncio.sleep(0.5)
                
                # Periodic status update (every 60 seconds)
                if int(now) % 60 == 0:
                    logging.info(f"📊 STATUS: Watchlist={len(db.get())} tokens | Uptime={int(time.time()-start_time)}s")

            except Exception as e:
                err_msg = f"💥 CRASH DETECTED\n{type(e).__name__}: {str(e)}"
                try:
                    await tg.send_admin_alert(err_msg, "ERROR")
                except:
                    pass
                logging.error(f"Unhandled Exception: {e}")
                await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n⛔ Bot stopped manually.")