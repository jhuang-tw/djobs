"""Shipping zone configuration and cost estimation by weight."""


class ShippingZone:
    def __init__(self, name: str, base_rate: float, per_kg_rate: float):
        self.name = name
        self.base_rate = base_rate
        self.per_kg_rate = per_kg_rate

    def calculate(self, weight_kg: float) -> float:
        return self.base_rate + (self.per_kg_rate * weight_kg)

class ShippingCalculator:
    def __init__(self):
        self._zones: dict[str, ShippingZone] = {}

    def register_zone(self, zone: ShippingZone) -> None:
        self._zones[zone.name] = zone

    def estimate(self, zone_name: str, weight_kg: float) -> float | None:
        zone = self._zones.get(zone_name)
        if zone is None:
            return None
        return zone.calculate(weight_kg)

    def cheapest_option(self, weight_kg: float) -> tuple[str, float] | None:
        if not self._zones:
            return None
        best = min(self._zones.items(), key=lambda z: z[1].calculate(weight_kg))
        return best[0], best[1].calculate(weight_kg)

    def available_zones(self) -> list[str]:
        return list(self._zones.keys())
