import re
import requests
from typing import List, Optional, Dict, Any, Tuple
from fastmcp.server.dependencies import get_http_request

from env import NOTEBOOK_API_URL

class NotebookClient:
    def __init__(self):
        self.base_url = NOTEBOOK_API_URL
        self.headers = self._get_headers(get_http_request().headers.get("Authorization"))

    def _get_headers(self, auth) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = auth
        return headers

    def _clean_html_tags(self, html_text: str) -> str:
        if not html_text:
            return ""
        clean = re.compile('<.*?>')
        return re.sub(clean, '', html_text)

    def fetch_contents(self, types: List[str], with_hidden: bool, parent_id: Optional[int] = None, page: int = 0) -> List[Dict[str, Any]]:
        params = {
            "sort": "id,DESC",
            "types": ",".join(types),
            "size": 256,
            "page": page,
            "withHidden": with_hidden
        }
        if parent_id is not None:
            params["parentId"] = parent_id

        response = requests.get(f"{self.base_url}/api/v1/content", params=params, headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json().get("value", [])

    def get_note_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        notes = self.fetch_contents(["NOTE"], True)
        for note in notes:
            if note.get("title") == title:
                return note
        return None

    def create_note(self, title: str, content_html: str) -> int:
        payload = {
            "title": title,
            "description": content_html,
            "type": "NOTE",
            "parentId": 0,
            "userId": 0,
            "order": 0,
            "input": title,
            "option": {}
        }
        resp = requests.post(f"{self.base_url}/api/v1/content", json=payload, headers=self.headers)
        resp.raise_for_status()
        return resp.json().get("id")

    def _create_snapshot(self, parent_id: int, note_data: Dict[str, Any]) -> None:
        """
        변경된 노트의 현재 상태를 SNAPSHOT으로 저장합니다.
        notebook.ts의 saveContents 로직 참조:
        const snapshot = { ...content, type: 'SNAPSHOT', id: undefined, parentId: savedId };
        """
        snapshot_payload = note_data.copy()
        snapshot_payload["type"] = "SNAPSHOT"
        snapshot_payload["parentId"] = parent_id
        
        # id는 새로 생성되어야 하므로 제거 (또는 None 설정)
        if "id" in snapshot_payload:
            del snapshot_payload["id"]
            
        requests.post(f"{self.base_url}/api/v1/content", json=snapshot_payload, headers=self.headers).raise_for_status()

    def create_note(self, title: str, content_html: str) -> int:
        """
        새 노트를 생성하고 스냅샷을 남깁니다.
        """
        payload = {
            "title": title,
            "description": content_html,
            "type": "NOTE",
            "parentId": 0,
            "userId": 0,
            "order": 0,
            "input": title,
            "option": {}
        }
        resp = requests.post(f"{self.base_url}/api/v1/content", json=payload, headers=self.headers)
        resp.raise_for_status()
        
        new_id = resp.json().get("id")
        
        # 스냅샷 생성
        self._create_snapshot(new_id, payload)
        
        return new_id

    def update_note_content(self, note_id: int, current_note_data: Dict[str, Any], new_content: str) -> None:
        """
        노트 내용을 수정하고 스냅샷을 남깁니다.
        """
        updated_data = {
            **current_note_data,
            "description": new_content,
            "type": "NOTE" # type 명시
        }
        
        payload = {
            "ids": [note_id],
            "updated": updated_data
        }
        requests.patch(f"{self.base_url}/api/v1/content", json=payload, headers=self.headers).raise_for_status()
        
        # 스냅샷 생성
        self._create_snapshot(note_id, updated_data)

    def rename_note(self, note_id: int, current_note_data: Dict[str, Any], new_title: str) -> None:
        """
        노트 제목을 변경(이동)하고 스냅샷을 남깁니다.
        """
        updated_data = {
            **current_note_data,
            "title": new_title,
            "type": "NOTE"
        }
        
        payload = {
            "ids": [note_id],
            "updated": updated_data
        }
        requests.patch(f"{self.base_url}/api/v1/content", json=payload, headers=self.headers).raise_for_status()
        
        # 스냅샷 생성
        self._create_snapshot(note_id, updated_data)

    def _extract_and_remove_card(self, html: str, header_text: str) -> Tuple[str, Optional[str]]:
        pattern = re.compile(r'(<h([1-6])\b[^>]*>(.*?)</h\2>)', re.IGNORECASE | re.DOTALL)
        matches = list(pattern.finditer(html))
        target_match = None
        
        for match in matches:
            if header_text in match.group(3):
                target_match = match
                break
                
        if not target_match:
            return html, None

        start_idx = target_match.start()
        current_level = int(target_match.group(2))
        end_idx = len(html)
        
        for match in matches:
            if match.start() <= start_idx:
                continue
            if int(match.group(2)) <= current_level:
                end_idx = match.start()
                break
                
        extracted_content = html[start_idx:end_idx]
        new_html = html[:start_idx] + html[end_idx:]
        
        return new_html, extracted_content

    def move_kanban_card_logic(self, source_title: str, target_title: str, header_text: str) -> str:
        """
        칸반 카드를 이동시키고, 영향을 받은 두 노트(소스, 타겟) 모두에 대해 스냅샷을 남깁니다.
        """
        source_note = self.get_note_by_title(source_title)
        target_note = self.get_note_by_title(target_title)
        
        if not source_note or not target_note:
            return "Error: Source or Target note not found."
        
        source_html = source_note.get("description", "") or ""
        target_html = target_note.get("description", "") or ""
        
        new_source_html, card_content = self._extract_and_remove_card(source_html, header_text)
        
        if not card_content:
            return f"Error: Card '{header_text}' not found in '{source_title}'."
        
        new_target_html = target_html + "\n" + card_content
        
        # 1. Update Target Note & Snapshot
        self.update_note_content(target_note["id"], target_note, new_target_html)
        
        # 2. Update Source Note & Snapshot
        self.update_note_content(source_note["id"], source_note, new_source_html)
        
        return f"Moved card '{header_text}' from '{source_title}' to '{target_title}' and created snapshots for both."