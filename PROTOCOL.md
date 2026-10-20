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
