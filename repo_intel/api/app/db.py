import os
from contextlib import contextmanager
import pymysql
from pymysql.cursors import DictCursor

def connect():
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "mysql"), port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"], charset="utf8mb4", cursorclass=DictCursor,
        autocommit=True, init_command="SET time_zone='+00:00'", connect_timeout=10)

@contextmanager
def cursor():
    conn = connect()
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()