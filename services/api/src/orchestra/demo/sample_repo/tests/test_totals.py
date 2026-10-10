from invoicekit.cart import Cart
from invoicekit.totals import invoice_summary, subtotal, tax_amount


def make_cart() -> Cart:
    cart = Cart()
    cart.add("Widget", 12.50, 2)
    cart.add("Gadget", 5.00, 1)
    return cart


def test_subtotal():
    assert subtotal(make_cart()) == 30.00


def test_tax_applies_after_discount():
    cart = make_cart()
    cart.discount_rate = 0.10
    assert round(tax_amount(cart, 0.10), 2) == 2.70


def test_summary_totals():
    summary = invoice_summary(make_cart(), tax_rate=0.08)
    assert summary["total"] == 32.40
    assert summary["average_item_price"] == 10.00
