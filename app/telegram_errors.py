"""Shared delivery errors, including when the bot runs as python -m app.telegram."""


class DeliveryError(Exception):
    def __init__(self, code='telegram_unavailable', retry_after=0):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after
