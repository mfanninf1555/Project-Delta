# Project-Delta Dashboard

`index.html` at the repo root is a single self-contained static page (inline CSS/JS, no build step). With GitHub Pages serving `main` / root it is available at:

**https://mfanninf1555.github.io/Project-Delta/**

`.nojekyll` is present so Pages serves files as-is.

The dashboard is read-only. It never writes to the repository, never trades, and does not change ledger logic. It reads the committed JSON files (relative paths with a cache-busting query string) and refreshes them, plus live quotes, every 60 seconds while the page is open (or on **Refresh**).

## Files read

| File | Fields used |
|---|---|
| `snapshot_latest.json` | `as_of`, `inception_date`, `total_value`, `cash`, `unallocated_cash`, `teams.teamN.cash`, `teams.teamN.value` (fallback when a team file is missing), `sp500_price` |
| `performance.json` | `as_of`, `inception_date`, `benchmark`, `benchmark_baseline.status`, `method`, `account.starting_value`, `account.current_value`, `account.returns.*`, `account.benchmark_returns.*`, `teams.teamN.returns.*` |
| `teams/teamN/cash.json` | `cash` |
| `teams/teamN/value.json` | `value` |
| `teams/teamN/holdings.json` | see below |

### Performance periods

Keys read from `returns` / `benchmark_returns`: `1d`, `mtd`, `qtd`, `ytd`, `1y`, `3y`, `5y`, `10y`, `since_inception`.
A `null` or missing value shows as `—`. Values are treated as **decimal fractions** (`0.0525` = +5.25%). If percent units are ever used instead, add `"returns_unit": "percent"` at the top level of `performance.json` and the dashboard will divide by 100.
The page shows Portfolio, S&P 500, and the Difference (portfolio − benchmark) for each period, plus a per-team row.

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
4. Otherwise shown as `—` and left out of gain/loss totals.

`avg_cost` = `cost_basis / shares` when it is missing. A derived cost basis is marked with `*` in the table.

## Live prices

On load and every 60 s the browser requests `https://query1.finance.yahoo.com/v8/finance/chart/<TICKER>?range=1d&interval=1d` and reads `chart.result[0].meta.regularMarketPrice`, for each holding plus `^GSPC`. Fallback chain, 6 s timeout each: Yahoo direct (query1) → Yahoo direct (query2) → api.allorigins.win → api.codetabs.com → corsproxy.io.
Yahoo sends no CORS headers, so browsers usually block the direct calls, and the free public proxies are best-effort and may be rate-limited or down. When every source fails, the row uses the last-committed price and is badged **committed**. Rows priced live are badged **live**, and the footer shows the last-refresh time and the number of quotes fetched.
Live prices are display-only and never feed the ledger. Official marks remain those committed by the Project Overseer under Amendment 1.
