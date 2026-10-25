# Alpha-Junior hardening and release gates

**Audit status:** partial hardening applied on a review branch. The changes do not certify live trading or claim profitable alpha. Do not deploy with live order execution enabled until the gates below are completed and verified.

## Changes in this branch

- Private API reads and mutations require a configured NEXUS_API_KEY; missing or known-placeholder credentials fail closed.
- Only liveness endpoints remain unauthenticated. Do not expose this service directly to the public internet without TLS, network controls, secret rotation and deployment review.
- Alpaca HTTP 401 no longer silently switches the client into simulation. Failed live position/order reconciliation raises an error rather than returning a fake empty list.
- The pre-trade guard rejects unknown/invalid account equity, unknown positions, invalid/non-finite orders and missing market price instead of substituting a fixed fallback equity or $100 price.
- Order request fields have explicit positive/finite/range constraints.
- The Python order validator checks symbol, side, quantity, price and order-type constraints before optional Zig validation.
- Invalid Kelly payoff inputs no longer receive a nonzero default allocation; malformed signal arrays produce no weights.
- The dashboard now checks HTTP status so a 401/503 body cannot be displayed as if it were account data.
- Readiness verification no longer counts simulation mode as a successful broker connection.

## Still blocking production certification

### P0 — do before any live automated trading

1. **Order authority and state:** all API submit/buy/sell routes currently use the mandatory pre-trade guard; the engine has been changed to abort on unknown account/positions and the guard re-reads live positions even when a caller passes a snapshot. Still trace every CLI, automation, UI, close/cancel and scheduled path to broker submission; enforce one non-bypassable order authority and idempotency. Verify every order decision against account, holdings, open orders, buying power, trading session, market-data freshness, risk limits and kill-switch status.
2. **Audit durability:** this branch now raises on governance audit persistence errors and halts new risk-increasing orders; if post-submit lineage persistence fails, it returns a distinct submitted-but-unreconciled status. Still required: test actual disk-full/locked/corrupt-DB scenarios, transaction semantics, append-only protections, retention, backup/restore and operational reconciliation.
3. **Broker lifecycle:** test partial fills, rejected/cancelled/expired orders, cancel/fill races, duplicate requests, rate limits, timeouts, disconnects, stale sessions, restart recovery and broker/client state divergence. Unknown state must halt new risk-increasing orders.
4. **Market-data truth:** eliminate silent fallback to synthetic data or untracked alternate feeds in any production signal path. Every bar must carry source, timestamp, feed, adjustment and freshness metadata. Missing OHLCV must not be fabricated by copying close values or zeros.
5. **Quant integrity:** fix and test any timestamp lookahead in signals/backtests; use purged walk-forward splits, point-in-time universes, realistic spread/impact/fees/borrow costs and tests for overlapping labels. Require deterministic replay and out-of-sample reports.
6. **Risk model correctness:** independently verify Kelly math, portfolio constraints after every transformation, drawdown denominators, return-frequency annualization, VaR/ES, concentration, gross/net exposures, shorting and portfolio-level risk. Every native/Python fallback must preserve identical validation semantics.
7. **Security and deployment:** rotate secrets, validate strong configuration, enforce TLS/private network boundaries, least-privilege service accounts, protected branches/reviews, dependency and container scanning, and safe permission defaults. Verify CORS is not mistaken for API authentication.

### P0 — model lifecycle and quant validation

- **Unfitted-model side effects:** current ensemble/time-series interfaces include prediction paths that attempt to fit on supplied frames or return fallback probability maps when fitting is unavailable. Refactor serving to load an explicitly trained, versioned artifact; prediction must not trigger training, and a failure must return a typed unavailable/no-trade state rather than fabricated calibrated probabilities.
- **Prediction/target alignment:** verify every target horizon and feature timestamp by contract; train/test separation must be temporal and account for overlapping labels. Do not interpret accuracy on an in-sample or shuffled frame as forecasting skill.
- **Production model registration:** register every ensemble model with name, immutable artifact hash, feature schema, calibration report and rollback target. Reject unknown, untrained, stale or schema-incompatible models at load time. The NCM prototype remains outside this registry until it passes these controls.

### P1 — model and operations

- Implement the separate NCM research track described in NCM_RESEARCH_PLAN.md; it is not live-trading code yet.
- Register actual fitted model artifacts, feature schemas, training data manifests and calibration metadata. A model name or saved score alone is not a reproducible artifact.
- Replace hard-coded/uncalibrated "confidence" and self-referential factor-weight updates with outcome-based, time-aligned evaluation.
- Test service readiness against real dependencies, not a constant healthy response. Include broker, database, market data, model registry and risk engine health.
- Test long-running recovery, restart budgets, clean installs on supported OSes, native build parity, backup restore and unattended 24/7 operation.
- Establish alert runbooks, manual kill switch, max-notional daily limits, loss/drawdown circuit breakers and a documented operator handover.

## Release evidence checklist

- [ ] All unit, contract, integration and fault-injection tests pass on a clean pinned environment.
- [ ] Security tests prove missing/invalid auth cannot access private reads or execute mutations.
- [ ] Every order path has a passing risk and lineage test; no bypass remains.
- [ ] Broker reconciliation survives restart and network/broker failures without creating duplicate exposure.
- [ ] Historical backtests are reproducible and free of detected leakage; results include realistic transaction costs.
- [ ] Walk-forward and untouched holdout results beat appropriate baselines with uncertainty and multiple-testing controls disclosed.
- [ ] NCM remains advisory until its own promotion gates pass.
- [ ] Paper trading runs with immutable model/data/config versions for a risk-approved period.
- [ ] Native builds and Python fallback parity pass on each supported operating system.
- [ ] Readiness verifier is demonstrated to fail for each intentionally broken dependency and pass only when prerequisites are truly available.
- [ ] An independent reviewer approves the exact commit and artifacts that are released.

**A green unit-test run is necessary but not sufficient.** No software process can guarantee zero losses, zero incidents, or a particular prediction rate.
