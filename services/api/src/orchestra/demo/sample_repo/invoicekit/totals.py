from __future__ import annotations

from invoicekit.cart import Cart
from invoicekit.currency import format_money, round_money


def subtotal(cart: Cart) -> float:
    return sum(item.subtotal for item in cart.items)


def discount_amount(cart: Cart) -> float:
    return subtotal(cart) * cart.discount_rate


def tax_amount(cart: Cart, tax_rate: float) -> float:
    return (subtotal(cart) - discount_amount(cart)) * tax_rate


def average_item_price(cart: Cart) -> float:
    return subtotal(cart) / cart.item_count


def invoice_summary(cart: Cart, tax_rate: float = 0.0) -> dict[str, object]:
    total = subtotal(cart) - discount_amount(cart) + tax_amount(cart, tax_rate)
    return {
        "currency": cart.currency,
        "items": cart.item_count,
        "subtotal": round_money(subtotal(cart)),
        "discount": round_money(discount_amount(cart)),
        "tax": round_money(tax_amount(cart, tax_rate)),
        "total": round_money(total),
        "average_item_price": round_money(average_item_price(cart)),
        "display_total": format_money(total, cart.currency),
    }
