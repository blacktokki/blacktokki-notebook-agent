import os
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY")

MODEL_NAME = os.getenv("MODEL_NAME", "intfloat/multilingual-e5-base")
TEXT_PREFIX =  os.getenv("TEXT_PREFIX", "passage: ")
QUERY_PREFIX =  os.getenv("QUERY_PREFIX", "query: ")
ROOT_PATH = os.getenv("ROOT_PATH", "/agent")

NOTEBOOK_API_URL = os.getenv("NOTEBOOK_API_URL", "https://blacktokki.kro.kr/notebook")