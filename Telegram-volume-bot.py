#!/usr/bin/env python3
"""
Telegram bot: CoinEx spot+futures USD-volume screener
"""

from __future__ import annotations
import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

from tabulate import tabulate  # type: ignore
import ccxt  # type: ignore
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

EXCHANGE_ID = os.getenv("EXCHANGE_ID", "coinex").lower()
DEFAULT_SPOT_MIN = float(os.getenv("SPOT_MIN_USD", 1_000_000))
DEFAULT_FUT_MIN = float(os.getenv("FUTURES_MIN_USD", 5_000_000))
TOKEN = os.environ.get("TELEGRAM_TOKEN")

STABLES = {"USD", "USDT", "USDC", "TUSD", "FDUSD", "USDD", "USDE", "DAI", "PYUSD"}

@dataclass
class MarketVol:
    symbol: str
    base: str
    quote: str
    last: float
    base_vol: float
    quote_vol: float

def parse_symbol(sym: str) -> Tuple[str, str]:
    pair = sym.split(":")[0]
    base, quote = pair.split("/")
    return base, quote

def to_marketvol(t: dict) -> MarketVol:
    sym = t.get("symbol")
    base, quote = parse_symbol(sym)
    last = float(t.get("last") or t.get("close") or 0.0)
    base_vol = float(t.get("baseVolume") or 0.0)
    quote_vol = float(t.get("quoteVolume") or 0.0)
    return MarketVol(symbol=sym, base=base, quote=quote, last=last, base_vol=base_vol, quote_vol=quote_vol)

def fmt_money(x: float) -> str:
    return f"{x:,.0f}"

def screen_coinex(spot_min_usd: float, fut_min_usd: float):
    spot = ccxt.__dict__[EXCHANGE_ID]({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    spot.load_markets()
    spot_tickers = spot.fetch_tickers()

    best_spot: Dict[str, MarketVol] = {}
    for sym, t in spot_tickers.items():
        mv = to_marketvol(t)
        if mv.quote not in STABLES:
            continue
        if mv.quote_vol >= spot_min_usd:
            prev = best_spot.get(mv.base)
            if prev is None or mv.quote_vol > prev.quote_vol:
                best_spot[mv.base] = mv

    swap = ccxt.__dict__[EXCHANGE_ID]({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    swap.load_markets()
    swap_tickers = swap.fetch_tickers()

    best_fut: Dict[str, MarketVol] = {}
    for sym, t in swap_tickers.items():
        mv = to_marketvol(t)
        if mv.quote not in STABLES:
            continue
        if mv.quote_vol >= fut_min_usd:
            prev = best_fut.get(mv.base)
            if prev is None or mv.quote_vol > prev.quote_vol:
                best_fut[mv.base] = mv

    bases = sorted(set(best_spot.keys()) & set(best_fut.keys()))

    rows = []
    for base in bases:
        s = best_spot[base]
        f = best_fut[base]
        rows.append([
            base,
            f.symbol, fmt_money(f.quote_vol),
            s.symbol, fmt_money(s.quote_vol),
            f.last or s.last,
        ])

    rows.sort(key=lambda r: float(r[2].replace(",", "")), reverse=True)
    return rows

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "👋 Hi! Send /screen to get coins where 24h **futures ≥ $5M** and **spot ≥ $1M** on CoinEx.\\n\\n"
        "Customize thresholds: e.g. `/screen spot=1500000 fut=8000000`."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

def parse_thresholds(args: List[str]) -> Tuple[float, float]:
    spot = DEFAULT_SPOT_MIN
    fut = DEFAULT_FUT_MIN
    text = " ".join(args)
    m1 = re.search(r"spot=(\\d+(?:\\.\\d+)?)", text)
    m2 = re.search(r"fut=(\\d+(?:\\.\\d+)?)", text)
    if m1: spot = float(m1.group(1))
    if m2: fut = float(m2.group(1))
    return spot, fut

async def screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        spot_min, fut_min = parse_thresholds(context.args)
        t0 = time.time()
        rows = await asyncio.to_thread(screen_coinex, spot_min, fut_min)
        dt = time.time() - t0

        if not rows:
            await update.message.reply_text(
                f"No matches right now with spot≥${spot_min:,.0f} and futures≥${fut_min:,.0f}."
            )
            return

        table = tabulate(
            rows,
            headers=["BASE", "FUTURES SYMBOL", "FUT 24h USD VOL", "SPOT SYMBOL", "SPOT 24h USD VOL", "LAST PRICE"],
            tablefmt="github",
        )
        txt = f"```\n{table}\n```\n⏱️ {dt:.1f}s • Source: CoinEx via CCXT"
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logging.exception("screen error")
        await update.message.reply_text(f"Error: {e}")

async def main() -> None:
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_TOKEN env var (from @BotFather)")
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("screen", screen))
    await application.initialize()
    await application.start()
    try:
        await application.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
