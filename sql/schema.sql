CREATE TABLE IF NOT EXISTS `lywsd03mmc_readings` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `mac` VARCHAR(17) NOT NULL,
  `temperature` DECIMAL(5,2) NOT NULL,
  `humidity` DECIMAL(5,2) NOT NULL,
  `voltage` DECIMAL(5,3) NOT NULL,
  `battery` TINYINT UNSIGNED NOT NULL,
  `rssi` SMALLINT NOT NULL,
  `timestamp` TIMESTAMP NOT NULL,
  PRIMARY KEY (`id`),
  INDEX `idx_mac_timestamp` (`mac`, `timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

