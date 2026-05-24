"""Regional tax rule management and tax breakdown calculation."""


class TaxRule:
    def __init__(self, region: str, rate: float, name: str = ""):
        self.region = region
        self.rate = rate
        self.name = name or f"Tax {region}"

    def compute(self, amount: float) -> float:
        return round(amount * self.rate, 2)

class TaxCalculator:
    def __init__(self):
        self._rules: dict[str, list[TaxRule]] = {}

    def add_rule(self, rule: TaxRule) -> None:
        self._rules.setdefault(rule.region, []).append(rule)

    def total_tax(self, region: str, amount: float) -> float:
        rules = self._rules.get(region, [])
        return sum(r.compute(amount) for r in rules)

    def tax_breakdown(self, region: str, amount: float) -> list[dict]:
        rules = self._rules.get(region, [])
        return [{"name": r.name, "rate": r.rate, "amount": r.compute(amount)} for r in rules]

    def effective_rate(self, region: str) -> float:
        rules = self._rules.get(region, [])
        return sum(r.rate for r in rules)
