from fastapi import FastAPI
from src.config import config
from pydantic import BaseModel
import csv


app = FastAPI()

#pydantic semantic checks for the historical model
class historicaldata_post(BaseModel):
    sym: str
    start_date: str
    end_date: str
    service_name: str = "unknown"


@app.get("/")
def landing():
    return ("welcome to the crypto market data module")

@app.get("/symbol/{sym}")
def get_full_historical_data(sym: str):
    f = config.DATA_ROOT_DIR + "1day\\" + sym + "\\" + "XRPAUD_01-Jan-2010_20-Sep-2021"
    with open(f, newline='') as f:
        reader = csv.reader(f)
        data = list(reader)
    return data

@app.get("/symbol/{sym}/from/{start_date}/to/{end_date}")
def get_ranged_historical_data(sym: str,start_date: str, end_date: str):
    return (sym+start_date+end_date)

@app.post("/historicaldata")
def get_ranged_historical_data(new_post: historicaldata_post):
    print (new_post)
    return ("Successful")


'''
apis:
1. get symbol with date range, default 1min
2. get symbol with date range and frequency (1m, 5m, 15m etc)
3. get streaming data -- which protocol to use?
    

future apis
accept the source binance or coinbase etc, default to binance
1. Design  decision -- how to stream data with min lag -- required for live trading, not now

'''


''' iki When to use get and when to use post'''

''' 
keep track of all the symbol pairs 
    1. as soon as the server starts up
    2. every 24 hrs
    3. on invoking the API
        i. use get method to invoke api and return any new symbols found

if any new symbol is listed in binance,
 1. update the symbol table 
 2. create a new table for the symbol
 3. start fetching the data
 
 '''


'''
write a genric function to create database tables automatically for coin pairs
create index on the table on timestamp column
table name format <source>_<coinpair> eg: BINANCE_XRPAUD
'''

'''
make config:
1. if csv dump is available , use that
2. if not , download from the website
3. Run a bg task to keep looping through each symbol, find out the last updated timestamp and fetch till current timestamp
'''


'''
Create multiple processes to fetch symbols (upto 5) paralelly from the source and store in the database
Make it scalable if the number of symbols are huge -- iki how to do this? docker based or something else?
'''

'''
create and setup a DB for storing the data
'''

'''
If any DB errors\ unable to connect to DB etc , send error info to the webserver, but keep the service running
Same for any other exceptions\ errors
'''
