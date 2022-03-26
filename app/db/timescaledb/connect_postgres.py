from psycopg2 import pool
import psycopg2
from app.config import config as cfg

class Database:
    __connection_pool = None

    @staticmethod
    def initialise(**kwargs):
        Database.__connection_pool = pool.SimpleConnectionPool(1, 10, **kwargs)

    @staticmethod
    def get_connection():
        return Database.__connection_pool.getconn()

    @staticmethod
    def return_connection(connection):
        Database.__connection_pool.putconn(connection)

    @staticmethod
    def close_all_connections():
        Database.__connection_pool.closeall()


class CursorFromConnectionPool:
    def __init__(self):
        self.conn = None
        self.cursor = None

    def __enter__(self):
        self.conn = Database.get_connection()
        self.cursor = self.conn.cursor()
        return self.cursor

    def get_cursor(self):
        self.conn = Database.get_connection()
        self.cursor = self.conn.cursor()
        return self.cursor

    def __exit__(self, exception_type, exception_value, exception_traceback):
        if exception_value:  # This is equivalent to `if exception_value is not None`
            self.conn.rollback()
        else:
            self.cursor.close()
            self.conn.commit()
        Database.return_connection(self.conn)


def connect():
    """ Connect to the Timescaledb database server """
    conn = None
    try:
        # read connection parameters
        #params = config()

        # connect to the Timescaledb server
        print('Connecting to the Timescaledb database...')
        conn = psycopg2.connect(
            host=cfg.TIMESCALE_HOST,
            database=cfg.TIMESCALE_DATABASE,
            user=cfg.TIMESCALE_USER,
            password=cfg.TIMESCALE_PASSWORD)

        # create a cursor
        cur = conn.cursor()

        # execute a statement
        print('Timescaledb database version:')
        cur.execute('SELECT version()')

        # display the Timescaledb database server version
        db_version = cur.fetchone()
        print(db_version)

        # close the communication with the Timescaledb
        cur.close()
    except (Exception, psycopg2.DatabaseError) as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()
            print('Database connection closed.')


if __name__ == '__main__':
    connect()
