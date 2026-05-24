"""Payment processing model with method types, completion, and refund logic."""

from enum import Enum

class PaymentMethod(Enum):
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    BANK_TRANSFER = "bank_transfer"
    CASH = "cash"

class Payment:
    def __init__(self, order_id: str, amount: float, method: PaymentMethod):
        self.order_id = order_id
        self.amount = amount
        self.method = method
        self.is_completed = False

    def process(self) -> bool:
        if self.amount <= 0:
            return False
        self.is_completed = True
        return True

    def refund(self) -> float:
        if not self.is_completed:
            return 0.0
        self.is_completed = False
        return self.amount
