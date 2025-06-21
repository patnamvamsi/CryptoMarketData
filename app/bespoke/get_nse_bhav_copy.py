# using https://pypi.org/project/nselib/
from datetime import datetime
import datetime as d
import pandas as pd

#start_date = datetime.strptime(start_date, '%d-%m-%Y').date()
#end_date = datetime.strptime(end_date, '%d-%m-%Y').date()

start_date = "01/01/2019"
end_date = "12/31/2019"

date_range = pd.bdate_range(start=start_date, end = end_date,freq='B')

dates_list = [x.date() for x in date_range]

from nselib import capital_market

for dates in dates_list:

    try:
        bus_date = dates.strftime("%d-%m-%Y")
        #print(bus_date)
        data = capital_market.bhav_copy_with_delivery(trade_date=bus_date)
        data.to_csv("/home/vamsi/Downloads//bhav_copy/bhav_copy_" + bus_date + ".csv", index=False)
    except Exception as e:
        print ("Not Found:" + bus_date)


'''
using Jugad data

# Making all necessary imports for the code
from datetime import date
from jugaad_data.nse import bhavcopy_save
import pandas as pd
from jugaad_data.holidays import holidays
from random import randint
import time, os
import traceback


from urllib3.exceptions import ReadTimeoutError

date_range = pd.bdate_range(start='07/09/2024', end = '11/05/2024',
                            freq='B')

savepath = "/home/vamsi/Downloads/NSE"

# start and end dates in "MM-DD-YYYY" format
# holidays() function in (year,month) format
# freq = 'C' is for custom

#bhavcopy_save(date(2020,2,1), savepath)


dates_list = [x.date() for x in date_range]
#print(dates_list[0])
for dates in dates_list:
    try:
        print("dates: " + str(dates))
        bhavcopy_save(dates, savepath)
        time.sleep(randint(1 ,4)) # adding random delay of 1-4 seconds
    except (ConnectionError, ReadTimeoutError) as e:
        time.sleep(10) # stop program for 10 seconds and try again.
        try:
            bhavcopy_save(dates, savepath)
            time.sleep(randint(1 ,4))
        except (ConnectionError, ReadTimeoutError) as e:
            print(f'{dates}: File not Found')
    except(Exception) as er:
        print(er)
        traceback.print_exc()

'''