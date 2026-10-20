import logging


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secret: str):
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secret:
            return True

        record.msg = record.getMessage().replace(self.secret, "[REDACTED]")
        record.args = ()
        if record.exc_info:
            exception_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = exception_text.replace(self.secret, "[REDACTED]")
        elif record.exc_text:
            record.exc_text = record.exc_text.replace(self.secret, "[REDACTED]")
        return True
