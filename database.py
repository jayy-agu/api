import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Local development: falls back to a SQLite file on disk, exactly as before.
# Production (Vercel or any serverless host): set DATABASE_URL to a real
# hosted Postgres connection string as an environment variable. Serverless
# functions have no persistent writable disk, so a local sqlite file can't
# be used there -- every write would fail with "unable to open database
# file" / "readonly database".
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./office_board.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()