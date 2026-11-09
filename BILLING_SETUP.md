# Forma plans and Razorpay

## Administration
Open Admin dashboard → Integrations → Plans & payments.

Proposed offers: Free ₹0, Pro ₹299/month, Premium ₹799/month. These are editable draft proposals, not approved prices. Free includes one daily draft and 10 monthly external-agent runs; Pro proposes 3 drafts/day and 100 runs/month with scheduling; Premium proposes 10 drafts/day and 500 runs/month. Administrators retain full existing workflows. Limits are enforced on the server. Provider quotas and the existing Gemini safety caps still apply.

Edit names, INR prices, limits, automation entitlement, and descriptions. Save draft, review, then Approve saved plan version. Unsaved edits are not approved. The previous approved offer remains public until the replacement is approved. Existing subscribers retain the exact price and entitlements from their purchased version. Feature-description text is display copy; only the explicit usage/automation controls enforce access.

## Razorpay setup
1. Obtain test API keys in https://dashboard.razorpay.com/app/keys.
2. Add Key ID, key secret, and a separate webhook signing secret in Plans & payments. Secrets are encrypted and not returned to the browser after saving.
3. Set the matching environment and enable test checkout only after approving an offer. Saving makes no external request.
4. Configure a public HTTPS webhook ending `/api/billing/webhook` in Razorpay for subscription lifecycle events (activated, charged, pending, halted, cancelled, completed, paused, resumed, authenticated). Localhost cannot receive remote webhooks.
5. Use a test checkout, verify status, and test cancellation and lifecycle delivery. Test subscriptions deliberately do not grant real paid access.
6. Before live activation, deploy behind HTTPS, configure the production origin/cookie settings, establish customer-facing billing/support/refund terms, and verify the complete test integration. Add fresh live credentials and webhook secret; explicitly authorize live payments in the admin form.

The current local HTTP server is a development runtime. Its existing Origin check and session-cookie settings require production deployment work; this change does not deploy the service or make it commercially launch-ready.

## Payment behavior
Razorpay monthly subscriptions use quantity one and up to 120 monthly cycles. Each checkout creates a provider plan from the approved price; prices and limits are not supplied by the customer. There is no initiation fee. Provider charges/taxes are outside this app; configure your final inclusive customer pricing before approving offers.

Checkout signatures use the saved subscription owner and Razorpay HMAC format. Signed webhooks fetch the current subscription state from Razorpay rather than trusting event order. Duplicate events are ignored. Paid access requires a live active subscription and unexpired current period; cancelled subscriptions retain access to the provider's current period end. Browser-only success never grants access. Expired or pending/halted subscriptions fall back to Free, and the scheduler rechecks entitlement before publishing. Existing manual drafts remain private and available.

Members can refresh status and cancel future renewals from Plans & billing. A failed or ambiguous API request must be checked in Razorpay before retrying; the UI does not automatically retry charges. Switching environment or API account is a maintenance operation: existing subscriptions must be reconciled before changing credentials. Credentials for previous accounts are not retained.

Refund/dispute administration, tax invoices, migrations between subscriptions, and production hosting are not implemented in this milestone. Review refunds and disputes directly in Razorpay. Do not activate live billing before defining those operational procedures.

## References
- https://razorpay.com/docs/payments/subscriptions/integration-guide/
- https://razorpay.com/docs/api/payments/subscriptions/create-subscription/
- https://razorpay.com/docs/webhooks/validate-test/
