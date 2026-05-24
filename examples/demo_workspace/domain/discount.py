"""Coupon code management with expiration and usage limits."""

from datetime import datetime

class CouponCode:
    def __init__(self, code: str, discount_percent: float, expires_at: datetime, max_uses: int = 100):
        self.code = code
        self.discount_percent = discount_percent
        self.expires_at = expires_at
        self.max_uses = max_uses
        self.used_count = 0

    def is_valid(self) -> bool:
        return self.used_count < self.max_uses and datetime.now() < self.expires_at

    def apply(self, amount: float) -> float:
        if not self.is_valid():
            return amount
        self.used_count += 1
        return amount * (1 - self.discount_percent / 100)

    def remaining_uses(self) -> int:
        return max(0, self.max_uses - self.used_count)
