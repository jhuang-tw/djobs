"""Email sending service with single and bulk delivery support."""


class EmailService:
    def __init__(self, smtp_host: str, smtp_port: int):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self._sent: list[dict] = []

    def send(self, to: str, subject: str, body: str) -> bool:
        if not to or not subject:
            return False
        self._sent.append({"to": to, "subject": subject, "body": body})
        return True

    def send_bulk(self, recipients: list[str], subject: str, body: str) -> int:
        count = 0
        for r in recipients:
            if self.send(r, subject, body):
                count += 1
        return count

    def sent_count(self) -> int:
        return len(self._sent)
