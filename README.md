# claude-picks: forward paper test

Claude's own discretionary stock/ETF picks against the Edge Stack paper bot (footurayouth/edge-bot). Both start from $108,086.79 at the 2026-11-20 close. Paper only: no real orders are ever placed.

- `RULES.md`: fixed rules, plus the realistic fill and cost model (Amendment 1).
- `.github/workflows/ledger.yml`: runs on weekdays at 21:30 UTC on GitHub's servers.
  1. Runs the fill tests.
  2. Runs `mtm.py`, which fills orders, applies stops and targets, and marks positions to the close.
  3. Runs `screen.py`, the S&P 500 screen.
  4. Commits the results.
- A Claude cloud routine runs on weekdays at 23:30 UTC on Anthropic's servers.
  1. Reads the screen, positions and news.
  2. Appends the next orders to `orders.csv` and its reasoning to `JOURNAL.md`.
  3. Updates `COMPARE.md` against the bot.
- `tests/test_fills.py`: 25 fill-logic checks. `check_orders.py` validates orders.csv. `ROUTINE_PROMPT.md` is the exact cloud-routine prompt.
