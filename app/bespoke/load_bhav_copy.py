import psycopg2
import csv
from app.config import config as cfg
import os
import time

# Database connection parameters
host = cfg.TIMESCALE_HOST
dbname = cfg.TIMESCALE_MARKET_DATA_DB
user = cfg.TIMESCALE_USERNAME
password = cfg.TIMESCALE_PASSWORD
table_name = "nse_bhav_copy"

# CSV file path
dir_path = '/home/vamsi/Downloads/bhav_copy_modified/'
files = os.listdir(dir_path)

#csv_file_path = '/home/vamsi/Downloads/bhav_copy_modified/bhav_copy_30-12-2022.csv'

# Establish a connection to the PostgreSQL database
conn = psycopg2.connect(
    host=host,
    dbname=dbname,
    user=user,
    password=password
)

# Create a cursor object to interact with the database
cursor = conn.cursor()
counter = 0
for file in files:
    counter = counter + 1
    csv_file_path = os.path.join(dir_path, file)
    # Open the CSV file and read its contents
    with open(csv_file_path, 'r') as f:
        # Skip the header row if the CSV has one
        next(f)  # Uncomment this line if your CSV has headers

        # Use the COPY command to load the CSV data into the PostgreSQL table
        try:
            cursor.copy_from(f, table_name, sep=',', null='')
        except Exception as e:
            print("Error: " + file)
            print("Error: " + str(e.with_traceback()))
    conn.commit()
    if counter == 25:
        time.sleep(1)
        counter = 0

# Close the cursor and connection
cursor.close()
conn.close()

print("CSV data has been successfully loaded into the table!")