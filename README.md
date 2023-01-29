# CryptoMarketData
One of the micro services of Vritti platform. 

Captures and store historical and streaming market data.

Currently, this microservice captures historical tick data for all available coins (Configurable to limit as per 
requirement).It can also subscribe and capture streaming live data(wss protocol).

In its initial avtar it supports Binance exchange, in future more adapters would be developed to connect to other exchanges.

In Pipeline:
CCXT,
Coinbase

The captured data is stored in Timescale DB. Support for KDB database in WIP

Prerequisites:
Timescale DB installed and running.

Steps to run as a service:
cd <your_project_location>/CryptoMarketData/app/

$python -m uvicorn main:app --reload --port 8002