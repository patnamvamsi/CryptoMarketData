from fastapi import FastAPI
from config import config
import csv


app = FastAPI()

@app.get("/")
def landing():
    return ("welcome to the crypto market data module")

@app.get("/symbol/{sym}")
def get_full_historical_data(sym: str):
    f = config.DATA_ROOT_DIR + "1day\\" +  sym + "\\" + "XRPAUD_01-Jan-2010_20-Sep-2021"
    with open(f, newline='') as f:
        reader = csv.reader(f)
        data = list(reader)
    return data

@app.get("/symbol/{sym}/from/{start_date}/to/{end_date}")
def get_ranged_historical_data(sym: str,start_date: str, end_date: str):
    return (sym+start_date+end_date)




