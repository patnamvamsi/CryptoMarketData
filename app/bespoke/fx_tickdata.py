# https://towardsdatascience.com/how-and-why-i-got-75gb-of-free-foreign-exchange-tick-data-9ca78f5fa26c
# Construct the years, weeks and symbol lists required for the scraper.
# libraries you will need

import requests
import fxcmpy
from fxcmpy import fxcmpy_tick_data_reader as tdr
import os


years = [2020, 2021, 2022]
weeks = list(range(53))
symbols = []
for pair in tdr.get_available_symbols():
    if pair not in symbols:
        symbols.append(pair)

# Scrape time
directory = "/media/vamsi/Elements/Dataset/Stock_market/FXCMPY1/"

for symbol in symbols:
    for year in years:
        for week in weeks:
            url = f"https://tickdata.fxcorporate.com/{symbol}/{year}/{week}.csv.gz"
            r = requests.get(url, stream=True)
            with open(f"{directory}{symbol}_{year}_w{week}.csv.gz", 'wb') as file:
                for chunk in r.iter_content(chunk_size=1024):
                    file.write(chunk)
                    print("------")
        print (f"{symbol}_{year}_w{week}")

# Check all the files for each currency pair was downloaded (should be 104 for each)
total = 0
for symbol in symbols:
    count = 0
    for file in os.listdir(directory):
        if file[:6] == symbol:
            count+=1
    total += count
    print(f"{symbol} files downloaded = {count} ")
print(f"\nTotal files downloaded = {total}")