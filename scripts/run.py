#!/usr/bin/env python3
"""Project-Delta Project Overseer run script.

Usage:
  scripts/run.py --slot {0900|1230|1630} [--dry-run] [--now ISO] [--repo DIR] [--inbox DIR] [--no-push]

See CONVENTIONS.md for schemas, interpretations and the proposal handoff process.
Implements PROTOCOL.md + Amendments 1-8. Never edits PROTOCOL.md.
Safeguards (CONVENTIONS.md 'Protected files and safeguards'): location/identity, protected-file integrity
and foreign-writer guards run before any work; any failure exits 3 with nothing written or committed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
PO_BOT = "project-overseer-delta"
TEAMS = ["team1", "team2", "team3", "team4"]
TEAM_START_CASH = 250000.0
MASTER_START = 1000000.0
NAME_LIMITS = {"team1": 8, "team2": 12, "team3": None, "team4": 10}
POSITION_LIMIT = 0.30
TOL = 0.01
BENCH = "^SP500TR"
SPX = "^GSPC"
SLOTS = {"0900": (9, 0), "1230": (12, 30), "1630": (16, 30)}
DEFAULT_INBOX = "/home/box/project-delta/inbox"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.\-][A-Z]{1,2})?$")
DISALLOWED_QUOTE_TYPES = {"OPTION", "FUTURE", "CRYPTOCURRENCY", "CURRENCY", "INDEX"}
PERIODS = ["1d", "mtd", "qtd", "ytd", "1y", "3y", "5y", "10y"]

LOG: list[str] = []


def log(msg: str) -> None:
    line = f"[{dt.datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    LOG.append(line)
    print(line, file=sys.stderr)


def zulu(t: dt.datetime) -> str:
    return t.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def r2(x: float) -> float:
    return round(x + 0.0, 2)


def r6(x: float) -> float:
    return round(x + 0.0, 6)


def rj(path):
    with open(path) as f:
        return json.load(f)


def wj(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


class ReconcileError(Exception):
    pass


# ---------------------------------------------------------------- calendar
def session_info(day: dt.date):
    import pandas_market_calendars as mcal
    cal = mcal.get_calendar("NYSE")
    sched = cal.schedule(start_date=day - dt.timedelta(days=15), end_date=day)
    days = [d.date() for d in sched.index]
    if day not in days:
        return None
    row = sched.loc[sched.index[days.index(day)]]
    close_et = row["market_close"].tz_convert(ET).to_pydatetime()
    open_et = row["market_open"].tz_convert(ET).to_pydatetime()
    prev = days[days.index(day) - 1] if days.index(day) > 0 else None
    prev_close = (sched.loc[sched.index[days.index(prev)]]["market_close"].tz_convert(ET).to_pydatetime()
                  if prev else None)
    return {"date": day, "open": open_et, "close": close_et, "prev_session": prev, "prev_close": prev_close,
            "early_close": (close_et.hour, close_et.minute) != (16, 0)}


# ---------------------------------------------------------------- prices
def yf_retry(fn, *a, tries=5, **k):
    """Call a yfinance function with exponential backoff (Yahoo rate-limits bursts)."""
    delay = 5
    for i in range(tries):
        try:
            return fn(*a, **k)
        except Exception as e:
            if i == tries - 1:
                raise
            log(f"yfinance retry {i + 1}/{tries - 1} after error: {e}")
            time.sleep(delay)
            delay *= 2


CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"


def yahoo_chart(t, start, end, interval, events=False):
    """Direct Yahoo Finance chart API (the endpoint yfinance itself uses). Fallback when yfinance's
    session/crumb is rate-limited. Returns (DataFrame[Close, Dividends, Stock Splits], meta, url)."""
    import pandas as pd
    import requests
    s_ts = int(dt.datetime.combine(start, dt.time(0), ET).timestamp())
    e_ts = int(dt.datetime.combine(end, dt.time(0), ET).timestamp())
    params = {"period1": s_ts, "period2": e_ts, "interval": interval, "includePrePost": "false"}
    if events:
        params["events"] = "div,split"
    url = CHART_URL.format(t=t)
    delay = 3
    for i in range(5):
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code == 200:
            break
        log(f"Yahoo chart API {t} HTTP {r.status_code}; retry in {delay}s")
        time.sleep(delay)
        delay *= 2
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    meta = res.get("meta", {})
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    tz = meta.get("exchangeTimezoneName", "America/New_York")
    idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert(tz)
    if interval == "1d":
        idx = idx.normalize()
    df = pd.DataFrame({"Close": [float("nan") if c is None else c for c in q.get("close", [])]}, index=idx)
    ev = res.get("events", {})
    div = pd.Series({pd.Timestamp(int(k), unit="s", tz="UTC").tz_convert(tz).normalize(): v["amount"]
                     for k, v in (ev.get("dividends") or {}).items()}, dtype=float)
    spl = pd.Series({pd.Timestamp(int(k), unit="s", tz="UTC").tz_convert(tz).normalize(): v["numerator"] / v["denominator"]
                     for k, v in (ev.get("splits") or {}).items()}, dtype=float)
    return df, meta, div.sort_index(), spl.sort_index()


class Pricer:
    def __init__(self, slot, now, sess, wait_close_min, indicative=False):
        self.slot, self.now, self.sess = slot, now, sess
        self.indicative = indicative
        self.wait_close_min = wait_close_min
        self.cache = {}
        self.bar_time = {}  # ticker -> actual price bar time (aware datetime)
        self.cache_qt = {}
        self.tickers = {}
        self.yf_down = False
        self.meta = {}

    def _yf(self, t):
        import yfinance as yf
        if t not in self.tickers:
            self.tickers[t] = yf.Ticker(t)
        return self.tickers[t]

    def price(self, t):
        if t in self.cache:
            return self.cache[t]
        if self.indicative:
            p, src = self._p1230(t)
            res = (p, "INDICATIVE (dry-run before official close; not a fill) " + src)
        else:
            res = getattr(self, "_p" + self.slot)(t)
        self.cache[t] = res
        return res

    def _hist(self, t, **kw):
        """yfinance first (2 quick tries); on failure/rate-limit fall back to the Yahoo chart API directly."""
        def f():
            h = self._yf(t).history(raise_errors=True, **kw)
            if h is None or len(h) == 0:
                raise RuntimeError(f"{t}: empty Yahoo history response")
            return h
        if not self.yf_down:
            try:
                return yf_retry(f, tries=2)
            except Exception as e:
                log(f"yfinance unavailable for {t} ({e}); using Yahoo chart API directly")
                if "Rate" in str(e) or "429" in str(e):
                    self.yf_down = True
        df, meta, _, _ = yahoo_chart(t, kw["start"], kw["end"], kw["interval"])
        self.meta[t] = meta
        if len(df) == 0:
            raise RuntimeError(f"{t}: empty Yahoo chart response")
        return df

    def _daily(self, t, day):
        h = self._hist(t, start=day - dt.timedelta(days=10), end=day + dt.timedelta(days=1),
                     interval="1d", auto_adjust=False)
        out = {}
        for idx, row in h.iterrows():
            if not math.isnan(row["Close"]):
                out[idx.date()] = float(row["Close"])
        return out

    def _src(self, t, desc, ts):
        url = f"https://finance.yahoo.com/quote/{t.replace('^', '%5E')}/history/"
        return f"Yahoo Finance {t} {desc} @ {ts} ({url})"

    def _p0900(self, t):  # previous official close
        prev = self.sess["prev_session"]
        d = self._daily(t, self.sess["date"])
        if prev not in d:
            raise RuntimeError(f"{t}: no official close for previous session {prev}")
        self.bar_time[t] = self.sess["prev_close"]
        return d[prev], self._src(t, f"official close (daily Close, session {prev})", f"{prev} close")

    def _p1630(self, t):  # official close of today (early close aware)
        day = self.sess["date"]
        deadline = time.time() + 60 * self.wait_close_min
        while True:
            d = self._daily(t, day)
            if day in d:
                self.bar_time[t] = self.sess["close"]
                return d[day], self._src(t, f"official close (daily Close, session {day}"
                                         + (", early close" if self.sess["early_close"] else "") + ")",
                                         f"{day} close {self.sess['close'].strftime('%H:%M')} ET")
            if time.time() > deadline:
                raise RuntimeError(f"{t}: official close for {day} not yet available")
            log(f"{t}: close for {day} not yet published; retrying in 60s")
            time.sleep(60)

    def _p1230(self, t):  # latest regular-session quote at decision time
        day = self.sess["date"]
        h = self._hist(t, start=day, end=day + dt.timedelta(days=1), interval="1m", prepost=False)
        h = h[[(i.to_pydatetime() <= self.now) and (i.to_pydatetime() < self.sess["close"]) for i in h.index]]
        h = h[h["Close"].notna()]
        if len(h) == 0:
            raise RuntimeError(f"{t}: no regular-session 1m quote for {day} up to {self.now}")
        ts = h.index[-1].tz_convert(ET)
        self.bar_time[t] = ts.to_pydatetime()  # bar start time: never later than the decision time
        return float(h["Close"].iloc[-1]), self._src(t, "latest regular-session quote (1m bar close)",
                                                     ts.strftime("%Y-%m-%dT%H:%M%z") + " ET")

    def quote_type(self, t):
        try:
            # instrumentType comes from the chart metadata of the price request (no extra Yahoo call)
            if t not in self.cache_qt:
                md = self.meta.get(t) or ({} if self.yf_down else self._yf(t).get_history_metadata())
                self.cache_qt[t] = (md or {}).get("instrumentType")
            return self.cache_qt[t]
        except Exception:
            return None

    def corporate_actions(self, t):
        try:
            if self.yf_down:
                raise RuntimeError("yfinance rate-limited")
            tk = self._yf(t)
            return yf_retry(lambda: tk.dividends, tries=2), yf_retry(lambda: tk.splits, tries=2)
        except Exception as e:
            log(f"yfinance corporate actions unavailable for {t} ({e}); using Yahoo chart API events")
            d = self.sess["date"]
            _, _, div, spl = yahoo_chart(t, d - dt.timedelta(days=60), d + dt.timedelta(days=1), "1d", events=True)
            return div, spl


# ---------------------------------------------------------------- ledger replay
def read_ledger(path):
    lines = []
    with open(path) as f:
        for n, raw in enumerate(f, 1):
            if raw.strip():
                try:
                    lines.append(json.loads(raw))
                except Exception as e:
                    raise ReconcileError(f"ledger line {n} is not valid JSON: {e}")
    return lines


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def replay(lines, before=None):
    """Return {team: {"cash": float, "pos": {ticker: {"shares", "cost_basis"}}}, "_unallocated": float}."""
    st = {t: {"cash": 0.0, "pos": {}} for t in TEAMS}
    unalloc = 0.0
    for ln in lines:
        if before is not None and parse_ts(ln["timestamp"]) >= before:
            continue
        a = ln["action"]
        if a == "DAY0_INIT":
            # Day-0 locked balance: 4 x 250000 team sleeves, 0 unallocated (Amendment 3)
            for t in TEAMS:
                st[t]["cash"] += TEAM_START_CASH
            unalloc += float(ln["size"]) - TEAM_START_CASH * len(TEAMS)
            continue
        team = ln.get("team")
        if team not in st:
            unalloc += float(ln.get("cash_delta", 0) or 0)
            continue
        s = st[team]
        s["cash"] = r2(s["cash"] + float(ln.get("cash_delta", 0) or 0))
        tk = ln.get("ticker")
        sd = float(ln.get("shares_delta", 0) or 0)
        cbd = float(ln.get("cost_basis_delta", 0) or 0)
        if tk and (sd or cbd):
            p = s["pos"].setdefault(tk, {"shares": 0.0, "cost_basis": 0.0})
            p["shares"] = r6(p["shares"] + sd)
            p["cost_basis"] = r2(p["cost_basis"] + cbd)
            if abs(p["shares"]) < 1e-9:
                del s["pos"][tk]
    return st, r2(unalloc)


# ---------------------------------------------------------------- proposals
def load_proposals(repo, inbox):
    """Returns list of (proposal_dict_or_None, origin_dict)."""
    out = []
    for team in TEAMS:
        d = os.path.join(inbox, team)
        for fp in sorted(glob.glob(os.path.join(d, "*.json"))):
            try:
                obj = rj(fp)
            except Exception as e:
                out.append((None, {"kind": "inbox", "path": fp, "team_dir": team, "error": f"invalid JSON: {e}"}))
                continue
            items = obj if isinstance(obj, list) else [obj]
            for i, p in enumerate(items):
                out.append((p, {"kind": "inbox", "path": fp, "index": i, "team_dir": team}))
        rp = os.path.join(repo, "teams", team, "proposals.json")
        if os.path.exists(rp):
            items = rj(rp) or []
            for i, p in enumerate(items):
                out.append((p, {"kind": "repo", "path": rp, "index": i, "team_dir": team}))
    return out


def validate_shape(p, origin, slot, session_date, seen_ids, done_ids):
    errs = []
    if not isinstance(p, dict):
        return ["proposal is not a JSON object"]
    for k in ["proposal_id", "team", "action", "rationale", "proposed_by", "team_overseer_approval"]:
        if not p.get(k):
            errs.append(f"missing {k}")
    if errs:
        return errs
    if p["team"] != origin["team_dir"]:
        errs.append(f"team {p['team']} does not match folder {origin['team_dir']}")
    if p["proposal_id"] in seen_ids:
        errs.append(f"duplicate proposal_id {p['proposal_id']}")
    a = p["action"]
    if a not in ("BUY", "SELL", "ADJUSTMENT"):
        errs.append(f"action {a} not allowed (BUY/SELL/ADJUSTMENT only; no shorting, leverage or options)")
    toa = p["team_overseer_approval"]
    if not isinstance(toa, dict) or not toa.get("bot_id") or not toa.get("approved_at"):
        errs.append("team_overseer_approval must have bot_id and approved_at (IM -> TO -> PO chain)")
    elif toa.get("bot_id") == p.get("proposed_by"):
        errs.append("Team Overseer approval must come from a different bot than the proposer")
    if a in ("BUY", "SELL"):
        t = p.get("ticker", "")
        if not isinstance(t, str) or not TICKER_RE.match(t):
            errs.append(f"ticker {t!r} invalid (plain listed stock/ETF symbols only; no options/futures/indices)")
        has_sh = p.get("shares") is not None
        has_no = p.get("notional_usd") is not None
        if has_sh == has_no:
            errs.append("exactly one of shares or notional_usd is required")
        v = p.get("shares") if has_sh else p.get("notional_usd")
        if v is not None and (not isinstance(v, (int, float)) or v <= 0):
            errs.append("size must be a positive number")
        if p.get("whole_shares") and not has_no:
            errs.append("whole_shares only applies to notional_usd orders")
        if p.get("allow_reduce_shares_to_cash") and not has_sh:
            errs.append("allow_reduce_shares_to_cash only applies to share-count orders")
    if a == "ADJUSTMENT":
        for k in ("cash_delta", "shares_delta", "cost_basis_delta"):
            if not isinstance(p.get(k, 0), (int, float)):
                errs.append(f"{k} must be numeric")
        if not p.get("terms_reference"):
            errs.append("ADJUSTMENT requires terms_reference (official terms URL/document)")
    if p.get("team") == "team3" and a in ("BUY", "SELL") and not p.get("rule_id"):
        errs.append("team3 proposals must cite rule_id (Team 3 must stay rules-based)")
    return errs


def slot_matches(p, slot, session_date):
    """Return (match_now, expired)."""
    es = p.get("execute_slot")
    ed = p.get("execute_date")
    if ed:
        d = dt.date.fromisoformat(ed)
        if d > session_date:
            return False, False
        if d < session_date:
            return False, True
    if es and es != "any" and es != slot:
        return False, False
    return True, False


def load_overrides(repo):
    path = os.path.join(repo, "overrides.jsonl")
    ov = {}
    if os.path.exists(path):
        for raw in open(path):
            if raw.strip():
                o = json.loads(raw)
                if o.get("granted_by") == PO_BOT:
                    ov[o["proposal_id"]] = o
    return ov


# ---------------------------------------------------------------- performance
def load_jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(x) for x in open(path) if x.strip()]


def annualize(r, days):
    return (1 + r) ** (365.25 / days) - 1


def compute_performance(eod, current, inception_date, bench_baseline):
    """eod: list of end-of-day points (sorted). current: dict for this run (same shape).
    Point shape: {"date", "values": {"account": v, team: v}, "index": {...}, "sp500tr": level}"""
    today = dt.date.fromisoformat(current["date"])
    keys = ["account"] + TEAMS
    pts = [p for p in eod if p["date"] < current["date"]]

    def ref_before(start):  # last EOD point strictly before `start` date
        c = [p for p in pts if dt.date.fromisoformat(p["date"]) < start]
        return c[-1] if c else None

    def ref_on_or_before(d):
        c = [p for p in pts + [current] if dt.date.fromisoformat(p["date"]) <= d]
        return c[-1] if c else None

    incep = eod[0] if eod else None
    starts = {
        "1d": None,
        "mtd": today.replace(day=1),
        "qtd": dt.date(today.year, 3 * ((today.month - 1) // 3) + 1, 1),
        "ytd": dt.date(today.year, 1, 1),
    }
    res = {k: {"returns": {}, "benchmark_returns": {}, "excess": {}, "period_details": {}} for k in keys}
    for per in PERIODS + ["since_inception"]:
        ref, basis, ann = None, "period", False
        if incep is None:
            ref = None
        elif per == "since_inception":
            ref, basis = incep, "since_inception"
        elif per == "1d":
            ref = pts[-1] if pts else None
        elif per in starts:
            ref = ref_before(starts[per])
            if ref is None:
                ref, basis = incep, "since_inception (period began before inception)"
        else:
            n = int(per[:-1])
            try:
                anchor = today.replace(year=today.year - n)
            except ValueError:
                anchor = today.replace(year=today.year - n, day=28)
            if dt.date.fromisoformat(inception_date) <= anchor:
                ref = ref_on_or_before(anchor)
                ann = True
            else:
                ref = None  # not yet available; since_inception reported separately
        for k in keys:
            if ref is None or current["date"] == "" or ref.get("index", {}).get(k) is None:
                r = b = None
            else:
                r = current["index"][k] / ref["index"][k] - 1
                b = (current["sp500tr"] / ref["sp500tr"] - 1) if (ref.get("sp500tr") and current.get("sp500tr")) else None
                days = (today - dt.date.fromisoformat(ref["date"])).days
                if ann and days > 0:
                    r = annualize(r, days)
                    b = annualize(b, days) if b is not None else None
            res[k]["returns"][per] = None if r is None else round(r, 8)
            res[k]["benchmark_returns"][per] = None if b is None else round(b, 8)
            res[k]["excess"][per] = None if (r is None or b is None) else round(r - b, 8)
            res[k]["period_details"][per] = None if ref is None else {
                "from_date": ref["date"], "to": current["as_of"], "basis": basis, "annualized": ann}
        if per == "since_inception" and incep is not None:
            days = (today - dt.date.fromisoformat(incep["date"])).days
            for k in keys:
                rr = res[k]["returns"]["since_inception"]
                bb = res[k]["benchmark_returns"]["since_inception"]
                res[k]["since_inception_annualized"] = {
                    "return": round(annualize(rr, days), 8) if (rr is not None and days >= 365) else None,
                    "benchmark": round(annualize(bb, days), 8) if (bb is not None and days >= 365) else None,
                    "note": "annualized only once >= 365 calendar days of history"}
    return res


# ---------------------------------------------------------------- Amendment 8 helpers
PRICE_SOURCE_LABEL = {"0900": "previous official close", "1230": "latest regular-session quote", "1630": "official close"}


def build_prices_json(pricer, slot, now, held):
    """Amendment 8(a) prices.json: Amendment-1 marks for every held ticker (union across teams) + ^SP500TR.
    Every as_of is the actual price bar time (UTC Z), clamped so it is never later than now."""
    def bt(t):
        b = pricer.bar_time.get(t)
        if b is None:
            return None
        return zulu(min(b, now))
    label = "latest regular-session quote" if pricer.indicative else PRICE_SOURCE_LABEL[slot]
    prices, times = {}, []
    for tk in held:
        pr, _ = pricer.price(tk)
        prices[tk] = {"price": pr, "as_of": bt(tk)}
        if bt(tk):
            times.append(bt(tk))
    sp = None
    try:
        lvl, _ = pricer.price(BENCH)
        sp = {"level": lvl, "as_of": bt(BENCH)}
        if bt(BENCH):
            times.append(bt(BENCH))
    except Exception as e:
        log(f"WARNING prices.json: ^SP500TR unavailable: {e}")
    return {"as_of": max(times) if times else None, "source": f"Yahoo Finance {label}",
            "prices": prices, "sp500_total_return": sp}


def amendment_notes(repo, amends, adopted):
    """Notes for amendments not referenced by the most recent frozen snapshot (Amendment 7 referencing)."""
    prev = set(range(1, 8))
    arch = sorted(glob.glob(os.path.join(repo, "snapshots", "*.json")))
    if arch:
        try:
            prev = set(rj(arch[-1]).get("amendments_referenced") or [])
        except Exception:
            pass
    return [f"Amendment {n} adopted {adopted.get(n, 'date n/a')}" for n in amends if n not in prev]


# ---------------------------------------------------------------- git
def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


# ---------------------------------------------------------------- safeguards (owner-approved 2026-10-26)
# After the 2026-10-26 16:37 ET incident (connector uploads from a /tmp script copy overwrote team values and
# replaced performance.json with a placeholder), every run verifies, before doing any work:
#   1. its own location and git identity (only the Overseer clone, as the Overseer, may run/commit/push),
#   2. that every protected file parses and has the keys this script writes (no placeholders),
#   3. that no one other than the Overseer changed protected paths since the last Overseer commit.
# Any failure logs a clear line, writes and commits nothing, moves no inbox file, and exits 3.
EXPECTED_REPO = "/home/box/project-delta/repo"
PO_EMAIL = "330484715+mfanninf1555@users.noreply.github.com"
# Everything the run writes or depends on. Engineer dashboard-only files (Amendment 8(b): index.html,
# DASHBOARD.md, .nojekyll, other dashboard assets) are deliberately NOT listed and stay committable by others.
PROTECTED_PATHS = ["ledger.jsonl", "decisions.jsonl", "overrides.jsonl", "snapshot_latest.json", "snapshots/",
                   "performance.json", "prices.json", "teams/", "runs/", "history/", "scripts/", "PROTOCOL.md",
                   "CONVENTIONS.md", "requirements.txt", ".gitignore", ".github/"]
PLACEHOLDER_RE = re.compile(r"placeholder|will update properly|\bTODO\b|\bTBD\b|\bFIXME\b|lorem ipsum", re.I)
PERF_KEYS = ["as_of", "run_id", "run_slot", "inception_date", "benchmark", "benchmark_baseline",
             "benchmark_current", "method", "note", "account", "teams"]
SNAP_KEYS = ["as_of", "run_id", "run_slot", "session_date", "early_close", "inception_date", "total_value", "cash",
             "unallocated_cash", "positions", "teams", "sp500_price", "sp500_price_source", "sp500_total_return",
             "sp500_total_return_source", "amendments_referenced", "amendment_notes", "reconciliation",
             "ledger_lines"]
PRICES_KEYS = ["as_of", "source", "prices", "sp500_total_return"]
LEDGER_KEYS = ["timestamp", "bot_id", "action", "ticker", "size", "decision_price", "price_source", "rationale",
               "approving_bot"]
HOLDING_KEYS = ["ticker", "shares", "cost_basis"]


class GuardError(Exception):
    pass


# Host check (adopted 2026-10-30, owner-approved): runs are allowed only on the Overseer's own box.
EXPECTED_HOSTNAME = "grok-bot-vm-429005534"
EXPECTED_MACHINE_ID = "be992e2cea5940209955b1d96b858ea2"


def read_machine_id(path="/etc/machine-id"):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def check_host(repo, hostname=None, machine_id=None):
    """Guard 0: refuse unless hostname, /etc/machine-id and repo path are the Overseer box's.
    hostname/machine_id parameters exist only so the check can be unit-tested; main() never passes them."""
    import socket
    hn = socket.gethostname() if hostname is None else hostname
    mid = read_machine_id() if machine_id is None else machine_id
    problems = []
    if hn != EXPECTED_HOSTNAME:
        problems.append(f"hostname {hn!r} != {EXPECTED_HOSTNAME!r}")
    if mid != EXPECTED_MACHINE_ID:
        problems.append(f"machine-id {(mid or 'unreadable')[:8]}... does not match")
    if os.path.abspath(repo) != EXPECTED_REPO or os.path.realpath(repo) != os.path.realpath(EXPECTED_REPO):
        problems.append(f"repo path {repo!r} != {EXPECTED_REPO!r}")
    if problems:
        raise GuardError("HOST CHECK FAILED: " + "; ".join(problems) + " - runs are allowed only on the Overseer box")
    return hn


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def check_location_identity(repo):
    """Guard 3: refuse unless this is the Overseer clone at EXPECTED_REPO, running its own script, with git's
    effective author/committer identity equal to the Overseer identity (env overrides included)."""
    want = os.path.realpath(EXPECTED_REPO)
    script = os.path.realpath(__file__)
    if os.path.realpath(repo) != want:
        raise GuardError(f"WRONG LOCATION: --repo {repo} is not {EXPECTED_REPO}; runs from copies are forbidden")
    if os.path.dirname(script) != os.path.join(want, "scripts"):
        raise GuardError(f"WRONG LOCATION: script {script} is not {EXPECTED_REPO}/scripts/run.py; script copies are forbidden")
    r = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    top = os.path.realpath(r.stdout.strip()) if r.returncode == 0 else None
    if top != want:
        raise GuardError(f"WRONG LOCATION: git top level is {top or 'not a git repo'}, expected {EXPECTED_REPO}")
    for var in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        r = subprocess.run(["git", "-C", repo, "var", var], capture_output=True, text=True)
        m = re.match(r"^(.*) <([^>]*)>", r.stdout.strip()) if r.returncode == 0 else None
        name, email = (m.group(1), m.group(2)) if m else (None, None)
        if name != PO_BOT or email != PO_EMAIL:
            raise GuardError(f"WRONG IDENTITY: effective {var} is {name!r} <{email}>, expected {PO_BOT} <{PO_EMAIL}>")


def check_protected_integrity(repo):
    """Guard 1: every protected data file parses as JSON with the top-level keys this script writes."""
    def bad(path, why):
        raise GuardError(f"PROTECTED FILE INVALID: {os.path.relpath(path, repo)}: {why}")

    def load(path, placeholder_scan=True):
        if not os.path.isfile(path):
            bad(path, "missing")
        raw = open(path, encoding="utf-8", errors="replace").read()
        if not raw.strip():
            bad(path, "empty file")
        if placeholder_scan and PLACEHOLDER_RE.search(raw):
            bad(path, f"placeholder text {PLACEHOLDER_RE.search(raw).group(0)!r}")
        try:
            return json.loads(raw)
        except Exception as e:
            bad(path, f"not valid JSON ({e})")

    def keys(path, obj, req):
        if not isinstance(obj, dict):
            bad(path, f"top level is {type(obj).__name__}, expected object")
        miss = [k for k in req if k not in obj]
        if miss:
            bad(path, f"missing keys {miss}")

    p = os.path.join(repo, "performance.json")
    o = load(p)
    keys(p, o, PERF_KEYS)
    if not isinstance(o["account"], dict) or not isinstance(o["teams"], dict) or set(TEAMS) - set(o["teams"]):
        bad(p, "account/teams sections malformed")
    snaps = [os.path.join(repo, "snapshot_latest.json")] + sorted(glob.glob(os.path.join(repo, "snapshots", "*.json")))
    for p in snaps:
        o = load(p)
        keys(p, o, SNAP_KEYS)
        if not _is_num(o["total_value"]) or not isinstance(o["teams"], dict):
            bad(p, "total_value/teams malformed")
    p = os.path.join(repo, "prices.json")
    o = load(p)
    keys(p, o, PRICES_KEYS)
    if not isinstance(o["prices"], dict):
        bad(p, "prices is not an object")
    for t in TEAMS:
        td = os.path.join(repo, "teams", t)
        for fn, req in (("cash.json", "cash"), ("value.json", "value")):
            p = os.path.join(td, fn)
            o = load(p)
            keys(p, o, [req])
            if not _is_num(o[req]):
                bad(p, f"{req} is not a number")
        p = os.path.join(td, "holdings.json")
        o = load(p)
        if not isinstance(o, list) or any(not isinstance(h, dict) or [k for k in HOLDING_KEYS if k not in h]
                                          or not _is_num(h["shares"]) for h in o):
            bad(p, f"expected a list of holdings with keys {HOLDING_KEYS}")
        p = os.path.join(td, "proposals.json")
        if os.path.exists(p):
            o = load(p, placeholder_scan=False)  # free-text rationales; shape check only
            if not isinstance(o, list):
                bad(p, "expected a list")
        for p in glob.glob(os.path.join(td, "*.json")):
            load(p, placeholder_scan=False)  # any other team JSON must at least parse
    # append-only logs: every line must be a JSON object (no placeholder scan: rationales are free text)
    p = os.path.join(repo, "ledger.jsonl")
    if not os.path.isfile(p):
        bad(p, "missing")
    n = 0
    for i, raw in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
        if not raw.strip():
            continue
        try:
            o = json.loads(raw)
        except Exception as e:
            bad(p, f"line {i} is not valid JSON ({e})")
        if not isinstance(o, dict) or [k for k in LEDGER_KEYS if k not in o]:
            bad(p, f"line {i} missing keys {[k for k in LEDGER_KEYS if not isinstance(o, dict) or k not in o]}")
        n += 1
    if n == 0:
        bad(p, "no ledger lines")
    for rel in ("decisions.jsonl", "overrides.jsonl", "history/eod.jsonl", "history/runs.jsonl"):
        p = os.path.join(repo, rel)
        if not os.path.exists(p):
            continue
        for i, raw in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
            if raw.strip():
                try:
                    if not isinstance(json.loads(raw), dict):
                        raise ValueError("not an object")
                except Exception as e:
                    bad(p, f"line {i} is not a valid JSON object ({e})")
    return n


def _is_po(name, email):
    return name == PO_BOT and email == PO_EMAIL


def foreign_commits(repo, rng):
    """Commits in a git range not authored by the Overseer that touch protected paths: [(sha, author, [paths])]."""
    out = git(repo, "log", "--format=%x01%H%x00%an%x00%ae", "--name-only", rng, "--", *PROTECTED_PATHS)
    res = []
    for block in out.split("\x01")[1:]:
        head, _, files = block.partition("\n")
        sha, an, ae = head.split("\x00")
        if not _is_po(an, ae):
            res.append((sha[:7], f"{an} <{ae}>", [f for f in files.split("\n") if f.strip()]))
    return res


def check_foreign_writes(repo):
    """Guard 2: protected paths at HEAD must equal those of the most recent Overseer-authored commit, and the
    working tree must not hold uncommitted changes to tracked protected files."""
    base = None
    for ln in git(repo, "log", "--format=%H%x00%an%x00%ae", "HEAD").splitlines():
        sha, an, ae = ln.split("\x00")
        if _is_po(an, ae):
            base = sha
            break
    if base is None:
        raise GuardError("FOREIGN WRITE TO PROTECTED FILES: no Overseer-authored commit found on HEAD")
    changed = [f for f in git(repo, "diff", "--name-only", base, "HEAD", "--", *PROTECTED_PATHS).splitlines() if f]
    if changed:
        who = foreign_commits(repo, f"{base}..HEAD") or [("?", "unknown", changed)]
        detail = "; ".join(f"{s} {a}: {', '.join(p)}" for s, a, p in who)
        raise GuardError(f"FOREIGN WRITE TO PROTECTED FILES: {detail} (last Overseer commit {base[:7]}; "
                         f"recover by reverting with normal revert commits, never force-push)")
    dirty = git(repo, "status", "--porcelain", "--untracked-files=no", "--", *PROTECTED_PATHS)
    if dirty:
        raise GuardError("FOREIGN WRITE TO PROTECTED FILES: uncommitted changes in the working tree: "
                         + ", ".join(l[3:] for l in dirty.splitlines()))
    return base[:7]


def check_incoming(repo):
    """Before pulling during push: refuse if origin/main gained foreign commits touching protected paths."""
    git(repo, "fetch", "-q", "origin", "main")
    bad = foreign_commits(repo, "HEAD..FETCH_HEAD")
    if bad:
        detail = "; ".join(f"{s} {a}: {', '.join(p)}" for s, a, p in bad)
        raise GuardError(f"FOREIGN WRITE TO PROTECTED FILES: {detail} (arrived on origin/main during the run)")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", required=True, choices=list(SLOTS))
    ap.add_argument("--dry-run", action="store_true", help="compute and validate only; write nothing, commit nothing")
    ap.add_argument("--now", help="override current time (ISO, ET if no offset) - testing only")
    ap.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    ap.add_argument("--inbox", default=DEFAULT_INBOX)
    ap.add_argument("--no-push", action="store_true", help="commit locally but do not pull/push (testing)")
    ap.add_argument("--no-git", action="store_true", help="write files but no git operations (testing)")
    ap.add_argument("--wait-close-minutes", type=int, default=20)
    ap.add_argument("--force", action="store_true", help="re-run a slot that already has a committed run")
    args = ap.parse_args()

    repo, inbox, slot = args.repo, args.inbox, args.slot
    # ---- host check first, live and dry runs alike, before anything else is read or written
    try:
        hostname = check_host(repo)
        if args.now and not args.dry_run:
            raise GuardError("HOST CHECK FAILED: --now is allowed only with --dry-run (live runs use the real clock)")
    except GuardError as e:
        print(str(e), file=sys.stderr)
        print(json.dumps({"slot": slot, "dry_run": args.dry_run, "status": "host_check_failed", "error": str(e)}, indent=2))
        return 3
    log(f"host check OK: {hostname}, machine-id {EXPECTED_MACHINE_ID[:8]}..., repo {EXPECTED_REPO}")
    now = dt.datetime.now(ET)
    if args.now:
        now = dt.datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=ET)
        now = now.astimezone(ET)
    sdate = now.date()
    run_id = f"{sdate.isoformat()}_{slot}"
    summary = {"run_id": run_id, "slot": slot, "dry_run": args.dry_run, "now_et": now.isoformat(), "hostname": hostname}

    # ---- safeguards: location/identity, protected-file integrity, foreign writers (before any work)
    try:
        check_location_identity(repo)
        n = check_protected_integrity(repo)
        if not args.dry_run and not args.no_git and not args.no_push:
            git(repo, "pull", "--rebase", "-q", "origin", "main")
            n = check_protected_integrity(repo)
        base = check_foreign_writes(repo)
        log(f"safeguards OK: location/identity, {n} ledger lines + protected files valid, "
            f"protected paths match last Overseer commit {base}")
    except GuardError as e:
        log(str(e))
        summary.update(status="guard_failed", error=str(e))
        gl = os.path.join("/tmp", f"project-delta-guard-{run_id}.log")  # outside the repo: nothing written there
        with open(gl, "w") as f:
            f.write("\n".join(LOG) + "\n\nSUMMARY " + json.dumps(summary, indent=2) + "\n")
        summary["log"] = gl
        print(json.dumps(summary, indent=2))
        return 3

    sess = session_info(sdate)
    if sess is None:
        summary.update(status="no_session", message=f"{sdate} is not an NYSE regular session (Amendment 5); no changes")
        print(json.dumps(summary, indent=2))
        return 0
    summary["early_close"] = sess["early_close"]
    if sess["early_close"]:
        log(f"early close day: official close {sess['close'].strftime('%H:%M')} ET (Amendment 5)")

    if not args.force and os.path.exists(os.path.join(repo, "snapshots", f"{sdate.isoformat()}_{slot}.json")):
        summary.update(status="already_done", message="frozen snapshot for this run already exists; use --force to re-run")
        print(json.dumps(summary, indent=2))
        return 0

    log_path = os.path.join(repo, "runs", f"{run_id}.log")
    try:
        rc = run(args, repo, inbox, slot, now, sess, run_id, summary)
    except GuardError as e:
        log(str(e))
        summary.update(status="guard_failed", error=str(e))
        rc = 3
    except ReconcileError as e:
        log(f"RECONCILIATION FAILURE: {e}")
        summary.update(status="reconcile_failed", error=str(e))
        rc = 2
    except Exception as e:
        log("ERROR: " + "".join(traceback.format_exception(e)))
        summary.update(status="error", error=str(e))
        rc = 3
    if rc != 0 or not args.dry_run:
        if args.dry_run:
            log_path = os.path.join("/tmp", f"project-delta-dryrun-{run_id}.log")
        elif summary.get("status") == "guard_failed":
            log_path = os.path.join("/tmp", f"project-delta-guard-{run_id}.log")  # keep the repo untouched
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w") as f:
            f.write("\n".join(LOG) + "\n\nSUMMARY " + json.dumps(summary, indent=2) + "\n")
        summary["log"] = log_path
    print(json.dumps(summary, indent=2))
    return rc


def run(args, repo, inbox, slot, now, sess, run_id, summary):
    sdate = sess["date"]
    ts_now = zulu(now)
    indicative = bool(args.dry_run and slot == "1630" and now < sess["close"] + dt.timedelta(minutes=15))
    if indicative:
        log("dry-run before the official close: pricing with latest regular-session quotes as INDICATIVE only")
        summary["indicative_prices"] = True
    pricer = Pricer(slot, now, sess, args.wait_close_minutes, indicative)
    ledger_path = os.path.join(repo, "ledger.jsonl")
    ledger = read_ledger(ledger_path)
    inc = rj(os.path.join(repo, "performance.json"))
    inception_date = inc.get("inception_date", "2026-10-20")
    if sdate < dt.date.fromisoformat(inception_date):
        raise RuntimeError(f"run date {sdate} precedes inception {inception_date}")
    if not ledger or ledger[0].get("action") != "DAY0_INIT":
        raise ReconcileError("ledger must start with the DAY0_INIT line")

    # ---- pre-run integrity: team files must equal ledger replay
    st, unalloc = replay(ledger)
    for t in TEAMS:
        c = rj(os.path.join(repo, "teams", t, "cash.json"))["cash"]
        if abs(c - st[t]["cash"]) > TOL:
            raise ReconcileError(f"{t}/cash.json {c} != ledger replay {st[t]['cash']}")
        h = {x["ticker"]: x["shares"] for x in rj(os.path.join(repo, "teams", t, "holdings.json"))}
        lp = {k: v["shares"] for k, v in st[t]["pos"].items()}
        if set(h) != set(lp) or any(abs(h[k] - lp[k]) > 1e-6 for k in h):
            raise ReconcileError(f"{t}/holdings.json does not match ledger replay")
    log(f"pre-run integrity OK: ledger lines={len(ledger)}, team files match replay")

    new_lines = []

    def add_line(team, action, ticker, size, price, source, rationale, cash_delta=0.0, shares_delta=0.0,
                 cost_basis_delta=0.0, **extra):
        ln = {"timestamp": ts_now, "bot_id": PO_BOT, "action": action, "ticker": ticker, "size": size,
              "decision_price": price, "price_source": source, "rationale": rationale,
              "approving_bot": PO_BOT, "team": team, "run_id": run_id,
              "cash_delta": r2(cash_delta), "shares_delta": r6(shares_delta), "cost_basis_delta": r2(cost_basis_delta)}
        ln.update(extra)
        new_lines.append(ln)
        s = st[team]
        s["cash"] = r2(s["cash"] + r2(cash_delta))
        if ticker and (shares_delta or cost_basis_delta):
            p = s["pos"].setdefault(ticker, {"shares": 0.0, "cost_basis": 0.0})
            p["shares"] = r6(p["shares"] + shares_delta)
            p["cost_basis"] = r2(p["cost_basis"] + cost_basis_delta)
            if abs(p["shares"]) < 1e-9:
                del s["pos"][ticker]
        return ln

    # ---- corporate actions (Amendment 2): splits first, then dividends
    booked = {(l.get("team"), l.get("ticker"), l["action"], l.get("event_date")) for l in ledger}
    tickers_ever = {}
    for l in ledger:
        if l.get("team") in TEAMS and l.get("ticker") and l["action"] in ("BUY", "SELL", "ADJUSTMENT", "SPLIT"):
            tickers_ever.setdefault(l["ticker"], set()).add(l["team"])
    window_start = max(dt.date.fromisoformat(inception_date), sdate - dt.timedelta(days=30))
    ca_count = 0
    for tk, teams in sorted(tickers_ever.items()):
        try:
            divs, splits = pricer.corporate_actions(tk)
        except Exception as e:
            log(f"WARNING corporate actions fetch failed for {tk}: {e}")
            continue
        events = []
        for idx, v in splits.items():
            events.append(("SPLIT", idx, float(v)))
        for idx, v in divs.items():
            events.append(("DIVIDEND", idx, float(v)))
        events.sort(key=lambda e: (e[1], e[0] != "SPLIT"))
        for kind, idx, val in events:
            ed = idx.date()
            if not (window_start < ed <= sdate) or val <= 0:
                continue
            cutoff = dt.datetime.combine(ed, dt.time(0, 0), ET)
            for team in sorted(teams):
                if (team, tk, kind, ed.isoformat()) in booked:
                    continue
                pre, _ = replay(ledger + new_lines, before=cutoff)
                held = pre[team]["pos"].get(tk, {}).get("shares", 0.0)
                if held <= 0:
                    continue
                src = f"Yahoo Finance {tk} {'splits' if kind == 'SPLIT' else 'dividends'} series, event date {ed} (https://finance.yahoo.com/quote/{tk}/history/)"
                if kind == "SPLIT":
                    delta = held * val - held
                    add_line(team, "SPLIT", tk, r6(delta), None, src,
                             f"Amendment 2: {val}:1 split effective {ed}; shares held before effective date {held}; cost basis unchanged",
                             shares_delta=delta, event_date=ed.isoformat(), split_ratio=val)
                else:
                    amt = held * val
                    add_line(team, "DIVIDEND", tk, held, val, src,
                             f"Amendment 2: cash dividend {val}/sh on ex-date {ed} x {held} sh held at prior close",
                             cash_delta=amt, event_date=ed.isoformat())
                booked.add((team, tk, kind, ed.isoformat()))
                ca_count += 1
    log(f"corporate actions booked: {ca_count}")

    # ---- proposals
    overrides = load_overrides(repo)
    done_ids = {l.get("proposal_id") for l in ledger if l.get("proposal_id")}
    for raw in load_jsonl(os.path.join(repo, "decisions.jsonl")):
        done_ids.add(raw.get("proposal_id"))
    props = load_proposals(repo, inbox)
    decisions, deferred, seen = [], [], set()
    file_status = {}  # path -> list of outcomes (processed vs deferred)

    def decide(p, origin, status, reasons, **extra):
        d = {"timestamp": ts_now, "run_id": run_id, "proposal_id": (p or {}).get("proposal_id"),
             "team": origin["team_dir"], "status": status, "reasons": reasons,
             "decided_by": PO_BOT, "source": origin["path"]}
        d.update(extra)
        decisions.append(d)
        file_status.setdefault(origin["path"], []).append(status)
        log(f"{status.upper():9s} {d['team']} {d['proposal_id']}: {'; '.join(reasons) if reasons else 'ok'}")

    def sleeve_value(team):
        v = st[team]["cash"]
        for tk, pos in st[team]["pos"].items():
            v += pos["shares"] * pricer.price(tk)[0]
        return v

    # TO-rejected proposals parked in inbox/rejected/: log once as to_rejected, never book
    for fp in sorted(glob.glob(os.path.join(inbox, "rejected", "*.json"))):
        try:
            rp_ = rj(fp)
        except Exception:
            continue
        for p_ in (rp_ if isinstance(rp_, list) else [rp_]):
            if isinstance(p_, dict) and p_.get("proposal_id") and p_["proposal_id"] not in done_ids:
                d_ = {"timestamp": ts_now, "run_id": run_id, "proposal_id": p_["proposal_id"], "team": p_.get("team"),
                      "status": "to_rejected", "reasons": [p_.get("team_overseer_rejection_reason", "rejected by Team Overseer")],
                      "decided_by": (p_.get("team_overseer_approval") or {}).get("bot_id") or "Team Overseer", "source": fp}
                decisions.append(d_)
                done_ids.add(p_["proposal_id"])
                log(f"TO_REJECTED {d_['team']} {d_['proposal_id']}: {d_['reasons'][0]} (parked in inbox/rejected, not booked)")

    order = sorted(props, key=lambda x: (x[1]["team_dir"], str((x[0] or {}).get("submitted_at", "")) if isinstance(x[0], dict) else ""))
    for p, origin in order:
        if p is None:
            decide(None, origin, "rejected", [origin["error"]])
            continue
        if isinstance(p, dict) and p.get("proposal_id") in done_ids:
            file_status.setdefault(origin["path"], []).append("already_processed")
            log(f"SKIP      {origin['team_dir']} {p['proposal_id']}: already decided in an earlier run")
            continue
        tod = (p.get("team_overseer_decision") or "approved") if isinstance(p, dict) else "approved"
        if tod == "rejected":
            decide(p, origin, "to_rejected", [f"rejected by Team Overseer: {p.get('team_overseer_rejection_reason', 'no reason given')}"])
            continue
        if isinstance(p, dict) and p.get("status") == "needs_clarification":
            deferred.append(p.get("proposal_id"))
            file_status.setdefault(origin["path"], []).append("deferred")
            log(f"HELD      {origin['team_dir']} {p.get('proposal_id')}: needs clarification - {p.get('clarification_needed', '')}")
            continue
        errs = validate_shape(p, origin, slot, sdate, seen, done_ids)
        if isinstance(p, dict) and p.get("proposal_id"):
            seen.add(p["proposal_id"])
        if errs:
            decide(p, origin, "rejected", errs)
            continue
        ok_now, expired = slot_matches(p, slot, sdate)
        if expired:
            decide(p, origin, "rejected", ["execute_date has passed"])
            continue
        if not ok_now:
            deferred.append(p["proposal_id"])
            file_status.setdefault(origin["path"], []).append("deferred")
            log(f"DEFERRED  {p['team']} {p['proposal_id']} (execute_slot/date not this run)")
            continue
        team, a = p["team"], p["action"]
        chain = {"proposed_by": p["proposed_by"], "team_overseer": p["team_overseer_approval"]["bot_id"],
                 "team_overseer_approved_at": p["team_overseer_approval"]["approved_at"]}
        if a == "ADJUSTMENT":
            tk = p.get("ticker")
            cd, sd, cbd = float(p.get("cash_delta", 0)), float(p.get("shares_delta", 0)), float(p.get("cost_basis_delta", 0))
            held = st[team]["pos"].get(tk, {}).get("shares", 0.0) if tk else 0.0
            reasons = []
            if st[team]["cash"] + cd < -TOL:
                reasons.append("would create negative cash (Amendment 4)")
            if tk and held + sd < -1e-9:
                reasons.append("would create negative shares (no shorting)")
            if reasons:
                decide(p, origin, "rejected", reasons)
                continue
            add_line(team, "ADJUSTMENT", tk, sd, None, p["terms_reference"], p["rationale"], cash_delta=cd,
                     shares_delta=sd, cost_basis_delta=cbd, proposal_id=p["proposal_id"], **chain)
            decide(p, origin, "approved", [])
            continue
        tk = p["ticker"]
        try:
            price, src = pricer.price(tk)
        except Exception as e:
            deferred.append(p["proposal_id"])
            file_status.setdefault(origin["path"], []).append("deferred")
            log(f"DEFERRED  {team} {p['proposal_id']}: no Amendment-1 price available ({e}); stays in inbox")
            continue
        qt = pricer.quote_type(tk)
        if qt in DISALLOWED_QUOTE_TYPES:
            decide(p, origin, "rejected", [f"instrument type {qt} not allowed (no options/futures/indices)"])
            continue
        if p.get("shares") is not None:
            shares = float(p["shares"])
            sizing = "shares"
        else:
            if p.get("whole_shares"):
                shares = float(math.floor(float(p["notional_usd"]) / price))
                sizing = f"notional_usd {p['notional_usd']} (whole shares, floor)"
                if shares < 1:
                    decide(p, origin, "rejected", [f"notional {p['notional_usd']} buys < 1 whole share at {price}"],
                           decision_price=price, price_source=src)
                    continue
            else:
                shares = math.floor(float(p["notional_usd"]) / price * 1e6) / 1e6  # fractional OK, floor to 1e-6
                sizing = f"notional_usd {p['notional_usd']} (fractional, floor 1e-6)"
        cash = st[team]["cash"]
        held = st[team]["pos"].get(tk, {}).get("shares", 0.0)
        reasons, notes = [], []
        if a == "SELL":
            if shares > held + 1e-9:
                reasons.append(f"sell {shares} > held {held} (no shorting)")
        else:
            cost = r2(shares * price)
            if cost > cash + 1e-9:
                if p.get("allow_reduce_shares_to_cash") and sizing == "shares":
                    new_sh = math.floor(cash / price)
                    if new_sh >= 1:
                        notes.append(f"reduced from {shares} to {new_sh} shares to avoid negative cash (proposal permits)")
                        shares = float(new_sh)
                    else:
                        reasons.append(f"cost {cost} exceeds cash {cash}; auto-rejected (Amendment 4)")
                else:
                    reasons.append(f"cost {cost} exceeds cash {cash}; would create negative cash (auto-rejected, Amendment 4)")
            if not reasons:
                sv = sleeve_value(team)
                pos_val = (held + shares) * price
                w = pos_val / sv if sv > 0 else 1
                if w > POSITION_LIMIT + 1e-9:
                    o = overrides.get(p["proposal_id"])
                    if o and w <= float(o.get("max_weight", 1.0)) + 1e-9:
                        notes.append(f"30% limit exceeded ({w:.2%}) under logged PO override")
                    else:
                        reasons.append(f"position would be {w:.2%} of current sleeve value {sv:,.2f} (>30%, no logged PO override; Amendment 4)")
                lim = NAME_LIMITS[team]
                names_after = len(set(st[team]["pos"]) | {tk})
                if lim and names_after > lim:
                    reasons.append(f"{names_after} names would exceed {team} limit of {lim} (Amendment 4)")
        if reasons:
            decide(p, origin, "rejected", reasons, decision_price=price, price_source=src)
            continue
        amount = r2(shares * price)
        extra = dict(proposal_id=p["proposal_id"], sizing=sizing, **chain)
        if p["proposal_id"] in overrides:
            extra["override"] = overrides[p["proposal_id"]]
        if a == "BUY":
            add_line(team, "BUY", tk, r6(shares), price, src, p["rationale"], cash_delta=-amount,
                     shares_delta=shares, cost_basis_delta=amount, **extra)
        else:
            pos = st[team]["pos"][tk]
            cbd = -r2(pos["cost_basis"] * shares / pos["shares"]) if pos["shares"] else 0
            add_line(team, "SELL", tk, r6(shares), price, src, p["rationale"], cash_delta=amount,
                     shares_delta=-shares, cost_basis_delta=cbd, **extra)
        decide(p, origin, "approved", notes, shares=r6(shares), decision_price=price, price_source=src, amount=amount)

    # ---- valuation
    teams_out, positions_all = {}, []
    for t in TEAMS:
        hv, hold = 0.0, []
        for tk, pos in sorted(st[t]["pos"].items()):
            pr, src = pricer.price(tk)
            mv = r2(pos["shares"] * pr)
            hv += pos["shares"] * pr  # unrounded; value rounded once below
            hold.append({"ticker": tk, "shares": pos["shares"], "cost_basis": pos["cost_basis"],
                         "avg_cost": round(pos["cost_basis"] / pos["shares"], 6) if pos["shares"] else None,
                         "last_price": pr, "market_value": mv, "price_source": src})
        value = r2(st[t]["cash"] + hv)
        for h in hold:
            h["weight"] = round(h["market_value"] / value, 6) if value else None
            if h["weight"] and h["weight"] > POSITION_LIMIT:
                log(f"NOTE {t} {h['ticker']} weight {h['weight']:.2%} > 30% via price drift (limit is enforced on proposals)")
            positions_all.append({"team": t, **h})
        teams_out[t] = {"cash": st[t]["cash"], "holdings_value": r2(hv), "value": value, "holdings": hold,
                        "value_raw": st[t]["cash"] + hv}

    # ---- reconciliation (Amendment 3): independent aggregate replay vs team sum
    all_lines = ledger + new_lines
    agg_cash, agg_pos = 0.0, {}
    for ln in all_lines:
        if ln["action"] == "DAY0_INIT":
            agg_cash += float(ln["size"])
            continue
        agg_cash += float(ln.get("cash_delta", 0) or 0)
        if ln.get("ticker") and ln.get("shares_delta"):
            agg_pos[ln["ticker"]] = agg_pos.get(ln["ticker"], 0.0) + float(ln["shares_delta"])
    master_raw = agg_cash + sum(sh * pricer.price(tk)[0] for tk, sh in agg_pos.items() if abs(sh) > 1e-9)
    master_total = r2(master_raw)
    st2, unalloc = replay(all_lines)
    team_sum = r2(sum(v["value"] for v in teams_out.values()))
    # each team value is rounded to cents once, so allow at most 0.5c per team of rounding drift inside TOL
    # compare unrounded sums (cent rounding of each team value is presentation only)
    diff = round(sum(v["value_raw"] for v in teams_out.values()) + unalloc - master_raw, 6)
    log(f"reconcile: sum(team values)={team_sum:,.2f} + unallocated={unalloc:,.2f} vs master={master_total:,.2f} diff={diff}")
    for t in TEAMS:
        if st2[t]["cash"] < -TOL:
            raise ReconcileError(f"{t} cash negative after run")
        if any(p["shares"] < -1e-9 for p in st2[t]["pos"].values()):
            raise ReconcileError(f"{t} has a negative share position")
        if abs(st2[t]["cash"] - st[t]["cash"]) > TOL:
            raise ReconcileError(f"{t} incremental state != full replay")
    if abs(diff) > TOL:
        raise ReconcileError(f"sum(team values)+unallocated {team_sum + unalloc:.2f} != master total {master_total:.2f}")
    summary["reconciliation"] = {"team_sum": team_sum, "unallocated_cash": unalloc, "master_total": master_total,
                                 "diff": diff, "ok": True}

    # ---- benchmark levels
    bench = {}
    for sym in (BENCH, SPX):
        try:
            bench[sym] = pricer.price(sym)
        except Exception as e:
            log(f"WARNING benchmark {sym} unavailable: {e}")
            bench[sym] = (None, None)

    # ---- performance
    hist_dir = os.path.join(repo, "history")
    eod_path = os.path.join(hist_dir, "eod.jsonl")
    eod = load_jsonl(eod_path)
    perf_old = inc
    baseline = (perf_old.get("benchmark_baseline") or {})
    vals = {"account": r2(team_sum + unalloc), **{t: teams_out[t]["value"] for t in TEAMS}}
    new_eod = None
    if not eod:
        # Inception point is fixed by the first 16:30 run on/after inception date
        if slot == "1630":
            if bench[BENCH][0] is None:
                raise RuntimeError("^SP500TR official close unavailable; cannot set inception baseline")
            new_eod = {"date": sdate.isoformat(), "run_id": run_id, "as_of": ts_now, "values": vals,
                       "flows": {k: 0.0 for k in vals}, "index": {k: 1.0 for k in vals},
                       "sp500tr": bench[BENCH][0], "sp500tr_source": bench[BENCH][1], "inception": True}
            baseline = {"date": sdate.isoformat(), "level": bench[BENCH][0], "source": bench[BENCH][1],
                        "status": "official close recorded by the 16:30 ET run",
                        "day0_intraday_level": baseline.get("day0_intraday_level")}
            current = new_eod
        else:
            current = None
    else:
        last = eod[-1]
        idx = {k: last["index"][k] * (vals[k] / last["values"][k]) if last["values"][k] else None for k in vals}
        current = {"date": sdate.isoformat(), "run_id": run_id, "as_of": ts_now, "values": vals,
                   "flows": {k: 0.0 for k in vals}, "index": idx, "sp500tr": bench[BENCH][0],
                   "sp500tr_source": bench[BENCH][1]}
        if slot == "1630" and last["date"] < sdate.isoformat():
            new_eod = current
    keys = ["account"] + TEAMS
    if current is None:
        res = {k: {"returns": {p: None for p in PERIODS + ["since_inception"]},
                   "benchmark_returns": {p: None for p in PERIODS + ["since_inception"]}} for k in keys}
        for k in keys:
            res[k]["returns"]["since_inception"] = 0
            res[k]["benchmark_returns"]["since_inception"] = 0
        perf_note = "inception baseline pending: set by the first 16:30 ET run (official close)"
    else:
        res = compute_performance(eod if eod else [new_eod], current, inception_date, baseline)
        perf_note = None
    perf = {
        "as_of": ts_now, "run_id": run_id, "run_slot": slot, "inception_date": inception_date,
        "benchmark": "S&P 500 total return (^SP500TR)",
        "benchmark_baseline": baseline,
        "benchmark_current": {"level": bench[BENCH][0], "source": bench[BENCH][1]},
        "method": ("Time-weighted return: a value index is chain-linked between end-of-day (16:30 ET) "
                   "valuation points, index_t = index_(t-1) * V_t / (V_(t-1) + external_flows); intra-day runs "
                   "extend the index from the last EOD point. Teams have no external flows after Day 0 "
                   "(Amendment 7), dividends/trades are internal. Period return = index_now / index_ref - 1 "
                   "where ref = last EOD point before the period start (1d: previous session close; MTD/QTD/YTD: "
                   "prior month/quarter/year end, or inception if later). 1y/3y/5y/10y are annualized and null "
                   "until that much history exists; since_inception is always reported (cumulative). Benchmark "
                   "uses ^SP500TR levels at the same reference points. See CONVENTIONS.md."),
        "note": perf_note,
        "account": {"starting_value": MASTER_START, "current_value": vals["account"], **res["account"]},
        "teams": {t: {"starting_value": TEAM_START_CASH, "current_value": vals[t], **res[t]} for t in TEAMS},
    }

    # ---- snapshot
    protocol_text = open(os.path.join(repo, "PROTOCOL.md")).read()
    adopted = {int(n): d for n, d in re.findall(r"^#*\s*Amendment\s+(\d+)\b[^\n]*?adopted (\d{4}-\d{2}-\d{2})", protocol_text, re.M)}
    amends = sorted({int(m) for m in re.findall(r"^#*\s*Amendment\s+(\d+)\b", protocol_text, re.M)})
    snap = {
        "as_of": ts_now, "run_id": run_id, "run_slot": slot, "session_date": sdate.isoformat(),
        "early_close": sess["early_close"], "inception_date": inception_date,
        "total_value": vals["account"], "cash": r2(sum(teams_out[t]["cash"] for t in TEAMS) + unalloc),
        "unallocated_cash": unalloc, "positions": positions_all,
        "teams": {t: {"cash": teams_out[t]["cash"], "holdings_value": teams_out[t]["holdings_value"],
                      "value": teams_out[t]["value"], "names": len(teams_out[t]["holdings"])} for t in TEAMS},
        "sp500_price": bench[SPX][0], "sp500_price_source": bench[SPX][1],
        "sp500_total_return": bench[BENCH][0], "sp500_total_return_source": bench[BENCH][1],
        "amendments_referenced": amends,
        "amendment_notes": amendment_notes(repo, amends, adopted),
        "reconciliation": summary["reconciliation"],
        "ledger_lines": len(all_lines),
    }

    approved = [d for d in decisions if d["status"] == "approved"]
    rejected = [d for d in decisions if d["status"] in ("rejected", "to_rejected")]
    summary.update(status="ok", session_date=sdate.isoformat(), approved=len(approved), rejected=len(rejected),
                   deferred=len(deferred), corporate_actions=ca_count, new_ledger_lines=len(new_lines),
                   total_value=vals["account"],
                   team_values={t: vals[t] for t in TEAMS}, team_cash={t: teams_out[t]["cash"] for t in TEAMS},
                   sp500tr=bench[BENCH][0],
                   decisions=[{k: d.get(k) for k in ("team", "proposal_id", "status", "reasons", "shares",
                                                       "decision_price", "amount")} for d in decisions])
    log(f"approved={len(approved)} rejected={len(rejected)} deferred={len(deferred)} new_lines={len(new_lines)}")

    prices_doc = build_prices_json(pricer, slot, now, sorted({tk for t in TEAMS for tk in st[t]["pos"]}))
    summary["prices_json_tickers"] = len(prices_doc["prices"])

    if args.dry_run:
        summary["would_append_ledger_lines"] = new_lines
        summary["prices_json_preview"] = prices_doc
        log("dry run: no files written, nothing committed, inbox untouched")
        return 0

    # ---- write files
    with open(ledger_path, "a") as f:  # append-only
        for ln in new_lines:
            f.write(json.dumps(ln) + "\n")
    if decisions:
        with open(os.path.join(repo, "decisions.jsonl"), "a") as f:
            for d in decisions:
                f.write(json.dumps(d) + "\n")
    for t in TEAMS:
        td = os.path.join(repo, "teams", t)
        wj(os.path.join(td, "cash.json"), {"cash": teams_out[t]["cash"]})
        wj(os.path.join(td, "value.json"), {"value": teams_out[t]["value"]})
        wj(os.path.join(td, "holdings.json"), teams_out[t]["holdings"])
        rp = os.path.join(td, "proposals.json")
        if os.path.exists(rp) and rj(rp):
            keep = [p for p in rj(rp) if isinstance(p, dict) and p.get("proposal_id") in deferred]
            wj(rp, keep)
    wj(os.path.join(repo, "snapshot_latest.json"), snap)
    arch = os.path.join(repo, "snapshots", f"{sdate.isoformat()}_{slot}.json")
    if os.path.exists(arch) and not args.force:
        raise RuntimeError(f"frozen snapshot {arch} already exists")
    wj(arch, snap)
    wj(os.path.join(repo, "performance.json"), perf)
    wj(os.path.join(repo, "prices.json"), prices_doc)  # Amendment 8(a): display-only, never feeds the ledger
    os.makedirs(hist_dir, exist_ok=True)
    with open(os.path.join(hist_dir, "runs.jsonl"), "a") as f:
        f.write(json.dumps({"run_id": run_id, "as_of": ts_now, "slot": slot, "values": vals,
                            "sp500tr": bench[BENCH][0], "index": (current or {}).get("index"),
                            "hostname": summary.get("hostname")}) + "\n")
    if new_eod:
        with open(eod_path, "a") as f:
            f.write(json.dumps(new_eod) + "\n")
    log_path = os.path.join(repo, "runs", f"{run_id}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as f:
        f.write("\n".join(LOG) + "\n\nSUMMARY " + json.dumps(summary, indent=2) + "\n")
    summary["log"] = log_path

    # ---- git
    if not args.no_git:
        check_host(repo)  # re-check host right before committing
        check_location_identity(repo)  # re-check right before committing (raises GuardError -> exit 3)
        git(repo, "add", "-A")
        msg = f"Run {sdate.isoformat()} {slot[:2]}:{slot[2:]} ET"
        git(repo, "commit", "-q", "-m", msg)
        if not args.no_push:
            for attempt in range(3):
                try:
                    check_incoming(repo)
                except GuardError:
                    # undo only our own unpushed run commit so nothing is pushed or moved; the slot can be re-run
                    git(repo, "reset", "-q", "--hard", "HEAD~1")
                    raise
                git(repo, "pull", "--rebase", "-q", "origin", "main")
                r = subprocess.run(["git", "-C", repo, "push", "-q", "origin", "HEAD:main"], capture_output=True, text=True)
                if r.returncode == 0:
                    break
                time.sleep(5)
            else:
                raise RuntimeError(f"push failed: {r.stderr}")
        summary["commit"] = git(repo, "rev-parse", "HEAD")

    # ---- move processed inbox files (only after successful write/commit)
    proc = os.path.join(inbox, "processed", run_id)
    for path, sts in file_status.items():
        if not path.startswith(os.path.abspath(inbox)) or "deferred" in sts:
            continue
        os.makedirs(proc, exist_ok=True)
        team = os.path.basename(os.path.dirname(path))
        shutil.move(path, os.path.join(proc, f"{team}__{os.path.basename(path)}"))
    if decisions:
        os.makedirs(proc, exist_ok=True)
        wj(os.path.join(proc, "decisions.json"), decisions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
