from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LineItem:
    name: str
    unit_price: float
    quantity: int = 1

    @property
    def subtotal(self) -> float:
        return self.unit_price * self.quantity


@dataclass
class Cart:
    currency: str = "USD"
    items: list[LineItem] = field(default_factory=list)
    discount_rate: float = 0.0

    def add(self, name: str, unit_price: float, quantity: int = 1) -> LineItem:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        item = LineItem(name=name, unit_price=unit_price, quantity=quantity)
        self.items.append(item)
        return item

    @property
    def item_count(self) -> int:
        return sum(item.quantity for item in self.items)
