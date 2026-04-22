import logging
from datetime import datetime
from typing import Callable, Protocol

from sqlalchemy import Index, create_engine
from sqlalchemy.dialects.mysql import BIGINT, DECIMAL, INTEGER, SMALLINT, TIMESTAMP, TINYINT, VARCHAR
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import Settings
from .models import DaikinMeasurement, Measurement

BLE_TABLE_NAME = "lywsd03mmc_readings"
DAIKIN_TABLE_NAME = "daikin_readings"


class Base(DeclarativeBase):
    pass


class Reading(Base):
    __tablename__ = BLE_TABLE_NAME
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


class DaikinReading(Base):
    __tablename__ = DAIKIN_TABLE_NAME
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    co2: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    eco2: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    pm1: Mapped[float] = mapped_column(DECIMAL(8, 2), nullable=False)
    pm25: Mapped[float] = mapped_column(DECIMAL(8, 2), nullable=False)
    pm10: Mapped[float] = mapped_column(DECIMAL(8, 2), nullable=False)
    tvoc: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    temperature: Mapped[float] = mapped_column(DECIMAL(5, 2), nullable=False)
    humidity: Mapped[float] = mapped_column(DECIMAL(5, 2), nullable=False)


class Storage(Protocol):
    def insert_ble(self, measurement: Measurement):
        ...

    def insert_daikin(self, measurement: DaikinMeasurement):
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
        Base.metadata.create_all(self.engine)
        logging.info("Ensured tables exist: %s, %s", BLE_TABLE_NAME, DAIKIN_TABLE_NAME)

    def insert_ble(self, measurement: Measurement):
        self._insert_with_retry(
            lambda: Reading(
                mac=measurement.mac,
                temperature=round(measurement.temperature, 2),
                humidity=round(measurement.humidity, 2),
                voltage=round(measurement.voltage, 3),
                battery=int(measurement.battery),
                rssi=int(measurement.rssi),
                timestamp=measurement.timestamp,
            )
        )
        logging.info(
            "Inserted %s t=%.2f h=%.2f v=%.3f b=%s rssi=%s",
            measurement.mac,
            measurement.temperature,
            measurement.humidity,
            measurement.voltage,
            measurement.battery,
            measurement.rssi,
        )

    def insert_daikin(self, measurement: DaikinMeasurement):
        self._insert_with_retry(
            lambda: DaikinReading(
                co2=int(measurement.co2),
                eco2=int(measurement.eco2),
                pm1=round(measurement.pm1, 2),
                pm25=round(measurement.pm25, 2),
                pm10=round(measurement.pm10, 2),
                tvoc=int(measurement.tvoc),
                temperature=round(measurement.temperature, 2),
                humidity=round(measurement.humidity, 2),
            )
        )
        logging.info(
            "Inserted daikin co2=%s eco2=%s pm1=%.2f pm25=%.2f pm10=%.2f tvoc=%s temp=%.2f humi=%.2f",
            measurement.co2,
            measurement.eco2,
            measurement.pm1,
            measurement.pm25,
            measurement.pm10,
            measurement.tvoc,
            measurement.temperature,
            measurement.humidity,
        )

    def _insert_with_retry(self, build_record: Callable[[], Base]):
        try:
            self._insert_once(build_record)
        except SQLAlchemyError as exc:
            logging.warning("MySQL write failed (%s), retrying once...", exc)
            self._connect()
            self._insert_once(build_record)

    def _insert_once(self, build_record: Callable[[], Base]):
        session = self.session_factory()
        try:
            session.add(build_record())
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


class NoopStorage:
    def insert_ble(self, measurement: Measurement):
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

    def insert_daikin(self, measurement: DaikinMeasurement):
        logging.info(
            "SKIP_MYSQL daikin co2=%s eco2=%s pm1=%.2f pm25=%.2f pm10=%.2f tvoc=%s temp=%.2f humi=%.2f",
            measurement.co2,
            measurement.eco2,
            measurement.pm1,
            measurement.pm25,
            measurement.pm10,
            measurement.tvoc,
            measurement.temperature,
            measurement.humidity,
        )

    def close(self):
        return
