"""
Document ingestion, scanned PDF detection, OCR & Vision page processing, text chunking,
document scoping (global vs. chat-specific), plausible relevance filtering, and local FAISS
vector storage using Ollama's nomic-embed-text.
"""
import base64
import io
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import docx
import faiss
import fitz  # PyMuPDF
import numpy as np
from PIL import Image
from pypdf import PdfReader

from backend.config import EMBEDDING_MODEL, get_data_dir
from backend.ollama_manager import OllamaManager, format_bytes

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

DOC_REFERENCE_WORDS = {
    "document", "documents", "doc", "docs", "file", "files", "pdf", "docx", "txt",
    "image", "images", "picture", "photo", "diagram", "scan", "scanned", "reading",
    "readings", "notes", "syllabus", "lecture", "chapter", "page", "pages", "slide",
    "slides", "handout", "uploaded", "excerpt", "excerpts", "summarize", "summary",
    "author", "paper", "article", "textbook", "worksheet", "assignment",
}

STOPWORDS = {
    "what", "when", "where", "which", "whom", "whose", "about", "from", "this",
    "that", "with", "have", "does", "explain", "tell", "fellow", "please", "could",
    "would", "should", "there", "their", "these", "those", "make", "create", "into",
    "give", "some", "more", "help", "write", "turn", "convert", "using", "based",
    "your", "will", "just", "know", "like", "need", "want", "good", "best", "much",
    "many", "very", "also", "here", "then", "than", "them", "they", "been", "were",
}


class ScannedDocumentError(Exception):
    """Raised when a PDF contains pages but no extractable text (scanned image PDF)."""


class EmptyDocumentError(Exception):
    """Raised when a document contains no readable text."""


def find_tesseract_binary() -> Optional[str]:
    """Locate tesseract.exe on Windows/macOS/Linux if installed."""
    in_path = shutil.which("tesseract")
    if in_path and os.path.exists(in_path):
        return in_path

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.join(local_app_data, "Programs", "Tesseract-OCR", "tesseract.exe"),
            os.path.join(local_app_data, "Tesseract-OCR", "tesseract.exe"),
            r"C:\ProgramData\chocolatey\bin\tesseract.exe",
        ]
        for path in candidates:
            if path and os.path.exists(path):
                return path
    return None


class DocumentStore:
    """Manages uploaded study documents (global and chat-scoped), OCR/Vision paths, chunks, and FAISS index."""

    def __init__(self, ollama: OllamaManager, data_dir: Optional[str] = None):
        self.ollama = ollama
        self.data_dir = data_dir or get_data_dir()
        self.store_dir = os.path.join(self.data_dir, "vector_store")
        self.files_dir = os.path.join(self.data_dir, "uploaded_files")
        self.vision_dir = os.path.join(self.data_dir, "vision_store")
        self.staged_dir = os.path.join(self.data_dir, "staged_uploads")
        os.makedirs(self.store_dir, exist_ok=True)
        os.makedirs(self.files_dir, exist_ok=True)
        os.makedirs(self.vision_dir, exist_ok=True)
        os.makedirs(self.staged_dir, exist_ok=True)

        self.docs_path = os.path.join(self.store_dir, "documents.json")
        self.chunks_path = os.path.join(self.store_dir, "chunks.json")
        self.embeddings_path = os.path.join(self.store_dir, "embeddings.npy")
        self.faiss_path = os.path.join(self.store_dir, "index.faiss")

        self._lock = threading.Lock()
        self._rapid_ocr = None
        self.documents: List[Dict[str, Any]] = []
        self.chunks: List[Dict[str, Any]] = []
        self.embeddings: Optional[np.ndarray] = None
        self.index: Optional[faiss.IndexFlatIP] = None

        self._load_state()

    def _load_state(self) -> None:
        """Load persisted documents, chunks, and FAISS index from disk with backward-compatible fields."""
        if os.path.exists(self.docs_path):
            try:
                with open(self.docs_path, "r", encoding="utf-8") as f:
                    self.documents = json.load(f)
            except Exception:
                self.documents = []

        doc_scope_map: Dict[str, Tuple[str, Optional[str]]] = {}
        for d in self.documents:
            scope = d.get("scope") if d.get("scope") in ("global", "chat") else "global"
            chat_id = d.get("chat_id") if scope == "chat" else None
            if scope == "chat" and not chat_id:
                scope = "global"
            d["scope"] = scope
            d["chat_id"] = chat_id

            read_mode = d.get("read_mode") or "text"
            d["read_mode"] = read_mode
            if not d.get("read_mode_label"):
                d["read_mode_label"] = self._format_read_mode_label(read_mode)
            d.setdefault("image_paths", [])
            doc_scope_map[d["id"]] = (scope, chat_id)

        if os.path.exists(self.chunks_path):
            try:
                with open(self.chunks_path, "r", encoding="utf-8") as f:
                    self.chunks = json.load(f)
            except Exception:
                self.chunks = []

        for c in self.chunks:
            doc_id = c.get("doc_id", "")
            if doc_id in doc_scope_map:
                c["scope"], c["chat_id"] = doc_scope_map[doc_id]
            else:
                scope = c.get("scope") if c.get("scope") in ("global", "chat") else "global"
                chat_id = c.get("chat_id") if scope == "chat" else None
                c["scope"] = scope
                c["chat_id"] = chat_id

        if os.path.exists(self.embeddings_path):
            try:
                arr = np.load(self.embeddings_path)
                if arr.ndim == 2 and arr.shape[0] == len(self.chunks) and arr.shape[0] > 0:
                    self.embeddings = arr.astype("float32")
            except Exception:
                self.embeddings = None

        if os.path.exists(self.faiss_path) and self.embeddings is not None:
            try:
                loaded_index = faiss.read_index(self.faiss_path)
                if loaded_index.ntotal == len(self.chunks):
                    self.index = loaded_index
            except Exception:
                self.index = None

        if self.index is None and self.embeddings is not None and len(self.chunks) > 0:
            self._rebuild_faiss_from_embeddings()

    @staticmethod
    def _format_read_mode_label(read_mode: str) -> str:
        if read_mode == "ocr":
            return "OCR"
        if read_mode == "vision":
            return "Vision"
        return "Text extracted"

    def _rebuild_faiss_from_embeddings(self) -> None:
        """Rebuild the FAISS IndexFlatIP from the in-memory normalized embeddings array."""
        if self.embeddings is None or len(self.chunks) == 0 or self.embeddings.shape[0] == 0:
            self.index = None
            self.embeddings = None
            for p in (self.embeddings_path, self.faiss_path):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            return

        dim = self.embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(self.embeddings)
        self.index = index
        faiss.write_index(self.index, self.faiss_path)
        np.save(self.embeddings_path, self.embeddings)

    def _save_metadata(self) -> None:
        with open(self.docs_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.documents, f, indent=2, ensure_ascii=False)
        os.replace(self.docs_path + ".tmp", self.docs_path)

        with open(self.chunks_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.chunks, f, indent=2, ensure_ascii=False)
        os.replace(self.chunks_path + ".tmp", self.chunks_path)

    # -------------------------------------------------------------------------
    # Text Extraction (PDF, DOCX, TXT) & OCR / Vision Helpers
    # -------------------------------------------------------------------------

    def extract_sections(self, filename: str, file_bytes: bytes) -> Tuple[List[Dict[str, str]], str]:
        """
        Extract structured sections [(section_label, text), ...] from PDF, DOCX, or TXT.
        Raises ScannedDocumentError if a PDF has pages but no selectable text.
        """
        ext = os.path.splitext(filename)[1].lower()
        if ext == ".pdf":
            return self._extract_pdf(filename, file_bytes), "PDF"
        elif ext == ".docx":
            return self._extract_docx(filename, file_bytes), "DOCX"
        elif ext == ".txt":
            return self._extract_txt(filename, file_bytes), "TXT"
        else:
            raise ValueError(
                f"Unsupported file format '{ext}'. Please upload a PDF (.pdf), Word (.docx), "
                "Text (.txt), or Image (.png, .jpg) file."
            )

    def _extract_pdf(self, filename: str, file_bytes: bytes) -> List[Dict[str, str]]:
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
        except Exception as e:
            raise ValueError(f"Could not open PDF file '{filename}': {e}")

        num_pages = len(reader.pages)
        if num_pages == 0:
            raise EmptyDocumentError(f"'{filename}' has 0 pages.")

        sections: List[Dict[str, str]] = []
        total_non_ws_chars = 0

        for idx, page in enumerate(reader.pages, start=1):
            try:
                raw_text = page.extract_text() or ""
            except Exception:
                raw_text = ""
            cleaned = self._clean_text(raw_text)
            non_ws = len(re.sub(r"\s+", "", cleaned))
            total_non_ws_chars += non_ws
            if non_ws > 0:
                sections.append({
                    "section": f"Page {idx}",
                    "text": cleaned,
                })

        if total_non_ws_chars < 25 or (num_pages >= 2 and (total_non_ws_chars / num_pages) < 8):
            raise ScannedDocumentError(
                f"'{filename}' looks like a scanned document. Would you like to process it with OCR "
                "(works with any model) or re-read it visually using a vision-capable model?"
            )

        return sections

    def _ocr_pil_image(self, pil_img: Image.Image) -> str:
        """
        Extract text from a PIL Image using Tesseract OCR (pytesseract) when available,
        falling back cleanly to offline RapidOCR if tesseract.exe is not installed on the OS.
        """
        rgb_img = pil_img.convert("RGB")
        tesseract_bin = find_tesseract_binary()
        if tesseract_bin:
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = tesseract_bin
                text = pytesseract.image_to_string(rgb_img) or ""
                cleaned = self._clean_text(text)
                if cleaned:
                    return cleaned
            except Exception:
                pass

        # Offline RapidOCR fallback (pure Python/ONNX, no external binary required)
        try:
            if self._rapid_ocr is None:
                from rapidocr_onnxruntime import RapidOCR
                self._rapid_ocr = RapidOCR()
            res, _ = self._rapid_ocr(np.array(rgb_img))
            if res:
                lines = [str(item[1]).strip() for item in res if len(item) >= 2 and str(item[1]).strip()]
                return self._clean_text("\n".join(lines))
        except Exception as e:
            raise RuntimeError(f"OCR engine failed: {e}")
        return ""

    def _extract_pdf_ocr(self, filename: str, file_bytes: bytes) -> List[Dict[str, str]]:
        """Render each page of a scanned PDF via PyMuPDF and extract text with OCR."""
        try:
            pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
        except Exception as e:
            raise ValueError(f"Could not open PDF '{filename}' for OCR: {e}")

        if len(pdf_doc) == 0:
            pdf_doc.close()
            raise EmptyDocumentError(f"'{filename}' has 0 pages.")

        sections: List[Dict[str, str]] = []
        try:
            for idx, page in enumerate(pdf_doc, start=1):
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                text = self._ocr_pil_image(img)
                if text and re.sub(r"\s+", "", text):
                    sections.append({
                        "section": f"Page {idx} (OCR)",
                        "text": text,
                    })
        finally:
            pdf_doc.close()

        if not sections:
            raise EmptyDocumentError(
                f"OCR could not detect readable text in '{filename}'. "
                "Try re-reading it visually with a vision-capable model instead."
            )
        return sections

    def _extract_image_ocr(self, filename: str, file_bytes: bytes) -> List[Dict[str, str]]:
        """Run OCR on an uploaded PNG/JPG image."""
        try:
            img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        except Exception as e:
            raise ValueError(f"Could not read image '{filename}': {e}")

        text = self._ocr_pil_image(img)
        if not text or not re.sub(r"\s+", "", text):
            raise EmptyDocumentError(
                f"OCR did not find readable text in '{filename}'. Try reading it with Vision mode instead."
            )
        return [{"section": "Image (OCR)", "text": text}]

    def _render_vision_images(
        self,
        doc_id: str,
        filename: str,
        file_bytes: bytes,
        max_pages: int = 8,
    ) -> Tuple[List[str], str]:
        """
        Render PDF pages (up to max_pages) or normalize an uploaded PNG/JPG image into
        PNG files stored under `vision_store/<doc_id>/`. Returns (image_paths, file_type).
        """
        ext = os.path.splitext(filename)[1].lower()
        doc_vision_dir = os.path.join(self.vision_dir, doc_id)
        if os.path.exists(doc_vision_dir):
            shutil.rmtree(doc_vision_dir, ignore_errors=True)
        os.makedirs(doc_vision_dir, exist_ok=True)

        image_paths: List[str] = []
        max_dim = 1280

        if ext == ".pdf":
            try:
                pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
            except Exception as e:
                raise ValueError(f"Could not open PDF '{filename}' for vision reading: {e}")

            if len(pdf_doc) == 0:
                pdf_doc.close()
                raise EmptyDocumentError(f"'{filename}' has 0 pages.")

            try:
                total_pages = min(len(pdf_doc), max_pages)
                for idx in range(total_pages):
                    page = pdf_doc[idx]
                    pix = page.get_pixmap(dpi=150)
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                    if max(img.size) > max_dim:
                        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                    out_path = os.path.join(doc_vision_dir, f"page_{idx + 1}.png")
                    img.save(out_path, format="PNG")
                    image_paths.append(out_path)
            finally:
                pdf_doc.close()
            return image_paths, "PDF"

        if ext in IMAGE_EXTENSIONS:
            try:
                img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            except Exception as e:
                raise ValueError(f"Could not open image '{filename}': {e}")
            if max(img.size) > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            out_path = os.path.join(doc_vision_dir, "page_1.png")
            img.save(out_path, format="PNG")
            image_paths.append(out_path)
            return image_paths, ext.lstrip(".").upper()

        raise ValueError(f"Visual reading is only supported for PDF, PNG, and JPG files (got '{ext}').")

    def _extract_docx(self, filename: str, file_bytes: bytes) -> List[Dict[str, str]]:
        try:
            doc = docx.Document(io.BytesIO(file_bytes))
        except Exception as e:
            raise ValueError(f"Could not read Word document '{filename}': {e}")

        sections: List[Dict[str, str]] = []
        current_heading = "Section 1"
        current_lines: List[str] = []
        section_counter = 1

        for para in doc.paragraphs:
            text = (para.text or "").strip()
            if not text:
                continue
            style_name = (para.style.name if para.style else "").lower()
            if "heading" in style_name or "title" in style_name:
                if current_lines:
                    sections.append({
                        "section": current_heading,
                        "text": self._clean_text("\n\n".join(current_lines)),
                    })
                    current_lines = []
                    section_counter += 1
                short_title = text[:60] + ("..." if len(text) > 60 else "")
                current_heading = f"Section {section_counter}: {short_title}"
                current_lines.append(text)
            else:
                current_lines.append(text)

        for t_idx, table in enumerate(doc.tables, start=1):
            table_rows: List[str] = []
            for row in table.rows:
                cells = [self._clean_text(cell.text) for cell in row.cells if cell.text and cell.text.strip()]
                if cells:
                    table_rows.append(" | ".join(cells))
            if table_rows:
                current_lines.append(f"[Table {t_idx}]\n" + "\n".join(table_rows))

        if current_lines:
            sections.append({
                "section": current_heading,
                "text": self._clean_text("\n\n".join(current_lines)),
            })

        total_chars = sum(len(re.sub(r"\s+", "", s["text"])) for s in sections)
        if total_chars == 0:
            raise EmptyDocumentError(
                f"'{filename}' does not contain any readable text. Please check the file and try again."
            )

        return sections

    def _extract_txt(self, filename: str, file_bytes: bytes) -> List[Dict[str, str]]:
        decoded = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                decoded = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if decoded is None:
            decoded = file_bytes.decode("utf-8", errors="replace")

        cleaned = self._clean_text(decoded)
        if not re.sub(r"\s+", "", cleaned):
            raise EmptyDocumentError(f"'{filename}' is empty.")

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
        sections: List[Dict[str, str]] = []
        current_buf: List[str] = []
        current_len = 0
        sec_num = 1
        current_label = "Section 1"

        for para in paragraphs:
            first_line = para.split("\n", 1)[0].strip()
            is_heading = first_line.startswith("#") or (
                len(first_line) < 65 and first_line.isupper() and len(first_line) > 3
            )
            if (is_heading and current_buf) or current_len + len(para) > 2800:
                if current_buf:
                    sections.append({
                        "section": current_label,
                        "text": "\n\n".join(current_buf),
                    })
                    sec_num += 1
                    current_buf = []
                    current_len = 0
                if is_heading:
                    clean_h = first_line.lstrip("#").strip()[:50]
                    current_label = f"Section {sec_num}: {clean_h}"
                else:
                    current_label = f"Section {sec_num}"

            current_buf.append(para)
            current_len += len(para)

        if current_buf:
            sections.append({
                "section": current_label,
                "text": "\n\n".join(current_buf),
            })

        return sections

    @staticmethod
    def _clean_text(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # -------------------------------------------------------------------------
    # Staged Scanned PDF Workflow (OCR vs. Vision Prompt)
    # -------------------------------------------------------------------------

    def stage_scanned_pdf(
        self,
        filename: str,
        file_bytes: bytes,
        scope: str = "global",
        chat_id: Optional[str] = None,
        replace_doc_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Temporarily stage a scanned PDF while asking the user to choose OCR or Vision."""
        safe_filename = os.path.basename(filename).strip() or "Scanned.pdf"
        staging_id = str(uuid.uuid4())
        pdf_path = os.path.join(self.staged_dir, f"{staging_id}.pdf")
        meta_path = os.path.join(self.staged_dir, f"{staging_id}.json")

        num_pages = 1
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            num_pages = len(doc)
            doc.close()
        except Exception:
            pass

        with open(pdf_path, "wb") as f:
            f.write(file_bytes)

        meta = {
            "staging_id": staging_id,
            "filename": safe_filename,
            "num_pages": num_pages,
            "scope": scope if scope in ("global", "chat") else "global",
            "chat_id": chat_id if scope == "chat" else None,
            "replace_doc_id": replace_doc_id,
            "created_at": time.time(),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        return {
            "staging_id": staging_id,
            "filename": safe_filename,
            "num_pages": num_pages,
            "scope": meta["scope"],
            "chat_id": meta["chat_id"],
            "prompt_message": (
                "This looks like a scanned document. Would you like to process it with OCR "
                "(works with any model) or re-read it visually using a vision-capable model?"
            ),
        }

    def process_staged_scanned_pdf(self, staging_id: str, mode: str) -> Dict[str, Any]:
        """Process a staged scanned PDF using either 'ocr' or 'vision', then clean up temp files."""
        clean_mode = "vision" if mode == "vision" else "ocr"
        pdf_path = os.path.join(self.staged_dir, f"{staging_id}.pdf")
        meta_path = os.path.join(self.staged_dir, f"{staging_id}.json")

        if not os.path.exists(pdf_path) or not os.path.exists(meta_path):
            raise FileNotFoundError("The pending scanned document expired or was not found. Please upload it again.")

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        with open(pdf_path, "rb") as f:
            file_bytes = f.read()

        try:
            record = self.add_document(
                filename=meta["filename"],
                file_bytes=file_bytes,
                replace_doc_id=meta.get("replace_doc_id"),
                scope=meta.get("scope"),
                chat_id=meta.get("chat_id"),
                processing_mode=clean_mode,
            )
            return record
        finally:
            for p in (pdf_path, meta_path):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    def cancel_staged_scanned_pdf(self, staging_id: str) -> None:
        for ext in (".pdf", ".json"):
            p = os.path.join(self.staged_dir, f"{staging_id}{ext}")
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # Chunking
    # -------------------------------------------------------------------------

    def _chunk_sections(
        self,
        doc_id: str,
        filename: str,
        sections: List[Dict[str, str]],
        scope: str = "global",
        chat_id: Optional[str] = None,
        read_mode: str = "text",
        chunk_size: int = 720,
        chunk_overlap: int = 130,
    ) -> List[Dict[str, Any]]:
        """Split document sections into overlapping chunks while preserving section, scope & read_mode."""
        chunks: List[Dict[str, Any]] = []
        chunk_idx = 1

        for sec in sections:
            sec_label = sec["section"]
            text = sec["text"]
            if len(text) <= chunk_size:
                chunks.append({
                    "chunk_id": str(uuid.uuid4()),
                    "doc_id": doc_id,
                    "filename": filename,
                    "section": sec_label,
                    "chunk_index": chunk_idx,
                    "text": text,
                    "scope": scope,
                    "chat_id": chat_id,
                    "read_mode": read_mode,
                })
                chunk_idx += 1
                continue

            segments = re.split(r"(?<=[.!?])\s+|\n\n+", text)
            current_piece = ""
            for seg in segments:
                seg = seg.strip()
                if not seg:
                    continue
                if len(current_piece) + len(seg) + 1 <= chunk_size:
                    current_piece = f"{current_piece} {seg}".strip() if current_piece else seg
                else:
                    if current_piece:
                        chunks.append({
                            "chunk_id": str(uuid.uuid4()),
                            "doc_id": doc_id,
                            "filename": filename,
                            "section": sec_label,
                            "chunk_index": chunk_idx,
                            "text": current_piece,
                            "scope": scope,
                            "chat_id": chat_id,
                            "read_mode": read_mode,
                        })
                        chunk_idx += 1
                        overlap_tail = current_piece[-chunk_overlap:] if len(current_piece) > chunk_overlap else current_piece
                        current_piece = f"{overlap_tail} {seg}".strip()
                    else:
                        for start in range(0, len(seg), chunk_size - chunk_overlap):
                            sub = seg[start : start + chunk_size].strip()
                            if sub:
                                chunks.append({
                                    "chunk_id": str(uuid.uuid4()),
                                    "doc_id": doc_id,
                                    "filename": filename,
                                    "section": sec_label,
                                    "chunk_index": chunk_idx,
                                    "text": sub,
                                    "scope": scope,
                                    "chat_id": chat_id,
                                    "read_mode": read_mode,
                                })
                                chunk_idx += 1
                        current_piece = ""

            if current_piece:
                chunks.append({
                    "chunk_id": str(uuid.uuid4()),
                    "doc_id": doc_id,
                    "filename": filename,
                    "section": sec_label,
                    "chunk_index": chunk_idx,
                    "text": current_piece,
                    "scope": scope,
                    "chat_id": chat_id,
                    "read_mode": read_mode,
                })
                chunk_idx += 1

        return chunks

    # -------------------------------------------------------------------------
    # Add / Re-upload / Scope Update / Delete Documents
    # -------------------------------------------------------------------------

    def _resolve_scope(
        self,
        replace_doc_id: Optional[str],
        scope: Optional[str],
        chat_id: Optional[str],
    ) -> Tuple[str, Optional[str]]:
        with self._lock:
            existing_target = None
            if replace_doc_id:
                for d in self.documents:
                    if d["id"] == replace_doc_id:
                        existing_target = d
                        break

        if scope in ("global", "chat"):
            effective_scope = scope
            effective_chat_id = chat_id if scope == "chat" else None
        elif existing_target:
            effective_scope = existing_target.get("scope", "global")
            effective_chat_id = existing_target.get("chat_id") if effective_scope == "chat" else None
        else:
            effective_scope = "global"
            effective_chat_id = None

        if effective_scope == "chat" and not effective_chat_id:
            effective_scope = "global"
            effective_chat_id = None

        return effective_scope, effective_chat_id

    def add_document(
        self,
        filename: str,
        file_bytes: bytes,
        replace_doc_id: Optional[str] = None,
        scope: Optional[str] = None,
        chat_id: Optional[str] = None,
        processing_mode: str = "auto",
    ) -> Dict[str, Any]:
        """
        Ingest a document (PDF, DOCX, TXT, PNG, JPG).
        `processing_mode`:
          - "auto": standard text extraction for PDF/DOCX/TXT; Vision mode for PNG/JPG images
          - "ocr": extract text with Tesseract/RapidOCR (for scanned PDFs or images) -> FAISS RAG
          - "vision": render pages / save image for multimodal Vision model direct reading
        """
        safe_filename = os.path.basename(filename).strip() or "Untitled.txt"
        ext = os.path.splitext(safe_filename)[1].lower()
        effective_scope, effective_chat_id = self._resolve_scope(replace_doc_id, scope, chat_id)
        doc_id = replace_doc_id or str(uuid.uuid4())

        # Determine read path
        if ext in IMAGE_EXTENSIONS:
            chosen_mode = "ocr" if processing_mode == "ocr" else "vision"
        elif ext == ".pdf" and processing_mode in ("ocr", "vision"):
            chosen_mode = processing_mode
        else:
            chosen_mode = "text"

        # Path A: Vision path (stores page PNGs directly without FAISS text chunks)
        if chosen_mode == "vision":
            with self._lock:
                if not replace_doc_id:
                    for d in self.documents:
                        if (
                            d["filename"].lower() == safe_filename.lower()
                            and d.get("scope", "global") == effective_scope
                            and d.get("chat_id") == effective_chat_id
                        ):
                            doc_id = d["id"]
                            break

            image_paths, file_type = self._render_vision_images(doc_id, safe_filename, file_bytes)

            with self._lock:
                existing_ids_to_remove = set()
                if replace_doc_id:
                    existing_ids_to_remove.add(replace_doc_id)
                else:
                    for d in self.documents:
                        if d["id"] == doc_id:
                            existing_ids_to_remove.add(d["id"])
                if existing_ids_to_remove:
                    self._remove_docs_internal(existing_ids_to_remove, keep_vision_dir=True)

                stored_file_path = os.path.join(self.files_dir, f"{doc_id}_{safe_filename}")
                try:
                    with open(stored_file_path, "wb") as f:
                        f.write(file_bytes)
                except Exception:
                    pass

                doc_record = {
                    "id": doc_id,
                    "filename": safe_filename,
                    "file_type": file_type,
                    "file_size": len(file_bytes),
                    "file_size_human": format_bytes(len(file_bytes)),
                    "num_sections": len(image_paths),
                    "num_chunks": len(image_paths),
                    "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "timestamp": time.time(),
                    "preview": f"Visual document ({len(image_paths)} page{'s' if len(image_paths) > 1 else ''})",
                    "scope": effective_scope,
                    "chat_id": effective_chat_id,
                    "read_mode": "vision",
                    "read_mode_label": self._format_read_mode_label("vision"),
                    "image_paths": image_paths,
                }
                self.documents.append(doc_record)
                self._save_metadata()
                self._rebuild_faiss_from_embeddings()
                return doc_record

        # Path B: Text extraction or OCR -> standard FAISS RAG pipeline
        if chosen_mode == "ocr":
            if ext == ".pdf":
                sections = self._extract_pdf_ocr(safe_filename, file_bytes)
                file_type = "PDF"
            elif ext in IMAGE_EXTENSIONS:
                sections = self._extract_image_ocr(safe_filename, file_bytes)
                file_type = ext.lstrip(".").upper()
            else:
                sections, file_type = self.extract_sections(safe_filename, file_bytes)
            read_mode = "ocr"
        else:
            sections, file_type = self.extract_sections(safe_filename, file_bytes)
            read_mode = "text"

        new_chunks = self._chunk_sections(
            doc_id=doc_id,
            filename=safe_filename,
            sections=sections,
            scope=effective_scope,
            chat_id=effective_chat_id,
            read_mode=read_mode,
        )
        if not new_chunks:
            raise EmptyDocumentError(f"Could not extract any text chunks from '{safe_filename}'.")

        texts_to_embed = [
            f"Document: {safe_filename} ({c['section']})\n{c['text']}"
            for c in new_chunks
        ]
        raw_vectors = self.ollama.get_embeddings(texts_to_embed, model=EMBEDDING_MODEL)
        vec_array = np.array(raw_vectors, dtype="float32")
        if vec_array.ndim != 2 or vec_array.shape[0] != len(new_chunks):
            raise RuntimeError("Embedding dimension mismatch from Ollama.")
        faiss.normalize_L2(vec_array)

        with self._lock:
            existing_ids_to_remove = set()
            if replace_doc_id:
                existing_ids_to_remove.add(replace_doc_id)
            else:
                for d in self.documents:
                    if (
                        d["filename"].lower() == safe_filename.lower()
                        and d.get("scope", "global") == effective_scope
                        and d.get("chat_id") == effective_chat_id
                    ):
                        existing_ids_to_remove.add(d["id"])
                        doc_id = d["id"]
                        for c in new_chunks:
                            c["doc_id"] = doc_id

            if existing_ids_to_remove:
                self._remove_docs_internal(existing_ids_to_remove)

            stored_file_path = os.path.join(self.files_dir, f"{doc_id}_{safe_filename}")
            try:
                with open(stored_file_path, "wb") as f:
                    f.write(file_bytes)
            except Exception:
                pass

            if self.embeddings is None or len(self.chunks) == 0:
                self.embeddings = vec_array
            else:
                if self.embeddings.shape[1] != vec_array.shape[1]:
                    self.chunks = []
                    self.documents = []
                    self.embeddings = vec_array
                else:
                    self.embeddings = np.vstack([self.embeddings, vec_array])

            self.chunks.extend(new_chunks)
            preview_text = sections[0]["text"][:180] + ("..." if len(sections[0]["text"]) > 180 else "")

            doc_record = {
                "id": doc_id,
                "filename": safe_filename,
                "file_type": file_type,
                "file_size": len(file_bytes),
                "file_size_human": format_bytes(len(file_bytes)),
                "num_sections": len(sections),
                "num_chunks": len(new_chunks),
                "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "timestamp": time.time(),
                "preview": preview_text,
                "scope": effective_scope,
                "chat_id": effective_chat_id,
                "read_mode": read_mode,
                "read_mode_label": self._format_read_mode_label(read_mode),
                "image_paths": [],
            }
            self.documents.append(doc_record)
            self._save_metadata()
            self._rebuild_faiss_from_embeddings()

            return doc_record

    def update_document_scope(
        self,
        doc_id: str,
        scope: str,
        chat_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a document's scope ('global' vs 'chat') and propagate to its chunks."""
        clean_scope = "chat" if (scope == "chat" and chat_id) else "global"
        clean_chat_id = chat_id if clean_scope == "chat" else None

        with self._lock:
            target_doc = None
            for d in self.documents:
                if d["id"] == doc_id:
                    d["scope"] = clean_scope
                    d["chat_id"] = clean_chat_id
                    target_doc = dict(d)
                    break

            if not target_doc:
                return None

            for c in self.chunks:
                if c.get("doc_id") == doc_id:
                    c["scope"] = clean_scope
                    c["chat_id"] = clean_chat_id

            self._save_metadata()
            return target_doc

    def _remove_docs_internal(self, doc_ids: set[str], keep_vision_dir: bool = False) -> None:
        """Internal helper (must be called under self._lock) to remove documents and their vectors."""
        keep_indices: List[int] = []
        kept_chunks: List[Dict[str, Any]] = []
        for i, chunk in enumerate(self.chunks):
            if chunk["doc_id"] not in doc_ids:
                keep_indices.append(i)
                kept_chunks.append(chunk)

        self.chunks = kept_chunks
        if self.embeddings is not None and len(keep_indices) > 0:
            self.embeddings = self.embeddings[keep_indices]
        else:
            self.embeddings = None

        for d in self.documents:
            if d["id"] in doc_ids:
                stored_path = os.path.join(self.files_dir, f"{d['id']}_{d['filename']}")
                if os.path.exists(stored_path):
                    try:
                        os.remove(stored_path)
                    except Exception:
                        pass
                if not keep_vision_dir:
                    vdir = os.path.join(self.vision_dir, d["id"])
                    if os.path.exists(vdir):
                        shutil.rmtree(vdir, ignore_errors=True)

        self.documents = [d for d in self.documents if d["id"] not in doc_ids]

    def remove_document(self, doc_id: str) -> bool:
        """Delete an uploaded document, its chunks, vision images, and update the FAISS index."""
        with self._lock:
            exists = any(d["id"] == doc_id for d in self.documents)
            if not exists:
                return False
            self._remove_docs_internal({doc_id})
            self._save_metadata()
            self._rebuild_faiss_from_embeddings()
            return True

    def list_documents(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self.documents))

    def get_applicable_documents(self, current_chat_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return documents available to `current_chat_id`:
        all global documents + chat-only documents belonging to `current_chat_id`.
        """
        with self._lock:
            result = []
            for d in reversed(self.documents):
                scope = d.get("scope", "global")
                if scope == "global":
                    result.append(dict(d))
                elif scope == "chat" and current_chat_id and d.get("chat_id") == current_chat_id:
                    result.append(dict(d))
            return result

    def get_vision_documents_for_chat(self, current_chat_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return applicable documents with read_mode == 'vision' along with base64-encoded PNG pages."""
        applicable = self.get_applicable_documents(current_chat_id)
        vision_docs: List[Dict[str, Any]] = []
        for doc in applicable:
            if doc.get("read_mode") != "vision":
                continue
            b64_images: List[str] = []
            for p in doc.get("image_paths", []):
                if p and os.path.exists(p):
                    try:
                        with open(p, "rb") as img_f:
                            b64_images.append(base64.b64encode(img_f.read()).decode("ascii"))
                    except Exception:
                        pass
            if b64_images:
                doc_copy = dict(doc)
                doc_copy["images_b64"] = b64_images
                vision_docs.append(doc_copy)
        return vision_docs

    # -------------------------------------------------------------------------
    # Semantic Retrieval (FAISS + Scope Filtering + Plausible Relevance Check)
    # -------------------------------------------------------------------------

    @staticmethod
    def _chunk_is_allowed(
        chunk: Dict[str, Any],
        current_chat_id: Optional[str],
        doc_ids: Optional[List[str]],
    ) -> bool:
        if doc_ids and chunk.get("doc_id") not in doc_ids:
            return False
        scope = chunk.get("scope") or "global"
        if scope == "global":
            return True
        if scope == "chat" and current_chat_id and chunk.get("chat_id") == current_chat_id:
            return True
        return False

    @staticmethod
    def _query_references_documents(query: str, applicable_docs: List[Dict[str, Any]]) -> bool:
        """Check whether the user explicitly references an uploaded document/reading/filename."""
        q_lower = query.lower()
        q_words = set(re.findall(r"[a-zA-Z0-9]{2,}", q_lower))
        if q_words & DOC_REFERENCE_WORDS:
            return True

        for d in applicable_docs:
            fname = os.path.splitext(d.get("filename", ""))[0].lower()
            name_words = [w for w in re.findall(r"[a-zA-Z0-9]{4,}", fname) if w not in STOPWORDS]
            if any(w in q_lower for w in name_words):
                return True
        return False

    def search(
        self,
        query: str,
        top_k: int = 4,
        current_chat_id: Optional[str] = None,
        doc_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant document chunks for a user question using FAISS vector similarity,
        strictly filtered by document scope AND plausible relevance so unrelated general
        questions receive `[]` instead of irrelevant document context.
        """
        query = (query or "").strip()
        if not query:
            return []

        applicable_docs = [
            d for d in self.get_applicable_documents(current_chat_id)
            if d.get("read_mode", "text") != "vision"
        ]
        if not applicable_docs:
            return []

        with self._lock:
            if not self.chunks or self.index is None or self.index.ntotal == 0:
                return []
            chunks_snapshot = list(self.chunks)
            index_ref = self.index

        if not any(self._chunk_is_allowed(c, current_chat_id, doc_ids) for c in chunks_snapshot):
            return []

        explicit_doc_ref = self._query_references_documents(query, applicable_docs)
        query_terms = {
            w.lower()
            for w in re.findall(r"[a-zA-Z0-9]{3,}", query)
            if w.lower() not in STOPWORDS
        }

        try:
            q_vecs = self.ollama.get_embeddings([query], model=EMBEDDING_MODEL)
            q_arr = np.array(q_vecs, dtype="float32")
            faiss.normalize_L2(q_arr)

            search_k = len(chunks_snapshot)
            scores, indices = index_ref.search(q_arr, search_k)
        except Exception:
            return self._lexical_search(
                query, chunks_snapshot, top_k, current_chat_id, doc_ids, explicit_doc_ref
            )

        candidates: List[Dict[str, Any]] = []
        for raw_score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(chunks_snapshot):
                continue
            chunk = chunks_snapshot[idx]
            if not self._chunk_is_allowed(chunk, current_chat_id, doc_ids):
                continue

            semantic_score = float(max(0.0, min(1.0, raw_score)))
            keyword_hits = 0
            if query_terms:
                text_lower = (chunk["text"] + " " + chunk["filename"]).lower()
                keyword_hits = sum(1 for term in query_terms if term in text_lower)
                keyword_boost = min(0.12, (keyword_hits / max(1, len(query_terms))) * 0.12)
            else:
                keyword_boost = 0.0

            combined_score = min(0.99, semantic_score + keyword_boost)

            # Plausible relevance gate
            if explicit_doc_ref:
                is_plausible = combined_score >= 0.30 or keyword_hits >= 1
            else:
                is_plausible = (keyword_hits >= 1 and combined_score >= 0.48) or (semantic_score >= 0.64)

            if not is_plausible:
                continue

            item = dict(chunk)
            item["score"] = round(combined_score, 4)
            item["score_percent"] = int(round(combined_score * 100))
            candidates.append(item)

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_k]

    def _lexical_search(
        self,
        query: str,
        chunks_snapshot: List[Dict[str, Any]],
        top_k: int,
        current_chat_id: Optional[str] = None,
        doc_ids: Optional[List[str]] = None,
        explicit_doc_ref: bool = False,
    ) -> List[Dict[str, Any]]:
        terms = [w.lower() for w in re.findall(r"[a-zA-Z0-9]{3,}", query) if w.lower() not in STOPWORDS]
        scored: List[Dict[str, Any]] = []
        for chunk in chunks_snapshot:
            if not self._chunk_is_allowed(chunk, current_chat_id, doc_ids):
                continue
            text_lower = (chunk["text"] + " " + chunk["filename"]).lower()
            hits = sum(1 for t in terms if t in text_lower)
            if hits > 0 or (explicit_doc_ref and not terms):
                score = min(0.95, 0.4 + 0.15 * hits)
                item = dict(chunk)
                item["score"] = round(score, 4)
                item["score_percent"] = int(round(score * 100))
                scored.append(item)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]
