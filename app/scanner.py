import logging
import threading
import time
from typing import Optional

import bluetooth._bluetooth as bluez

from .bluetooth_utils import (
    disable_le_scan,
    enable_le_scan,
    parse_le_advertising_events,
    raw_packet_to_str,
    toggle_device,
)
from .config import Settings, normalize_mac
from .models import Measurement, now_utc_plus_8
from .storage import Storage


class PassiveScanner:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage
        self.sock = None
        self.stop_event = threading.Event()
        self.last_packet_time = time.monotonic()
        self.adv_counter: dict[str, str] = {}

    def start(self):
        toggle_device(self.settings.ble_interface, True)
        self.sock = bluez.hci_open_dev(self.settings.ble_interface)
        enable_le_scan(self.sock, filter_duplicates=False)
        self.last_packet_time = time.monotonic()

        if self.settings.watchdog_seconds > 0:
            thread = threading.Thread(target=self._watchdog, daemon=True)
            thread.start()
            logging.info("Watchdog enabled: %s seconds", self.settings.watchdog_seconds)

        logging.info("Passive scan started on hci%s", self.settings.ble_interface)
        parse_le_advertising_events(self.sock, handler=self._packet_handler, debug=False)

    def stop(self):
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        if self.sock is not None:
            try:
                disable_le_scan(self.sock)
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.storage.close()
        logging.info("Scanner stopped")

    def _watchdog(self):
        while not self.stop_event.is_set():
            time.sleep(1)
            elapsed = time.monotonic() - self.last_packet_time
            if elapsed <= self.settings.watchdog_seconds:
                continue
            logging.warning(
                "No BLE packet received for %.1fs, restarting scan",
                elapsed,
            )
            if self.sock is None:
                continue
            try:
                disable_le_scan(self.sock)
                enable_le_scan(self.sock, filter_duplicates=False)
                self.last_packet_time = time.monotonic()
            except Exception as exc:
                logging.error("Watchdog restart failed: %s", exc)

    def _packet_handler(self, mac: str, adv_type: int, data: bytes, rssi: int):
        if self.stop_event.is_set():
            return
        self.last_packet_time = time.monotonic()

        mac = normalize_mac(mac)
        if self.settings.allowed_macs and mac not in self.settings.allowed_macs:
            return

        data_str = raw_packet_to_str(data)
        measurement = self._decode_atc_or_custom(mac, adv_type, data_str, rssi)
        if measurement is None:
            return
        self.storage.insert_ble(measurement)

    def _decode_atc_or_custom(
        self, mac: str, adv_type: int, data_str: str, rssi: int
    ) -> Optional[Measurement]:
        preamble = "161a18"
        packet_start = data_str.find(preamble)
        if packet_start == -1:
            return None

        offset = packet_start + len(preamble)
        data_identifier = data_str[offset - 4 : offset].upper()
        if data_identifier != "1A18":
            return None

        payload = data_str[offset:]
        if len(payload) not in (16, 22, 26, 30):
            return None
        if len(payload) in (16, 22):
            logging.debug("Skip encrypted packet from %s", mac)
            return None

        mac_key = mac.replace(":", "")
        adv_number = payload[-4:-2] if len(payload) == 30 else payload[-2:]
        if self.adv_counter.get(mac_key) == adv_number:
            return None
        self.adv_counter[mac_key] = adv_number

        if len(payload) == 26:
            logging.debug("BLE packet - ATC1441: %s %02x %s %d", mac, adv_type, data_str, rssi)
            temperature = int.from_bytes(
                bytearray.fromhex(payload[12:16]), byteorder="big", signed=True
            ) / 10.0
            humidity = int(payload[16:18], 16)
            battery = int(payload[18:20], 16)
            voltage = int(payload[20:24], 16) / 1000.0
        else:
            logging.debug("BLE packet - Custom: %s %02x %s %d", mac, adv_type, data_str, rssi)
            temperature = int.from_bytes(
                bytearray.fromhex(payload[12:16]), byteorder="little", signed=True
            ) / 100.0
            humidity = int.from_bytes(
                bytearray.fromhex(payload[16:20]), byteorder="little", signed=False
            ) / 100.0
            voltage = int.from_bytes(
                bytearray.fromhex(payload[20:24]), byteorder="little", signed=False
            ) / 1000.0
            battery = int.from_bytes(
                bytearray.fromhex(payload[24:26]), byteorder="little", signed=False
            )

        return Measurement(
            mac=mac,
            temperature=temperature,
            humidity=humidity,
            voltage=voltage,
            battery=battery,
            rssi=rssi,
            timestamp=now_utc_plus_8(),
        )
