import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from .config import Settings
from .models import DaikinMeasurement, Measurement

DAIKIN_PROXY_MAC = "AA:BB:CC:DD:EE:FF"


class SensorForwarder:
    def __init__(self, settings: Settings):
        self.enabled = settings.sensor_forward_enabled
        self.target_url = settings.sensor_forward_url
        self.executor = (
            ThreadPoolExecutor(max_workers=2, thread_name_prefix="sensor-forward")
            if self.enabled
            else None
        )
        if self.enabled:
            logging.info("Sensor forwarding enabled: %s", self.target_url)

    def report_ble(self, measurement: Measurement):
        if not self.enabled:
            return
        self._submit(
            {
                "name": measurement.mac,
                "temp": self._format_float(measurement.temperature, 2),
                "humi": self._format_float(measurement.humidity, 2),
                "bat": str(int(measurement.battery)),
                "volt": self._format_float(measurement.voltage, 3),
                "rssi": str(int(measurement.rssi)),
            }
        )

    def report_daikin(self, measurement: DaikinMeasurement):
        if not self.enabled:
            return
        self._submit(
            {
                "name": DAIKIN_PROXY_MAC,
                "temp": self._format_float(measurement.temperature, 2),
                "humi": self._format_float(measurement.humidity, 2),
                "bat": "0",
                "volt": "0",
                "rssi": "0",
            }
        )

    def close(self):
        if self.executor is None:
            return
        self.executor.shutdown(wait=False, cancel_futures=False)
        self.executor = None

    def _submit(self, params: dict[str, str]):
        if self.executor is None:
            return
        try:
            self.executor.submit(self._send, params)
        except RuntimeError:
            logging.warning("Sensor forwarding skipped because reporter is shutting down")

    def _send(self, params: dict[str, str]):
        try:
            final_url = self._build_url(params)
            with urlopen(final_url, timeout=3) as resp:
                status = resp.getcode()
            if status >= 400:
                logging.warning("Sensor forwarding got HTTP %s from %s", status, self.target_url)
        except Exception as exc:
            logging.warning("Sensor forwarding failed to %s: %s", self.target_url, exc)

    def _build_url(self, params: dict[str, str]) -> str:
        parsed = urlsplit(self.target_url)
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        query_map = dict(query_pairs)
        query_map.update(params)
        merged_query = urlencode(query_map)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, merged_query, parsed.fragment))

    @staticmethod
    def _format_float(value: float, precision: int) -> str:
        return f"{value:.{precision}f}"
