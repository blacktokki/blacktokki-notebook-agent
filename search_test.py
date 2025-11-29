from dotenv import load_dotenv
import os
import sys
import chromadb
from chromadb.utils import embedding_functions

load_dotenv()
MODEL_NAME = os.getenv("MODEL_NAME", "intfloat/multilingual-e5-base")
VECTOR_DB_PATH = "./chroma_db_store"
COLLECTION_NAME = "note_collection"
QUERY_PREFIX =  os.getenv("QUERY_PREFIX", "query: ")


def search_note(user_id, query_text):
    client = chromadb.PersistentClient(path=VECTOR_DB_PATH)
    
    # 저장할 때 썼던 것과 동일한 모델 함수를 사용해야 합니다.
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=MODEL_NAME
    )
    
    collection = client.get_collection(name=COLLECTION_NAME, embedding_function=ef)

    # 검색 수행
    results = collection.query(
        query_texts=[f"{QUERY_PREFIX}{query_text}"],
        where={"user_id": user_id},
        n_results=3  # 가장 유사한 2개 찾기
    )

    print(f"--- 질문: {query_text} ---")
    for i in range(len(results['documents'][0])):
        print(f"순위 {i+1}:")
        print(f"메타데이터: {results['metadatas'][0][i]}")
        print(f"거리(유사도 역수): {results['distances'][0][i]}")
        print(f"내용: \n{results['documents'][0][i]}\n")
        print("-" * 20)

# 테스트 실행
for keyword in sys.argv[1:]:
    search_note(9, keyword)