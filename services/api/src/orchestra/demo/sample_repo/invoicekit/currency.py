from __future__ import annotations

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def round_money(amount: float) -> float:
    return round(amount + 1e-9, 2)


def format_money(amount: float, currency: str) -> str:
    return f"{SYMBOLS.get(currency, currency + ' ')}{round_money(amount):,.2f}"
