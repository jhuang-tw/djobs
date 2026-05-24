"""Pricing rules engine with quantity-based discount calculation."""


class PricingRule:
    def __init__(self, name: str, discount_percent: float, min_quantity: int = 0):
        self.name = name
        self.discount_percent = discount_percent
        self.min_quantity = min_quantity

    def applies_to(self, quantity: int) -> bool:
        return quantity >= self.min_quantity

    def calculate_discount(self, price: float) -> float:
        return price * (self.discount_percent / 100)

class PricingEngine:
    def __init__(self):
        self._rules: list[PricingRule] = []

    def add_rule(self, rule: PricingRule) -> None:
        self._rules.append(rule)

    def best_discount(self, price: float, quantity: int) -> float:
        applicable = [r for r in self._rules if r.applies_to(quantity)]
        if not applicable:
            return 0.0
        return max(r.calculate_discount(price) for r in applicable)

    def final_price(self, unit_price: float, quantity: int) -> float:
        discount = self.best_discount(unit_price, quantity)
        return max(0, (unit_price - discount) * quantity)
