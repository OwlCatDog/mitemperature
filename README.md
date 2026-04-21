# LYWSD03MMC Passive

(p.s. 90%的代码都是AI生成的)

原项目 https://github.com/JsBergbau/MiTemperature2

这是一个基于 `MiTemperature2` 被动扫描逻辑精简出的独立项目：

- 仅保留 `LYWSD03MMC` 相关的广播解析逻辑
- 仅支持 passive mode（BLE 广播监听）
- 接收到广播后写入 MySQL
- 配置全部来自环境变量
- 提供 Docker 化运行方式

镜像: sydneymrcat/mitemperature

## 说明

- 解析格式：`ATC1441` 与 `Custom`（明文）
- 解析逻辑来源：`MiTemperature2.py` 中 `decode_data_atc` 的核心思路（含广告计数去重）
- 当前不处理加密包（长度 16/22 的 encrypted payload 会跳过）

## 数据表结构

项目会按以下字段写入（可自动建表）：

- `id`
- `mac`
- `temperature`
- `humidity`
- `voltage`
- `battery`
- `rssi`
- `timestamp`

参考 SQL 在 [sql/schema.sql](/home/sydneyowl/Desktop/MiTemperature2/lywsd03mmc-mysql-passive/sql/schema.sql)。

## 环境变量

可参考 [.env.example](/home/sydneyowl/Desktop/MiTemperature2/lywsd03mmc-mysql-passive/.env.example)：

- `BLE_INTERFACE`：蓝牙接口号，默认 `0`（即 `hci0`）
- `WATCHDOG_SECONDS`：扫描 watchdog，`0` 为关闭
- `LOG_LEVEL`：日志级别，默认 `INFO`
- `SENSOR_MACS`：可选，逗号分隔 MAC 白名单；为空则接收所有可解析设备
- `SKIP_MYSQL`：测试开关，`true` 时不连接 MySQL、不写库，只打印解析结果
- `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE`
- `MYSQL_TABLE`：表名，默认 `lywsd03mmc_readings`
- `MYSQL_CREATE_TABLE`：是否自动建表，默认 `true`

## 本地运行

```bash
cd lywsd03mmc-mysql-passive
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a
source .env
set +a
python -m app.main
```

## Docker 运行

1. 准备配置：

```bash
cd lywsd03mmc-mysql-passive
cp .env.example .env
```

2. 构建并启动：

```bash
docker compose up --build -d
```

3. 查看日志：

```bash
docker compose logs -f
```

## 运行注意

- 该容器使用 `network_mode: host`，因此访问 MySQL 时请按你的实际网络拓扑配置 `MYSQL_HOST`。
- 若你暂时没有 MySQL，可将 `SKIP_MYSQL=true` 先验证扫描与解析链路。
