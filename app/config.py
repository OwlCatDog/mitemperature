import os
import re
from dataclasses import dataclass


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
