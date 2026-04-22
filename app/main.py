#!/usr/bin/env python3

import logging
import signal

from .config import Settings
from .scanner import PassiveScanner
from .storage import MySQLWriter, NoopWriter


def build_writer(settings: Settings):
    if settings.skip_mysql:
        logging.warning("SKIP_MYSQL enabled. Data will not be written to database.")
        return NoopWriter()
    return MySQLWriter(settings)


def main():
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        logging.basicConfig(level=logging.ERROR, format="%(asctime)s %(levelname)s %(message)s")
        logging.error("%s", exc)
        raise SystemExit(2)

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    writer = build_writer(settings)
    scanner = PassiveScanner(settings, writer)

    def handle_signal(sig, _frame):
        logging.info("Signal received: %s", sig)
        scanner.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        scanner.start()
    except KeyboardInterrupt:
        pass
    finally:
        scanner.stop()


if __name__ == "__main__":
    main()
