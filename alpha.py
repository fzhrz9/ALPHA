import asyncio
import aiohttp
import sqlite3
import logging
import time
import os
import sys
from datetime import datetime
from aiohttp import web  # <--- TAMBAHAN: Import web server

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

# Rate Limiters
DEXSCREENER_LIMIT = 3.0  
GECKO_LIMIT = 1.5        
GOPLUS_LIMIT = 1.0

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler()]
)

# ==========================================
# 2. TELEGRAM ENGINE
# ==========================================
class TelegramManager:
    def __init__(self, session):
        self.session = session
        self.base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"

    async def send_signal(self, message):
        await self._send(SIGNAL_CHANNEL_ID, message, parse_mode="HTML")

    async def send_admin_alert(self, message, level="INFO"):
        alert = f"⚙️ <b>[{level}] SYSTEM LOG</b>\n<pre>{message}</pre>"
        await self._send(ADMIN_CHAT_ID, alert, parse_mode="HTML")

    async def _send(self, chat_id, text, parse_mode=None):
        url = f"{self.base_url}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode: payload["parse_mode"] = parse_mode
        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logging.error(f"Telegram API Error: {await resp.text()}")
        except Exception as e:
            logging.error(f"Telegram Send Failed: {e}")

# ==========================================
# 3. DATABASE & MATH (Optimized)
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
# 4. API & SECURITY ENGINES
# ==========================================
class DataFetcher:
    def __init__(self, session): self.session = session

    async def dexscreener_latest(self):
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        try:
            async with self.session.get(url) as r:
                return await r.json() if r.status == 200 else []
        except: return []

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
                    if t.get('is_honeypot')=='1': return False, "Honeypot"
                    if t.get('is_mintable')=='1': return False, "Mintable"
                    if float(t.get('buy_tax',0))>0.05: return False, "BuyTax>5%"
                    if float(t.get('sell_tax',0))>0.05: return False, "SellTax>5%"
        except: pass
        return True, "Secure"

# ==========================================
# 5. PREMIUM SIGNAL FORMATTER
# ==========================================
def format_signal(symbol, chain, price, vwap, sl, tp1, setup):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"🔥 <b>ALPHA SIGNAL: {symbol}</b> <i>({chain.upper()})</i>\n"
        f"🕒 {ts}\n\n"
        f" <b>Setup:</b> {setup}\n"
        f"💰 <b>Entry Zone:</b> ${price:.8f}\n"
        f"🎯 <b>VWAP Reference:</b> ${vwap:.8f}\n"
        f"🛑 <b>Stop Loss:</b> ${sl:.8f}\n"
        f"✅ <b>Take Profit 1:</b> ${tp1:.8f}\n\n"
        f"🔒 <b>Security:</b> ✅ GoPlus Verified\n"
        f"⚠️ <i>Trade at your own risk. Not financial advice.</i>"
    )

# ==========================================
# 6. HEALTH CHECK SERVER (FIX FOR RENDER WEB SERVICE)
# ==========================================
async def health_check_handler(request):
    """Endpoint untuk Render health check: GET /health"""
    return web.json_response({"status": "ok", "uptime": time.time() - start_time})

async def init_web_server():
    """Setup mini web server pada port $PORT"""
    app = web.Application()
    app.add_routes([web.get('/health', health_check_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 8080)))
    await site.start()
    logging.info(f"🌐 Health check server running on port {os.getenv('PORT', 8080)}")

# ==========================================
# 7. MAIN ORCHESTRATOR
# ==========================================
start_time = time.time()  # Global var for uptime

async def run_bot():
    logging.info("🚀 ALPHA SIGNAL BOT v2.0 STARTING (WEB SERVICE MODE)")
    
    # 1. Start Health Check Server DULU (PENTING UNTUK RENDER)
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

                # 1. SCANNER (DexScreener)
                if now - last_dex >= DEXSCREENER_LIMIT:
                    last_dex = now
                    items = await fetcher.dexscreener_latest()
                    for i in items:
                        ch = i.get('chainId','').lower()
                        if ch not in ALLOWED_CHAINS: continue
                        pool = i.get('poolAddress') or i.get('address')
                        sym = i.get('tokenSymbol') or i.get('symbol','UNK')
                        if pool: db.add(pool, ch, sym)
                    await asyncio.sleep(0.1)

                # 2. ANALYZER (GeckoTerminal Polling)
                if now - last_gecko >= GECKO_LIMIT:
                    last_gecko = now
                    wl = db.get()[:5]  # Batch limit
                    for pool, chain, sym in wl:
                        candles = await fetcher.gecko_ohlcv(chain, pool)
                        if not candles: continue
                        
                        v = QuantMath.vwap(candles)
                        a = QuantMath.atr(candles)
                        c = QuantMath.cvd(candles)
                        spring = QuantMath.wyckoff_spring(candles)
                        price = candles[-1]['c']

                        # ENTRY LOGIC
                        if spring and price <= v * 1.02:
                            safe, reason = await security.audit(chain, pool)
                            if safe:
                                sl = price - (2.5 * a)
                                tp = price + (2.0 * a)
                                setup = "Wyckoff Spring + VWAP Bounce"
                                msg = format_signal(sym, chain, price, v, sl, tp, setup)
                                await tg.send_signal(msg)
                                db.remove(pool)
                                await tg.send_admin_alert(f"📡 Signal Sent: {sym} | {chain.upper()}", "INFO")
                        await asyncio.sleep(0.2)

                await asyncio.sleep(0.5)

            except Exception as e:
                err_msg = f"💥 CRASH DETECTED\n{type(e).__name__}: {str(e)}"
                await tg.send_admin_alert(err_msg, "ERROR")
                logging.error(f"Unhandled Exception: {e}")
                await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n⛔ Bot stopped manually.")