# invoicekit

Small library for building invoices from shopping carts: line items, discounts,
tax, and a JSON summary used by the billing service.

```python
from invoicekit.cart import Cart
from invoicekit.totals import invoice_summary

cart = Cart(currency="USD")
cart.add("Widget", unit_price=12.50, quantity=2)
print(invoice_summary(cart, tax_rate=0.08))
```
