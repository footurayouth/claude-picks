"""Stdlib validator for orders.csv (run by the cloud routine before committing). Exit 1 if any row is invalid."""
import csv, sys
from datetime import date
from pathlib import Path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
ETF_OK = set()
src = (ROOT / "mtm.py").read_text()
exec(src[src.index("ETF_OK = {"):src.index("}", src.index("ETF_OK = {")) + 1])  # reuse the exact ETF list
univ = {r["Symbol"].replace(".", "-") for r in csv.DictReader(open(ROOT / "sp500_current.csv"))} | ETF_OK
bad = 0
lines = open(ROOT / "orders.csv").read().splitlines()
hdr = "decided,ticker,action,pct,limit,sl,tp,reason"
if lines[0] != hdr: print("header changed:", lines[0]); sys.exit(1)
for i, ln in enumerate(lines[1:], start=2):
    f = next(csv.reader([ln]))
    def err(m):
        global bad; bad += 1; print(f"line {i}: {m}: {ln}")
    if len(f) != 8: err(f"{len(f)} fields, need 8 (commas in reason?)"); continue
    dec, t, a, pct, lim, sl, tp, why = f
    try: date.fromisoformat(dec)
    except ValueError: err("decided must be YYYY-MM-DD")
    if a not in ("BUY", "SHORT", "CLOSE"): err("action must be BUY, SHORT or CLOSE")
    if a == "CLOSE": continue
    if t not in univ: err(f"{t} not in universe (S&P 500 + ETF list in mtm.py)")
    cap = 0.5 if t in ETF_OK else 0.25
    try:
        if not (0 < float(pct) <= cap): err(f"pct must be in (0, {cap}]")
    except ValueError: err("pct must be a number like 0.10")
    for k, v in (("limit", lim), ("sl", sl), ("tp", tp)):
        if v:
            try:
                if float(v) <= 0: err(f"{k} must be > 0")
            except ValueError: err(f"{k} must be a number or blank")
    try:
        s_, t_ = float(sl) if sl else None, float(tp) if tp else None
        if s_ and t_ and ((a == "BUY" and s_ >= t_) or (a == "SHORT" and s_ <= t_)): err("sl/tp on the wrong side")
    except ValueError: pass
print("orders.csv OK" if not bad else f"{bad} problem(s)"); sys.exit(1 if bad else 0)
