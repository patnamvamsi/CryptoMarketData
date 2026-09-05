FROM python:3.10

COPY shared /shared
COPY CryptoMarketData /marketdata

WORKDIR /marketdata

RUN pip3 install --upgrade pip \
    && pip3 install -e /shared \
    && pip3 install -r requirements.txt

WORKDIR /marketdata/app

# Headless Chromium for browser-driven Zerodha auto-login
RUN playwright install --with-deps chromium

ENV PYTHONPATH="/marketdata"

EXPOSE 8002

CMD ["python", "-m", "uvicorn", "main:app", "--host=0.0.0.0", "--reload", "--port", "8002"]
