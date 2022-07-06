# CryptoMarketData
Gather and store historical and streaming data

Currently, this microservice captures historical tick data for all available coins (Configurable to limit as per 
requirement).It can also subscribe and capture streaming live data.

It supports only Binance exchange now, in future more adapters would be developed to connect to other exchanges.

In Pipeline:
CCXT
Coinbase

The captured data is stored in Timescale DB. Support for KDB database in WIP

Steps to run as a service:
cd <your_project_location>/CryptoMarketData/app/

$python -m uvicorn main:app --reload --port 8002