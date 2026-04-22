#!/usr/bin/env python3
import logging
import os
import re
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Protocol

import bluetooth._bluetooth as bluez
from sqlalchemy import Index, create_engine
from sqlalchemy.dialects.mysql import BIGINT, DECIMAL, SMALLINT, TINYINT, TIMESTAMP, VARCHAR
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .bluetooth_utils import (
    disable_le_scan,
    enable_le_scan,
    parse_le_advertising_events,
    raw_packet_to_str,
    toggle_device,
)

UTC_PLUS_8 = timedelta(hours=8)
MYSQL_TABLE_NAME = "lywsd03mmc_readings"


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def normalize_mac(mac: str) -> str:
    cleaned = mac.strip().upper().replace("-", ":")
    if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", cleaned):
        raise ValueError(f"Invalid MAC address: {mac}")
    return cleaned


def now_utc_plus_8() -> datetime:
    return datetime.utcnow() + UTC_PLUS_8


@dataclass
class Settings:
    ble_interface: int
    watchdog_seconds: int
    log_level: str
    allowed_macs: set[str]
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    mysql_create_table: bool
    skip_mysql: bool

    @staticmethod
    def from_env() -> "Settings":
        skip_mysql = env_bool("SKIP_MYSQL", False)
        required = {
            "MYSQL_HOST": os.getenv("MYSQL_HOST"),
            "MYSQL_USER": os.getenv("MYSQL_USER"),
            "MYSQL_PASSWORD": os.getenv("MYSQL_PASSWORD"),
            "MYSQL_DATABASE": os.getenv("MYSQL_DATABASE"),
        }
        if not skip_mysql:
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        raw_macs = os.getenv("SENSOR_MACS", "")
        allowed_macs = set()
        if raw_macs.strip():
            for item in raw_macs.split(","):
                allowed_macs.add(normalize_mac(item))

        return Settings(
            ble_interface=env_int("BLE_INTERFACE", 0),
            watchdog_seconds=env_int("WATCHDOG_SECONDS", 0),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            allowed_macs=allowed_macs,
            mysql_host=required["MYSQL_HOST"] or "",
            mysql_port=env_int("MYSQL_PORT", 3306),
            mysql_user=required["MYSQL_USER"] or "",
            mysql_password=required["MYSQL_PASSWORD"] or "",
            mysql_database=required["MYSQL_DATABASE"] or "",
            mysql_create_table=env_bool("MYSQL_CREATE_TABLE", True),
            skip_mysql=skip_mysql,
        )


@dataclass
class Measurement:
    mac: str
    temperature: float
    humidity: float
    voltage: float
    battery: int
    rssi: int
    timestamp: datetime


class Base(DeclarativeBase):
    pass


class Reading(Base):
    __tablename__ = MYSQL_TABLE_NAME
    __table_args__ = (
        Index("idx_mac_timestamp", "mac", "timestamp"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    mac: Mapped[str] = mapped_column(VARCHAR(17), nullable=False)
    temperature: Mapped[float] = mapped_column(DECIMAL(5, 2), nullable=False)
    humidity: Mapped[float] = mapped_column(DECIMAL(5, 2), nullable=False)
    voltage: Mapped[float] = mapped_column(DECIMAL(5, 3), nullable=False)
    battery: Mapped[int] = mapped_column(TINYINT(unsigned=True), nullable=False)
    rssi: Mapped[int] = mapped_column(SMALLINT, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)


class Writer(Protocol):
    def insert(self, measurement: Measurement):
        ...

    def close(self):
        ...


class MySQLWriter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = None
        self.session_factory = None
        self._connect()
        if settings.mysql_create_table:
            self.ensure_table()

    def _connect(self):
        self.close()
        self.engine = create_engine(
            URL.create(
                "mysql+mysqlconnector",
                username=self.settings.mysql_user,
                password=self.settings.mysql_password,
                host=self.settings.mysql_host,
                port=self.settings.mysql_port,
                database=self.settings.mysql_database,
            ),
            pool_pre_ping=True,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.engine.connect():
            pass
        logging.info(
            "Connected to MySQL %s:%s/%s",
            self.settings.mysql_host,
            self.settings.mysql_port,
            self.settings.mysql_database,
        )

    def ensure_table(self):
        Base.metadata.create_all(self.engine, tables=[Reading.__table__])
        logging.info("Ensured table exists: %s", MYSQL_TABLE_NAME)

    def insert(self, measurement: Measurement):
        self._insert_with_retry(measurement)
        logging.info(
            "Inserted %s t=%.2f h=%.2f v=%.3f b=%s rssi=%s",
            measurement.mac,
            measurement.temperature,
            measurement.humidity,
            measurement.voltage,
            measurement.battery,
            measurement.rssi,
        )

    def _insert_with_retry(self, measurement: Measurement):
        try:
            self._insert_once(measurement)
        except SQLAlchemyError as exc:
            logging.warning("MySQL write failed (%s), retrying once...", exc)
            self._connect()
            self._insert_once(measurement)

    def _insert_once(self, measurement: Measurement):
        session = self.session_factory()
        try:
            session.add(
                Reading(
                    mac=measurement.mac,
                    temperature=round(measurement.temperature, 2),
                    humidity=round(measurement.humidity, 2),
                    voltage=round(measurement.voltage, 3),
                    battery=int(measurement.battery),
                    rssi=int(measurement.rssi),
                    timestamp=measurement.timestamp,
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self):
        self.session_factory = None
        if self.engine is not None:
            try:
                self.engine.dispose()
            except Exception:
                pass
            self.engine = None


class NoopWriter:
    def insert(self, measurement: Measurement):
        logging.info(
            "SKIP_MYSQL mode %s t=%.2f h=%.2f v=%.3f b=%s rssi=%s ts=%s",
            measurement.mac,
            measurement.temperature,
            measurement.humidity,
            measurement.voltage,
            measurement.battery,
            measurement.rssi,
            measurement.timestamp.isoformat(sep=" "),
        )

    def close(self):
        return


class PassiveScanner:
    def __init__(self, settings: Settings, writer: Writer):
        self.settings = settings
        self.writer = writer
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
        self.writer.close()
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
        self.writer.insert(measurement)

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
    if settings.skip_mysql:
        logging.warning("SKIP_MYSQL enabled. Data will not be written to database.")
        writer = NoopWriter()
    else:
        writer = MySQLWriter(settings)
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
