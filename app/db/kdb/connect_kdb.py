import qpython
from qpython import qconnection, MetaData
from qpython.qtype import QException, QKEYED_TABLE
import pandas as pd

class connect_kdb:

    def __init__(self):

        q = qconnection.QConnection(host='192.168.0.101', port=8080, pandas=True)
        q.open()
        print(q.is_connected())
        print(q.host)
        #q.query()
        self.conn = q
        #df = pd.read_csv('LSKUSDT_01-Jan-2010_20-Aug-2021.csv')

        #q.sendSync('{xrpaud_kline::x}', df)
        #q("xrpaud_kline:([]opentime:`timespan$(); sym:`g#`symbol$(); open:`float$(); high:`float$(); low:`float$(); close:`float$(); volume:`float$();closetime:`timespan$(); asset_vol:`float$(); taker_buy_base_asset_volume:`float$(); taker_buy_quote_asset_volume:`float$();ignore:`float$())")

        '''df = pd.DataFrame({'vamsi': [7, 1, 5], 'patnam': ['a','b','c']})
        df = df.astype({"vamsi": float, "patnam": str})
    
        df = pd.DataFrame({'patnam': ['a', 'b', 'c']})
        df = df.astype({"patnam": str})
    
        q("xrp_test:([]patnam:`g#`symbol$())")
        '''

        #q.sendSync('{xrp_test::x}', df)
        #q.sendSync('{t insert x}', df)


        print('Connected to KDB')



    def create_new_table(self,table_name,table_type):

        if table_type == 'KLINE':
            create_kline_table = table_name + ':([]opentime:`float$(); open:`float$(); high:`float$(); low:`float$(); close:`float$(); volume:`float$();closetime:`float$(); asset_vol:`float$(); trade_count:`float$() ; taker_buy_base_asset_volume:`float$(); taker_buy_quote_asset_volume:`float$();ignore:`float$())'
            self.conn(create_kline_table)

        if table_type == 'DEPTH':
            create_depth_table = table_name + ''

    def create_table_from_dataframe(self, table_name, df):
        self.conn.sendSync('{' + table_name + ' insert x}', df)
        print('Table created:' + table_name)
        print('No of Rows:' + str(len(df)))

'''
https://github.com/exxeleron/qPython/blob/master/doc/source/usage-examples.rst

create table:
xrpaud_kline:([]opentime:`timespan$(); sym:`g#`symbol$(); open:`float$(); high:`float$(); low:`float$(); close:`float$(); volume:`float$();closetime:`timespan$(); asset_vol:`float$(); trade_count:`float$() ;taker_buy_base_asset_volume:`float$(); taker_buy_quote_asset_volume:`float$();ignore:`float$())

insert table:
.Q.fs[{`xrpaud_kline insert flip colnames!("pfffffpfffff";",")0:x}]`:file.csv

xrpaud:("pfffffpfffff";enlist",")0:`:5minutes.csv 
`xrpaud upsert ("pfffffpfffff";enlist ",")0:`5minutes.csv

count select from xrpaud where PERIOD=x

sample data:
1593561600.0,9138.08000000,9138.16000000,9100.00000000,9117.23000000,365.82786000,1593561899999,3333795.23051561,3437,143.86467300,1311043.45651440,0
'''
'''
[
    [
        1499040000000,      # Open time
        "0.01634790",       # Open
        "0.80000000",       # High
        "0.01575800",       # Low
        "0.01577100",       # Close
        "148976.11427815",  # Volume
        1499644799999,      # Close time
        "2434.19055334",    # Quote asset volume
        308,                # Number of trades
        "1756.87402397",    # Taker buy base asset volume
        "28.46694368",      # Taker buy quote asset volume
        "17928899.62484339" # Can be ignored
    ]
]
'''
def stop(self):
    self._stopper.set()

def stopped(self):
    return self._stopper.isSet()

def run(self, data):
    """Sync data with kdb"""
    data.meta = MetaData(**{'qtype': QKEYED_TABLE})
    data.reset_index(drop=True)
    data.set_index(['timestamp'], inplace=True)
    try:
        self.q.sync('insert', np.string_('trades'), data)
        temp = self.q('trades')
        # print(len(temp))
        print('KDB updated')
    except QException as e:
        print(str(e))

def commit(self):
    self.q('save `trade_hist')