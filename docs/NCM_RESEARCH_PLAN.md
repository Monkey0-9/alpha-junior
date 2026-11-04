# Neural Causal Models (NCM) research and promotion plan

**Implementation status:** research prototype in `nexus/research/ncm/`.  
**Trading status:** not connected to the live ensemble or execution path.  
**Acronym used here:** NCM means *Neural Causal Model*; the term can mean different things in other research communities.

## Research question

Can a defensible causal graph and neural structural mechanisms improve conditional return/risk forecasts or stress-scenario quality beyond existing Alpha-Junior signals and strong non-neural baselines, consistently across time, assets and regimes, after realistic costs?

The existence of a neural causal model is not proof that the graph is causally identified. Graph edges, interventions, confounders, time lags and measurement assumptions must be justified independently.

## Current prototype

- Explicit directed acyclic graph (DAG), ordered parent sets and topological validation.
- MLP mechanism per non-root variable, with train-sample normalization and residual bootstrap for stochastic generation.
- Empirical root-node sampling and scalar/vector interventions using a deterministic random seed.
- Chronological holdout evaluation with a configurable purge gap and mean baseline.
- Local artifact save/load with a colocated SHA256 manifest. Artifacts use joblib/pickle; load only trusted files.
- Outputs are research metrics and simulated variables only. No broker API, ensemble registration or trading authority is exposed.

## Proposed Alpha-Junior integration boundary

1. **Data contract:** features must be point-in-time and include source, feed, event time, publication/availability time, ingestion time, adjustment, timezone, currency and missingness indicators.
2. **Graph registry:** persist graph version, parent sets, time lags, assumptions, confounders, identification argument, author and review history.
3. **Mechanism training:** fit only on the chronological training window. Do not fit or recalibrate during inference.
4. **Forecast interface:** emit a predictive distribution/quantiles with model version, horizon, feature timestamp, regime, calibration version and data-quality flags. Do not report uncalibrated confidence as probability.
5. **Counterfactual interface:** accept explicitly named interventions. Label outputs as model-generated scenarios, never observed market outcomes.
6. **Decision boundary:** NCM can propose a research forecast; portfolio sizing and risk remain separate. Missing, stale, malformed, out-of-distribution or uncalibrated NCM output maps to NO_TRADE for the NCM signal.
7. **Artifact lifecycle:** persist the training manifest/hash, graph, feature schema, model weights, scaler, random seeds, library versions, training period, calibration and evaluation report, approvers and rollback artifact.

## Evaluation protocol

### Data and splits
- Sort by event and availability time; use point-in-time universe membership including delisted securities where data permits.
- Purge at least the target horizon and use an embargo for overlapping labels.
- Fit imputation, scaling, feature selection, graph learning, calibration and hyperparameter selection on training/tune data only.
- Keep a final untouched test interval that is opened once after model selection is frozen.
- Evaluate rolling walk-forward windows and multiple market regimes. Record all trials, including failures.

### Baselines and ablations
- Zero-return and market/regime baselines.
- Regularized linear regression and logistic regression.
- A strong tree-based tabular model.
- Existing Alpha-Junior alpha/ensemble using identical timestamp, universe and cost assumptions.
- NCM with graph-only, neural-mechanism, no-intervention and shuffled-graph negative controls.

### Forecast metrics
- MAE/RMSE for point forecasts.
- Log loss, Brier score and reliability curves for probabilities.
- Quantile coverage, interval width and pinball loss for distributions.
- Rank IC and IC stability by symbol, horizon, market regime and sub-period.
- Calibration error and prediction coverage under distribution shift.

### Portfolio metrics
- Net Sharpe/Sortino, drawdown magnitude and duration, tail loss/expected shortfall, turnover, concentration, exposure, slippage sensitivity, capacity and rejected-order rate.
- Include fees, spread, market impact, borrow and funding where relevant. Report gross and net results.

### Statistical and operational discipline
- Block-bootstrap confidence intervals and multiple-testing correction across variants.
- Deflated-Sharpe/probability-of-backtest-overfitting analysis where appropriate.
- p50/p95/p99 latency, cold start, memory, compute requirements, deterministic replay, artifact reload and rollback.
- Stress missing data, stale data, regime shifts, corrupted artifacts and inference timeout.

## Promotion gates

NCM remains research-only until:
1. Point-in-time data lineage, temporal leakage, purge/embargo and negative-control tests pass.
2. The final holdout is untouched until model selection is frozen.
3. Forecast skill and calibration justify complexity versus the strongest baseline with uncertainty disclosed.
4. Net-of-cost portfolio results are robust across rolling windows/regimes and do not worsen drawdown, tail risk, concentration, turnover or capacity.
5. Artifacts and reports reproduce from a clean environment and immutable hashes.
6. Shadow inference and paper trading operate through a risk-approved period with zero unexplained stale-input, duplicate-order, reconciliation or guard-bypass incidents.
7. Independent quant, risk, engineering and security reviewers approve the same artifact.
8. Promotion is a manual, signed, reversible release action. No self-promotion based only on one Sharpe number or a model-generated explanation.

A failure of any gate means do not promote. No accuracy or return improvement is assumed in advance.
