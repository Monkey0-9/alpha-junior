> **SUPERSEDED — NOT A PRODUCTION READINESS CERTIFICATE (5 November 2026).** This document contains historical setup notes and/or claims about a machine-specific deployment that have not been re-verified against the current repository. Do not rely on its “active”, “complete”, “production ready”, “24/7”, or unauthenticated-endpoint statements. Current release status is **NOT PRODUCTION CERTIFIED**. See [docs/ALPHA_JUNIOR_RELEASE_AUDIT_2026-11-05.md](docs/ALPHA_JUNIOR_RELEASE_AUDIT_2026-11-05.md), [docs/EXECUTION_AUTOMATION_SPEC.md](docs/EXECUTION_AUTOMATION_SPEC.md), and [README.md](README.md). Live trading and worldwide deployment require the release gates to pass on the exact deployment artifact.

---

# Nexus Deployment Guide - Institutional Edition

This guide outlines the steps required to deploy the Nexus Trading Platform into a production environment.

## 📋 Prerequisites

### 1. Toolchains

Ensure the following are installed on the production host:

- **Python 3.10+** (Control Plane)
- **Rust/Cargo** (Risk Engine)
- **Go 1.21+** (Platform Auditor)
- **Zig 0.11+** (Order Validator)

### 2. Infrastructure

- **Alpaca API Credentials**: Live or Paper keys in `.env`.
- **Backend API**: Accessible on `localhost:8000` (or as configured).
- **Streamlit**: For the monitoring UI.

## 🚀 Deployment Steps

### Phase 1: Environment Setup

```bash
# Clone the repository
git clone <repo-url>
cd mini-quant-fund

# Install Python dependencies
pip install -r requirements.txt
```

### Phase 2: Polyglot Compilation

Nexus offloads performance-critical tasks to compiled satellites. Run the orchestrator to build them:

```bash
python nexus/build_polyglot.py
```

### Phase 3: Production Verification

Before initiating live trading, run the comprehensive verification suite:

```bash
python verify_production_ready.py
```

Check the generated `PRODUCTION_READINESS_REPORT.md` for any failures.

### Phase 4: Launch

Use the orchestrator to start the full stack:

```bash
python nexus_orchestrator.py
```

## 🛡️ Risk Management Note

The platform is currently configured with a **5% position limit** and **15% drawdown limit**. Ensure these parameters in `nexus/utils/config.py` align with your institutional risk profile before going live.

---
*Nexus Trading Platform. Engineered for Superior Execution.*
