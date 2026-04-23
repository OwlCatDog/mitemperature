import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import Settings
from .models import DaikinMeasurement, now_utc_plus_8
from .reporter import SensorForwarder
from .storage import Storage


def _first_value(query: dict[str, list[str]], *names: str) -> str:
    for name in names:
        values = query.get(name)
        if values and values[0].strip() != "":
            return values[0].strip()
    raise ValueError(f"Missing required query parameter: {' or '.join(names)}")


def parse_daikin_measurement(query: dict[str, list[str]]) -> DaikinMeasurement:
    return DaikinMeasurement(
        co2=int(_first_value(query, "co2")),
        eco2=int(_first_value(query, "eco2")),
        pm1=float(_first_value(query, "pm1")),
        pm25=float(_first_value(query, "pm25")),
        pm10=float(_first_value(query, "pm10")),
        tvoc=int(_first_value(query, "tvoc")),
        temperature=float(_first_value(query, "temp", "temperature")),
        humidity=float(_first_value(query, "humi", "humidity")),
        timestamp=now_utc_plus_8(),
    )


class HttpReportServer:
    def __init__(self, settings: Settings, storage: Storage, forwarder: SensorForwarder | None = None):
        self.settings = settings
        self.storage = storage
        self.forwarder = forwarder
        self.server = None
        self.thread = None

    def start(self):
        self.server = ThreadingHTTPServer(
            (self.settings.http_server_host, self.settings.http_server_port),
            self._build_handler(),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logging.info(
            "HTTP report server listening on %s:%s%s",
            self.settings.http_server_host,
            self.settings.http_server_port,
            self.settings.http_report_path,
        )

    def stop(self):
        if self.server is None:
            return
        try:
            self.server.shutdown()
            self.server.server_close()
        finally:
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None
        logging.info("HTTP report server stopped")

    def _build_handler(self):
        storage = self.storage
        forwarder = self.forwarder
        report_path = self.settings.http_report_path

        class ReportHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == "/healthz":
                    self._send_text(200, "ok\n")
                    return
                if parsed.path != report_path:
                    self._send_text(404, "not found\n")
                    return

                try:
                    measurement = parse_daikin_measurement(parse_qs(parsed.query, keep_blank_values=True))
                    storage.insert_daikin(measurement)
                    if forwarder is not None:
                        forwarder.report_daikin(measurement)
                except ValueError as exc:
                    self._send_text(400, f"{exc}\n")
                    return
                except Exception as exc:
                    logging.exception("Failed to process daikin report: %s", exc)
                    self._send_text(500, "internal error\n")
                    return

                self._send_text(200, "ok\n")

            def log_message(self, fmt: str, *args):
                logging.info("HTTP %s - %s", self.address_string(), fmt % args)

            def _send_text(self, status_code: int, body: str):
                payload = body.encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return ReportHandler
