# NEXUS QUICK REFERENCE GUIDE

## START SYSTEM

```bash
python nexus_orchestrator.py
```

**Output**:

- API: <http://127.0.0.1:8001> (auto-selected port)
- UI: <http://localhost:8502>

---

## VERIFY SYSTEM READY

```bash
python verify_production_ready.py
```

**Expected Output**: `✓ SYSTEM IS PRODUCTION READY FOR REAL TRADES`

---

## KEY PORTS & URLS

```
API Backend:      http://127.0.0.1:8001
Swagger Docs:     http://127.0.0.1:8001/docs
ReDoc:            http://127.0.0.1:8001/redoc
Terminal UI:      http://localhost:8502
Network UI:       http://192.168.1.3:8502
```

---

## IMPORTANT CONFIGURATION (.env)

### Risk Parameters

```env
NEXUS_MAX_POSITION_SIZE=0.05        # 5% per trade (adjust for risk)
NEXUS_MAX_DRAWDOWN=0.15             # 15% portfolio max loss
NEXUS_MAX_OPEN_POSITIONS=12         # 12 concurrent trades
NEXUS_MAX_DAILY_TRADES=20           # 20 trades per day
```

### Broker Settings

```env
ALPACA_PAPER_TRADING=true           # Set to false for LIVE (⚠️ CAUTION)
ALPACA_API_KEY=your_key
ALPACA_API_SECRET=your_secret
```

---

## EMERGENCY STOP

```bash
# In running terminal:
Ctrl+C

# Gracefully stops all:
- API Backend
- Core Engine  
- Streamlit UI
- Closes all positions (if configured)
```

---

## MONITORING COMMANDS

### Check System Status

```bash
python -c "from nexus.utils.config import Config; Config.ensure_ready()"
```

### View Recent Trades

```bash
python -c "from nexus.execution.alpaca import get_client; import asyncio; print(asyncio.run(get_client().get_orders(status='all', limit=10)))"
```

### View Open Positions

```bash
python -c "from nexus.execution.alpaca import get_client; import asyncio; print(asyncio.run(get_client().get_positions()))"
```

### Check Account

```bash
python -c "from nexus.execution.alpaca import get_client; import asyncio; acc=asyncio.run(get_client().get_account()); print(f'Status: {acc.get(\"status\")}\nBuying Power: ${acc.get(\"buying_power\", 0):.2f}')"
```

---

## TROUBLESHOOTING

### Port Already in Use

```bash
# Check what's using the port:
netstat -ano | findstr :8001

# Kill the process:
taskkill /PID {pid} /F
```

### Reset Virtual Environment

```bash
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### View Logs

```bash
# Tail logs (requires PowerShell):
Get-Content nexus_platform.log -Tail 50 -Wait

# Or check the log file directly:
cat nexus_platform.log
```

---

## BEFORE GOING LIVE

- [ ] 30+ days of successful paper trading
- [ ] Positive Sharpe ratio (>1.0)
- [ ] Win rate >50%
- [ ] Max drawdown <10%
- [ ] All compliance checks passing
- [ ] Emergency stop tested
- [ ] Monitoring confirmed
- [ ] Risk parameters verified
- [ ] Team approval obtained

---

## CRITICAL LIMITS (DO NOT EXCEED)

```
Position Size:        5% of portfolio
Drawdown Limit:       15% of portfolio
Daily Trades:         20 maximum
Open Positions:       12 maximum
Single Symbol:        5% max
Daily Loss:           15% max
Position Hold Time:   Until target reached
```

---

## PROFIT TARGETS & STOPS

```
Take Profit:   +8% per position
Stop Loss:     -4% per position
Regime-based:  Dynamic adjustment for market conditions
```

---

## MARKET HOURS

```
Pre-Market:    7:00 AM - 9:30 AM EST
Regular:       9:30 AM - 4:00 PM EST
After-Hours:   4:00 PM - 8:00 PM EST (selected stocks)

Nexus Trading: Regular hours only (9:30 AM - 4:00 PM EST)
```

---

## KEY FILES

```
nexus_orchestrator.py              Main entry point
verify_production_ready.py         Production verification
PRODUCTION_READINESS_REPORT.md     Full verification report
COMPREHENSIVE_VERIFICATION_REPORT.md  Detailed results
LIVE_TRADING_STARTUP_GUIDE.md      Launch guide
nexus_platform.log                 System logs
.env                               Configuration
requirements.txt                   Dependencies
pyproject.toml                      Project config
```

---

## DAILY CHECKLIST

```
☐ System startup: python nexus_orchestrator.py
☐ Verify ready: python verify_production_ready.py
☐ Check positions: Open Terminal UI (http://localhost:8502)
☐ Monitor P&L: Throughout trading day
☐ Review compliance: Check audit logs daily
☐ Check alerts: Email/SMS if configured
☐ Graceful shutdown: Ctrl+C at market close
☐ Log review: Check nexus_platform.log for errors
```

---

## SUPPORT & DOCUMENTATION

```
Full Report:     PRODUCTION_READINESS_REPORT.md
Verification:    COMPREHENSIVE_VERIFICATION_REPORT.md
Startup Guide:   LIVE_TRADING_STARTUP_GUIDE.md
API Docs:        http://127.0.0.1:8001/docs (when running)
```

---

## SYSTEM STATUS CODES

```
✅ Green   - All systems operational
🟡 Yellow  - Warning, monitor closely
🔴 Red     - Critical issue, stop trading
⚫ Blue    - Market closed, no trading
```

---

## QUICK SWITCHES

### From Paper to LIVE Trading (⚠️ USE CAUTION)

```env
# In .env file:
ALPACA_PAPER_TRADING=false
```

### From LIVE to Paper Trading (Safe)

```env
# In .env file:
ALPACA_PAPER_TRADING=true
```

### Reduce Risk (Conservative)

```env
NEXUS_MAX_POSITION_SIZE=0.02       # 2% per trade
NEXUS_MAX_OPEN_POSITIONS=6         # 6 concurrent
```

### Increase Risk (Aggressive)

```env
NEXUS_MAX_POSITION_SIZE=0.10       # 10% per trade
NEXUS_MAX_OPEN_POSITIONS=20        # 20 concurrent
```

---

## PERFORMANCE TARGETS

```
Uptime:           99%+ (auto-restart on failures)
API Response:     <100ms average
Data Latency:     2-3 seconds per symbol
Decision Time:    <500ms from signal to execution
Memory Usage:     ~200-300MB
CPU Usage:        <25% average
```

---

## ALERT THRESHOLDS

System alerts when:

- Drawdown exceeds 15%
- Single position exceeds 5%
- Daily trades exceed 20
- Open positions exceed 12
- API connection fails
- Market hours ended
- Risk VaR exceeds limit

---

## TEST COMMANDS

```bash
# Full verification suite
python verify_production_ready.py

# Run all tests
python -m pytest tests/ -v

# Check imports
python -c "import nexus; print('OK')"

# Test Alpaca connection
python nexus_orchestrator.py  # Watch for successful startup
```

---

**Last Updated**: May 6, 2026  
**Status**: ✅ PRODUCTION READY  
**Version**: 1.0.0
