"""Stdlib validator for orders.csv (run by the cloud routine before committing). Exit 1 if any row is invalid."""
import csv, sys
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
ETF_OK = set()
src = (ROOT / "mtm.py").read_text()
exec(src[src.index("ETF_OK = {"):src.index("}", src.index("ETF_OK = {")) + 1])  # reuse the exact ETF list
univ = {r["Symbol"].replace(".", "-") for r in csv.DictReader(open(ROOT / "sp500_current.csv"))} | ETF_OK
bad = 0
today_ny = datetime.now(ZoneInfo("America/New_York")).date()
held = set(json.loads((ROOT / "state.json").read_text()).get("pos", {})) if (ROOT / "state.json").exists() else set()
sess = (ROOT / "screen_date.txt").read_text().strip() if (ROOT / "screen_date.txt").exists() else "9999-12-31"
closes = {r["t"]: float(r["close"]) for r in csv.DictReader(open(ROOT / "screen_latest.csv"))} if (ROOT / "screen_latest.csv").exists() else {}
lines = open(ROOT / "orders.csv").read().splitlines()
hdr = "decided,ticker,action,pct,limit,sl,tp,reason"
if lines[0] != hdr: print("header changed:", lines[0]); sys.exit(1)
warn = 0
for i, ln in enumerate(lines[1:], start=2):
    if not ln.strip(): continue
    f = next(csv.reader([ln]))
    is_new = len(f) < 1 or f[0] >= sess
    def err(m):
        global bad, warn
        if is_new: bad += 1; print(f"line {i}: ERROR {m}: {ln}")
        else: warn += 1; print(f"line {i}: warning (old row, ledger already handled it) {m}: {ln}")
    if len(f) != 8: err(f"{len(f)} fields, need 8 (commas in reason?)"); continue
    dec, t, a, pct, lim, sl, tp, why = f
    try:
        if date.fromisoformat(dec) > today_ny: err(f"decided is after today's New York date {today_ny}")
    except ValueError: err("decided must be YYYY-MM-DD")
    if a not in ("BUY", "SHORT", "CLOSE"): err("action must be BUY, SHORT or CLOSE")
    if a == "CLOSE":
        if t not in univ and t not in held: err(f"CLOSE {t}: not in universe and not held")
        continue
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
    px = closes.get(t)
    if dec >= sess and px and sl and tp:   # new rows only: levels must bracket the latest close
        try:
            s_, t_ = float(sl), float(tp)
            if (a == "BUY" and not s_ < px < t_) or (a == "SHORT" and not t_ < px < s_):
                err(f"sl/tp must bracket the latest close {px:.2f}")
        except ValueError: pass
print("orders.csv OK" if not bad else f"{bad} problem(s) in new rows", f"({warn} old-row warnings)" if warn else ""); sys.exit(1 if bad else 0)
