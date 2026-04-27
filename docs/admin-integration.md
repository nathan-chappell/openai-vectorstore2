# Admin Integration

The public repository owns the app-domain services and can run without any private packages. Shared production auth, admin, and payment behavior can later be supplied by the private `ai-portfolio-admin` package through a narrow adapter boundary.

## Default Mode

Default mode is selected with:

```bash
ADMIN_INTEGRATION_PROVIDER=default
```

This mode uses the in-repo auth and billing services:

- local-dev auth works when `ALLOW_LOCAL_DEV_AUTH=true`;
- Clerk auth works through the documented Clerk env vars;
- admin endpoints can activate users and grant manual credit;
- usage debits and billing status are stored in this app's database;
- payment checkout is deliberately unavailable;
- optional PayPal receipt uploads can grant immediate receipt-backed credit when `PAYPAL_RECIPIENT_EMAIL` is configured;
- users can request free credit for admin approval through the same account/admin boundary.

The default provider is the only mode required for a public clone, local tests, and early beta demos where credit is manually granted, requested as free beta credit, or granted from PayPal receipt evidence.

## Private Shared Mode

Private shared mode is selected with:

```bash
ADMIN_INTEGRATION_PROVIDER=ai_portfolio_admin
ADMIN_SHARED_MODULE=backend.app.admin.shared_adapter
```

The private source of truth is expected to be:

```bash
git@github.com:nathan-chappell/ai-portfolio-admin.git
```

The host app should mount or install that package outside the public app-domain modules. A likely setup is a git submodule such as `vendor/ai-portfolio-admin` plus an editable install in the app environment. The public app imports private implementation details only through `backend.app.admin`.

The host app should expose app-owned factories from `ADMIN_SHARED_MODULE`, starting with:

- `build_auth_service(settings)` for an auth service compatible with this app's `AuthService` boundary;
- `payment_integration_status(settings)` for checkout availability and provider status.

Keep imports one-way: host apps import shared contracts/helpers from `vendor/ai-portfolio-admin`, while the shared submodule must not import host modules such as `backend.app`.

The current public PayPal flow does not require a PayPal business ID or API integration. It creates a reference code, asks the user to send payment to the configured PayPal recipient, accepts a text/PDF/email-style receipt upload, checks amount/currency/recipient/reference, grants immediate credit when the receipt looks safe enough, and leaves final confirmation or rejection/reversal to the admin panel.

Future payment work should stay provider-neutral at the app boundary: create checkout requests, verify provider callbacks or webhooks, idempotently grant credits, store provider references, and expose updated balances. PayPal-specific order creation, approval, capture, webhook verification, refund, and chargeback logic belongs in the private shared package plan before it replaces the receipt-first flow.
