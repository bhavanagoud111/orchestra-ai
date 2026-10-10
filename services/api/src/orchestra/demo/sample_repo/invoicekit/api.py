"""HTTP handler used by the billing service (framework-agnostic)."""

from __future__ import annotations

from invoicekit.cart import Cart
from invoicekit.totals import invoice_summary


def handle_summary_request(payload: dict) -> dict:
    cart = Cart(currency=payload.get("currency", "USD"), discount_rate=payload.get("discount_rate", 0.0))
    for line in payload.get("items", []):
        cart.add(line["name"], float(line["unit_price"]), int(line.get("quantity", 1)))
    return {"status": "ok", "summary": invoice_summary(cart, tax_rate=payload.get("tax_rate", 0.0))}
