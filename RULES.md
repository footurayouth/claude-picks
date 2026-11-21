# Claude picks vs Edge bot — forward paper test (fixed 2026-11-21, do not change)

Purpose: does Claude's discretionary judgment on stocks/ETFs beat the Edge Stack paper bot (footurayouth/edge-bot)?
Paper only. No real orders are ever placed anywhere.

## Start
- Start: close of 2026-11-20 with $108,086.79, the bot's equity that day (logs/performance.csv). Both curves are compared from here.

## What Claude may do
- Universe: US-listed stocks and unlevered ETFs. No leveraged or inverse ETFs, no options, no crypto.
- Long or short. Max gross exposure 2.0x equity (same as the bot's max_gross). Max 25% of equity in any one stock (ETFs max 50%).
- Decide only after the US close, using information available then. Write orders to orders.csv the same evening.

## How fills work (mechanical, done by mtm.py)
- Every order fills at the next session's OPEN (superseded by Amendments 1-2: raw prices, limit orders allowed).
- Size = pct of equity at the previous close / open price, rounded down to whole shares.
- Stop-loss (sl) and take-profit (tp) are checked on daily high/low from the fill day on.
  - If both are touched the same day, the stop counts first.
  - If the open gaps past a level, the fill is at the open.
- CLOSE orders exit at the next open. A new BUY/SHORT on a held ticker is ignored; close first.
- Cost: superseded by Amendment 1.
- Orders are never edited after they are written. Mistakes are fixed with a new order the next evening.

## Judging
- Compare total return from 2026-11-20 against the bot, and against SPY. Report max drawdown.
- Minimum fair look: 6 months. Before that, the gap is mostly luck.

## Amendment 1 (2026-11-21, before any fill, at the user's request: make it realistic). Replaces the cost line above.
Account model: IBKR-like margin account, USD. The bot is also on IBKR.
- **Hours:** decisions are made after the 16:00 ET close. Orders go in the next session only (market-on-open or a day limit). No pre/after-market fills. Holidays are skipped automatically.
- **Order types:**
  - Market at open.
  - Day LIMIT entry (`limit` column): fills only if price trades through the limit, at the limit, or at the open if it gaps past. Otherwise it expires.
  - GTC stop-market (sl): fills at the stop, or at the open if it gaps through, plus stop slippage.
  - GTC take-profit LIMIT (tp): needs trade-through, fills at the limit with no slippage, or at the open if it gaps past.
  - If both sl and tp are touched the same day, the stop counts. If a limit entry and its stop are both touched the same day, the stop is hit after the fill.
- **Slippage per side:**

  | Instrument | Base | Extra at open | Extra on stops |
  |---|---|---|---|
  | Liquid ETFs | 2 bp | +3 bp | +3 bp |
  | Stocks | 5 bp | +8 bp | +10 bp |
  | Stocks under $10 | 20 bp | +8 bp | +10 bp |

  Limit fills have no slippage.
- **Commission:** IBKR fixed, $0.005/share, min $1, max 1% of trade value.
- **Financing:**
  - Free cash (cash minus short proceeds) earns T-bill (^IRX) - 0.5%.
  - Negative cash pays T-bill + 1.5%.
  - Shorts pay a 0.5%/yr borrow fee (general collateral; hard-to-borrow names may cost more, which is not modelled).
- **Caps at fill:**
  - Gross exposure is capped at 2.0x the previous close equity, so orders get scaled down.
  - Each order is capped at 1% of that day's dollar volume.
- **Prices and dividends:** raw (unadjusted) prices, as really traded. Cash dividends are paid to longs and charged to shorts on the ex-date. Splits rescale shares, entry, sl and tp.
- **Not modelled:** partial fills, halts, intraday path order beyond the rules above, FX for an EUR investor (the bot has it too).
Fill logic is tested in tests/test_fills.py (all pass).

## Amendment 2 (2026-11-21, before any fill): fixes from the independent review
- **Universe** (enforced in code): current S&P 500 members plus the unlevered ETF list in mtm.py (`ETF_OK`).
  - Shorts are limited to the same list, which is easy to borrow.
  - Per-order caps: 25% of equity for a stock, 50% for an ETF.
  - Invalid rows (bad action, pct, ticker or format) are rejected with a log line. They never crash the ledger.
- **`decided`** = the America/New_York date when the order is written. An order fills only at a session after that date.
- **Commit-time guard:** an order whose line was committed (git) after 09:30 ET on its fill day waits for the next session.
- **Data:** Yahoo's back-adjusted splits are undone, so every bar is the true raw price.
  - An unexplained >35% overnight move in a held name halts the run.
  - Today's bar is never used before 16:20 ET.
  - If a held or ordered ticker has no bar in the latest 2 sessions, the run stops and retries the next day.
  - Orders wait up to 5 sessions for a missing bar.
- **Fill rules:**
  - The volume cap uses the previous session's dollar volume.
  - A limit filled at the open is never worse than the limit.
  - No same-day take-profit on the day of a limit entry.
  - A split on the fill day rescales that order's limit, sl and tp.
- **Costs added:**
  - SEC fee (0.278 bp) and FINRA TAF on sells.
  - 15% US withholding on dividends received.
  - Fund capital-gain distributions credited.
  - No interest on the first $10k of free cash. ^IRX is used as a proxy for IBKR's benchmark rate.
- **Not modelled:** forced liquidation. If gross exceeds 2.5x a warning is logged, and the decision step must cut exposure.
- **Housekeeping:** the ledger writes its outputs only after the whole day simulates without error. Tests: tests/test_fills.py (25 checks).
