from app.config import config
from binance import ThreadedWebsocketManager

api_key = config.API_KEY
api_secret = config.API_SECRET

def main():

    symbol = 'XRPAUD'

    twm = ThreadedWebsocketManager(api_key=api_key, api_secret=api_secret)
    # start is required to initialise its internal loop
    twm.start()

    def handle_socket_message(msg):
        print(f"message type: {msg['e']}")
        print(msg)

    twm.start_kline_socket(callback=handle_socket_message, symbol=symbol)

    # multiple sockets can be started
    twm.start_depth_socket(callback=handle_socket_message, symbol=symbol)

    # or a multiplex socket can be started like this
    # see Binance docs for stream names
    streams = ['xrpaud@miniTicker', 'xrpaud@bookTicker']
    twm.start_multiplex_socket(callback=handle_socket_message, streams=streams)

    twm.join()

    #stop selective streams:
    #depth_stream_name = twm.start_depth_socket(callback=handle_socket_message, symbol=symbol)
    # some time later
    #twm.stop_socket(depth_stream_name)


    #stop all streams:
    #twm.stop()

if __name__ == "__main__":
   main()

#psql -x "postgres://tsdbadmin:password@192.168.0.101:5432/tsdb?sslmode=require"
#psql -U postgres -h 192.168.0.101

#\COPY xrpaud FROM sample.csv CSV;