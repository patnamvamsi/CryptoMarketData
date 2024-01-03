from app.config import config
from binance import ThreadedWebsocketManager
from binance import Client
import app.db.timescaledb.crud as q
from app.ingest import historical_data_to_db as h


api_key = config.API_KEY
api_secret = config.API_SECRET


class StreamKLineData:
    ''' Refactor later to handle multiple type of streams
     and to cater to multiple symbols in a distributed env'''

    def __init__(self, session):
        self.session = session
        self.symbols = q.get_active_symbols(self.session, True)
        self.stream = []
        self.build_stream_names()

        # fill up the latest gaps and then start the stream

        c = h.BinanceDownloader(session)
        c.fetch_all_historical_data()
        for symbol in self.symbols:
            c.fetch_recent_historical_data(symbol.lower())


    def build_stream_names(self):
        # other streams: https://binance-docs.github.io/apidocs/futures/en/#individual-symbol-mini-ticker-stream
        for symbol in self.symbols:
            self.stream.append(symbol.lower() + '@kline_' + Client.KLINE_INTERVAL_1MINUTE)
        print(str(self.stream) + " Subscribed")
        return self.stream


    def main(self):
        kline = Client.KLINE_INTERVAL_1MINUTE
        self.twm = ThreadedWebsocketManager(api_key=api_key, api_secret=api_secret)
        # start is required to initialise its internal loop
        self.twm.start()

        def handle_socket_message(msg):
            msg = msg['data']
            print(f"message type: {msg['e']}")
            print(msg)
            if msg['e'] == 'kline':
                symbol, candle_stick = convert_to_candle_stick(msg)
                print(symbol, candle_stick)
                q.insert_kline_rows(symbol, kline, candle_stick, self.session)

        def convert_to_candle_stick(msg):
            symbol = msg['s']
            # normalise as per csv kline
            candle_stick = [[msg['k']['t'], msg['k']['o'], msg['k']['h'], msg['k']['l'], msg['k']['c'], msg['k']['v'], msg['k']['T'], msg['k']['q'], msg['k']['n'], msg['k']['V'], msg['k']['Q'], msg['k']['B']]]
            return symbol, candle_stick

        multiplex_socket = self.twm.start_multiplex_socket(callback=handle_socket_message, streams=self.stream)
        self.twm.join()

        #https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
        #kline_socket = self.twm.start_kline_socket(callback=handle_socket_message, symbol=symbol)
        # multiple sockets can be started
        #depth_socket = twm.start_depth_socket(callback=handle_socket_message, symbol=symbol)
        # see Binance docs for stream names
        #streams = ['xrpaud@miniTicker', 'xrpaud@bookTicker']
        #stop selective streams:
        #depth_stream_name = twm.start_depth_socket(callback=handle_socket_message, symbol=symbol)
        # some time later
        #twm.stop_socket(depth_stream_name)

    '''
    def __del__(self):
        self.twm.stop()
    '''

if __name__ == "__main__":
    stream = StreamKLineData()
    stream.main()


