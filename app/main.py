#!/usr/bin/env python3

import logging
import signal
import threading

from .config import Settings
from .http_server import HttpReportServer
from .reporter import SensorForwarder
from .scanner import PassiveScanner
from .storage import MySQLWriter, NoopStorage


def build_storage(settings: Settings):
    if settings.skip_mysql:
        logging.warning("SKIP_MYSQL enabled. Data will not be written to database.")
        return NoopStorage()
    return MySQLWriter(settings)


def main():
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        logging.basicConfig(
            level=logging.ERROR,
            format="%(asctime)s %(levelname)s %(message)s",
            force=True,
        )
        logging.error("%s", exc)
        raise SystemExit(2)

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    storage = build_storage(settings)
    forwarder = SensorForwarder(settings)
    scanner = PassiveScanner(settings, storage, forwarder) if settings.ble_scanner_enabled else None
    http_server = (
        HttpReportServer(settings, storage, forwarder) if settings.http_server_enabled else None
    )
    stop_event = threading.Event()

    def shutdown():
        if stop_event.is_set():
            return
        stop_event.set()
        if http_server is not None:
            http_server.stop()
        if scanner is not None:
            scanner.stop()
        forwarder.close()
        storage.close()

    def handle_signal(sig, _frame):
        logging.info("Signal received: %s", sig)
        shutdown()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        if http_server is not None:
            http_server.start()
        if scanner is not None:
            scanner.start()
        else:
            while not stop_event.wait(1):
                pass
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()


if __name__ == "__main__":
    main()
