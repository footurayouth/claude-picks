"""Claude picks vs Edge bot vs SPY since the common start. Stdlib only.
Usage: python3 compare.py <path to edge-bot logs/performance.csv>   (writes COMPARE.md next to this file)"""
import csv, sys
from pathlib import Path
ROOT = Path(__file__).parent
START, START_EQ = "2026-11-20", 108086.79
eq = {r["date"]: float(r["equity"]) for r in csv.DictReader(open(ROOT / "equity.csv"))}
bot, spy = {}, {}
for r in csv.DictReader(open(sys.argv[1])):   # columns by name: equity_usd, spy_close (SPY price, not a %)
    if r.get("date", "")[:4].isdigit() and r["date"] >= START:
        bot[r["date"]] = float(r["equity_usd"]); spy[r["date"]] = float(r["spy_close"])
def mdd(series):
    peak, m = None, 0.0
    for k in sorted(series):
        v = series[k]; peak = v if peak is None else max(peak, v); m = min(m, v / peak - 1)
    return m * 100
pct = lambda v, b: f"{(v / b - 1) * 100:+.2f}%"
s0 = spy.get(START)
dates = sorted(set(eq) | set(bot))
rows = ["| date | Claude $ | Claude | Edge bot $ | Edge bot | SPY |", "|---|---|---|---|---|---|"]
for d in dates:
    c, b, s = eq.get(d), bot.get(d), spy.get(d)
    rows.append(f"| {d} | {'' if c is None else f'{c:,.0f}'} | {'' if c is None else pct(c, START_EQ)} | "
                f"{'' if b is None else f'{b:,.0f}'} | {'' if b is None else pct(b, START_EQ)} | {'' if s is None or not s0 else pct(s, s0)} |")
lc, lb = max(eq), (max(bot) if bot else None)
summary = (f"Claude {pct(eq[lc], START_EQ)} (max DD {mdd(eq):.1f}%) as of {lc}"
           + (f" | Edge bot {pct(bot[lb], START_EQ)} (max DD {mdd(bot):.1f}%) as of {lb}" if lb else "")
           + (f" | SPY {pct(spy[lb], s0)}" if lb and s0 else ""))
(ROOT / "COMPARE.md").write_text(f"# Claude picks vs Edge bot (start {START}, ${START_EQ:,.2f} each)\n\n{summary}\n\n" + "\n".join(rows) + "\n")
print(summary)
