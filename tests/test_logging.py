import io
import logging
import unittest

from ia_admin.logging_utils import SecretRedactionFilter


class SecretRedactionFilterTests(unittest.TestCase):
    def test_redacts_secret_from_message_and_exception_traceback(self) -> None:
        secret = "123456789:secret-telegram-token"
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.addFilter(SecretRedactionFilter(secret))
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test-secret-redaction")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.ERROR)

        try:
            raise RuntimeError(f"request failed for https://api.telegram.org/bot{secret}/getUpdates")
        except RuntimeError:
            logger.exception("Telegram polling failed: %s", secret)

        logged = output.getvalue()
        self.assertNotIn(secret, logged)
        self.assertEqual(logged.count("[REDACTED]"), 2)
        logger.handlers.clear()


if __name__ == "__main__":
    unittest.main()
