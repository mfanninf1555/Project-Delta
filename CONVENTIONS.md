# Project-Delta conventions

This document records operating conventions for the Project Overseer tooling. **It does not change
PROTOCOL.md.** Protocol changes require an owner-written dated amendment (Amendment 7). Where the
protocol is silent or ambiguous, the reading used by the tooling is stated in this file under
"Interpretations".

## Roles and bot IDs
- Project Overseer (PO): `project-overseer-delta`. It is the only bot that writes ledger lines and commits to `main` (Amendment 6).
- Team Overseers (TO), for example `Team 2 Overseer - Delta`. Investment Managers (IM), for example `T2-M1 Quality Compounders - Delta`.
- Approval chain: IM proposes, TO approves, PO approves at the run. The PO's approval is the run itself: every booked line carries `approving_bot: project-overseer-delta`.

## Running
```
/home/box/project-delta/repo/scripts/run_slot.sh 0900   # 09:00 ET
/home/box/project-delta/repo/scripts/run_slot.sh 1230   # 12:30 ET
/home/box/project-delta/repo/scripts/run_slot.sh 1630   # 16:30 ET
# same thing: /home/box/project-delta/venv/bin/python scripts/run.py --slot 1630 [--dry-run]
```
- Venv: `/home/box/project-delta/venv`. Pinned dependencies are in `requirements.txt`.
- `--dry-run` computes and validates everything. It writes nothing to the repo, makes no commit, and leaves the inbox untouched. If a 1630 dry-run happens before the official close, it uses the latest regular-session quotes and marks them **INDICATIVE**.
- Test-only flags: `--now ISO`, `--repo DIR`, `--inbox DIR`, `--no-git`, `--no-push`, `--force`.
- Exit codes: `0` means OK, no session, or already done. `2` means reconciliation failed: the run log is written and nothing is committed. `3` means any other error, for example a missing price. A non-zero exit leaves the inbox untouched, so the slot can be re-run.
- The script is idempotent per slot. If `snapshots/YYYY-MM-DD_HHMM.json` already exists, the run exits with `already_done`.
- Commit message: `Run YYYY-MM-DD HH:MM ET`. The script runs `git pull --rebase` before the run and again before the push.

## Market calendar (Amendment 5)
- The NYSE calendar comes from `pandas_market_calendars`. On a day without a session the script prints `{"status":"no_session"}`, exits 0 and changes nothing, so the prior snapshot stays current.
- Early-close days: the 16:30 run still fires and uses the official early close.

## Prices (Amendment 1)
| Slot | Price used for fills and marks |
|---|---|
| 0900 | Official close of the previous NYSE session (daily `Close`, `auto_adjust=False`) |
| 1230 | Latest regular-session quote at decision time: the close of the last 1-minute bar at or before the run time, regular session only |
| 1630 | Official close of today's session (daily `Close`). If the close is not published yet, the run retries every 60 s for up to 20 min, then fails without committing |

- Source: Yahoo Finance through `yfinance`. If yfinance is rate-limited or fails, the script calls the Yahoo Finance chart API directly (`https://query1.finance.yahoo.com/v8/finance/chart/<T>`), which is the same Yahoo data.
- Each `price_source` records the provider, ticker, field, and the bar date or timestamp, plus the Yahoo URL.
- There are no commissions and no slippage. Fills happen at exactly the logged price.
- Benchmark: `^SP500TR` (S&P 500 total return). `^GSPC` is recorded only as information (`sp500_price`).

## Proposal handoff (inbox)
Team Overseers cannot push. They drop TO-approved proposals into the shared box inbox:
```
/home/box/project-delta/inbox/team1/   team2/   team3/   team4/      (world-writable)
/home/box/project-delta/inbox/processed/<run_id>/                    (archive + decisions.json)
```
- Use one JSON file per proposal (a JSON list of proposals is also accepted). Write the file to `name.json.tmp`, then rename it to `name.json`. Only `*.json` files are read, so the run never picks up a half-written file.
- The script also reads `teams/teamN/proposals.json` in the repo. That file normally stays `[]`. TOs should use the inbox.
- After a successful run, processed files move to `inbox/processed/<run_id>/teamN__<file>`, together with `decisions.json`. Proposals for a later slot or date, held proposals, and proposals with no price stay in the inbox.
- Every decision is appended to `decisions.jsonl` in the repo: approved, rejected, or `to_rejected`.

### Proposal file format
```json
{
  "proposal_id": "team2-20261020-MSFT",          // unique, required
  "team": "team2",                               // must match the inbox folder
  "action": "BUY",                               // BUY | SELL | ADJUSTMENT
  "ticker": "MSFT",                              // Yahoo symbol (e.g. BRK-B)
  "shares": 35,                                  // EITHER shares (fixed count; fractional allowed)
  "notional_usd": null,                          // OR notional_usd (dollar amount)
  "whole_shares": false,                         // notional only: true => floor(notional/price) whole shares
  "allow_reduce_shares_to_cash": true,           // shares only: trim to floor(cash/price) instead of reject
  "rationale": "why (required)",
  "proposed_by": "T2-M1 Quality Compounders - Delta",
  "team_overseer_approval": {"bot_id": "Team 2 Overseer - Delta", "approved_at": "2026-10-20T12:48:00-04:00"},
  "execute_date": "2026-10-20",                  // optional; earlier dates expire (rejected), later dates wait
  "execute_slot": "1630",                        // optional: 0900 | 1230 | 1630 | any
  "rule_id": "T3 rulebook v1: ...",              // REQUIRED for team3 (rules-based mandate)
  "submitted_at": "2026-10-20T12:47:00-04:00"
  // any extra fields (risks, sources, indicative_price, original_file, ...) are preserved in processed/
}
```
- Notional orders without `whole_shares` buy fractional shares, `floor(notional / fill_price)` to 6 decimals. Notional orders with `whole_shares: true` buy whole shares, `floor(notional / fill_price)`. If that is less than 1 share, the proposal is rejected.
- TO-rejected proposals are parked in `/home/box/project-delta/inbox/rejected/`. They are never booked. The next run logs each one once as `to_rejected`, in the run log and in `decisions.jsonl`.
- `"team_overseer_decision": "rejected"` (with `team_overseer_rejection_reason`) records a TO rejection in the run log and in `decisions.jsonl` as `to_rejected`. It never produces a ledger line.
- `"status": "needs_clarification"` holds a proposal in the inbox. It is logged and not booked.
- ADJUSTMENT proposals cover mergers, spin-offs and delistings (Amendment 2). They carry `cash_delta`, `shares_delta`, `cost_basis_delta` and `terms_reference`, which must be the official terms URL. The TO proposes them and the PO writes them.

### Automatic checks at each run (Amendment 4 and scope)
These checks run in order inside each team, against the team state as updated by earlier fills in the same run:
1. Shape: required fields, a valid ticker (plain stock or ETF symbols only, so no options, futures or indices), and the TO approval present and from a bot other than the proposer.
2. Negative cash: a BUY that costs more than team cash is auto-rejected. If `allow_reduce_shares_to_cash` is set, it is trimmed instead.
3. No shorting: a SELL larger than the shares held is rejected. Leverage is impossible because cash can never go below 0. Options are rejected.
4. 30% position limit: `(held + new shares) × decision price / current sleeve value`, where sleeve value = cash + all holdings marked at the same slot prices. More than 30% is rejected unless `overrides.jsonl` has a PO override for that `proposal_id`.
5. Name limits after the trade: Team 1 ≤ 8, Team 2 ≤ 12, Team 4 ≤ 10. Team 3 has no hard limit but must cite `rule_id`.

### PO override (Amendment 4)
To grant an override, append one line to `overrides.jsonl` and commit it. The override is copied into the ledger line it authorises.
```json
{"proposal_id": "team1-20261021-XYZ", "granted_by": "project-overseer-delta", "granted_at": "2026-10-21T08:55:00-04:00", "max_weight": 0.35, "reason": "..."}
```

## Ledger (`ledger.jsonl`, append-only)
- One JSON object per line. Lines are never edited or deleted. Corrections are new `ADJUSTMENT` lines.
- Line 1 is the Day-0 line: `action: DAY0_INIT`, `size: 1000000`, read as 4 × 250000 team sleeves and 0 unallocated (Amendment 3).
- Fields: `timestamp` (UTC, Z), `bot_id`, `action`, `ticker`, `size`, `decision_price`, `price_source`, `rationale`, `approving_bot`, `team`, `run_id`, `cash_delta`, `shares_delta`, `cost_basis_delta`.
- Trade lines also carry `proposal_id`, `sizing`, `proposed_by`, `team_overseer`, `team_overseer_approved_at`, and `override` when one applies.
- Actions:
  - `BUY` and `SELL`: `size` is shares.
  - `DIVIDEND`: `size` is the shares held, `decision_price` is the per-share amount, and `event_date` is the ex-date.
  - `SPLIT`: `size` is the change in shares, plus `split_ratio` and `event_date`.
  - `ADJUSTMENT`.
- Cash amounts are rounded to cents per line. Shares are kept to 6 decimals.
- The ledger is the source of record. Team files must equal a full replay of the ledger at the start of every run, otherwise the run fails reconciliation.

## Dividends and splits (Amendment 2)
- On every run, the script checks the Yahoo dividend and split series for each ticker the team has held, over the last 30 days (never before inception).
- A dividend is booked to team cash on or after its ex-date. The amount is shares held at the prior close (ledger lines before 00:00 ET on the ex-date) × the per-share amount.
- A split adjusts the share count for shares held before the effective date. Cost basis and value are unchanged.
- Every event is booked once, keyed by team, ticker, action and event_date.
- Limitations:
  - Yahoo reports cash dividends by ex-date, using its stated amount as the declared amount.
  - If a later split happens, Yahoo adjusts historical dividend amounts. Booking on the ex-date avoids this.
  - Mergers, spin-offs and delistings need TO ADJUSTMENT proposals.

## Team files (Amendment 3; format unchanged)
- `teams/teamN/cash.json`: `{"cash": 157549.16}`.
- `teams/teamN/value.json`: `{"value": 249999.99}`.
- `teams/teamN/holdings.json`: a list of `{ticker, shares, cost_basis, avg_cost, last_price, market_value, weight, price_source}`.
- `teams/teamN/proposals.json`: `[]`. It is reset after processing, and only deferred items are kept.

## Snapshot (`snapshot_latest.json`, plus frozen `snapshots/YYYY-MM-DD_HHMM.json`)
- Fields: `as_of` (UTC), `run_id`, `run_slot`, `session_date`, `early_close`, `inception_date`, `total_value`, `cash`, `unallocated_cash`, `positions[]` (each tagged with its team), `teams{teamN:{cash, holdings_value, value, names}}`, `sp500_price` and its source (`^GSPC`), `sp500_total_return` and its source (`^SP500TR` level), `amendments_referenced` (parsed from the PROTOCOL.md amendment headings), `amendment_notes` (for example `Amendment 8 adopted 2026-10-20` for amendments not in the previous frozen snapshot), `reconciliation`, `ledger_lines`.
- `cash` is team cash plus unallocated cash. Frozen snapshots are never overwritten.

## Price marks (`prices.json`, Amendment 8(a))
- The Project Overseer writes this file on every scheduled run and commits it together with the snapshot. It is **display-only** for the dashboard and never feeds the ledger.
- Prices follow the Amendment 1 rule for the slot. The file covers every held ticker across all teams (the union, using Yahoo symbols such as `BRK-B`) and the `^SP500TR` level. A dry-run prints a preview in `prices_json_preview`.
```json
{"as_of": "2026-10-20T20:00:00Z",
 "source": "Yahoo Finance official close",
 "prices": {"MSFT": {"price": 531.78, "as_of": "2026-10-20T20:00:00Z"}},
 "sp500_total_return": {"level": 17324.24, "as_of": "2026-10-20T20:00:00Z"}}
```
- `source` is one of `Yahoo Finance previous official close` (09:00), `Yahoo Finance latest regular-session quote` (12:30) or `Yahoo Finance official close` (16:30).
- Every `as_of` is the **actual price bar time** in UTC, never the run time or the scheduled time, and never in the future:
  - official closes: the session close time (16:00 ET, or the early close);
  - 12:30 quotes: the start time of the last 1-minute regular-session bar.
- The top-level `as_of` is the latest bar time in the file. `prices` is `{}` when nothing is held.

## Dashboard commits (Amendment 8(b))
- The Project Delta Engineer bot may commit dashboard-only files directly to `main`: `index.html`, `DASHBOARD.md`, `.nojekyll`, and other static assets used only by the dashboard.
- Everything else is committed only by the Project Overseer: the ledger, snapshots, performance, team files, `prices.json`, scripts, `CONVENTIONS.md` and `PROTOCOL.md`.
- The run script commits with `git add -A` after `pull --rebase`, so it picks up only its own outputs.

## Reconciliation (Amendment 3)
On every run:
- Σ team values + `unallocated_cash` must equal the master total, within $0.01. The master total is computed independently, from an aggregate replay of the whole ledger with positions marked at the same prices.
- Team cash must never be negative, and no position may hold negative shares.
- If any check fails, the script writes `runs/<run_id>.log`, commits nothing, and exits 2.
- Each team value is rounded to cents once, so a team's value can differ from 250,000 by about a cent at fill time.

## Performance (`performance.json`)
- **Time-weighted return.** End-of-day points (from the 16:30 run) are appended to `history/eod.jsonl`, with a chain-linked index: `index_t = index_{t-1} × V_t / (V_{t-1} + F_t)`.
  - `F` is external flows. After Day 0 there are none, because sleeves are isolated (Amendment 7). Dividends and trades are internal.
  - The 09:00 and 12:30 runs extend the index from the last EOD point. Every run is also appended to `history/runs.jsonl`.
- **Reference points.** Each period return is `index_now / index_ref − 1`:
  - `1d`: the previous session's EOD point.
  - `mtd`, `qtd`, `ytd`: the last EOD point before the period start. If the period started before inception, the reference is the inception point, and `period_details.basis` says `since_inception`.
  - `1y`, `3y`, `5y`, `10y`: the EOD point on or before the same date N years ago, annualized as `(1+R)^(365.25/days) − 1`. These are `null` until that much history exists. This is the reading of the protocol's "3/5/10-year **or since inception**": the window returns are null until they exist, and `since_inception` is always reported.
  - `since_inception`: cumulative return from the inception point. The annualized version appears in `since_inception_annualized` only once there are at least 365 days of history.
- **Benchmark.** `^SP500TR` levels are taken at the same reference points. `excess` = portfolio return − benchmark return.
- The same method is used for the whole account and for each team.
- **Inception.** The inception date is 2026-10-20 and the inception value is $1,000,000.
  - The Day-0 commit recorded an *intraday* `^SP500TR` level (`benchmark_baseline.day0_intraday_level`) for information only.
  - The **official baseline is the 2026-10-20 official close**. The first 16:30 run writes it to `benchmark_baseline` and to the first `history/eod.jsonl` point.
  - Before that point exists, period returns are null and since-inception is 0.

## Run logs
- Each run writes `runs/YYYY-MM-DD_HHMM.log` and prints a JSON summary to stdout.
- A failed run also writes its log (dry-run logs go to `/tmp`). The log of a failed run is committed with the next successful run.

## Interpretations of ambiguities (stated here, not protocol changes)
1. **30% limit.** The limit is measured against the team's *current* sleeve value at decision time, marked at the run's Amendment-1 prices, after the earlier fills in the same run. It is checked on BUY proposals only. A position that rises above 30% purely from price drift is logged as a NOTE and is not force-sold.
2. **TO adjustment lines** (Amendment 2: mergers, spin-offs, delistings). The TO proposes the ADJUSTMENT through the inbox and the PO writes it to the ledger, because only the PO commits (Amendment 6).
3. **"3/5/10-year or since inception".** Fixed windows are reported once the history exists. Until then they are null, and `since_inception` is always present.
4. **Unallocated cash.** Unallocated cash is an explicit `unallocated_cash` field in the snapshot, which is 0 under the Day-0 lock. Reconciliation is Σ team values + unallocated = master total.
5. **Single Day-0 ledger line.** `DAY0_INIT` (1,000,000) is the opening entry and allocates 4 × 250,000 to the team sleeves.
6. **Team 1 "4–8 names".** Only the maximum of 8 is enforced, because idle cash is allowed (Amendment 4).
7. **Team 3 "rules-based".** It is enforced by requiring a `rule_id`. Team-internal rules, such as per-book caps, are the team's responsibility and are not enforced by the PO.
