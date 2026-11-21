"""Daily screen helper: S&P 500 + major ETFs, last close stats. Writes screen_latest.csv."""
import pandas as pd, numpy as np, yfinance as yf
from pathlib import Path
ROOT = Path(__file__).parent
sp = pd.read_csv(ROOT / "sp500_current.csv")
tick = [s.replace(".", "-") for s in sp["Symbol"]] + ["SPY", "QQQ", "IWM", "DIA", "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "GLD", "TLT", "EEM", "EFA"]
px = yf.download(tick, period="14mo", progress=False, auto_adjust=True, group_by="ticker", threads=True)
import datetime as dt
from zoneinfo import ZoneInfo
now = dt.datetime.now(ZoneInfo("America/New_York"))
if now.time() < dt.time(16, 20): px = px[px.index.date < now.date()]
spy = px["SPY"]["Close"]; rows = []
for t in tick:
    try: d = px[t].dropna()
    except KeyError: continue
    if len(d) < 220: continue
    c = d["Close"]; h, l = d["High"].iloc[-1], d["Low"].iloc[-1]
    ch = c.diff(); up = ch.clip(lower=0).ewm(alpha=.5).mean(); dn = (-ch.clip(upper=0)).ewm(alpha=.5).mean()
    atr = (pd.concat([d["High"] - d["Low"], (d["High"] - c.shift()).abs(), (d["Low"] - c.shift()).abs()], axis=1).max(1)).rolling(14).mean().iloc[-1]
    rows.append(dict(t=t, close=c.iloc[-1], r1=c.pct_change().iloc[-1]*100, r5=(c.iloc[-1]/c.iloc[-6]-1)*100,
        r21=(c.iloc[-1]/c.iloc[-22]-1)*100, r63=(c.iloc[-1]/c.iloc[-64]-1)*100, r252=(c.iloc[-1]/c.iloc[-253]-1)*100,
        vs50=(c.iloc[-1]/c.rolling(50).mean().iloc[-1]-1)*100, vs200=(c.iloc[-1]/c.rolling(200).mean().iloc[-1]-1)*100,
        off_hi=(c.iloc[-1]/c.iloc[-252:].max()-1)*100, rsi2=100-100/(1+up.iloc[-1]/max(dn.iloc[-1],1e-9)),
        ibs=(c.iloc[-1]-l)/(h-l)*100 if h > l else 50, atr_pct=atr/c.iloc[-1]*100,
        vol_x=d["Volume"].iloc[-1]/d["Volume"].iloc[-21:-1].mean()))
s = pd.DataFrame(rows).set_index("t").round(2)
sec = dict(zip([x.replace(".", "-") for x in sp["Symbol"]], sp["GICS Sector"])); s["sector"] = s.index.map(sec).fillna("ETF")
s.to_csv(ROOT / "screen_latest.csv")
(ROOT / "screen_date.txt").write_text(str(spy.dropna().index[-1].date()) + "\n")
print("as of", spy.index[-1].date(), "| breadth: % above 200d", round((s.vs200 > 0).mean()*100), "| % above 50d", round((s.vs50 > 0).mean()*100))
pd.set_option("display.width", 220)
etf = s[s.sector == "ETF"]; print(etf[["close","r1","r5","r21","r63","r252","vs50","vs200","off_hi","rsi2","ibs"]].sort_values("r21"))
print("\nsector avg r1/r5/r21:"); print(s[s.sector != "ETF"].groupby("sector")[["r1","r5","r21","r63","vs200"]].mean().round(1).sort_values("r21"))
