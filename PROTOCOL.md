Project Synopsis — Isolated 20-Year Paper Portfolio

This is a new, fully isolated project. No files, bots, memory, or decisions from any other project may be used or referenced.

Objective
Hypothetical $1,000,000 starting capital managed by a hierarchy of Grok bots whose sole mandate is to maximize account value after 20 years. All activity is paper trading only.

Structure
- One Project Overseer owns the master ledger, daily snapshots, performance reporting, and final audit.
- Four Team Overseers, each managing an independent $250,000 sleeve:
  1. High-conviction concentrated growth (4–8 names max, large positions allowed)
  2. Quality compounders / wide-moat (durable businesses, lower turnover)
  3. Factor / systematic (rules-based only, more positions)
  4. Thematic / macro overlay (explicit macro or theme views)
- Each team has four Investment Managers who propose trades only within their assigned area.

Capital is isolated by team. Proposals flow upward (Investment Manager → Team Overseer → Project Overseer) before any ledger entry.

Integrity Rules
Append-only master ledger with timestamp, bot ID, action, ticker, size, decision price, price source, rationale, and approving bot. No overwrites; corrections are new adjustment lines. Strict cash accounting from the locked Day-0 balance. Daily frozen snapshots. Primary benchmark is S&P 500 total return.

Automation
Three fully automated runs every market day (Eastern Time), requiring no user input after setup:
- 09:00 (30 min before open)
- 12:30 (3 hours into session)
- 16:30 (30 min after close)

On each run the Project Overseer collects proposals, applies approvals, updates the ledger and snapshots, calculates all performance periods (1-day, monthly, quarterly, yearly, 3/5/10-year or since inception), and commits the files to the dedicated GitHub repository. The app reads those files and updates in real time.

Out of Scope
Real brokerage execution, leverage, shorting, options, or any influence from outside this project unless explicitly amended.

Amendments

Amendments 1–7 (adopted 2026-10-20)

Amendment 1 — Fill and price rule
- 09:00 ET run uses the previous official close.
- 12:30 ET run uses the latest available regular-session quote at the moment the proposal is approved.
- 16:30 ET run uses the official closing price.
- Price source: Yahoo Finance or the exchange official close (logged with URL or ticker+timestamp).
- No commissions, no slippage. Fills are assumed at the exact logged price.

Amendment 2 — Dividends and corporate actions
- Cash dividends are booked to the team’s cash on the ex-date at the declared amount.
- Stock splits and reverse splits adjust share count and cost basis on the effective date; total value is unchanged.
- Mergers, spin-offs, and delistings are handled by the Team Overseer with a logged adjustment line; cash or new shares are added at the official terms.
- The S&P 500 total-return series (dividends reinvested) remains the sole benchmark.

Amendment 3 — Team-level cash and value
Each team folder now contains cash.json and value.json.
- Starting cash per team: 250000.
- snapshot_latest.json remains the whole-account view; team files are the sleeve-level source of truth.
- Project Overseer reconciles the sum of team values + any unallocated cash to the master total on every run.

Amendment 4 — Approval and risk limits
- No single position may exceed 30 % of a team’s sleeve without Project Overseer override (logged).
- Team 1: maximum 8 names. Team 2: maximum 12 names. Team 3: no hard name limit but must stay rules-based. Team 4: maximum 10 names.
- Idle cash is allowed. No forced investment.
- A team may not spend more cash than it holds. Proposals that would create negative cash are auto-rejected.
- Turnover is unconstrained except by the team mandate (Team 2 prefers lower turnover).

Amendment 5 — Market calendar
- Runs occur only on days the NYSE is open for a regular session.
- On early-close days the 16:30 run still fires and uses the official early close.
- Holidays: no runs; the prior snapshot remains current.

Amendment 6 — Automation ownership
- The Project Overseer bot owns the three scheduled routines and is the only bot that commits to main.
- Team Overseers and Investment Managers wake on the same schedule, submit proposals, and do not push.
- “Real-time” means the app refreshes after each successful commit (three times per market day).

Amendment 7 — 20-year isolation and amendments
- The four $250 k sleeves remain permanently isolated. No reallocation between teams unless the owner issues a written amendment.
- Any protocol change must be added as a new dated amendment in PROTOCOL.md, committed by the Project Overseer, and referenced in the next snapshot.
