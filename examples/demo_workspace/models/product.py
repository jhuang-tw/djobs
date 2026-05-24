"""Product model with stock availability, discounts, and restocking."""


class Product:
    def __init__(self, sku: str, name: str, price: float, stock: int):
        self.sku = sku
        self.name = name
        self.price = price
        self.stock = stock

    def is_available(self) -> bool:
        return self.stock > 0

    def apply_discount(self, percent: float) -> float:
        return self.price * (1 - percent / 100)

    def restock(self, quantity: int) -> None:
        self.stock += quantity
