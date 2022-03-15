Purpose:

1. Capture and store market data in real time
2. Capture and store historical data via flat files
3. Provide API for external systems to consume data

Design:

1. Connect to binance via kafka (use WSS) to get market data
2. Listen to kafka topic for market data and populate KDB
3. Fetch historical data in flat files and load into the DB


Features:
1. On startup,Identify gaps between latest data and current time, fetch historical data from flat files and populate KDB
2. parallelly start the kafka connection and start fetching the market data

Config Driven:
1. Configure top symbols that should be live streamed. 
2. fetch the historical data for the rest of the symbols from the flat files.


