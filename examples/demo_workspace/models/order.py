"""Order and order item models with total and item count computation."""

from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class OrderItem:
    product_sku: str
    quantity: int
    unit_price: float

    def subtotal(self) -> float:
        return self.quantity * self.unit_price

@dataclass
class Order:
    customer_id: str
    items: list = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def total(self) -> float:
        return sum(item.subtotal() for item in self.items)

    def item_count(self) -> int:
        return sum(item.quantity for item in self.items)
