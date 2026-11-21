"""Fill-logic checks on synthetic bars. Exit code 1 on any failure."""
import sys, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from mtm import simulate, slip, fees, to_true_raw, validate

D = pd.to_datetime(["2026-11-23", "2026-11-24", "2026-11-25"])
COLS = ["Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits", "Capital Gains"]
UNIV = {"AAPL", "A", "B", "C", "L", "S", "X", "TINY", "SPY"}
V = 1e8
def run(bars, orders, rf=0.0, days=D, st=None, prev_dv=None, can_fill=None):
    st = st or {"cash": 100000.0, "pos": {}, "last": "2026-11-20", "done_orders": 0, "eq": 100000.0}
    T, E, L = [], [], []
    o = pd.DataFrame(orders, columns=["decided", "ticker", "action", "pct", "limit", "sl", "tp", "reason"]).astype(object)
    def bar(t, d):
        if t not in bars or d not in D: return None
        row = bars[t][list(D).index(d)]
        return None if row is None else pd.Series(dict(zip(COLS, list(row) + [0] * (8 - len(row)))))
    simulate(st, o, bar, list(days), lambda d: rf, T.append, E.append, UNIV, prev_dv, can_fill, L.append)
    return st, T, E, L
res = []
def ok(c, m): print(("PASS " if c else "FAIL ") + m); res.append(bool(c))
B = lambda o, h, l, c, v=V: (o, h, l, c, v)
od = lambda t, a, pct, lim=None, sl=None, tp=None, dec="2026-11-21": (dec, t, a, pct, lim, sl, tp, "")

st, T, E, L = run({"AAPL": [B(100, 101, 99, 100)] * 3}, [od("AAPL", "BUY", 0.25)])
px = 100 * (1 + slip("AAPL", 100, "open")); p = st["pos"]["AAPL"]
ok(abs(p["entry"] - px) < 1e-9 and p["sh"] == 250, f"market open buy {px:.4f} x 250")
ok(abs(st["cash"] - (100000 - 250 * px - fees(250, px))) < 1e-6, "cash after buy incl. commission")
ok(fees(-250, 100) > fees(250, 100), "sells pay SEC fee + TAF")
st, T, E, L = run({"AAPL": [B(100, 101, 99, 100), B(90, 92, 89, 91), B(91, 92, 90, 91)]}, [od("AAPL", "BUY", 0.25, None, 95, 120)])
f = float(T[1].split(",")[4]); ok(abs(f - 90 * (1 - slip("AAPL", 90, "stop"))) < 1e-6, f"gap-through stop fills at open minus slip {f:.4f}")
st, T, E, L = run({"AAPL": [B(100, 110, 99, 105), B(105, 109, 104, 106), B(106, 110.01, 105, 107)]}, [od("AAPL", "BUY", 0.25, None, 90, 110)])
ok(len(T) == 2 and "2026-11-25" in T[1] and abs(float(T[1].split(",")[4]) - 110) < 1e-9, "target needs trade-through, fills at limit")
st, T, E, L = run({"AAPL": [B(100, 111, 89, 100)] * 3}, [od("AAPL", "BUY", 0.25, None, 90, 110)])
ok("stop" in T[1], "stop and target same day -> stop")
st, T, E, L = run({"AAPL": [B(100, 101, 98.5, 100)] * 3}, [od("AAPL", "BUY", 0.25, 98)])
ok(not st["pos"], "limit not traded through -> expires")
st, T, E, L = run({"AAPL": [B(100, 101, 97, 100)] * 3}, [od("AAPL", "BUY", 0.25, 98)])
ok(st["pos"]["AAPL"]["entry"] == 98, "limit traded through -> fills at limit")
st, T, E, L = run({"AAPL": [B(97.95, 101, 97, 100)] * 3}, [od("AAPL", "BUY", 0.25, 98)])
ok(st["pos"]["AAPL"]["entry"] <= 98, f"limit fill at open never worse than limit ({st['pos']['AAPL']['entry']:.4f})")
st, T, E, L = run({"AAPL": [B(100, 110, 95, 105)] * 3}, [od("AAPL", "BUY", 0.25, 96, 80, 108)])
ok(len([x for x in T if "target" in x and "2026-11-23" in x]) == 0, "no same-day target after a limit entry")
tk = ("SPY", "A", "B", "C", "L", "S", "X", "AAPL")
st, T, E, L = run({s: [B(100, 101, 99, 100)] * 3 for s in tk},
                  [od("SPY", "BUY", 0.5)] + [od(s, "SHORT" if s in "AS" else "BUY", 0.25) for s in tk[1:]])
g = sum(abs(p["sh"]) * 100 for p in st["pos"].values()); ok(195000 <= g <= 200000 + 1, f"gross capped at 2x ({g:,.0f} of 225,000 asked)")
st, T, E, L = run({"TINY": [B(50, 51, 49, 50, 10000)] * 3}, [od("TINY", "BUY", 0.25)], prev_dv=lambda t, d: 500000)
ok(st["pos"]["TINY"]["sh"] * 50 <= 5000 + 50, "size <= 1% of previous session dollar volume")
st, T, E, L = run({"A": [B(100, 100, 100, 100)] * 3}, [od("A", "SHORT", 0.25)], rf=0.04)
ok(st["cash"] > 100000 + 25000 * 0.99, f"short proceeds credited, carry small (cash {st['cash']:.2f})")
# validation
for bad, why in ((od("AAPL", "SELL", 0.1), "action SELL"), (od("AAPL", "BUY", 15), "pct 15"), (od("AAPL", "BUY", ""), "blank pct"),
                 (od("ZZZZ", "BUY", 0.1), "outside universe"), (od("SPY", "BUY", 0.6), "ETF pct > 0.5")):
    st, T, E, L = run({"AAPL": [B(100, 101, 99, 100)] * 3, "SPY": [B(100, 101, 99, 100)] * 3}, [bad])
    ok(not st["pos"] and any("rejected" in x for x in L), f"rejects {why}")
# carry-forward on missing bar; CLOSE not consumed
st, T, E, L = run({"AAPL": [None, B(100, 101, 99, 100), B(100, 101, 99, 100)]}, [od("AAPL", "BUY", 0.25)])
ok("AAPL" in st["pos"] and "2026-11-24" in T[0], "order waits for a missing bar, fills next day")
# commit-time guard
st, T, E, L = run({"AAPL": [B(100, 101, 99, 100)] * 3}, [od("AAPL", "BUY", 0.25)], can_fill=lambda r, d: d >= pd.Timestamp("2026-11-25"))
ok("2026-11-25" in T[0], "order committed after an open waits for the next open")
# dividends (15% withholding on longs, shorts pay full), capital gains, splits
bars = {"L": [B(100, 100, 100, 100), (99, 99, 99, 99, V, 1.0, 0, 0.5), B(99, 99, 99, 99)],
        "S": [B(100, 100, 100, 100), (99, 99, 99, 99, V, 1.0, 0, 0), B(99, 99, 99, 99)]}
st, T, E, L = run(bars, [od("L", "BUY", 0.25), od("S", "SHORT", 0.25)])
dl = [float(x.split(",")[-1]) for x in T if ",L,DIV" in x]; ds = [float(x.split(",")[-1]) for x in T if ",S,DIV" in x]
cg = [float(x.split(",")[-1]) for x in T if ",L,CAPGAIN" in x]
ok(dl == [212.5] and ds == [-250.0] and cg == [125.0], f"dividend long net 15% WHT {dl}, short pays {ds}, cap gain {cg}")
st, T, E, L = run({"X": [B(100, 100, 100, 100), (50, 51, 49, 50, V, 0, 2.0), B(50, 50, 50, 50)]}, [od("X", "BUY", 0.25, None, 45, 120)])
p = st["pos"]["X"]; ok(p["sh"] == 500 and p["sl"] == 22.5 and p["tp"] == 60 and abs(float(E[1].split(",")[1]) - float(E[0].split(",")[1])) < 1, "split: shares x2, sl/tp halved, equity unchanged")
st, T, E, L = run({"X": [(50, 51, 49, 50, V, 0, 2.0), B(50, 50, 50, 50), B(50, 50, 50, 50)]}, [od("X", "BUY", 0.25, None, 90, 240)])
p = st["pos"]["X"]; ok(p["sl"] == 45 and p["tp"] == 120, "split on fill day rescales order sl/tp")
try:
    run({"X": [B(100, 100, 100, 100), B(50, 51, 49, 50), B(50, 50, 50, 50)]}, [od("X", "BUY", 0.25)]); ok(False, "unexplained 50% gap halts")
except RuntimeError: ok(True, "unexplained 50% gap halts the run")
# Yahoo back-adjusted -> true raw
y = pd.DataFrame({"Open": [91.85, 92.0, 92.5], "High": [92, 93, 93], "Low": [91, 91, 92], "Close": [91.8, 92.3, 92.6],
                  "Volume": [1e6, 1.5e7, 1.5e7], "Dividends": [0, 0, 0], "Stock Splits": [0, 15.0, 0]}, index=D)
r = to_true_raw(y); ok(abs(r["Close"].iloc[0] - 91.8 * 15) < 1e-6 and r["Close"].iloc[1] == 92.3 and r["Volume"].iloc[0] == 1e6 / 15, "to_true_raw undoes split back-adjustment")
n = sum(res); print(f"{n}/{len(res)} passed"); print("ALL PASS" if all(res) else "SOME FAILED"); sys.exit(0 if all(res) else 1)
