# Neural Causal Models (NCM): research and promotion plan

**Status: an isolated research prototype exists under `nexus/research/ncm/`. It is not integrated with the Alpha-Junior ensemble or execution path, and no claim is made that it improves trading performance.**

## Meaning and scope

For this plan, **NCM means Neural Causal Model**: a structural causal model whose causal mechanisms are represented by neural networks. This is an explicit working interpretation because "NCM" is not a universally unique acronym. The foundational implementation and research are described by [CausalAILab/NeuralCausalModels](https://github.com/CausalAILab/NeuralCausalModels), while [time-series neural causal simulation work](https://github.com/thummd/tncm) explores causal generative models for financial series. These are research references, not evidence of trading alpha.

NCM is not synonymous with a generic neural network, and causal-looking outputs must not be presented as proven causal effects without defensible identification assumptions. The NCM track must initially be offline and advisory-only. It must never bypass the portfolio/risk guard, broker reconciliation, order audit, or no-trade decision.

## Intended research questions

1. Do causal features improve forecasts beyond a zero-return/market baseline, a regularized linear model, the current Alpha-Junior signal, and a strong tree-based model?
2. Do interventional or counterfactual scenarios improve risk decisions during regime shifts, or do they merely reproduce assumptions encoded into the graph?
3. Are forecast distributions calibrated and stable out of sample, after point-in-time feature controls and realistic trading costs?
4. Does NCM improve net portfolio outcomes without worsening drawdown, concentration, turnover, tail risk, or operational latency?

## Proposed architecture

1. **Point-in-time data contract.** Every feature needs source, event time, publication/availability time, ingestion time, adjustment/version metadata, timezone, and missingness status. No feature may be available to a prediction before it would have been available in production.
2. **Causal graph registry.** Version each candidate graph, parent set, lag convention, domain assumptions, and identification rationale. For time series, unroll temporal dependencies into lagged nodes; prohibit same-timestamp leakage and cyclic causal graphs.
3. **Mechanism learner.** The current prototype fits a conditional MLP mechanism for each non-root graph node and uses residual bootstrap for simulation. It accepts a declared graph; it does not discover causal structure or prove identifiability. Upgrade it only with explicit exogenous-noise mechanisms, separate train/tune/calibration/final-test periods, and reproducible artifacts.
4. **Forecast interface.** Emit a distribution or calibrated quantiles for returns/risk over fixed horizons—not an uncalibrated "confidence" percentage. Each prediction includes model version, feature timestamp, data-quality flags, horizon, regime, and reason codes.
5. **Counterfactual interface.** Support named, bounded scenario interventions such as volatility or sector shocks. Mark assumptions and uncertainty; never label a generated counterfactual as an observed market fact.
6. **Decision boundary.** NCM produces an advisory forecast only. A separate portfolio/risk layer combines it with existing signals, costs, risk budgets, liquidity, current holdings, broker status, and kill-switch state. Any missing/invalid/stale input maps to NO_TRADE.
7. **Artifact registry.** Store training dataset manifest/hash, feature schema, graph version, code/dependency versions, random seeds, fit configuration, calibration state, evaluation report, approvals, and rollback target. A code version alone is not a model artifact.

## Benchmark and validation protocol

### Data splits

Use rolling walk-forward evaluation with a final untouched holdout. Purge training examples whose target windows overlap validation/test windows and apply an embargo appropriate to the prediction horizon. Never shuffle samples across time. Keep universes and delisted names point-in-time where possible; include dividends/splits and timestamped availability for external features. Fit scalers, imputers, feature selectors, and calibrators on training data only.

### Required competitors

- Zero-return and market/regime baselines.
- Regularized linear/logistic regression.
- A well-tuned non-neural tabular model such as gradient-boosted trees.
- The current Alpha-Junior alpha/ensemble with the same timestamps, universe, costs, and risk budget.
- NCM with ablations: graph-only baseline, learned mechanisms, no-counterfactual baseline, and shuffled/non-causal graph negative controls.

### Required metrics

**Forecast:** MAE/RMSE for point forecasts; log loss/Brier score and reliability/calibration error for probabilities; quantile coverage and interval width for distributions; rank IC and IC stability by symbol, regime, and horizon.

**Portfolio:** net Sharpe/Sortino, maximum drawdown and duration, turnover, gross/net exposure, concentration, tail loss/expected shortfall, slippage sensitivity, capacity, rejected-order rates, and P&L after spread, fees, market impact, and borrow costs where applicable.

**Statistical discipline:** block-bootstrap confidence intervals, multiple-testing correction across model/feature/horizon trials, and a deflated-Sharpe/probability-of-backtest-overfitting assessment. Record every experiment, including failures. No cherry-picked period or single accuracy number is sufficient.

**Operational:** p50/p95/p99 inference latency, cold-start time, memory, CPU/GPU requirements, degraded-data behavior, restart/reload behavior, deterministic replay, and ability to roll back to the last approved model.

## Promotion gates

A candidate remains **research-only** unless all gates pass:

- Data lineage and temporal leakage tests pass.
- Final holdout is untouched until model selection is frozen.
- Probabilistic outputs beat or justify themselves against the strongest baseline on preregistered metrics; calibration is not worse.
- Net portfolio improvement remains positive after conservative cost/stress assumptions, with uncertainty reported and risk limits not degraded.
- Results persist across rolling windows and regimes; one lucky period is insufficient.
- Negative controls behave as expected; graph assumptions and limitations are disclosed.
- Code, data manifest, model artifact, environment and reports are reproducible.
- Shadow inference and paper trading operate for a risk-approved period with no unexplained divergence, stale inputs, duplicate orders, or risk-guard bypass.
- Independent quantitative, engineering, risk, and security reviewers approve the same immutable artifact hash.
- Promotion is manual, signed, and reversible. No self-promotion from a single metric or LLM judgement.

A failure at any gate means **do not promote**. Do not imply an expected improvement in prediction rate or accuracy before testing demonstrates it.

## Implementation sequence

1. Repair source provenance, point-in-time timestamps, data quality and leakage tests before using the prototype on market data.
2. Freeze a baseline dataset/feature contract and repeatable walk-forward harness.
3. Extend the current research prototype with graph/version registry, configurable experiment manifests, and isolated execution while preserving its no-trade/no-promotion boundary.
4. Add baseline and negative-control experiments before complexity tuning.
5. Persist artifacts and signed evaluation manifests in the model registry.
6. Add shadow-only inference and monitoring for calibration drift, covariate shift, missing data and latency.
7. Complete paper-trading evaluation and independent review; keep live execution disabled until all platform-wide safety gates also pass.

## References

- [Neural Causal Models, CausalAILab](https://github.com/CausalAILab/NeuralCausalModels)
- [Neural Causal Models for Counterfactuals, CausalAILab](https://github.com/CausalAILab/NCMCounterfactuals)
- [Towards Causal Market Simulators / TNCM-VAE](https://github.com/thummd/tncm)
- [Financial time-series forecasting survey, Neurocomputing (2022)](https://doi.org/10.1016/j.neucom.2022.09.003)
