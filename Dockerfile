FROM python:3.9-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libbluetooth-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /build/requirements.txt

# pybluez 0.23 requires old setuptools and no build isolation.
RUN pip install --no-cache-dir "setuptools==57.5.0" "wheel<0.38" \
    && pip wheel --no-cache-dir --no-build-isolation --wheel-dir /wheels pybluez==0.23 \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r /build/requirements.txt

FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libbluetooth3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
COPY --from=builder /wheels /wheels

RUN pip install --no-cache-dir --no-index --find-links=/wheels \
       pybluez==0.23 -r /app/requirements.txt \
    && rm -rf /wheels

COPY app /app/app

CMD ["python", "-m", "app.main"]
