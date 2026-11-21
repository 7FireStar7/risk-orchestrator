import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
DSN = os.getenv("DATABASE_URL")

def conn():
    return psycopg.connect(DSN, row_factory=dict_row)

def init_db():
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with conn() as c:
        c.execute(sql)