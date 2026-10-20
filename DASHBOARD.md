# Project-Delta Dashboard

`index.html` at the repo root is a single self-contained static page (inline CSS/JS, no build step). With GitHub Pages serving `main` / root it is available at:

**https://mfanninf1555.github.io/Project-Delta/**

`.nojekyll` is present so Pages serves files as-is.

The dashboard is read-only. It never writes to the repository, never trades, and does not change ledger logic. It reads the committed JSON files (relative paths with a cache-busting query string) and reloads them, and tries for live quotes, every 5 minutes while the tab is visible. Auto-refresh pauses while the tab is hidden, and **Refresh** reloads immediately. All fetches use relative URLs resolved against the page's own address, so the page works from any static host or subpath. All displayed times are shown in America/New_York and labelled ET.

**Caching:** GitHub Pages serves files with `Cache-Control: max-age=600` and ignores the `?v=` query string, so after a commit the page can show data up to ~10 minutes old.

## Files read

| File | Fields used |
|---|---|
| `snapshot_latest.json` | `as_of`, `inception_date`, `total_value`, `cash`, `unallocated_cash`, `teams.teamN.cash`, `teams.teamN.value` (fallback when a team file is missing or invalid), `sp500_total_return`, `sp500_total_return_source.bar_timestamp` |
| `performance.json` | `as_of`, `inception_date`, `benchmark`, `benchmark_baseline.level` / `.day0_intraday_level` / `.status`, `method`, `account.starting_value`, `account.current_value`, `account.returns.*`, `account.benchmark_returns.*`, `teams.teamN.returns.*`, `teams.teamN.benchmark_returns.*` |
| `teams/teamN/cash.json` | `cash` |
| `teams/teamN/value.json` | `value` |
| `teams/teamN/holdings.json` | see below |
| `prices.json` (optional) | see below |

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

**Future-dated timestamps:** a committed as_of (a holding's `as_of`, `snapshot_latest.json` `as_of` or its S&P TR timestamp, or a `prices.json` as_of) more than 5 minutes ahead of the current time would block every live quote. When comparing live with committed, such a timestamp is treated as "now" and any fresh live quote fetched in the current cycle wins over it. A yellow warning names the field and its value so the writer's clock or time zone can be fixed. The S&P card shows the **S&P 500 total return index** (`sp500_total_return`, and `^SP500TR` live) and its change against `performance.json` `benchmark_baseline.level`, or `day0_intraday_level` while the official baseline is pending.

## Optional `prices.json` (committed marks)

Yahoo sends no CORS headers, and public proxies are unreliable, so the dependable way to show fresher prices is a committed file at the repo root:

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
- If `prices.json` is absent (HTTP 404, the expected state until a writer is approved), it is ignored quietly, apart from the browser's own network log line. If it is present but fails to load or parse, an error is shown, the footer says "prices.json failed to load/parse", and the last good copy from the session is used if there is one.
- Like every other file, it is read-only to the dashboard and not part of the ledger.

### Proposal only: automated price commits (NOT implemented)

> Requires Matt's approval and a protocol amendment. Under Amendment 6 the Project Overseer is the only bot that commits to `main`, so no workflow or scheduled committer has been added.

Option A, preferred: the Project Overseer writes `prices.json` in each of its three scheduled runs (09:00 / 12:30 / 16:30 ET), using the Amendment 1 price rule, and commits it with the snapshot. This needs no new committer.

Option B: a GitHub Actions workflow (e.g. a `*/15 13-21 * * 1-5` cron during NYSE hours) fetches the Yahoo chart endpoint server-side, where CORS does not apply, and commits only `prices.json`, or publishes it as a Pages artifact without committing. Because this adds a second committer or automation, it needs a new dated amendment in PROTOCOL.md first.

## Load failures and reconciliation

- Every file fetch tells apart "missing" (404), "failed" (network/HTTP error, empty file, or invalid JSON) and "ok". Problems are listed in a red box.
- `holdings.json` is checked strictly: `[]` means 100% cash. A missing or failed file keeps the last good holdings from earlier in the session (with a warning). If there are none, the team is shown at its committed `value.json` value (else the snapshot value), and its holdings are marked unavailable.
- If `cash.json` or `value.json` fails, the snapshot's `teams.teamN.cash` / `.value` is used, with a warning.
- **Amendment 3 check:** "Project Overseer reconciles the sum of team values + any unallocated cash to the master total on every run." The page checks Σ `teams/teamN/value.json` + `unallocated_cash` = `snapshot_latest.json` `total_value` within a $1.00 tolerance, and shows ✓ or a yellow warning. It also warns when a team's `value.json` differs from the snapshot's team value, when Σ team cash + unallocated ≠ snapshot `cash`, or when cash + holdings at their committed prices (unpriced at cost) differ from `value.json`.

## Live quotes (best effort)

- **Batched requests:** one request per refresh for up to 20 symbols, via the Yahoo spark endpoint `https://query1.finance.yahoo.com/v7/finance/spark?symbols=A,B,^SP500TR&range=1d&interval=1d` (`meta.regularMarketPrice`, `meta.regularMarketTime`). Fallback is the per-symbol chart endpoint `v8/finance/chart/SYM`; it probes one symbol first and fans out only if that works.
- **Sources:** direct, then api.allorigins.win, api.codetabs.com and corsproxy.io (which now requires an API key). The chain stops at the first method that returns quotes, and the working method is remembered in `localStorage` and tried first next time.
- **Back-off:** if every source fails, automatic live attempts back off for 5, 10, 20, 40, then 60 minutes. Committed files still reload every 5 minutes, and **Refresh** always retries straight away.
- **Display only:** live quotes never feed the ledger. Official marks remain those committed under Amendment 1.
