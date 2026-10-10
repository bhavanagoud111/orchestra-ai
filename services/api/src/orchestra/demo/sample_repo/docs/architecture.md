# invoicekit architecture

- `cart.py` — `Cart` and `LineItem` value objects. Carts may be empty: the billing
  service creates a cart before items are added and requests a summary to render
  the checkout page.
- `totals.py` — pure functions that compute subtotal, discount, tax and the
  summary dictionary returned to the billing service.
- `currency.py` — rounding and display formatting.
- `api.py` — request handler that builds a cart from JSON and returns a summary.

Invariant: `invoice_summary` must never raise for a valid cart, including an empty one.
