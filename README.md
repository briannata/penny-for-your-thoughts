# Penny for Your Thoughts

Penny for Your Thoughts is a personal finance workflow that turns a household's spending spreadsheet into monthly insights. It reads an existing Google Sheet and builds a separate report, preserving the original budget and the way people already track their expenses.

The project combines deterministic accounting with AI-generated explanations to support spending awareness and more realistic budgeting.

## What it does

- **Tracks household and individual spending.** Reports monthly totals and category breakdowns together and by payer.
- **Suggests flexible budgets.** Uses completed months to show typical spending and observed ranges, alongside tentative allowances.
- **Flags spending changes.** Highlights category increases of at least $50 and 25% above the median of the previous three completed months.
- **Explains completed months.** Generates concise AI narratives only after a month is marked complete.
- **Handles late entries.** Detects changes to historical data, marks affected narratives outdated, and waits for renewed approval before replacing them.
- **Refreshes daily.** A GitHub Actions workflow updates the separate reporting spreadsheet.

## How it works

The workflow reads transaction dates, amounts, descriptions, categories and payers from monthly tabs, plus a separate fixed-expense schedule. Python validates the records and calculates the report figures. The AI receives aggregated figures and findings to explain, while the accounting remains in code.

Deficit carryovers are tracked separately from new spending. Fixed-expense allocations are included once and distinguished from recorded purchases. Refunds remain negative, and identical transaction rows are preserved because they may represent legitimate separate purchases.

The report brings together a monthly overview, category breakdowns, budget suggestions, anomaly flags, supporting transactions and approved narratives. Completion controls and refresh status make it clear which months are ready for analysis and when the data was last updated.

## Privacy and cost controls

The original spreadsheet is accessed through a read-only client, and writes are restricted to a separate report. Credentials and financial exports remain outside the published source code.

AI requests use aggregate spending data rather than individual merchant descriptions or spreadsheet links. Cached narratives avoid repeat requests for unchanged data. A persistent reservation ledger limits this workflow's estimated AI spending to a $2 monthly target, subject to model pricing and preserved state. Numerical reports continue updating when AI is disabled or the budget is exhausted.

## Scope

The workflow analyzes manually recorded spending; it does not connect to banks or verify that every purchase has been entered. Per-person figures describe who paid, not who benefited or owes money. Fixed allocations applied to historical months are estimates, and suggested budget ranges describe past spending rather than guaranteed affordability.

The current implementation is tailored to a household workbook with monthly transaction tabs and a separate fixed-expense schedule.

## Built with

Python, Google Sheets API, OpenAI Responses API and GitHub Actions. An offline Excel preview supports local review, and automated tests cover accounting rules, completion approvals, change detection, cost reservations and simulated cloud syncs.
