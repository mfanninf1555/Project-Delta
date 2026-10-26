# Project-Delta Dashboard

`index.html` at the repo root is a single self-contained static page (inline CSS/JS, no build step). With GitHub Pages serving `main` / root it is available at:

**https://mfanninf1555.github.io/Project-Delta/**

`.nojekyll` is present so Pages serves files as-is.

The dashboard is read-only. It never writes to the repository, never trades, and does not change ledger logic. It reads the committed JSON files (relative paths with a cache-busting query string) and reloads them, and tries for live quotes, every 5 minutes while the tab is visible. Auto-refresh pauses while the tab is hidden, and **Refresh** reloads immediately. All fetches use relative URLs resolved against the page's own address, so the page works from any static host or subpath. All displayed times are shown in America/New_York and labelled ET.

**Caching:** GitHub Pages serves files with `Cache-Control: max-age=600` and ignores the `?v=` query string, so after a commit the page can show data up to ~10 minutes old.

## Files read

| File | Fields used |
|---|---|
| `snapshot_latest.json` | `as_of`, `inception_date`, `total_value`, `cash`, `unallocated_cash`, `teams.teamN.cash`, `teams.teamN.value` (fallback when a team file is missing or invalid), `sp500_total_return`, `sp500_total_return_source` (an object with `bar_timestamp`, or a plain string, which is shown as the source label) |
| `performance.json` | `as_of`, `inception_date`, `benchmark`, `benchmark_baseline.level` / `.day0_intraday_level` / `.status`, `method`, `account.starting_value`, `account.current_value`, `account.returns.*`, `account.benchmark_returns.*`, `teams.teamN.returns.*`, `teams.teamN.benchmark_returns.*` |
| `teams/teamN/cash.json` | `cash` |
| `teams/teamN/value.json` | `value` |
| `teams/teamN/holdings.json` | see below |
| `prices.json` (optional) | see below |
| `ledger.jsonl` | Realized Positions and Dividends tabs only (see below) |

### Performance periods

Keys read from `returns` / `benchmark_returns`: `1d`, `mtd`, `qtd`, `ytd`, `1y`, `3y`, `5y`, `10y`, `since_inception`.
A `null` or missing value shows as `—`. Values are treated as **decimal fractions** (`0.0525` = +5.25%). If percent units are ever used instead, add `"returns_unit": "percent"` at the top level of `performance.json` and the dashboard will divide by 100.
For the account and for each team, the page shows the return, the S&P 500 TR return (`benchmark_returns`), and the Difference for each period.

## Holdings JSON shape (expected)

Day-0 files are an empty array `[]`, which is valid: the team shows as 100% cash.

Once positions exist, each `teams/teamN/holdings.json` should be an array of objects:

```json
[
  {
    "ticker": "AAPL",
    "shares": 100,
    "cost_basis": 21050.00,
    "avg_cost": 210.50,
    "last_price": 212.30,
    "price_source": "Yahoo Finance AAPL official close 2026-10-21",
    "as_of": "2026-10-21T20:30:00Z"
  }
]
```

| Field | Required | Meaning |
|---|---|---|
| `ticker` | yes | Symbol as used on Yahoo Finance (e.g. `BRK-B`). |
| `shares` | yes | Current share count, adjusted for splits (Amendment 2). |
| `cost_basis` | **recommended** | Total dollars paid for the current position (sum of fills at the logged decision prices; split-adjusted, per Amendment 2). |
| `avg_cost` | optional | `cost_basis / shares`. Derived when it is missing. |
| `last_price` | recommended | Last-committed mark price from the most recent run (Amendment 1 price rule). |
| `price_source`, `as_of` | optional | Informational. |

The names follow the ledger's existing vocabulary (`ticker`, `size`, `decision_price`, `price_source`, `timestamp` in `ledger.jsonl`), so the dashboard also accepts these alternates:

- shares: `shares` \| `quantity` \| `qty` \| `size`
- ticker: `ticker` \| `symbol`
- committed price: `last_price` \| `price` \| `committed_price` \| `current_price` \| `mark_price` \| `close` \| `last_close` (or `market_value`/`value` ÷ shares)
- cost basis: `cost_basis` \| `total_cost` \| `cost`
- avg cost: `avg_cost` \| `average_cost` \| `avg_price` \| `cost_per_share`
- entry price: `entry_price` \| `fill_price` \| `decision_price` \| `purchase_price` \| `buy_price`

The file may also be `{"holdings": [...]}` or an object keyed by ticker (`{"AAPL": {"shares": 100, ...}}`).

### How cost basis is resolved (backward compatible)

1. `cost_basis` if present.
2. Otherwise `shares × avg_cost`.
3. Otherwise `shares × entry price` (e.g. `decision_price`).
4. Otherwise shown as `—`.

`avg_cost` = `cost_basis / shares` when it is missing. A derived cost basis is marked with `*` in the table.

## Prices and valuation

For each holding, the price comes from the first of these that is available:

1. **Live quote**: fetched in the browser this cycle, and newer than the committed price. It is badged **live** while its own quote time is under 15 minutes old, and **delayed** after that. Every quote expires 15 minutes after it was fetched, and any symbol that a refresh cycle fails to fetch loses its live price straight away, so a failed refresh never leaves a stale **live** badge. The tooltip and the cell show the quote time and source.
2. **Committed price**: the newer of the holding's own `last_price` (time = the holding's `as_of`, else the file-level `as_of`, else `snapshot_latest.json` `as_of`) and the matching `prices.json` entry. Badged **committed**, with its as_of time shown.
3. **Cost basis**: if no price is available, the holding is valued at cost (G/L 0), badged **at cost**, and footnoted with †. This matches how team `value.json` handles unpriced positions.
4. With neither price nor cost, the holding is badged **excluded** and left out of cost, value and G/L, so subtotals always reconcile.

A holding with a price but no cost data counts in value only. The amount involved is footnoted.

The headline value card counts every price source actually used in the total, e.g. "6 of 7 priced from live/delayed quotes (4 live, 2 delayed), 1 committed", plus any "at cost" or "excluded" holdings.

**Future-dated timestamps:** a committed as_of (a holding's `as_of`, `snapshot_latest.json` `as_of` or its S&P TR timestamp, or a `prices.json` as_of) more than 5 minutes ahead of the current time would block every live quote. Such a timestamp can't be trusted for the "newer than committed" test, so a live quote overrides it only if the quote time is known and falls on or after the 09:30 ET open of the most recent regular session. That is today's open on a weekday after 09:30 ET, and otherwise the previous weekday's open. NYSE holidays are not modelled, which only makes the rule stricter. So during the session the current quote wins, after hours the closing quote still wins, and an older quote (e.g. 26 hours old) is rejected. The same rule applies to the S&P card. A yellow warning names the field and its value so the writer's clock or time zone can be fixed. The S&P card shows the **S&P 500 total return index** (`sp500_total_return`, and `^SP500TR` live) and its change against `performance.json` `benchmark_baseline.level`, or `day0_intraday_level` while the official baseline is pending.

## Optional `prices.json` (committed marks)

Yahoo sends no CORS headers, and public proxies are unreliable, so the dependable way to show fresher prices is a committed file at the repo root. Since Amendment 8 the Project Overseer writes it (see below):

```json
{
  "as_of": "2026-10-21T20:30:00Z",
  "source": "Yahoo Finance official close",
  "prices": {
    "AAPL": { "price": 230.00, "as_of": "2026-10-21T20:30:00Z" },
    "KO":   { "price": 62.00 }
  },
  "sp500_total_return": { "level": 17402.11, "as_of": "2026-10-21T20:30:00Z" }
}
```

- `prices.TICKER.price` is required. `prices.TICKER.as_of` is optional and defaults to the top-level `as_of`. Ticker matching ignores case. A bare number (`"KO": 62.0`) is also accepted.
- `sp500_total_return` may be an object (`level`, optional `as_of`) or a number (uses the top-level `as_of`).
- A `prices.json` price is used only when it is **newer** than the holding's own `last_price`. The same rule applies to the benchmark level versus `snapshot_latest.json`.
- Under Amendment 8 the file should always be present. If it is ever absent (HTTP 404), it is ignored quietly, apart from the browser's own network log line. If it is present but fails to load or parse, an error is shown, the footer says "prices.json failed to load/parse", and the last good copy from the session is used if there is one.
- Like every other file, it is read-only to the dashboard and not part of the ledger.

### Writer: Project Overseer under Amendment 8 (implemented)

**Amendment 8** (adopted 2026-10-20 and approved by Matt; commit `0de255b`) adopted Option A from the earlier proposal:

- The Project Overseer writes `prices.json` at the repo root on every scheduled run (09:00 / 12:30 / 16:30 ET), using the Amendment 1 price rule for that slot, and commits it with the snapshot.
- The file covers every held ticker across all teams, as Yahoo symbols, plus the `^SP500TR` level. `as_of` is the actual bar time in UTC (`Z`).
- `prices.json` is display-only and never feeds the ledger.
- The Day-0 file is committed with an empty `prices` object and an `sp500_total_return` level. The dashboard handles that without errors: no holdings are affected, and the S&P card uses its level because it is newer than the snapshot's.
- Option B (a GitHub Actions/cron price committer) was **not adopted**. There is no workflow in this repo.
- Under Amendment 8(b), dashboard-only files (`index.html`, `DASHBOARD.md`, `.nojekyll`, dashboard-only static assets) may be committed directly to `main` by the Project Delta Engineer bot. Everything else, including `prices.json`, stays with the Project Overseer.

**Precedence note:** the Overseer writes holdings (`last_price`, no per-holding `as_of`) and `prices.json` in the same run. Holdings therefore take the snapshot `as_of` (the run time, e.g. 4:35 PM ET), which is a little later than the `prices.json` bar time (e.g. the 4:00 PM ET close). When the two prices are the same (relative difference ≤ 1e-6), the dashboard shows the `prices.json` entry and its **bar time**, tagged "· prices.json", because that is when the price was actually set. When they differ, the newer one wins as before. The S&P card follows the same rule: if the `prices.json` `sp500_total_return` level equals the snapshot level (or is newer), its `as_of` is shown as the committed time. `sp500_total_return_source` may be a plain string with no `bar_timestamp`, in which case the snapshot `as_of` is the snapshot's time.

## Display rules

- **Zeros:** any dollar amount with |x| < $0.005 is shown as `$0.00`, and any percentage that rounds to 0.00% is shown as `0.00%`: no sign and neutral colour (never `-$0.00` or a red/green zero).
- **Fetched vs used quotes:** the price-status line says how many quotes were fetched and how many are actually in use, e.g. "99 of 99 quotes fetched …; 0 of 99 fetched quotes in use (99 not newer than the committed marks, so committed prices are shown)". After the close, quotes stamped at the 4:00 PM bar are not newer than the committed `prices.json` marks, so they are fetched but not used.

## Tabs: Overview, Realized Positions, Dividends

The page has three tabs. The URL hash selects the tab, so each tab can be linked: `#overview` (default; the dashboard described above, unchanged), `#realized` and `#dividends`. Warnings, errors and the footer are shown on every tab.

The two ledger tabs read **only** `ledger.jsonl` (fetched with a relative URL, like the other files). It is parsed line by line: blank lines are skipped, and a line that is not valid JSON is skipped and counted, with a yellow warning naming the line numbers. A 404 or failed fetch is reported in the red box like any other file, and the tabs say "ledger.jsonl unavailable". The last good copy from the session is kept on a later failure. The dashboard never writes the ledger.

**Filters (both tabs):** a **Team** dropdown (All teams / Team 1-4) and a **Ticker** box (case-insensitive substring, so `ap` matches `APA` and `AAPL`). The filters combine (AND). Totals are computed over the filtered rows only, and the tab shows "Showing X of Y …" when a filter is active. If nothing matches, the tab says so ("No sales match the filter …"). Each tab has its own filters. They are kept in the hash, e.g. `#realized?team=team3&ticker=ap&order=newest`, so filtered views can be linked. Switching tabs keeps each tab's filters.

**Order:** rows are sorted by date (first column), **oldest first (chronological) by default**. The clearly labelled **Order** button switches to newest first (`order=newest` in the hash). Every row has a visible **Team** column, including in the stacked mobile card layout (≤ 640 px), and the full team name is in its tooltip.

### Realized Positions

Rows: ledger lines with `action == "SELL"`. If there are none, the tab says **"No sales yet."**

| Column | Source |
|---|---|
| Sale date (ET) | `timestamp`, plus `event_date` if present |
| Team | `team` |
| Ticker | `ticker` |
| Shares sold | `size`, else `abs(shares_delta)` |
| Sale price | `decision_price` (tooltip: `price_source`) |
| Proceeds | `cash_delta` |
| Cost basis removed | `abs(cost_basis_delta)` (`scripts/run.py` removes the pro-rata average cost: `cost_basis × shares_sold / shares_held`) |
| Realized G/L $ | `cash_delta + cost_basis_delta` (proceeds − cost removed) |
| Realized G/L % | G/L $ ÷ cost basis removed |
| Note | `rationale`, truncated to 60 characters; click to expand |

A footer row totals the dollar amounts: proceeds, cost removed, G/L $ and the G/L % on the total cost removed.

### Dividends

**How dividends are actually recorded** (from `scripts/run.py`, `CONVENTIONS.md` and Amendment 2; the dashboard does not change any of this):

- A cash dividend is booked as one ledger line with `action: "DIVIDEND"`, credited to **team cash on the ex-date** (Amendment 2). It is booked once per (team, ticker, ex-date).
- `size` = shares held at the prior close (a ledger replay before 00:00 ET on the ex-date), `decision_price` = the per-share amount, `cash_delta` = the total cash, `event_date` = the **ex-date**, `timestamp` = when the run booked it, and `price_source` = the data source.
- **No pay date is stored.** The Date column therefore shows the ex-date (`event_date`) and says so. It would show `pay_date` first if that field were ever added, and falls back to `timestamp` ("booked") if neither is present.
- **There is no dividend reinvestment (DRIP).** `run.py` has no reinvest path. Dividends stay as cash, and any later purchase is an ordinary `BUY` decided by the team. So every current row shows **Reinvested: No (cash)**, with `—` for shares purchased and cost.

| Column | Source |
|---|---|
| Pay / ex-date | `pay_date`, else `event_date` (ex-date), else `timestamp`; the source is labelled under the date, along with the booking time |
| Team, Ticker | `team`, `ticker` |
| Shares held | `shares_held` or `size`; if missing, derived by replaying BUY/SELL/ADJUSTMENT/SPLIT lines before the ex-date (marked "derived") |
| Amount / share | `amount_per_share`, `per_share`, `dividend_per_share` or `decision_price`; if missing, `cash_delta ÷ shares held` (marked "derived") |
| Total cash | `cash_delta` |
| Reinvested | **Yes** only with an explicit ledger link (below), otherwise **No (cash)** |
| Shares purchased / Cost of reinvested shares | the sum over the explicitly linked BUY lines of `shares_delta` (else `size`) / `abs(cash_delta)` (else `cost_basis_delta`); with no linked BUY, `reinvest_shares` / `reinvest_cost` on the DIVIDEND line |

A total row sums the total cash (and reinvested cost) for the filtered rows.

**Explicit reinvestment links** (none exist today; listed so that a future writer has a contract). A dividend counts as reinvested only if:

- the DIVIDEND line itself has `reinvested`, `reinvest`, `drip` or `dividend_reinvestment` set to `true`, or carries `reinvest_shares` / `reinvested_shares`; or
- a `BUY` line for the same team and ticker has `dividend_ref` / `reinvest_of` / `drip_of` / `dividend_id` equal to the dividend's `id`, `timestamp`, `event_date` or `"team:TICKER:event_date"`; or
- a `BUY` line for the same team and ticker has one of the flags above set to `true` **and** `dividend_event_date` equal to the dividend's `event_date`.

A `BUY` near the dividend in time or amount is **never** treated as a reinvestment.

## Load failures and reconciliation

- Every file fetch tells apart "missing" (404), "failed" (network/HTTP error, empty file, or invalid JSON) and "ok". Problems are listed in a red box.
- `holdings.json` is checked strictly: `[]` means 100% cash. A missing or failed file keeps the last good holdings from earlier in the session (with a warning). If there are none, the team is shown at its committed `value.json` value (else the snapshot value), and its holdings are marked unavailable.
- If `cash.json` or `value.json` fails, the snapshot's `teams.teamN.cash` / `.value` is used, with a warning.
- **Amendment 3 check:** "Project Overseer reconciles the sum of team values + any unallocated cash to the master total on every run." The page checks Σ `teams/teamN/value.json` + `unallocated_cash` = `snapshot_latest.json` `total_value` within a $1.00 tolerance, and shows ✓ or a yellow warning. It also warns when a team's `value.json` differs from the snapshot's team value, when Σ team cash + unallocated ≠ snapshot `cash`, or when cash + holdings at their committed prices (unpriced at cost) differ from `value.json`.

## Live quotes (best effort)

- **Batched requests:** the Yahoo spark endpoint `https://query1.finance.yahoo.com/v7/finance/spark?symbols=A,B,^SP500TR&range=1d&interval=1d` (`meta.regularMarketPrice`, `meta.regularMarketTime`) takes at most 20 symbols per request; Yahoo rejects 21 or more. Symbols are sent in chunks of 20, one after another: about 89 tickers plus `^SP500TR` is 5 requests per 5-minute cycle.
  - The first chunk is a probe. If it fails, that source is abandoned after a single request.
  - If a later chunk fails, the quotes already fetched are kept, and the missing symbols fall back to committed prices.
- **Per-symbol fallback:** the chart endpoint `v8/finance/chart/SYM` is used only when there are 25 symbols or fewer. It probes one symbol first and fans out only if that works. With larger portfolios it is skipped, to avoid about 90 requests per cycle.
- **Sources:** direct, then api.allorigins.win, api.codetabs.com and corsproxy.io (which now requires an API key). The chain stops at the first method that returns quotes, and the working method is remembered in `localStorage` and tried first next time.
- **Back-off:** with about 90 symbols, a cycle where every source fails costs 4 requests (one probe per source). After any fully failed cycle, automatic live attempts back off for 5, 10, 20, 40, then 60 minutes. Committed files still reload every 5 minutes, and **Refresh** always retries straight away.
- **Display only:** live quotes never feed the ledger. Official marks remain those committed under Amendment 1.
