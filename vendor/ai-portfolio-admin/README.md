# AI Portfolio Admin

Shared admin/auth/credits/payments package for portfolio apps such as PlodAI and OpenAI Vectorstore2.

This repo owns generic account and money-adjacent contracts:

- Clerk metadata helpers for `active`, `role`, and `credit_floor_usd`.
- Admin user summaries and manual credit grant contracts.
- Free-credit request contracts and policy evaluation, including configurable trusted-network grants such as LinkedIn connections.
- Provider-neutral payment status and PayPal receipt-review contracts.
- Reusable React/TypeScript admin panel contracts and callback-driven components.

Host apps still own app-domain billing events, database migrations, OpenAI usage metadata, storage, and app-specific APIs. The shared package should be consumed through a narrow adapter in each host app.

## Local Checks

From this repo, using a neighboring app venv if needed:

```bash
../openai-vectorstore2/.venv/bin/python -m pytest -q
../openai-vectorstore2/.venv/bin/pyright
../openai-vectorstore2/.venv/bin/ruff check src tests
```
