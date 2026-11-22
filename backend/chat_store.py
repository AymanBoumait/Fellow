"""
Persistent chat session store for Fellow:
- Create, list, load, rename, and delete chats
- Append user and Fellow messages (with retrieved document chunks/sources)
- Export any chat to a well-formatted Markdown (.md) document
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.config import get_data_dir


class ChatStore:
    """Thread-safe local JSON storage for multiple saved Fellow conversations."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or get_data_dir()
        self.chats_path = os.path.join(self.data_dir, "chats.json")
        self._lock = threading.Lock()
        self._chats: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.chats_path):
            try:
                with open(self.chats_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for chat in data:
                        for msg in chat.get("messages", []):
                            if not msg.get("id"):
                                msg["id"] = str(uuid.uuid4())
                    return data
            except Exception:
                pass
        return []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.chats_path), exist_ok=True)
        tmp = self.chats_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._chats, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.chats_path)

    def list_chats(self) -> List[Dict[str, Any]]:
        """Return lightweight chat summaries ordered from newest to oldest."""
        with self._lock:
            ordered = sorted(self._chats, key=lambda c: c.get("updated_at_ts", 0), reverse=True)
            return [
                {
                    "id": c["id"],
                    "title": c["title"],
                    "created_at": c.get("created_at", ""),
                    "updated_at": c.get("updated_at", ""),
                    "updated_at_ts": c.get("updated_at_ts", 0),
                    "message_count": len(c.get("messages", [])),
                    "last_preview": (
                        c["messages"][-1]["content"][:80]
                        if c.get("messages")
                        else "Fellow is ready to help"
                    ),
                }
                for c in ordered
            ]

    def get_chat(self, chat_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for c in self._chats:
                if c["id"] == chat_id:
                    return dict(c)
        return None

    def create_chat(self, title: str = "New Study Session") -> Dict[str, Any]:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        now_ts = time.time()
        chat = {
            "id": str(uuid.uuid4()),
            "title": (title or "New Study Session").strip(),
            "created_at": now_str,
            "updated_at": now_str,
            "updated_at_ts": now_ts,
            "messages": [],
        }
        with self._lock:
            self._chats.insert(0, chat)
            self._save()
        return dict(chat)

    def rename_chat(self, chat_id: str, new_title: str) -> Optional[Dict[str, Any]]:
        cleaned = (new_title or "").strip()
        if not cleaned:
            return None
        with self._lock:
            for c in self._chats:
                if c["id"] == chat_id:
                    c["title"] = cleaned[:100]
                    c["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    c["updated_at_ts"] = time.time()
                    self._save()
                    return dict(c)
        return None

    def delete_chat(self, chat_id: str) -> bool:
        with self._lock:
            before = len(self._chats)
            self._chats = [c for c in self._chats if c["id"] != chat_id]
            if len(self._chats) < before:
                self._save()
                return True
        return False

    def truncate_from_message(
        self,
        chat_id: str,
        message_id: Optional[str] = None,
        message_index: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Remove the target user message and all messages after it in `chat_id`
        so an edited message can be re-appended and re-streamed cleanly.
        """
        with self._lock:
            for c in self._chats:
                if c["id"] != chat_id:
                    continue
                messages = c.get("messages", [])
                cut_idx: Optional[int] = None

                if message_id:
                    for idx, m in enumerate(messages):
                        if m.get("id") == message_id:
                            cut_idx = idx
                            break

                if cut_idx is None and message_index is not None:
                    if 0 <= message_index < len(messages):
                        cut_idx = message_index

                if cut_idx is None:
                    return None

                # If editing the very first user message and the chat title was auto-derived from it,
                # reset to "New Study Session" so add_message auto-titles from the edited prompt
                if cut_idx == 0 and messages:
                    old_first = messages[0].get("content", "").strip().split("\n")[0]
                    old_first = re.sub(r"\s+", " ", old_first).strip()
                    old_auto_title = (old_first[:46] + "...") if len(old_first) > 48 else old_first
                    if c.get("title") == old_auto_title:
                        c["title"] = "New Study Session"

                c["messages"] = messages[:cut_idx]
                c["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                c["updated_at_ts"] = time.time()
                self._save()
                return dict(c)
        return None

    def add_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        message_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Append a message to a chat and auto-generate a friendly title on the first user turn."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = {
            "id": message_id or str(uuid.uuid4()),
            "role": role,
            "content": content,
            "timestamp": now_str,
            "model": model,
            "sources": sources or [],
        }
        with self._lock:
            for c in self._chats:
                if c["id"] == chat_id:
                    if (
                        role == "user"
                        and len(c["messages"]) == 0
                        and c["title"] in ("New Study Session", "New Chat", "Untitled Chat")
                    ):
                        first_line = content.strip().split("\n")[0]
                        first_line = re.sub(r"\s+", " ", first_line).strip()
                        c["title"] = (first_line[:46] + "...") if len(first_line) > 48 else (first_line or "Study Chat")

                    c["messages"].append(msg)
                    c["updated_at"] = now_str
                    c["updated_at_ts"] = time.time()
                    self._save()
                    return dict(c)
        return None

    def export_markdown(self, chat_id: str) -> Optional[Tuple[str, str]]:
        """Return (safe_filename, markdown_content) for exporting a chat to a .md file."""
        chat = self.get_chat(chat_id)
        if not chat:
            return None

        title = chat.get("title") or "Fellow Study Session"
        safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", title).strip("_") or "Fellow_Chat"
        filename = f"{safe_slug[:50]}.md"

        lines: List[str] = [
            f"# {title}",
            "",
            f"*Exported from **Fellow** — Offline Study & Research Companion*",
            f"*Date: {chat.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M'))}*",
            "",
            "---",
            "",
        ]

        for msg in chat.get("messages", []):
            role = msg.get("role", "user")
            ts = msg.get("timestamp", "")
            content = msg.get("content", "")
            sources = msg.get("sources", []) or []

            if role == "user":
                lines.append(f"### You ({ts})")
                lines.append("")
                lines.append(content)
                lines.append("")
            else:
                model_tag = f" *({msg['model']})*" if msg.get("model") else ""
                lines.append(f"### Fellow{model_tag} ({ts})")
                lines.append("")
                lines.append(content)
                lines.append("")
                if sources:
                    unique_citations = []
                    seen = set()
                    for s in sources:
                        cite = f"`{s.get('filename', 'Document')}` ({s.get('section', 'Section')})"
                        if cite not in seen:
                            seen.add(cite)
                            unique_citations.append(cite)
                    lines.append(f"> **Sources used:** {', '.join(unique_citations)}")
                    lines.append("")

            lines.append("---")
            lines.append("")

        return filename, "\n".join(lines)
