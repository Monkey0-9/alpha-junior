# Alpha-Junior — Quantitative Research and Execution Platform

> **Release status: NOT PRODUCTION CERTIFIED.** This repository is an evolving quantitative research and broker-integration project. It is not certified for unattended live trading, multi-user service, or worldwide deployment. Do not enable live automated execution solely because CI passes or a backtest looks attractive.

Alpha-Junior (Nexus) combines market-data ingestion, research signals, model experimentation, portfolio construction, broker integration, risk controls, and an operator-facing dashboard. The release audit and launch gates are documented below.

## Current engineering status

- **Broker integration:** Alpaca client and API routes exist. Live broker lifecycle, partial fills, timeouts, restarts, and reconciliation require sandbox integration and fault-injection sign-off.
- **Execution safety:** a mandatory pre-trade boundary and persistent execution-halt mechanism are under development. The halt must be manually cleared only after external broker reconciliation.
- **Research models:** technical/factor signals and tree/neural model wrappers exist. Untrained inference returns HOLD; automatic retraining and promotion are disabled by default.
- **NCM:** a declared-graph Neural Causal Model research prototype exists under `nexus/research/ncm/`. It is offline/advisory-only and is not connected to executable trades.
- **Native components:** C++ extensions are built by CI on Linux, Windows and macOS. Rust/Go/Zig components still require separate build, parity and operational validation for each supported target.
- **Security:** non-liveness API routes require a strong `NEXUS_API_KEY`. Network exposure, TLS, secrets management, rate limits, roles, tenant isolation and deployment hardening are separate release requirements.

## Architecture overview

```text
Dashboard / CLI
      |
      v
Authenticated FastAPI
      |
      +--> Broker client ----> Broker account / orders / positions
      |          |
      |          +--> Market data and clock
      |
      +--> Mandatory pre-trade guard
      |          |
      |          +--> Account/position/mark freshness
      |          +--> Portfolio governance and durable audit
      |          +--> Persistent execution circuit breaker
      |
      +--> Monitoring and read-only research endpoints

Research pipeline
      |
      +--> Point-in-time market data
      +--> Alpha / factor features
      +--> GBM / LSTM / Transformer experiments
      +--> NCM research-only experiments
      +--> Walk-forward / holdout / cost-aware evaluation
      +--> Candidate artifact and review workflow
```

## Getting started

Python 3.11 is the current CI target. The portable CI dependency set is provided for tests and static checks; it is **not** a complete, certified production dependency lock.

```bash
git clone https://github.com/Monkey0-9/alpha-junior.git
cd alpha-junior

python -m venv .venv
# Activate the virtual environment for your OS.
python -m pip install --upgrade pip
python -m pip install -r requirements-ci.txt

# Build the native C++ extensions.
cd nexus/cpp_extensions
python setup.py build_ext --inplace
cd ../..

# Create local configuration from the example.
cp .env.example .env
```

Do not put real credentials in Git. Use a secrets manager or protected environment variables for deployed systems. The example configuration intentionally uses placeholders and disables alternate market-data fallback, automatic retraining and automatic model promotion.

## Configuration

Important controls include:

- `ALPACA_API_KEY` and `ALPACA_API_SECRET`: broker credentials.
- `ALPACA_PAPER_TRADING=true`: use broker paper trading while validating the integration.
- `NEXUS_API_KEY`: a randomly generated secret of at least 32 characters.
- `NEXUS_ALLOW_SIMULATION_FALLBACK=false`: simulation is never an implicit fallback.
- `NEXUS_ALLOW_YAHOO_FALLBACK=false`: alternate feed use must be explicit and tagged.
- `NEXUS_MAX_MARKET_DATA_AGE_SECONDS=180`: maximum age for the live pre-trade market mark.
- `NEXUS_AUTO_RETRAIN_ENABLED=false` and `NEXUS_AUTO_MODEL_PROMOTION=false`: retraining/promotion are disabled until the artifact and review gates are satisfied.

## Run checks

```bash
ruff check nexus
mypy nexus
pytest -q
python verify_production_ready.py
```

The GitHub Actions workflow builds native C++ extensions and runs lint, typing and tests on Linux, Windows and macOS. A green CI matrix is necessary but **does not certify broker, market-data, quant, security, legal or operational readiness**.

## Research and operating documents

- [Release audit and P0/P1 readiness gates](docs/ALPHA_JUNIOR_RELEASE_AUDIT_2026-11-05.md)
- [NCM research and promotion protocol](docs/NCM_RESEARCH_PLAN.md)
- [Automated execution lifecycle and controls](docs/EXECUTION_AUTOMATION_SPEC.md)
- [Global and multi-user launch checklist](docs/GLOBAL_LAUNCH_CHECKLIST.md)

## Persistent execution halt

Ambiguous order submit/cancel/close outcomes must stop risk-increasing execution. The persistent state is stored under `data/execution_halt.json`; inspect broker open orders, fills and positions independently before clearing it.

```bash
python scripts/clear_execution_halt.py \
  --operator "operator-id" \
  --reconciliation-reference "broker-export-or-incident-reference" \
  --confirm-broker-reconciled
```

This command records an operator assertion and reference; it does not itself contact a broker or prove reconciliation. Keep the execution service stopped while reconciling.

## License and deployment notice

Before multi-user or cross-border deployment, verify the repository license, broker terms, market-data entitlements, local permissions, exchange rules, privacy obligations, record-retention duties and tax/account requirements for each jurisdiction. No worldwide trading authorization or regulatory approval is implied.
