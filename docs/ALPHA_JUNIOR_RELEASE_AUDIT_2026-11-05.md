# Alpha-Junior release audit and production-readiness plan

**Audit date:** 5 November 2026  
**Reviewed branch:** `master` at `ddd9b3a0e880d597187a51f045a41290ec30890e` before the hardening branch was created  
**Implementation branch:** `hardening/alpha-junior-release-20261105`  
**Release classification:** **NOT PRODUCTION CERTIFIED**

This review simulates the scrutiny expected from specialist quantitative research, execution, platform, security, data, ML, QA, SRE, product, and operational reviewers. It is not a claim that several hundred real engineers reviewed or independently approved this code.

## Executive assessment

Alpha-Junior contains a meaningful research and trading-system foundation: a FastAPI broker API, an Alpaca client, an asynchronous orchestration loop, technical/factor signals, a model ensemble, portfolio sizing, risk controls, SQLite audit history, Python/C++ kernels, and a test suite.

The repository did **not** demonstrate a complete, institutionally controlled live-trading lifecycle at the start of this review. The highest-risk defects were not cosmetic:
- Missing API authentication allowed private GET data to be read, and an unset key opened the entire API.
- Broker authentication failure could silently turn live mode into synthetic simulation.
- Broker read failures were represented as empty positions or fallback equity.
- Order timeouts did not distinguish an unknown broker outcome from a definite rejection.
- Position close and order lifecycle paths needed a single controlled approval and reconciliation boundary.
- Governance audit persistence failures were logged instead of blocking an order.
- Missing market data was silently substituted with Yahoo or fabricated OHLCV columns.
- The risk engine could substitute zero risk when benchmark or native metrics were unavailable.
- The GBM predictor could train itself on an inference frame, and its final training row had an unknown forward return.
- Candidate model promotion instantiated a fresh, untrained model class after evaluating a candidate.

The implementation branch addresses a number of these issues and adds tests and research foundations. Remaining gates below are intentionally not described as complete.

## Changes made on the implementation branch

### Security and access control
- Every non-liveness API route requires a strong configured `NEXUS_API_KEY`; missing or weak key returns HTTP 503, invalid key returns HTTP 401.
- Comparison uses constant-time HMAC comparison.
- Public health endpoints are liveness-only and no longer reveal database or broker internals.
- Order schemas reject unexpected fields, invalid sides/types, non-finite quantities, and invalid conditional-order parameters.

### Broker and order safety
- Alpaca 401 no longer silently switches to simulation.
- Failed live position/order reads raise explicit reconciliation errors rather than returning an empty portfolio.
- Order submission, cancel, and close timeouts are represented as unknown outcomes.
- A persistent execution circuit breaker is written atomically to `data/execution_halt.json`; malformed state is treated as halted.
- An operator-only reset command requires an operator identity, reconciliation reference, and explicit broker-reconciled confirmation.
- Core engine order-status lookup no longer treats HTTP 404 or unknown states as proof that an order is terminal.
- Core order dispatch is routed through the guarded API; direct duplicate governance/trade persistence in the scheduler is removed.
- The pre-trade guard refreshes account and positions, checks equity consistency, and requires a fresh timestamped live market mark.
- Position closes use a guarded and audited route.

### Audit and risk
- SQLite is root-relative and configured with WAL, full synchronous mode, and a busy timeout.
- Governance audit persistence failure blocks approval; trade-history persistence failure is surfaced to the order boundary.
- Invalid Kelly inputs and malformed signal arrays produce no weights.
- Missing benchmark returns block new risk-increasing orders; unavailable Rust risk output is no longer presented as zero risk.

### Data, model lifecycle, and build
- Alternate Yahoo fallback is opt-in and is no longer used to silently replace requested intraday intervals.
- OHLCV columns are validated; missing OHLCV is not filled from close or zero.
- The GBM model excludes the unknown final forward label and never auto-fits from `predict` or `predict_proba`.
- Untrained forecasts are HOLD/unavailable rather than pseudo-probabilities.
- Automatic retraining and promotion require explicit opt-in and are disabled in the example configuration.
- CI uses a small portable dependency set and builds C++ extensions on Linux, Windows, and macOS.
- C++ compilation no longer uses `-ffast-math`, which can invalidate finite-value and NaN/Inf checks.

### Neural Causal Models
- A research-only NCM prototype lives under `nexus/research/ncm/`.
- It supports a declared DAG, neural conditional mechanisms, interventions, SHA256-manifested local artifacts, and purged chronological holdout evaluation.
- It is not registered in the production ensemble, does not produce executable orders, and has not demonstrated an advantage on real market data.

## Remaining production blockers

### P0 — must close before any live automated execution
1. **Complete order-path inventory:** enumerate every API, UI, CLI, scheduler, strategy, stop-loss, take-profit, position-close, and recovery path that can submit/cancel/replace/close an order. Prove every path reaches one non-bypassable authority.
2. **Broker reconciliation:** test duplicate client IDs, duplicate retries, accepted-but-timed-out order posts, partial fills, cancel/fill races, rejected/expired orders, order lookup outages, rate limits, restarts, and manual adjustments. Reconcile broker orders and positions before any release-halt reset.
3. **Daily and portfolio controls:** enforce daily loss, peak-to-trough drawdown, gross/net exposure, leverage, buying power, position count, concentration, turnover, liquidity, borrow, instrument eligibility, and market-session constraints at the final order boundary. Test both risk-increasing and risk-reducing orders.
4. **Audit recovery:** inject disk-full, permission, database-locked, corrupt-file, crash-after-submit, and restore-from-backup faults. Prove that no order is blindly retried after a possible broker acceptance.
5. **Market-data provenance:** propagate source, venue/feed, event timestamp, ingestion timestamp, adjustment method, currency, timezone, and freshness through every feature, prediction, backtest and order decision. Research cache fallback must never be confused with an executable live mark.
6. **Quant validity:** inspect every strategy and backtest for look-ahead, same-bar fills, survivorship/universe leakage, overlapping labels, corporate actions, market hours, spread/impact/fees/borrow, partial fills and realistic capacity. Run independent reproducibility and leakage tests.
7. **Risk model correctness:** independently verify portfolio VaR/ES, volatility units, annualization, drawdown denominator/peak watermark, correlation matrices, position constraints after every transformation, long/short exposure, options/derivatives risk, and stress calibration.
8. **Security deployment:** secret rotation, TLS termination, private networking, rate limiting, API audit, dependency and container scanning, SBOM, least privilege, backup encryption, protected branches, code review, incident process and disaster recovery.
9. **Operational kill switch:** demonstrate that the persistent halt stops every new entry across all processes and survives restarts; prove clear/reset is impossible without documented reconciliation and authorized operator approval.

### P1 — model, performance, and operations
- Replace heuristic confidence with out-of-sample calibration and uncertainty estimates.
- Build a real fitted-artifact registry: feature schema, training-data manifest, code/dependency hash, model weights, scaler, calibration, evaluation report, approvals, rollback pointer and immutable artifact checksum.
- Add temporal purging/embargo based on forecast horizon and point-in-time feature-availability checks.
- Run model drift, data drift, latency, missing-data, inference failure and model-version rollback drills.
- Add portfolio-wide exposure aggregation and risk limits before parallel order submission.
- Validate rate-limit handling, bounded concurrency, retry budgets, backpressure, circuit breakers, log correlation IDs, alerting, and 24/7 restart/reconciliation.
- Test Docker and clean installs on the actual deployment OS/architecture; review the broad workstation lockfile separately from the new CI dependency set.

### P2 — product, global launch and usability
- Provide a clear operator console for account mode, broker connectivity, clock status, pending orders, positions, risk budget, halt state, reconciliation queue, data freshness, model versions and audit history.
- Add role-based permissions, multi-user audit identity, approvals, account isolation, and read-only analyst access before any multi-user service.
- For every target country, verify broker availability, instrument eligibility, exchange calendars, market-data licensing, tax/account treatment, regulatory permissions and required disclosures with qualified local counsel and the broker. There is no universal global authorization implied by this repository.

## Required release evidence

- [ ] All CI jobs pass on the exact release commit.
- [ ] All order paths have code-level call-graph review and tests proving there is no bypass.
- [ ] Fault-injection tests pass for broker, audit storage, cache, data, API, and process restarts.
- [ ] A sandbox/paper-trading soak completes with immutable configuration/model/data versions.
- [ ] Position and order reconciliation survives restarts and ambiguous responses without duplicate exposure.
- [ ] Historical results are reproducible and leakage-tested; performance is reported net of conservative costs and uncertainty.
- [ ] Model artifacts are reproducible, calibrated, independently reviewed and reversible.
- [ ] Security, deployment, operational, legal and broker reviews sign off for each target jurisdiction.
- [ ] The exact released artifact is manually approved; live execution remains disabled until that approval.

A green CI run is necessary but not sufficient. Neither model sophistication nor a high historical hit rate guarantees future profitability, prediction accuracy, uptime, or absence of loss.
