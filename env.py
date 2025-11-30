import os
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY")
PAT_SECRET_KEY = os.getenv("PAT_SECRET_KEY")
PAT_EXPIRATION_DAYS = os.getenv("PAT_EXPIRATION_DAYS", 7)

MODEL_NAME = os.getenv("MODEL_NAME", "intfloat/multilingual-e5-base")
TEXT_PREFIX =  os.getenv("TEXT_PREFIX", "passage: ")
QUERY_PREFIX =  os.getenv("QUERY_PREFIX", "query: ")
ROOT_PATH = os.getenv("ROOT_PATH", "/agent")