# Global and multi-user launch checklist

This is an engineering deployment checklist, not legal advice or a claim that Alpha-Junior is authorized to trade in every country.

For each target jurisdiction and each broker/account, maintain a signed launch record covering:

- Broker account availability, API permission, paper/live environment, supported asset types, account residency and eligibility.
- Local licensing/registration requirements for the operator, investment manager, advisory activity, signal distribution, copy trading, custody or execution service.
- Exchange and venue rules, market hours/holidays, order types, short-sale constraints, tick sizes, lot sizes, price bands and circuit breakers.
- Market-data entitlement, redistribution rights, delayed/real-time status, venue coverage, derived data and retention requirements.
- Currency conversion, funding/settlement, margin/leverage, borrow availability, financing and corporate action handling.
- Tax/account reporting, record retention, privacy/data residency and consumer/institutional disclosures.
- Local incident contacts, broker escalation, business continuity and recovery test.

## Deployment tiers

1. **Research only:** historical datasets and reproducible experiments; no broker credentials.
2. **Simulation:** synthetic data clearly labeled; no real broker orders; never mix simulated fills with live portfolio state.
3. **Broker paper:** sandbox/paper account, real market data if licensed, all order paths and reconciliation tests.
4. **Single-account pilot:** only after approvals, with strict notional/loss caps, manual monitoring and an operator kill switch.
5. **Multi-user/multi-account:** account isolation, roles, separate audit ledgers, per-user approvals, secrets vault, tenant testing and incident runbooks.
6. **Regional rollout:** repeat broker, market, regulatory, security and tax review for each jurisdiction/instrument combination.

No global rollout should be inferred from one passing CI workflow or one broker paper account.
