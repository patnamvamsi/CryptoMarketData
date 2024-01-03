from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import config as cfg


def get_session_pool():
    # Create the database engine with a pool
    engine = create_engine('postgresql://' + cfg.TIMESCALE_USERNAME + ':' +
                                  cfg.TIMESCALE_PASSWORD + '@' +
                                  cfg.TIMESCALE_HOST + ':' +
                                  cfg.TIMESCALE_PORT + '/' +
                                  cfg.TIMESCALE_MARKET_DATA_DB, pool_size=5, max_overflow=10)


    # Create a session factory

    return sessionmaker(bind=engine)

def example_usage():
    # Create a session
    Session = get_session_pool()
    for y in range(200):

        session = Session()
        x = session.execute("SELECT symbol from binance_symbols    WHERE active ='true' order by  priority")
        print(x.fetchall())
        session.close()


if __name__ == "__main__":
    example_usage()
