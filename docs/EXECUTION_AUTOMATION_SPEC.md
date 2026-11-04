# Alpha-Junior execution automation specification

## Required order lifecycle

Use an explicit state machine:
`INTENT_CREATED → VALIDATED → RISK_APPROVED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED`

Alternative terminal or exception states:
`REJECTED`, `CANCEL_REQUESTED`, `CANCELLED`, `EXPIRED`, `SUBMISSION_UNKNOWN`, `CANCEL_UNKNOWN`, `RECONCILIATION_REQUIRED`, `EXECUTION_HALTED`.

Never translate a timeout, 404, unknown status, stale position snapshot or broker read error into a definite rejection or empty state.

## Idempotency and reconciliation
- Every intent must have a stable client order ID derived from an immutable intent record; retries must reuse the same ID.
- Before risk-increasing dispatch, reconcile broker account, positions, open orders, buying power and clock.
- After submit/cancel/replace timeout, trip the persistent execution halt and reconcile before any retry.
- Track accepted order IDs and all partial fills; do not size a second order using holdings that omit pending open quantity.
- On restart, reconcile broker open orders and positions before enabling the scheduler.

## Final pre-trade controls
- Strong API authentication and operator/user authorization.
- Instrument eligibility, market session and broker mode.
- Current broker equity and holdings; gross/net exposure and leverage.
- Per-name, sector, asset-class, currency and portfolio concentration.
- Maximum order notional, minimum liquidity, spread, slippage and participation rate.
- Daily trade count, turnover, daily loss, peak-to-trough drawdown and exposure caps.
- Data freshness, source, feed and timestamp.
- Strategy/model approval state, model version and data-quality flags.
- Durable audit approval and persistent kill-switch state.

## Automation policy
- Scheduled research, data checks, model evaluation and monitoring may run unattended.
- Retraining is opt-in and must write candidate artifacts only.
- Promotion is a manual approval action until the artifact lifecycle and independent review gates are implemented.
- Order scheduling must use broker clock and instrument calendar; never infer that a market is open from an API outage or missing clock payload.
- A kill switch must survive process/container restart and require operator identity plus a reconciliation reference to clear.

## Multi-account and worldwide deployment
- Isolate API keys, broker sessions, order ledgers, risk budgets, audit records and operator permissions per account.
- Route only instruments explicitly supported by the account/broker and allowed in the applicable jurisdiction.
- Market calendar, session, currency, timezone, corporate actions, market-data entitlements and trading restrictions are instrument-specific.
- Worldwide rollout requires a separate broker, security, market-data, regulatory and tax review for every supported country and product class.

## Operator console requirements
Expose account mode (paper/live/simulation), broker/data connectivity, market clock, current positions, open orders, partial fills, order state, risk limits, daily P&L, model versions, data freshness, execution halt reason, incident reference, audit trail and manual reconciliation workflow. Read-only users must not receive order permissions by default.
