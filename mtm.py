"""Mark-to-market the Claude-picks paper account. Run after each US close. See RULES.md (Amendments 1-2)."""
import json, math, subprocess, sys, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd

ROOT = Path(__file__).parent
NY = ZoneInfo("America/New_York")
START, START_EQ = "2026-11-20", 108086.79
ETF_OK = {"SPY", "QQQ", "IWM", "DIA", "MDY", "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB",
          "XLRE", "XLC", "GLD", "SLV", "TLT", "IEF", "SHY", "HYG", "LQD", "EEM", "EFA", "VTI", "VOO", "IVV", "RSP",
          "IBB", "XBI", "KRE", "XHB", "XRT", "GDX", "USO", "VNQ", "EWJ", "EWZ", "FXI", "IGV", "SOXX", "ITA"}
GROSS_MAX, PART_MAX = 2.0, 0.01           # max gross exposure; max 1% of previous session's dollar volume per order
CAP_STOCK, CAP_ETF = 0.25, 0.50           # max fraction of equity per order
BORROW, CASH_SPREAD, DEBIT_SPREAD, CASH_FREE_BAND = 0.005, 0.005, 0.015, 10000.0
WHT = 0.15                                # US dividend withholding (EU resident with W-8BEN)
SEC_FEE, TAF, TAF_MAX = 0.0000278, 0.000166, 8.30
SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 15, 20)  # a gap within 3% of these with no split event = suspected unrecorded split

def looks_like_split(prev_close, open_):
    ratio = max(open_ / prev_close, prev_close / open_)
    return any(abs(ratio / k - 1) < 0.03 for k in SPLIT_RATIOS)
STALE_MAX = 5                             # sessions an order may wait for a missing bar before it is dropped

def universe():
    sp = pd.read_csv(ROOT / "sp500_current.csv")["Symbol"].str.replace(".", "-", regex=False)
    return set(sp) | ETF_OK

def slip(t, price, kind):
    """One-side slippage fraction. kind: 'open' (market at open), 'stop' (stop-market), 'limit' (none)."""
    if kind == "limit": return 0.0
    etf = t in ETF_OK
    base = 0.0002 if etf else (0.0020 if price < 10 else 0.0005)
    return base + {"open": 0.0003 if etf else 0.0008, "stop": 0.0003 if etf else 0.0010}[kind]

def fees(sh, price):
    """IBKR fixed commission ($0.005/sh, min $1, max 1%) + SEC fee and FINRA TAF on sells."""
    c = min(max(abs(sh) * 0.005, 1.0), 0.01 * abs(sh) * price)
    if sh < 0: c += abs(sh) * price * SEC_FEE + min(abs(sh) * TAF, TAF_MAX)
    return c

def num(x):
    try:
        v = float(x); return None if math.isnan(v) else v
    except (TypeError, ValueError): return None

def validate(o, univ):
    """Return (clean order dict, None) or (None, reason)."""
    a = str(o.get("action", "")).strip()
    t = str(o.get("ticker", "")).strip().upper().replace(".", "-")
    if a not in ("BUY", "SHORT", "CLOSE"): return None, f"bad action {a!r}"
    if not t or t == "NAN": return None, "no ticker"
    d = dict(decided=str(o.get("decided", "")).strip(), ticker=t, action=a, row=int(o["line"]))
    try: pd.Timestamp(d["decided"])
    except Exception: return None, f"bad decided {d['decided']!r}"
    if a == "CLOSE": return d, None
    if t not in univ: return None, f"{t} not in allowed universe (S&P 500 + listed ETFs)"
    pct, cap = num(o.get("pct")), (CAP_ETF if t in ETF_OK else CAP_STOCK)
    if pct is None or not (0 < pct <= cap): return None, f"pct {o.get('pct')!r} not in (0, {cap}]"
    d.update(pct=pct, limit=num(o.get("limit")), sl=num(o.get("sl")), tp=num(o.get("tp")))
    for k in ("limit", "sl", "tp"):
        if d[k] is not None and d[k] <= 0: return None, f"{k} must be > 0"
    return d, None

def simulate(st, orders, bar, days, rf_of, log_trade, log_eq, univ, prev_dv=None, can_fill=None, log=print):
    """Advance the account over `days` (each processed once, in order).
    bar(t, d) -> Series(Open,High,Low,Close,Volume[,Dividends,Stock Splits,Capital Gains]) in TRUE RAW prices, or None.
    orders: list of dicts (csv rows) each with "line" = its line number in orders.csv (the order id).
    prev_dv(t, d) -> previous session's dollar volume (None = unknown). can_fill(line, d) -> False if order not yet eligible."""
    prev_dv = prev_dv or (lambda t, d: None); can_fill = can_fill or (lambda row, d: True)
    st.setdefault("pending", []); st.setdefault("seen", [])
    seen = set(st["seen"])

    def trade(d, t, sh, ref, kind, why, cap_price=None):
        s = slip(t, ref, kind); price = ref * (1 + s) if sh > 0 else ref * (1 - s)
        if cap_price is not None: price = min(price, cap_price) if sh > 0 else max(price, cap_price)
        fee = fees(sh, price)
        st["cash"] -= sh * price + fee
        p = st["pos"].get(t); pnl = ""
        if p and sh == -p["sh"]: pnl = round(-sh * (price - p["entry"]) - fee - p["fee_in"], 2)
        log_trade(f"{d.date()},{t},{'BUY' if sh > 0 else 'SELL'},{abs(sh)},{price:.4f},{why},{fee:.2f},{pnl}")
        return price, fee

    for d in days:
        prev_eq = st["eq"]
        # 0) carry since previous session: cash interest (none on first $10k), margin debit, short borrow
        nd = max((d - pd.Timestamp(st["last"])).days, 1); rf = rf_of(d)
        short_val = sum(-p["sh"] * p["last"] for p in st["pos"].values() if p["sh"] < 0)
        free_cash = st["cash"] - short_val
        if free_cash > CASH_FREE_BAND: st["cash"] += (free_cash - CASH_FREE_BAND) * max(rf - CASH_SPREAD, 0) * nd / 360
        elif free_cash < 0: st["cash"] += free_cash * (rf + DEBIT_SPREAD) * nd / 360
        st["cash"] -= short_val * BORROW * nd / 360
        # 0b) corporate actions on held positions (ex-date)
        for t, p in st["pos"].items():
            b = bar(t, d)
            if b is None: continue
            r = float(b.get("Stock Splits", 0) or 0)
            if r > 0 and r != 1:
                p["sh"] = int(round(p["sh"] * r))
                for k in ("entry", "last", "sl", "tp"):
                    if p[k] is not None: p[k] = p[k] / r
                log_trade(f"{d.date()},{t},SPLIT,{r},,split,0,")
            else:   # main() holds back recent split-like gaps for a retry; anything reaching here is treated as a real move
                ratio = max(b["Open"] / p["last"], p["last"] / b["Open"])
                if ratio > 1.2: log(f"{t} gapped {(b['Open'] / p['last'] - 1) * 100:+.0f}% at the {d.date()} open"
                                    + (" (looks like a split but none recorded: CHECK)" if looks_like_split(p["last"], b["Open"]) else " (real move)"))
            for col, w in (("Dividends", WHT), ("Capital Gains", 0.0)):
                dv = float(b.get(col, 0) or 0)
                if dv > 0:
                    amt = p["sh"] * dv * ((1 - w) if p["sh"] > 0 else 1)  # longs net of withholding; shorts pay in full
                    st["cash"] += amt
                    log_trade(f"{d.date()},{t},{'DIV' if col == 'Dividends' else 'CAPGAIN'},{p['sh']},{dv:.4f},{col.lower()},0,{amt:.2f}")
        # 1) queue newly eligible order rows (decided strictly before today), validated
        for o in orders:
            if o["line"] in seen: continue
            try: dec = pd.Timestamp(str(o.get("decided", "")).strip())
            except Exception: dec = pd.Timestamp("1900-01-01")
            if dec >= d: continue
            seen.add(o["line"]); st["seen"] = sorted(seen)
            clean, why = validate(o, univ)
            if clean is None: log(f"order line {o['line']} rejected: {why}"); continue
            clean["age"] = 0; st["pending"].append(clean)
        # 1b) fill pending orders: market at open, or day limit
        keep = []
        for o in st["pending"]:
            t, b = o["ticker"], bar(o["ticker"], d)
            if not can_fill(o["row"], d):
                log(f"order line {o['row']} ({t}) committed after the {d.date()} open: waits a day"); keep.append(o); continue
            if b is None:
                o["age"] += 1
                if o["age"] <= STALE_MAX: log(f"no bar for {t} on {d.date()}, order carried"); keep.append(o)
                else: log(f"order line {o['row']} ({t}) dropped: no data for {STALE_MAX} sessions")
                continue
            r = float(b.get("Stock Splits", 0) or 0)
            f = r if (r > 0 and r != 1) else 1.0                 # split on fill day: rescale order prices
            if o["action"] == "CLOSE":
                if t in st["pos"]: trade(d, t, -st["pos"][t]["sh"], b["Open"], "open", "close"); st["pos"].pop(t)
                else: log(f"CLOSE {t}: not held")
                continue
            if t in st["pos"]: log(f"{t} already held, order line {o['row']} ignored"); continue
            buy = o["action"] == "BUY"; lim = None if o["limit"] is None else o["limit"] / f
            cap_price = None
            if lim is not None:
                if buy and b["Low"] < lim: ref = min(b["Open"], lim)
                elif not buy and b["High"] > lim: ref = max(b["Open"], lim)
                else: log(f"{t} limit {lim:.2f} not reached on {d.date()}, expired"); continue
                kind = "limit" if ref == lim else "open"; cap_price = lim
            else: ref, kind = b["Open"], "open"
            gross_now = sum(abs(p["sh"] * p["last"]) for p in st["pos"].values())
            dv = prev_dv(t, d)
            notional = min(prev_eq * o["pct"], max(GROSS_MAX * prev_eq - gross_now, 0), PART_MAX * dv if dv else float("inf"))
            sh = math.floor(notional / ref) * (1 if buy else -1)
            if sh == 0: log(f"{t} size 0 after caps, skipped"); continue
            price, fee = trade(d, t, sh, ref, kind, "open", cap_price)
            st["pos"][t] = {"sh": sh, "entry": price, "fee_in": fee, "last": price, "limit_day": str(d.date()) if lim is not None else None,
                            "sl": None if o["sl"] is None else o["sl"] / f, "tp": None if o["tp"] is None else o["tp"] / f}
        st["pending"] = keep
        # 2) GTC stop-market and take-profit limit on today's range. Stop first. No same-day target after a limit entry.
        for t in list(st["pos"]):
            p, b = st["pos"][t], bar(t, d)
            if b is None: continue
            long = p["sh"] > 0; hit = None
            if p["sl"] is not None and ((long and b["Low"] <= p["sl"]) or (not long and b["High"] >= p["sl"])):
                gap = (long and b["Open"] < p["sl"]) or (not long and b["Open"] > p["sl"])
                hit = ("stop", b["Open"] if gap else p["sl"], "stop")
            elif p["tp"] is not None and p.get("limit_day") != str(d.date()) and \
                    ((long and b["High"] > p["tp"]) or (not long and b["Low"] < p["tp"])):
                gap = (long and b["Open"] > p["tp"]) or (not long and b["Open"] < p["tp"])
                hit = ("target", b["Open"] if gap else p["tp"], "limit")
            if hit: trade(d, t, -p["sh"], hit[1], hit[2], hit[0]); st["pos"].pop(t)
            else: p["last"] = float(b["Close"])
        e = st["cash"] + sum(p["sh"] * p["last"] for p in st["pos"].values())
        if e <= 0: raise RuntimeError(f"equity {e:.2f} <= 0 on {d.date()}")
        gross = sum(abs(p["sh"] * p["last"]) for p in st["pos"].values()) / e
        if gross > GROSS_MAX * 1.25: log(f"WARNING gross {gross:.2f}x above 2.5x on {d.date()}: reduce exposure")
        st["eq"] = e; st["last"] = str(d.date())
        log_eq(f"{d.date()},{e:.2f},{gross:.2f}")

# ---------------- data layer (Yahoo) ----------------
def to_true_raw(df):
    """Yahoo's auto_adjust=False prices are still split-adjusted backwards. Undo it so each bar is what really traded."""
    df = df.copy()
    s = df.get("Stock Splits", pd.Series(0.0, index=df.index)).fillna(0).replace(0, 1.0)
    f = s[::-1].cumprod()[::-1].shift(-1).fillna(1.0)  # product of split ratios strictly after each date
    for c in ("Open", "High", "Low", "Close", "Adj Close", "Dividends", "Capital Gains"):
        if c in df: df[c] = df[c] * f
    if "Volume" in df: df["Volume"] = df["Volume"] / f
    return df

def commit_times():
    """orders.csv file line number -> commit time (UTC) of the line, via git blame. Empty if not a git repo."""
    try:
        out = subprocess.run(["git", "blame", "--line-porcelain", "orders.csv"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        if out.returncode != 0: return {}
        res, line_no, t, sha = {}, None, None, None
        for ln in out.stdout.splitlines():
            parts = ln.split(" ")
            if ln.startswith("\t"):
                if line_no is not None and line_no >= 2: res[line_no] = 0 if sha == "0" * 40 else t
                line_no = None
            elif len(parts) >= 3 and len(parts[0]) == 40 and parts[1].isdigit() and parts[2].isdigit():
                sha, line_no = parts[0], int(parts[2])
            elif ln.startswith("committer-time "): t = int(parts[1])
        return res
    except Exception: return {}

def main():
    import yfinance as yf
    state_f = ROOT / "state.json"
    st = json.loads(state_f.read_text()) if state_f.exists() else {"cash": START_EQ, "pos": {}, "last": START, "done_orders": 0, "eq": START_EQ}
    st.setdefault("eq", START_EQ); st.setdefault("pending", []); st.setdefault("seen", [])
    if st.pop("done_orders", 0): raise RuntimeError("old state format with processed orders: migrate by hand")
    import csv as _csv
    lines = (ROOT / "orders.csv").read_text().splitlines()
    hdr = next(_csv.reader([lines[0]]))
    orders = []
    for n, ln in enumerate(lines[1:], start=2):
        if not ln.strip(): continue
        f = next(_csv.reader([ln]))
        o = dict(zip(hdr, f + [""] * (len(hdr) - len(f)))) if len(f) <= len(hdr) else {"action": "BAD_FORMAT"}
        o["line"] = n; orders.append(o)
    univ = universe()
    norm = lambda x: str(x).strip().upper().replace(".", "-")
    tickers = sorted(({norm(o.get("ticker", "")) for o in orders} & univ) | set(st["pos"]) | {o["ticker"] for o in st["pending"]} | {"SPY"})
    raw = yf.download(tickers, start="2026-10-15", progress=False, auto_adjust=False, actions=True, group_by="ticker")
    px = {t: to_true_raw(raw[t].dropna(how="all")) for t in tickers if t in raw.columns.get_level_values(0)}
    now_ny = dt.datetime.now(NY)
    for t in px:   # never use today's bar before the close is final
        if now_ny.time() < dt.time(16, 20): px[t] = px[t][px[t].index.date < now_ny.date()]
    irx = yf.download("^IRX", start="2026-10-01", progress=False, auto_adjust=False)["Close"].squeeze() / 100
    rf_of = lambda d: float(irx[irx.index <= d].iloc[-1]) if (irx.index <= d).any() else 0.04
    def bar(t, d):
        if t not in px or d not in px[t].index: return None
        r = px[t].loc[d]
        return None if r[["Open", "High", "Low", "Close"]].isna().any() else r.fillna(0)
    def prev_dv(t, d):
        h = px.get(t)
        if h is None: return None
        h = h[h.index < d].dropna(subset=["Close", "Volume"])
        return float(h["Close"].iloc[-1] * h["Volume"].iloc[-1]) if len(h) else None
    ct = commit_times()
    def can_fill(row, d):
        if not ct: return True                       # not a git checkout (local use)
        t = ct.get(row, 0)
        if t == 0: return False                      # uncommitted line: not yet decided officially
        open_utc = dt.datetime.combine(d.date(), dt.time(9, 30), NY).timestamp()
        return t < open_utc
    days = [d for d in px["SPY"].index if d > pd.Timestamp(st["last"])]
    # retry later instead of locking in a day where held/pending tickers are missing (within last 2 sessions)
    recent = set(px["SPY"].index[-2:]); last5 = list(px["SPY"].index[-5:])
    dead = {t for t in set(st["pos"]) | {o["ticker"] for o in st["pending"]} if all(bar(t, d) is None for d in last5)}
    held_dead = dead & set(st["pos"])
    if held_dead: sys.exit(f"held position(s) {sorted(held_dead)} have no price data for 5 sessions (delisted/renamed?): fix by hand")
    need = (set(st["pos"]) | {o["ticker"] for o in st["pending"]}) - dead   # dead pending tickers age out in simulate()
    def split_suspect(t, d):
        h = px.get(t)
        if h is None or d not in h.index: return False
        prev = h[h.index < d]["Close"].dropna()
        r = h.loc[d]
        return len(prev) > 0 and not (float(r.get("Stock Splits", 0) or 0) > 0) and looks_like_split(float(prev.iloc[-1]), float(r["Open"]))
    for i, d in enumerate(days):
        sus = [t for t in need if split_suspect(t, d)]
        if sus and d not in recent:   # Yahoo never recorded it: fail loudly instead of booking a false loss
            sys.exit(f"{d.date()}: {sus} gapped like a split for 3+ sessions with no split event: verify and fix by hand")
        if sus:
            msg = f"{d.date()}: {sus} gapped like an unrecorded split: holding this day back, will retry next run"
            print(msg); open(ROOT / "ledger_log.txt", "a").write(msg + "\n")
            days = days[:i]; break
        if d in recent and any(bar(t, d) is None for t in need):
            msg = f"data missing on {d.date()} for {[t for t in need if bar(t, d) is None]}: stopping, will retry next run"
            print(msg); open(ROOT / "ledger_log.txt", "a").write(msg + "\n")
            days = days[:i]; break
    eq_f, trades_f = ROOT / "equity.csv", ROOT / "trades.csv"
    if not eq_f.exists(): eq_f.write_text(f"date,equity,gross\n{START},{START_EQ},0\n")
    if not trades_f.exists(): trades_f.write_text("date,ticker,side,shares,price,why,fees,pnl\n")
    T, E, LOG = [], [], []
    simulate(st, orders, bar, days, rf_of, T.append, E.append, univ, prev_dv, can_fill, LOG.append)
    for x in LOG: print(x)
    # write all outputs only after the simulation succeeded
    with open(trades_f, "a") as f: f.writelines(x + "\n" for x in T)
    with open(eq_f, "a") as f: f.writelines(x + "\n" for x in E)
    state_f.write_text(json.dumps(st, indent=1))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(ROOT / "ledger_log.txt", "a") as f:
        f.write(f"--- run {stamp}: processed {len(E)} session(s), last {st['last']}, equity {st['eq']:,.2f}\n")
        f.writelines(x + "\n" for x in LOG)
    eq = pd.read_csv(eq_f, index_col=0, parse_dates=True)["equity"]
    spy = px["SPY"]["Close"]; base = spy[spy.index <= pd.Timestamp(START)].iloc[-1]
    print(f"Claude picks: ${eq.iloc[-1]:,.0f} ({(eq.iloc[-1]/START_EQ-1)*100:+.2f}%) maxDD {(eq/eq.cummax()-1).min()*100:.1f}% as of {eq.index[-1].date()}")
    print(f"SPY:          {(spy.iloc[-1]/base-1)*100:+.2f}% as of {spy.index[-1].date()}")
    for t, p in st["pos"].items():
        print(f"  {t:6s} {p['sh']:+6d} @ {p['entry']:.2f} last {p['last']:.2f} ({(p['last']/p['entry']-1)*100*(1 if p['sh']>0 else -1):+.1f}%) sl {p['sl']} tp {p['tp']}")
    for o in st["pending"]: print(f"  pending: row {o['row']} {o['action']} {o['ticker']}")

if __name__ == "__main__":
    main()
