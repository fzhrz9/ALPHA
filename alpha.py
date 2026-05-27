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
        return await self._send(SIGNAL_CHANNEL_ID, message, parse_mode="HTML", inline_keyboard=inline_keyboard)

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
        import os
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'quant_bot.db')
        logging.info(f"💾 DB path: {db_path}")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._setup()

    def _setup(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                     (pool_address TEXT PRIMARY KEY, chain TEXT, symbol TEXT, added_at REAL, token_address TEXT DEFAULT '')''')
        try:
            c.execute("ALTER TABLE watchlist ADD COLUMN token_address TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # Column already exists
        c.execute('''CREATE TABLE IF NOT EXISTS pending
                     (pool_address TEXT PRIMARY KEY, chain TEXT, symbol TEXT, token_address TEXT,
                      liquidity_usd REAL, volume_24h REAL, market_cap REAL,
                      pool_created_at TEXT, added_at REAL)''')
        self.conn.commit()

    def add_pending(self, pool, chain, symbol, token_address, liquidity_usd, volume_24h, market_cap, pool_created_at):
        self.conn.execute(
            "INSERT OR IGNORE INTO pending VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pool, chain, symbol, token_address, liquidity_usd, volume_24h, market_cap, pool_created_at, time.time())
        )
        self.conn.commit()

    def promote_ready_pending(self):
        """Semak pending pools — promot ke watchlist jika umur sudah >= 1h."""
        rows = self.conn.execute(
            "SELECT pool_address, chain, symbol, token_address, pool_created_at FROM pending"
        ).fetchall()
        promoted = []
        for pool, chain, symbol, token_address, pool_created_at in rows:
            try:
                if not pool_created_at:
                    continue  # pool_created_at kosong — skip, jangan promot
                created_str = pool_created_at.replace('Z', '').split('.')[0]
                created = datetime.strptime(created_str, "%Y-%m-%dT%H:%M:%S")
                age_hours = (datetime.utcnow() - created).total_seconds() / 3600
            except Exception:
                continue  # Format tidak dikenali — skip selamat, semak semula nanti
            if age_hours >= 1:
                self.add(pool, chain, symbol, token_address)
                self.conn.execute("DELETE FROM pending WHERE pool_address=?", (pool,))
                self.conn.commit()
                promoted.append((symbol, chain))
        return promoted

    def pending_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]

    def add(self, pool, chain, symbol, token_address=''):
        self.conn.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?, ?, ?)", 
                          (pool, chain, symbol, time.time(), token_address))
        self.conn.commit()

    def get(self):
        return self.conn.execute("SELECT pool_address, chain, symbol, token_address FROM watchlist").fetchall()

    def remove(self, pool):
        self.conn.execute("DELETE FROM watchlist WHERE pool_address=?", (pool,))
        self.conn.commit()

    def cleanup_stale_watchlist(self, max_hours=6):
        """Buang token yang dah lama dalam watchlist tanpa signal (> max_hours)."""
        cutoff = time.time() - (max_hours * 3600)
        removed = self.conn.execute(
            "DELETE FROM watchlist WHERE added_at < ?", (cutoff,)
        ).rowcount
        self.conn.commit()
        return removed

class QuantMath:
    # ── ASAS ──────────────────────────────────────────────
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
    def vwap(candles):
        if not candles: return 0
        tpv = sum(((c['h']+c['l']+c['c'])/3)*c['v'] for c in candles)
        vol = sum(c['v'] for c in candles)
        return tpv/vol if vol > 0 else 0

    @staticmethod
    def avg_volume(candles, n=20):
        vols = [c['v'] for c in candles[-n:] if c['v'] > 0]
        return sum(vols)/len(vols) if vols else 0

    # ── STRUKTUR PASARAN ──────────────────────────────────
    @staticmethod
    def swing_points(candles, lb=3):
        """Kesan swing high dan swing low menggunakan lookback lb."""
        highs, lows = [], []
        for i in range(lb, len(candles)-lb):
            if candles[i]['h'] == max(c['h'] for c in candles[i-lb:i+lb+1]):
                highs.append((i, candles[i]['h']))
            if candles[i]['l'] == min(c['l'] for c in candles[i-lb:i+lb+1]):
                lows.append((i, candles[i]['l']))
        return highs, lows

    @staticmethod
    def market_structure(highs, lows):
        """
        Tentukan struktur: uptrend (HH+HL), downtrend (LH+LL), atau choppy.
        Returns (trend, label)
        """
        if len(highs) < 2 or len(lows) < 2:
            return 'unknown', 'unknown'
        hh = highs[-1][1] > highs[-2][1]
        hl = lows[-1][1]  > lows[-2][1]
        lh = highs[-1][1] < highs[-2][1]
        ll = lows[-1][1]  < lows[-2][1]
        if hh and hl:  return 'uptrend',   'HH+HL'
        if lh and ll:  return 'downtrend',  'LH+LL'
        if hh and ll:  return 'choppy',     'HH+LL'
        if lh and hl:  return 'choppy',     'LH+HL'
        return 'choppy', 'mixed'

    @staticmethod
    def choch(highs, lows, last_price):
        """
        Change of Character — downtrend bertukar bullish.
        Syarat: selepas LH+LL, harga tutup melebihi LH terakhir.
        """
        if len(highs) < 2 or len(lows) < 2:
            return False
        last_lh  = highs[-1][1]
        prev_lh  = highs[-2][1]
        is_lh    = last_lh < prev_lh          # struktur LH sahih
        broke_lh = last_price > last_lh        # harga tembus LH
        return is_lh and broke_lh

    # ── FIBONACCI ─────────────────────────────────────────
    @staticmethod
    def fibonacci(swing_low, swing_high):
        """Paras Fibonacci retracement standard."""
        if swing_high <= swing_low: return {}
        d = swing_high - swing_low
        return {
            '0.236': swing_high - 0.236*d,
            '0.382': swing_high - 0.382*d,
            '0.5':   swing_high - 0.5  *d,
            '0.618': swing_high - 0.618*d,
            '0.786': swing_high - 0.786*d,
        }

    # ── FAIR VALUE GAP (FVG) ──────────────────────────────
    @staticmethod
    def find_fvg(candles):
        """
        Bullish FVG: jurang antara candle[i-2] high dan candle[i] low.
        Gap wujud apabila candle[i-2].high < candle[i].low.
        """
        gaps = []
        for i in range(2, len(candles)):
            gl = candles[i-2]['h']   # bawah gap
            gh = candles[i]['l']     # atas gap
            if gh > gl:
                gaps.append({'low': gl, 'high': gh, 'mid': (gl+gh)/2, 'idx': i})
        return gaps

    # ── ENJIN BREAKOUT ────────────────────────────────────
    @staticmethod
    def breakout_engine(candles_h1, candles_m15):
        """
        Breakout tulen (bukan fakeout):
        1. Kenal pasti rintangan H1 (swing high terakhir)
        2. Harga M15 tutup DI ATAS rintangan (bukan sekadar wick)
        3. 2+ candle M15 berturut-turut di atas rintangan
        4. Volume breakout >= 1.5x purata (pengesahan)
        5. CHoCH disahkan pada H1
        Returns: (signal:bool, data:dict, reason:str)
        """
        MIN_H1, MIN_M15 = 6, 20
        if len(candles_h1) < MIN_H1 or len(candles_m15) < MIN_M15:
            return False, {}, f"Data kurang (H1:{len(candles_h1)}, M15:{len(candles_m15)})"

        highs_h1, lows_h1 = QuantMath.swing_points(candles_h1, lb=3)
        if not highs_h1:
            return False, {}, "Tiada swing high H1"

        resistance = highs_h1[-1][1]
        price_now  = candles_m15[-1]['c']

        # Periksa 2 candle M15 terakhir tutup di atas rintangan (bukan sekadar 1)
        recent3 = candles_m15[-3:]
        closes_above = sum(1 for c in recent3 if c['c'] > resistance)
        if closes_above < 2:
            return False, {}, f"Hanya {closes_above}/3 candle M15 tutup atas rintangan"

        # Body close check — bukan sekadar wick
        bo_candle = candles_m15[-2]
        body_close = bo_candle['c'] > resistance
        if not body_close:
            return False, {}, "Hanya wick melebihi rintangan (fakeout pattern)"

        # Pengesahan volume: breakout candle >= 1.5x purata
        avg_vol = QuantMath.avg_volume(candles_m15, n=20)
        bo_vol  = bo_candle['v']
        if avg_vol > 0 and bo_vol < 1.5 * avg_vol:
            return False, {}, f"Volume rendah pada breakout ({bo_vol:.0f} < {1.5*avg_vol:.0f}) — potensi fakeout"

        # CHoCH H1
        trend_h1, struct_h1 = QuantMath.market_structure(highs_h1, lows_h1)
        choch_ok = QuantMath.choch(highs_h1, lows_h1, price_now) if trend_h1 == 'downtrend' else True

        atr_m15 = QuantMath.atr(candles_m15)
        if atr_m15 == 0:
            return False, {}, "ATR M15 = 0"

        sl  = resistance - (1.0 * atr_m15)
        tp1 = price_now  + (1.5 * atr_m15)
        tp2 = price_now  + (2.5 * atr_m15)
        tp3 = price_now  + (4.0 * atr_m15)
        rr  = (tp1 - price_now) / (price_now - sl) if price_now > sl else 0

        if rr < 1.5:
            return False, {}, f"Risk-Reward terlalu rendah ({rr:.2f}x < 1.5x)"

        data = {
            'engine':     'BREAKOUT',
            'resistance': resistance,
            'price':      price_now,
            'sl':         sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
            'rr':         rr,
            'bo_vol_ratio': bo_vol/avg_vol if avg_vol else 0,
            'choch':      choch_ok,
            'struct_h1':  struct_h1,
            'atr_m15':    atr_m15,
        }
        reason = f"Breakout atas ${resistance:.8f} | Vol {bo_vol/avg_vol:.1f}x purata | {struct_h1}"
        if choch_ok:
            reason += " | CHoCH ✓"
        return True, data, reason

    # ── ENJIN BUY THE DIP ─────────────────────────────────
    @staticmethod
    def dip_engine(candles_h1, candles_m15):
        """
        Buy the Dip yang selamat:
        1. Uptrend H1 (HH+HL)
        2. Harga retrace ke zon Fib 0.618-0.786 ATAU ke dalam FVG
        3. Volume pullback < 50% purata impulse (pullback sihat, bukan dump)
        4. Tiada candle rug (-30% dalam satu candle M15)
        5. Tanda pembalikan: candle M15 terkini tutup di atas paras support
        Returns: (signal:bool, data:dict, reason:str)
        """
        MIN_H1, MIN_M15 = 10, 20
        if len(candles_h1) < MIN_H1 or len(candles_m15) < MIN_M15:
            return False, {}, f"Data kurang (H1:{len(candles_h1)}, M15:{len(candles_m15)})"

        highs_h1, lows_h1 = QuantMath.swing_points(candles_h1, lb=3)
        trend_h1, struct_h1 = QuantMath.market_structure(highs_h1, lows_h1)

        if trend_h1 != 'uptrend':
            return False, {}, f"Bukan uptrend H1: {struct_h1}"

        if not highs_h1 or not lows_h1:
            return False, {}, "Tiada swing point H1"

        swing_high = highs_h1[-1][1]
        swing_low  = lows_h1[-1][1]
        fibs       = QuantMath.fibonacci(swing_low, swing_high)
        if not fibs:
            return False, {}, "Fibonacci tidak sah (swing_high <= swing_low)"

        price_now = candles_m15[-1]['c']

        # Zon Fibonacci 0.618 – 0.786
        in_fib = fibs['0.786'] <= price_now <= fibs['0.618']

        # FVG H1
        fvg_list = QuantMath.find_fvg(candles_h1)
        in_fvg   = any(g['low'] <= price_now <= g['high'] for g in fvg_list[-5:])

        if not in_fib and not in_fvg:
            return False, {}, f"Harga ${price_now:.8f} bukan dalam zon Fib/FVG (618:{fibs['0.618']:.8f} 786:{fibs['0.786']:.8f})"

        # Pengesanan rug: candle M15 turun > 30% dalam satu candle
        for c in candles_m15[-10:]:
            if c['o'] > 0 and c['l'] / c['o'] < 0.70:
                return False, {}, f"Candle rug dikesan: turun {(1-c['l']/c['o'])*100:.0f}% dalam satu bar"

        # Volume pullback mesti RENDAH (bukan dump/distribusi)
        avg_vol    = QuantMath.avg_volume(candles_m15, n=20)
        pull_vols  = [c['v'] for c in candles_m15[-5:]]
        avg_pull   = sum(pull_vols)/len(pull_vols) if pull_vols else 0
        if avg_vol > 0 and avg_pull > 2.0 * avg_vol:
            return False, {}, f"Volume tinggi pada pullback ({avg_pull:.0f} > {2*avg_vol:.0f}) — mungkin dump/distribusi"

        # Tanda pembalikan: candle terkini tutup di atas support (swing_low atau fib)
        support = fibs['0.786'] if in_fib else (min(g['low'] for g in fvg_list[-5:] if g['low'] <= price_now <= g['high']) if in_fvg else swing_low)
        reversal_ok = candles_m15[-1]['c'] > support and candles_m15[-1]['c'] > candles_m15[-1]['o']

        if not reversal_ok:
            return False, {}, "Tiada tanda pembalikan pada M15 (candle terkini masih bearish)"

        atr_m15 = QuantMath.atr(candles_m15)
        if atr_m15 == 0:
            return False, {}, "ATR M15 = 0"

        sl  = swing_low - (0.5 * atr_m15)
        tp1 = price_now + (1.5 * atr_m15)
        tp2 = swing_high
        tp3 = swing_high + (0.5 * (swing_high - swing_low))
        rr  = (tp1 - price_now) / (price_now - sl) if price_now > sl else 0

        if rr < 1.5:
            return False, {}, f"Risk-Reward terlalu rendah ({rr:.2f}x < 1.5x)"

        fib_label = "Fib 0.618-0.786" if in_fib else "FVG H1"
        data = {
            'engine':     'DIP',
            'swing_high': swing_high,
            'swing_low':  swing_low,
            'fibs':       fibs,
            'in_fib':     in_fib,
            'in_fvg':     in_fvg,
            'fib_label':  fib_label,
            'price':      price_now,
            'sl':         sl, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
            'rr':         rr,
            'struct_h1':  struct_h1,
            'atr_m15':    atr_m15,
            'support':    support,
        }
        reason = f"Dip ke {fib_label} dalam uptrend {struct_h1} | RR {rr:.1f}x"
        return True, data, reason

# ==========================================
# 5. API & SECURITY ENGINES
# ==========================================
class DataFetcher:
    def __init__(self, session):
        self.session = session

    def _parse_pool(self, pool, chain):
        """Parse satu pool dari GeckoTerminal API response."""
        attrs    = pool.get('attributes', {})
        pool_id  = pool.get('id', '')
        pool_address = pool_id.replace(f'{chain}_', '') if pool_id.startswith(f'{chain}_') else pool_id
        base_token_id = pool.get('relationships', {}).get('base_token', {}).get('data', {}).get('id', '')
        token_address = base_token_id.replace(f'{chain}_', '') if base_token_id.startswith(f'{chain}_') else base_token_id
        if not token_address:
            token_address = pool_address
        name   = attrs.get('name', 'UNK')
        symbol = name.split(' / ')[0].strip() if '/' in name else name
        return {
            'chain':          chain,
            'pool_address':   pool_address,
            'token_address':  token_address,
            'symbol':         symbol,
            'price_usd':      float(attrs.get('base_token_price_usd', 0) or 0),
            'volume_24h':     float(attrs.get('volume_usd', {}).get('h24', 0) or 0),
            'liquidity_usd':  float(attrs.get('reserve_in_usd', 0) or 0),
            'market_cap':     float(attrs.get('market_cap_usd', 0) or 0) or float(attrs.get('fdv_usd', 0) or 0),
            'pool_created_at': attrs.get('pool_created_at', ''),
            'fdv':            float(attrs.get('fdv_usd', 0) or 0),
        }

    async def _fetch_pools(self, chain, endpoint, page=1):
        url = f"https://api.geckoterminal.com/api/v2/networks/{chain}/{endpoint}?page={page}"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    return [self._parse_pool(p, chain) for p in data.get('data', [])]
                logging.warning(f"   ⚠️ {chain.upper()} {endpoint}: HTTP {r.status}")
        except Exception as e:
            logging.error(f"   ❌ {chain.upper()} {endpoint} error: {e}")
        return []

    async def scan_trending_pools(self):
        """
        Scan trending pools — token lebih matang dengan volume sebenar.
        Sumber: /trending_pools (token aktif diperdagangkan, bukan baru launched)
        Filter umur: 4h - 168h (token cukup matang untuk analisis teknikal)
        """
        all_pools = []
        for chain in ['bsc', 'base', 'solana']:
            pools = await self._fetch_pools(chain, 'trending_pools')
            valid = 0
            for p in pools:
                if p['liquidity_usd'] < 0:
                    continue
                age_hours = self._age_hours(p['pool_created_at'])
                if age_hours is None:
                    continue
                p['age_hours'] = age_hours
                all_pools.append(p)
                valid += 1
            logging.info(f"   ✅ {chain.upper()}: {valid} trending pools")
            await asyncio.sleep(0.5)
        return all_pools

    def _age_hours(self, pool_created_at):
        if not pool_created_at:
            return None
        try:
            s = pool_created_at.replace('Z','').split('.')[0]
            created = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
            return (datetime.utcnow() - created).total_seconds() / 3600
        except Exception:
            return None

    async def _ohlcv(self, net, pool, tf_type, aggregate, limit):
        """Generic OHLCV fetcher. tf_type: 'minute' atau 'hour'"""
        url = (f"https://api.geckoterminal.com/api/v2/networks/{net}/pools/{pool}"
               f"/ohlcv/{tf_type}?aggregate={aggregate}&limit={limit}")
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    raw = (await r.json()).get('data',{}).get('attributes',{}).get('ohlcv_list',[])
                    return [{'t':x[0],'o':x[1],'h':x[2],'l':x[3],'c':x[4],'v':x[5]} for x in reversed(raw)]
        except: pass
        return []

    async def ohlcv_h1(self, net, pool):
        """H1 candles — 48 jam ke belakang"""
        return await self._ohlcv(net, pool, 'hour', 1, 48)

    async def ohlcv_m15(self, net, pool):
        """M15 candles — 24 jam ke belakang"""
        return await self._ohlcv(net, pool, 'minute', 15, 96)

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
        except Exception as e:
            logging.error(f"GoPlus API error for {addr}: {e}")
            return False, "API Error"
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
    def format_premium_signal(symbol, chain, engine_data, pool_address, token_address):
        ts    = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        cd    = SignalFormatter.get_chain_display_name(chain)
        eng   = engine_data.get('engine', 'SIGNAL')
        price = engine_data['price']
        sl    = engine_data['sl']
        tp1   = engine_data['tp1']
        tp2   = engine_data['tp2']
        tp3   = engine_data['tp3']
        rr    = engine_data.get('rr', 0)

        if eng == 'BREAKOUT':
            icon   = "🚀"
            setup  = "Breakout Disahkan | Bukan Fakeout"
            extras = (
                f"🧱 <b>Rintangan Ditembus:</b> <code>{engine_data.get('resistance',0):.8f}</code>\n"
                f"📊 <b>Volume Breakout:</b> {engine_data.get('bo_vol_ratio',0):.1f}x purata\n"
                f"📐 <b>Struktur H1:</b> {engine_data.get('struct_h1','')}"
                + (" | CHoCH ✓" if engine_data.get('choch') else "") + "\n"
            )
        else:
            icon   = "🎯"
            setup  = "Buy the Dip | Bukan Dump/Rug"
            fibs   = engine_data.get('fibs', {})
            extras = (
                f"📐 <b>Struktur H1:</b> {engine_data.get('struct_h1','')} (Uptrend)\n"
                f"🌀 <b>Zon Entry:</b> {engine_data.get('fib_label','')}\n"
                + (f"   Fib 0.618: <code>{fibs.get('0.618',0):.8f}</code> | "
                   f"Fib 0.786: <code>{fibs.get('0.786',0):.8f}</code>\n" if engine_data.get('in_fib') else "")
                + f"🏔 <b>Swing High:</b> <code>{engine_data.get('swing_high',0):.8f}</code>\n"
                + f"🏔 <b>Swing Low:</b>  <code>{engine_data.get('swing_low',0):.8f}</code>\n"
            )

        message = (
            f"{icon} <b>ALPHA {eng}: {symbol}</b> <i>({cd})</i>\n"
            f"🕒 {ts}\n\n"
            f"📊 <b>Setup:</b> {setup}\n\n"
            f"{extras}\n"
            f"📋 <b>CA:</b> <code>{token_address}</code>\n\n"
            f"💰 <b>Entry:</b>   <code>{price:.8f}</code>\n"
            f"🛑 <b>Stop Loss:</b> <code>{sl:.8f}</code>\n\n"
            f"✅ <b>TP1:</b> <code>{tp1:.8f}</code> <i>(Conservative)</i>\n"
            f"✅ <b>TP2:</b> <code>{tp2:.8f}</code> <i>(Moderate)</i>\n"
            f"✅ <b>TP3:</b> <code>{tp3:.8f}</code> <i>(Moonshot)</i>\n\n"
            f"⚖️ <b>Risk:Reward:</b> {rr:.1f}x\n"
            f"🔒 <b>Security:</b> ✅ GoPlus Verified\n"
            f"⚠️ <i>Trade at your own risk. Not financial advice.</i>"
        )
        keyboard = SignalFormatter.build_inline_keyboard(chain, pool_address, token_address)
        return message, keyboard

# ==========================================
# 7. MAIN ORCHESTRATOR
# ==========================================
async def run_bot():
    logging.info("🚀 ALPHA SIGNAL BOT v3.0 — BREAKOUT + DIP ENGINE (H1/M15)")

    await init_web_server()

    async with aiohttp.ClientSession() as session:
        tg       = TelegramManager(session)
        fetcher  = DataFetcher(session)
        security = SecurityGuard(session)
        db       = Database()

        await tg.send_admin_alert("✅ Bot v3.0 Online | Breakout + Dip Engine | Trending Pools", "INFO")
        wl_count = len(db.get())
        logging.info(f"💾 DB loaded — {wl_count} token dalam watchlist")

        last_scan   = 0
        last_gecko  = 0
        last_status = 0

        while True:
            try:
                now = time.time()

                # ══════════════════════════════════════════
                # SCANNER — Trending Pools (setiap 5 minit)
                # ══════════════════════════════════════════
                if now - last_scan >= 300.0:
                    last_scan = now
                    logging.info("🔍 Scanning trending pools (BSC/BASE/SOL)...")
                    pools = await fetcher.scan_trending_pools()
                    logging.info(f"📥 {len(pools)} trending pools diterima")

                    added = 0
                    for p in pools:
                        liq  = p['liquidity_usd']
                        vol  = p['volume_24h']
                        mc   = p['market_cap']
                        age  = p.get('age_hours', 0)

                        # Filter asas — token matang dengan aktiviti sebenar
                        if liq  < 30000:  continue
                        if vol  < 50000:  continue
                        if mc   < 100000 or mc > 10000000: continue
                        if age  < 4 or age > 168:          continue

                        db.add(p['pool_address'], p['chain'], p['symbol'],
                               p.get('token_address', p['pool_address']))
                        added += 1
                        logging.info(
                            f"   ✅ WATCHLIST: {p['symbol']} ({p['chain'].upper()}) | "
                            f"MC: ${mc:,.0f} | Liq: ${liq:,.0f} | Age: {age:.1f}h"
                        )

                    if added == 0:
                        logging.info("   ℹ️ Tiada token baharu ditambah ke watchlist")
                    logging.info(f"📋 Watchlist: {len(db.get())} token")
                    await asyncio.sleep(0.1)

                # ══════════════════════════════════════════
                # ANALYZER — Breakout + Dip Engine (1.5s)
                # ══════════════════════════════════════════
                if now - last_gecko >= GECKO_LIMIT:
                    last_gecko = now
                    wl = db.get()[:1]  # 1 token per kitaran (jimat rate limit)
                    logging.info(f"📊 Menganalisa {len(wl)} token | Watchlist: {len(db.get())}")

                    for pool, chain, sym, token_address in wl:
                        logging.info(f"   📈 {sym} ({chain.upper()}) — Ambil H1 + M15...")

                        h1  = await fetcher.ohlcv_h1(chain, pool)
                        await asyncio.sleep(2.0)   # 2s antara H1 dan M15 (jimat rate limit)
                        m15 = await fetcher.ohlcv_m15(chain, pool)
                        await asyncio.sleep(2.0)   # 2s selepas M15 sebelum analisis

                        if len(h1) < 6 or len(m15) < 20:
                            logging.warning(f"   ⚠️ {sym}: Data tidak cukup (H1:{len(h1)}, M15:{len(m15)})")
                            continue

                        price = m15[-1]['c'] if m15 else 0
                        if price <= 0:
                            continue

                        # ── Cuba BREAKOUT engine dahulu
                        bo_ok, bo_data, bo_reason = QuantMath.breakout_engine(h1, m15)
                        if bo_ok:
                            logging.info(f"   🚀 BREAKOUT: {sym} | {bo_reason}")
                            engine_data = bo_data
                            engine_ok   = True
                        else:
                            logging.info(f"   ❌ Breakout: {sym} — {bo_reason}")
                            # ── Cuba DIP engine
                            dip_ok, dip_data, dip_reason = QuantMath.dip_engine(h1, m15)
                            if dip_ok:
                                logging.info(f"   🎯 DIP: {sym} | {dip_reason}")
                                engine_data = dip_data
                                engine_ok   = True
                            else:
                                logging.info(f"   ❌ Dip: {sym} — {dip_reason}")
                                engine_ok = False

                        if not engine_ok:
                            await asyncio.sleep(0.2)
                            continue

                        # ── Semak keselamatan token
                        safe, reason = await security.audit(chain, token_address)
                        if not safe:
                            logging.warning(f"   🚫 Security gagal {sym}: {reason}")
                            await tg.send_admin_alert(
                                f"🚫 Security Fail: {sym} | {reason} | {chain.upper()}", "WARNING"
                            )
                            db.remove(pool)
                            await asyncio.sleep(0.2)
                            continue

                        logging.info(f"   ✅ Security lulus: {sym}")

                        # ── Format + hantar signal
                        msg, keyboard = SignalFormatter.format_premium_signal(
                            sym, chain, engine_data, pool, token_address
                        )
                        success = await tg.send_signal(msg, inline_keyboard=keyboard)

                        if success:
                            db.remove(pool)
                            logging.info(f"📡 Signal dihantar: {sym} | {engine_data['engine']} | {chain.upper()}")
                            await tg.send_admin_alert(
                                f"📡 Signal: {sym} | {engine_data['engine']} | RR:{engine_data.get('rr',0):.1f}x | {chain.upper()}",
                                "INFO"
                            )
                            if m15:
                                sl, tp1, tp2, tp3 = engine_data['sl'], engine_data['tp1'], engine_data['tp2'], engine_data['tp3']
                                chart_url = SignalFormatter.generate_chart_url(
                                    m15, price, sl, tp1, tp2, tp3, sym, chain
                                )
                                if chart_url:
                                    caption = (
                                        f"📊 <b>{sym} ({chain.upper()}) — {engine_data['engine']}</b>\n"
                                        f"💰 Entry: <code>{price:.8f}</code>\n"
                                        f"🛑 SL: <code>{sl:.8f}</code>\n"
                                        f"✅ TP1: <code>{tp1:.8f}</code> | TP2: <code>{tp2:.8f}</code>"
                                    )
                                    asyncio.create_task(tg.send_chart_photo(chart_url, caption))
                        else:
                            logging.error(f"❌ Gagal hantar signal: {sym}")

                        await asyncio.sleep(0.5)

                # ══════════════════════════════════════════
                # STATUS (setiap 60s)
                # ══════════════════════════════════════════
                if now - last_status >= 60.0:
                    last_status = now
                    stale = db.cleanup_stale_watchlist(max_hours=6)
                    if stale:
                        logging.info(f"🧹 {stale} token lapuk dibuang dari watchlist")
                    logging.info(
                        f"📊 STATUS: Watchlist={len(db.get())} | Uptime={int(time.time()-start_time)}s"
                    )

                await asyncio.sleep(0.5)

            except Exception as e:
                err = f"💥 CRASH: {type(e).__name__}: {e}"
                try:
                    await tg.send_admin_alert(err, "ERROR")
                except:
                    pass
                logging.error(err)
                await asyncio.sleep(10)
if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n⛔ Bot stopped manually.")
