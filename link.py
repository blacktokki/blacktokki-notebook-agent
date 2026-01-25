import requests
from bs4 import BeautifulSoup
from typing import Dict

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Referer": "https://www.google.com",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

def get_link_preview(url: str) -> Dict[str, str]:
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        og_data = {}
        # og: 태그 추출
        for meta in soup.find_all("meta"):
            if meta.get("property") and meta.get("property").startswith("og:"):
                prop = meta.get("property")[3:]  # 'og:' 접두사 제거
                og_data[prop] = meta.get("content")
        
        data = {
            "title": og_data.get("title", soup.title.string.strip() if soup.title else None),
            "description": og_data.get("description", soup.body.get_text() if soup.body else None),
        }
        return data
    except Exception:
        return {}
