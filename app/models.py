from dataclasses import dataclass
from datetime import datetime, timedelta

UTC_PLUS_8 = timedelta(hours=8)


def now_utc_plus_8() -> datetime:
    return datetime.utcnow() + UTC_PLUS_8


@dataclass
class Measurement:
    mac: str
    temperature: float
    humidity: float
    voltage: float
    battery: int
    rssi: int
    timestamp: datetime


@dataclass
class DaikinMeasurement:
    co2: int
    eco2: int
    pm1: float
    pm25: float
    pm10: float
    tvoc: int
    temperature: float
    humidity: float
