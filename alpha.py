import asyncio
import aiohttp
import sqlite3
import json
import math
import logging
import time
from collections import deque
from datetime import datetime
import os

# ==========================================
# 1. KONFIGURASI RANGKAIAN (TIADA ETH!)
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID", "YOUR_PRIVATE_CHANNEL_ID")

# Rangkaian Fokus: Solana, Base, BSC
RPC_SOL = "https://api.mainnet-beta.solana.com"
RPC_BASE = "https://mainnet.base.org"
RPC_BSC = "https://rpc.ankr.com/bsc"

# MEV Protection RPC (Selain Flashbots ETH)
# Solana guna Jito, Base/BSC guna Private RPC seperti BloXroute atau 48 Club
MEV_RPC_SOL = "https://mainnet.block-engine.jito.wtf/api/v1/transactions" 
MEV_RPC_BSC = "https://rpc-bsc.48.club" # Anti-MEV / Priority RPC untuk BSC

# Whitelist Chain (Block Ethereum & Chain sampah)
ALLOWED_CHAINS = {'solana', 'base', 'bsc'}

# Rate Limiters (Optimum untuk Free Tier)
DEXSCREENER_LIMIT = 3.0  
GECKO_LIMIT = 1.5        
GOPLUS_LIMIT = 1.0

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==========================================
# 2. DATABASE (SQLite - Jimat RAM)
# ==========================================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('quant_bot.db', check_same_thread=False)
        self.setup()

    def setup(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS watchlist 
                     (pool_address TEXT PRIMARY KEY, chain TEXT, symbol TEXT, added_at REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS smart_money 
                     (wallet_address TEXT PRIMARY KEY, chain TEXT, win_rate REAL)''')
        self.conn.commit()

    def add_to_watchlist(self, pool, chain, symbol):
        self.conn.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?, ?)", 
                          (pool, chain, symbol, time.time()))
        self.conn.commit()

    def get_watchlist(self):
        return self.conn.execute("SELECT pool_address, chain, symbol FROM watchlist").fetchall()

db = Database()

# ==========================================
# 3. ENJIN MATEMATIK QUANT (Pure Python)
# ==========================================
class QuantMath:
    @staticmethod
    def calculate_vwap(candles):
        if not candles: return 0
        cum_tpv = sum(((c['h'] + c['l'] + c['c']) / 3) * c['v'] for c in candles)
        cum_vol = sum(c['v'] for c in candles)
        return cum_tpv / cum_vol if cum_vol > 0 else 0

    @staticmethod
    def calculate_atr(candles, period=14):
        if len(candles) < period + 1: return 0
        trs = []
        for i in range(1, len(candles)):
            h, l, pc = candles[i]['h'], candles[i]['l'], candles[i-1]['c']
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    @staticmethod
    def calculate_cvd(candles):
        cvd = 0
        for c in candles:
            delta = c['v'] if c['c'] > c['o'] else -c['v']
            cvd += delta
        return cvd

    @staticmethod
    def detect_wyckoff_spring(candles, lookback=20):
        if len(candles) < lookback: return False
        recent = candles[-lookback:]
        swing_low = min(c['l'] for c in recent[:-1])
        last_candle = candles[-1]
        if last_candle['c'] > swing_low and any(c['l'] < swing_low for c in recent[-5:]):
            return True
        return False

    @staticmethod
    def dynamic_slippage(trade_size_usd, liquidity_usd):
        if liquidity_usd == 0: return 20.0 
        impact = (trade_size_usd / liquidity_usd) * 100
        return min(impact + 2.0, 15.0) 

# ==========================================
# 4. ENJIN KESELAMATAN (GoPlus + Honeypot.is)
# ==========================================
class SecurityGuard:
    def __init__(self, session):
        self.session = session
        # Mapping Chain DexScreener -> GoPlus ID
        self.chain_map = {'bsc': '56', 'base': '8453', 'solana': 'solana'}

    async def audit_token(self, chain, token_address):
        chain_id = self.chain_map.get(chain)
        if not chain_id: return False, "Unsupported Chain"

        # 1. GoPlus Check
        goplus_url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={token_address}"
        async with self.session.get(goplus_url) as resp:
            if resp.status == 200:
                data = await resp.json()
                # Solana return format sometimes differs, handle safely
                result = data.get('result', {})
                if isinstance(result, dict):
                    token_data = result.get(token_address.lower(), result.get(token_address, {}))
                else:
                    token_data = {}
                
                if token_data.get('is_honeypot') == '1': return False, "Honeypot"
                if token_data.get('is_mintable') == '1': return False, "Mintable"
                if float(token_data.get('buy_tax', 0)) > 0.05: return False, "Buy Tax > 5%"
                if float(token_data.get('sell_tax', 0)) > 0.05: return False, "Sell Tax > 5%"
                
        # 2. Honeypot.is Backup (Hanya untuk EVM: BSC, Base)
        if chain in ['bsc', 'base']:
            hp_url = f"https://api.honeypot.is/v2/IsHoneypot?address={token_address}&chainID={chain_id}"
            try:
                async with self.session.get(hp_url) as resp:
                    if resp.status == 200:
                        hp_data = await resp.json()
                        if hp_data.get('honeypotResult', {}).get('isHoneypot', True):
                            return False, "Honeypot.is flagged"
            except: pass

        return True, "Secure"

# ==========================================
# 5. ENJIN API & POLLING (Async REST)
# ==========================================
class DataFetcher:
    def __init__(self, session):
        self.session = session

    async def fetch_dexscreener_trending(self):
        # Scan token profile/latest atau search
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logging.error(f"DexScreener Error: {e}")
        return []

    async def fetch_gecko_ohlcv(self, network, pool_address):
        # GeckoTerminal network name mapping
        gecko_net = {'solana': 'solana', 'base': 'base', 'bsc': 'bsc'}.get(network, network)
        url = f"https://api.geckoterminal.com/api/v2/networks/{gecko_net}/pools/{pool_address}/ohlcv/minute?aggregate=1&limit=100"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    raw = data.get('data', {}).get('attributes', {}).get('ohlcv_list', [])
                    candles = [{'t': r[0], 'o': r[1], 'h': r[2], 'l': r[3], 'c': r[4], 'v': r[5]} for r in reversed(raw)]
                    return candles
        except Exception as e:
            logging.error(f"Gecko Error: {e}")
        return []

# ==========================================
# 6. ENJIN STRATEGI & SIGNAL
# ==========================================
class StrategyEngine:
    @staticmethod
    def check_vol_mc_breakout(volume_24h, market_cap):
        if market_cap == 0: return False
        return (volume_24h / market_cap) > 0.40  

# ==========================================
# 7. ENJIN EKSEKUSI (Web3 & MEV Protect)
# ==========================================
class ExecutionEngine:
    def build_swap_tx(self, chain, token_in, token_out, amount, slippage):
        if chain == 'solana':
            logging.info("Building Solana Swap via Jito Block Engine (Anti-MEV)")
            # Guna library 'solders' atau 'solana-py' untuk bina TX dan hantar ke MEV_RPC_SOL
        elif chain in ['base', 'bsc']:
            logging.info(f"Building {chain.upper()} Swap via Private RPC (Anti-MEV)")
            # Guna 'web3.py' dan hantar ke MEV_RPC_BSC / Base Private RPC
        return "0x_simulated_tx_hash"

# ==========================================
# 8. TELEGRAM BOT NOTIFIER
# ==========================================
class TelegramNotifier:
    def __init__(self, session):
        self.session = session
        self.url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    async def send_signal(self, message):
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        try:
            async with self.session.post(self.url, json=payload) as resp:
                if resp.status != 200: logging.error("Telegram send failed")
        except Exception as e:
            logging.error(f"Telegram Error: {e}")

# ==========================================
# 9. MAIN ORCHESTRATOR (THE BRAIN)
# ==========================================
async def main():
    logging.info("🚀 Institutional Quant Bot Starting (NO ETH MODE)...")
    
    async with aiohttp.ClientSession() as session:
        fetcher = DataFetcher(session)
        security = SecurityGuard(session)
        tg = TelegramNotifier(session)
        
        last_dex_scan = 0
        last_gecko_scan = 0
        
        while True:
            current_time = time.time()
            
            # TASK 1: DexScreener Scanner (Every 3 Seconds)
            if current_time - last_dex_scan >= DEXSCREENER_LIMIT:
                last_dex_scan = current_time
                
                # Imbas DexScreener
                profiles = await fetcher.fetch_dexscreener_trending()
                for item in profiles:
                    chain = item.get('chainId', '').lower()
                    # 🚨 FILTER UTAMA: BUANG ETH & CHAIN SAMPAH
                    if chain not in ALLOWED_CHAINS:
                        continue
                        
                    # Tapisan Volum & Market Cap (Strategi B)
                    # Nota: API profile mungkin tak ada MC, anda boleh panggil search endpoint jika perlu
                    pool_addr = item.get('poolAddress') or item.get('address')
                    symbol = item.get('tokenSymbol') or item.get('symbol', 'UNKNOWN')
                    
                    if pool_addr:
                        db.add_to_watchlist(pool_addr, chain, symbol)
                        
                await asyncio.sleep(0.1) 

            # TASK 2: GeckoTerminal Watchlist Polling (Every 1.5 Seconds)
            if current_time - last_gecko_scan >= GECKO_LIMIT:
                last_gecko_scan = current_time
                watchlist = db.get_watchlist()
                
                # Poll secara bergilir (Batch 5 token) untuk elak RAM spike & API Ban
                for pool, chain, symbol in watchlist[:5]: 
                    candles = await fetcher.fetch_gecko_ohlcv(chain, pool)
                    if not candles: continue
                    
                    vwap = QuantMath.calculate_vwap(candles)
                    atr = QuantMath.calculate_atr(candles)
                    cvd = QuantMath.calculate_cvd(candles)
                    is_spring = QuantMath.detect_wyckoff_spring(candles)
                    
                    current_price = candles[-1]['c']
                    
                    # LOGIC ENTRY (Anti-FOMO & Anti-Fakeout)
                    if is_spring and current_price <= vwap * 1.02:
                        is_safe, reason = await security.audit_token(chain, pool) # Guna pool/token address
                        
                        if is_safe:
                            sl_price = current_price - (2.5 * atr)
                            tp1_price = current_price + (2.0 * atr)
                            
                            signal_msg = (
                                f"🚨 <b>QUANT SIGNAL: {symbol}</b> ({chain.upper()})\n"
                                f"📊 <b>Setup:</b> Wyckoff Spring + VWAP Bounce\n"
                                f"💰 <b>Price:</b> ${current_price:.8f}\n"
                                f"🎯 <b>VWAP:</b> ${vwap:.8f}\n"
                                f"🛑 <b>SL (2.5 ATR):</b> ${sl_price:.8f}\n"
                                f"✅ <b>TP1 (2 ATR):</b> ${tp1_price:.8f}\n"
                                f"🔒 <b>Security:</b> Passed GoPlus Audit\n"
                                f"⚠️ <i>Execute via Jito/Private RPC (No MEV)</i>"
                            )
                            await tg.send_signal(signal_msg)
                            
                            # Buang dari watchlist selepas signal supaya tak spam
                            db.conn.execute("DELETE FROM watchlist WHERE pool_address=?", (pool,))
                            db.conn.commit()
                    
                    await asyncio.sleep(0.2) 

            await asyncio.sleep(0.5) 

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")