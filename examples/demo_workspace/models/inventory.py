"""Inventory stock management with add, remove, and low-stock detection."""


class InventoryManager:
    def __init__(self):
        self._stock: dict[str, int] = {}

    def add_stock(self, sku: str, quantity: int) -> None:
        self._stock[sku] = self._stock.get(sku, 0) + quantity

    def remove_stock(self, sku: str, quantity: int) -> bool:
        current = self._stock.get(sku, 0)
        if current < quantity:
            return False
        self._stock[sku] = current - quantity
        return True

    def check_stock(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def low_stock_items(self, threshold: int = 5) -> list[str]:
        return [sku for sku, qty in self._stock.items() if qty < threshold]
