"""Multi-channel notification service with queued delivery."""

from enum import Enum

class Channel(Enum):
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    WEBHOOK = "webhook"

class Notification:
    def __init__(self, recipient: str, channel: Channel, message: str):
        self.recipient = recipient
        self.channel = channel
        self.message = message
        self.delivered = False

    def mark_delivered(self) -> None:
        self.delivered = True

class NotificationService:
    def __init__(self):
        self._queue: list[Notification] = []
        self._sent: list[Notification] = []

    def enqueue(self, recipient: str, channel: Channel, message: str) -> None:
        self._queue.append(Notification(recipient, channel, message))

    def process_queue(self) -> int:
        count = 0
        while self._queue:
            n = self._queue.pop(0)
            n.mark_delivered()
            self._sent.append(n)
            count += 1
        return count

    def pending_count(self) -> int:
        return len(self._queue)
