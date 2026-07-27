# Payments

Treat the payment provider as the source of payment state and your database as a reconciled view. Verify webhook signatures, store event IDs for idempotency, and handle out-of-order and repeated delivery. Never store raw card data. Separate checkout intent from fulfillment, and provide manual reconciliation and refund guidance. Do not call live payment APIs without explicit credentials and authorization.
