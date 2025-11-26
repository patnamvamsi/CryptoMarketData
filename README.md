# CryptoMarketData
One of the micro services of Vritti platform. 

Captures and store historical and streaming market data.

Currently, this microservice captures historical tick data for all available coins (Configurable to limit as per 
requirement).It can also subscribe and capture streaming live data(wss protocol).

In its initial avatar it supports Binance exchange, in future more adapters would be developed to connect to other exchanges.

In Pipeline:
CCXT,
Coinbase

The captured data is stored in Timescale DB. Support for KDB database in WIP

Upcoming features:
1. the streaming data to be sent vi kafka, to be  


## How to set up and run
Prerequisites:
1. Timescale DB installed and running.
2. Rename app/config/config_template.py to app/config/config.py and add relavant keys
3. Run the initial setup script from app/db/timescaledb/scripts/setup_db.sql 
4. Run intialise.py

Steps to run as a service:
cd <your_project_location>/CryptoMarketData/app/

$python -m uvicorn main:app --reload --port 8002