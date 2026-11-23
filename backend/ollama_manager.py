"""
Ollama service manager for Fellow:
- Health check, binary discovery, and automatic background startup
- Installed model listing with friendly labels and exact disk sizes
- Model deletion with freed disk space calculation
- Full download controller with progress (%, MB), Pause, Resume, and Cancel/Cleanup
- Embeddings generation (nomic-embed-text) and streaming chat completions
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Generator, List, Optional
import requests

from backend.config import (
    CURATED_MODELS,
    EMBEDDING_MODEL,
    OLLAMA_BASE_URL,
    get_friendly_model_info,
    get_resource_path,
)


def format_bytes(num_bytes: int) -> str:
    """Format byte count into human-readable MB or GB."""
    if not num_bytes or num_bytes <= 0:
        return "0 MB"
    gb = num_bytes / (1024 ** 3)
    if gb >= 0.95:
        return f"{gb:.2f} GB"
    mb = num_bytes / (1024 ** 2)
    if mb >= 1:
        return f"{mb:.1f} MB"
    kb = num_bytes / 1024
    return f"{kb:.0f} KB"


def is_vision_model(model_name: str, details: Optional[Dict[str, Any]] = None) -> bool:
    """Detect whether an Ollama model supports image / visual inputs."""
    name_lower = (model_name or "").strip().lower()
    if "embed" in name_lower:
        return False

    family_str = ""
    if isinstance(details, dict):
        fam = details.get("family", "") or ""
        fams = details.get("families") or []
        family_str = f"{fam} {' '.join(fams)}".lower()

    vision_markers = (
        "lightonocr",
        "maternion",
        "rana",
        "vl",
        "vision",
        "llava",
        "moondream",
        "minicpm-v",
        "pixtral",
        "gemma3",
        "qwen3.5",
        "qwen2.5vl",
        "clip",
        "mllama",
    )
    combined = f"{name_lower} {family_str}"
    return any(marker in combined for marker in vision_markers)


def is_ocr_specialized_model(model_name: str) -> bool:
    """Return True if the model is a dedicated single-turn OCR/vision reader (e.g. LightOnOCR-3)."""
    lower = (model_name or "").lower()
    return "lightonocr" in lower


def _find_bundled_ollama_binary() -> Optional[str]:
    """Locate an Ollama binary bundled directly inside the packaged Fellow app bundle."""
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates: List[str] = []

    if sys.platform == "darwin":
        candidates.extend([
            os.path.abspath(os.path.join(exe_dir, "..", "Resources", "ollama", "ollama")),
            os.path.abspath(os.path.join(exe_dir, "..", "Resources", "ollama", "bin", "ollama")),
            os.path.abspath(os.path.join(exe_dir, "ollama", "ollama")),
            get_resource_path(os.path.join("ollama", "ollama")),
            get_resource_path(os.path.join("ollama", "bin", "ollama")),
        ])
    elif sys.platform == "win32":
        candidates.extend([
            os.path.abspath(os.path.join(exe_dir, "ollama", "ollama.exe")),
            get_resource_path(os.path.join("ollama", "ollama.exe")),
        ])

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def find_ollama_binary() -> Optional[str]:
    """Locate the ollama executable in PATH, standard install paths, or Fellow's bundled sidecar."""
    in_path = shutil.which("ollama")
    if in_path and os.path.exists(in_path):
        return in_path

    candidates: List[str] = []
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        candidates.extend([
            os.path.join(local_app_data, "Programs", "Ollama", "ollama.exe"),
            os.path.join(prog_files, "Ollama", "ollama.exe"),
            os.path.expanduser("~\\AppData\\Local\\Programs\\Ollama\\ollama.exe"),
        ])
    elif sys.platform == "darwin":
        candidates.extend([
            "/usr/local/bin/ollama",
            "/opt/homebrew/bin/ollama",
            "/Applications/Ollama.app/Contents/Resources/ollama",
        ])
    else:
        candidates.extend([
            "/usr/local/bin/ollama",
            "/usr/bin/ollama",
        ])

    for path in candidates:
        if path and os.path.exists(path):
            return path

    return _find_bundled_ollama_binary()


def get_ollama_models_dir() -> str:
    """Locate Ollama's models directory for partial blob cleanup."""
    custom = os.environ.get("OLLAMA_MODELS")
    if custom and os.path.exists(custom):
        return custom
    return os.path.expanduser("~/.ollama/models")


class DownloadTask:
    """Represents an active, paused, completed, or cancelled model download."""

    def __init__(self, model_name: str):
        self.model = model_name
        self.status = "starting"  # starting | downloading | paused | completed | cancelled | error
        self.status_text = "Connecting to Ollama..."
        self.percent: float = 0.0
        self.completed_bytes: int = 0
        self.total_bytes: int = 0
        self.error: Optional[str] = None
        self.cleaned_bytes: int = 0
        self.updated_at: float = time.time()

        self.layers: Dict[str, Dict[str, int]] = {}
        self.seen_digests: set[str] = set()

        self._lock = threading.Lock()
        self._pause_requested = threading.Event()
        self._cancel_requested = threading.Event()
        self._active_response: Optional[requests.Response] = None
        self._thread: Optional[threading.Thread] = None

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            completed_mb = round(self.completed_bytes / (1024 ** 2), 1)
            total_mb = round(self.total_bytes / (1024 ** 2), 1)
            return {
                "model": self.model,
                "status": self.status,
                "status_text": self.status_text,
                "percent": round(self.percent, 1),
                "completed_bytes": self.completed_bytes,
                "total_bytes": self.total_bytes,
                "completed_mb": completed_mb,
                "total_mb": total_mb,
                "completed_human": format_bytes(self.completed_bytes),
                "total_human": format_bytes(self.total_bytes),
                "error": self.error,
                "cleaned_human": format_bytes(self.cleaned_bytes) if self.cleaned_bytes else None,
                "updated_at": self.updated_at,
            }


class OllamaManager:
    """Manages communication with local Ollama instance and model downloads."""

    def __init__(self, base_url: str = OLLAMA_BASE_URL):
        self.base_url = base_url.rstrip("/").replace("://localhost:", "://127.0.0.1:")
        self.session = requests.Session()
        self._downloads: Dict[str, DownloadTask] = {}
        self._downloads_lock = threading.Lock()
        self._spawned_process: Optional[subprocess.Popen] = None
        self._spawned_bundled: bool = False

    def warmup_model(self, model_name: str) -> None:
        """Pre-load the selected chat model into memory in a background thread so first reply is instant."""
        if not model_name or "embed" in model_name.lower():
            return

        def _worker():
            try:
                self.session.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model_name,
                        "messages": [],
                        "keep_alive": "30m",
                    },
                    timeout=15.0,
                )
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def is_running(self, timeout: float = 1.5) -> bool:
        try:
            resp = self.session.get(f"{self.base_url}/api/tags", timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def get_version(self) -> Optional[str]:
        try:
            resp = self.session.get(f"{self.base_url}/api/version", timeout=1.5)
            if resp.status_code == 200:
                return resp.json().get("version")
        except Exception:
            pass
        return None

    def start_ollama_service(self) -> bool:
        """If Ollama is installed on disk (or bundled in the app) but not running, start `ollama serve` silently."""
        if self.is_running(timeout=1.5):
            return True

        binary = find_ollama_binary()
        if not binary:
            return False

        bundled_bin = _find_bundled_ollama_binary()
        is_bundled = bool(
            bundled_bin and os.path.abspath(binary) == os.path.abspath(bundled_bin)
        )

        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

            self._spawned_process = subprocess.Popen(
                [binary, "serve"],
                cwd=os.path.dirname(os.path.abspath(binary)) or None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._spawned_bundled = is_bundled
            # Wait up to 10 seconds for Ollama API (including first-run Metal init on macOS)
            for _ in range(20):
                time.sleep(0.5)
                if self.is_running(timeout=1.0):
                    return True
        except Exception:
            pass
        return self.is_running(timeout=1.5)

    def stop_spawned_service(self) -> None:
        """Stop an Ollama background server only if Fellow spawned its own bundled sidecar."""
        if not self._spawned_bundled or self._spawned_process is None:
            return
        if self._spawned_process.poll() is not None:
            return
        try:
            self._spawned_process.terminate()
            self._spawned_process.wait(timeout=3)
        except Exception:
            try:
                self._spawned_process.kill()
            except Exception:
                pass
        finally:
            self._spawned_bundled = False

    def check_status(self, auto_start: bool = True) -> Dict[str, Any]:
        """Check whether Ollama is installed and running; auto-start if installed."""
        running = self.is_running(timeout=1.8)
        binary = find_ollama_binary()
        installed = bool(binary) or running

        auto_started = False
        if not running and installed and auto_start:
            running = self.start_ollama_service()
            auto_started = running

        version = self.get_version() if running else None
        models = self.list_installed_models() if running else []
        installed_names = [m["name"] for m in models]

        has_embedding_model = any(
            m["name"] == EMBEDDING_MODEL or m["name"].startswith(f"{EMBEDDING_MODEL}:")
            for m in models
        )
        chat_models = [m for m in models if not m["is_embedding"]]

        return {
            "installed": installed,
            "running": running,
            "auto_started": auto_started,
            "binary_path": binary,
            "base_url": self.base_url,
            "version": version,
            "download_url": "https://ollama.com/download",
            "installed_models": models,
            "installed_model_names": installed_names,
            "has_embedding_model": has_embedding_model,
            "has_chat_model": len(chat_models) > 0,
        }

    def list_installed_models(self) -> List[Dict[str, Any]]:
        """Return installed models with human sizes and friendly labels."""
        try:
            resp = self.session.get(f"{self.base_url}/api/tags", timeout=3.0)
            if resp.status_code != 200:
                return []
            raw_models = resp.json().get("models", []) or []
        except Exception:
            return []

        result: List[Dict[str, Any]] = []
        for item in raw_models:
            name = item.get("name", "")
            # Normalize ":latest" only for display comparison while keeping exact tag
            clean_name = name[:-7] if name.endswith(":latest") else name
            size_bytes = int(item.get("size", 0) or 0)
            details = item.get("details", {}) or {}
            is_embedding = "embed" in name.lower()
            supports_vision = is_vision_model(clean_name, details)

            friendly = get_friendly_model_info(clean_name)
            simple_lbl = friendly["simple_label"]
            if supports_vision and "vision" not in simple_lbl.lower():
                simple_lbl = f"{simple_lbl} (Vision)"

            result.append({
                "name": name,
                "clean_name": clean_name,
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / (1024 ** 2), 1),
                "size_human": format_bytes(size_bytes),
                "modified_at": item.get("modified_at", ""),
                "is_embedding": is_embedding,
                "supports_vision": supports_vision,
                "vision_badge": "Supports images and scanned documents" if supports_vision else None,
                "simple_label": "Document Search Engine" if is_embedding else simple_lbl,
                "title": "Nomic Embed Text" if is_embedding else friendly["title"],
                "tier_name": "System" if is_embedding else friendly["tier_name"],
                "description": (
                    "Reads and indexes your uploaded PDFs, Word docs, and text files for search"
                    if is_embedding
                    else friendly["description"]
                ),
                "parameter_size": details.get("parameter_size", ""),
                "quantization_level": details.get("quantization_level", ""),
                "family": details.get("family", ""),
            })
        return result

    def is_model_installed(self, model_name: str) -> bool:
        target = (model_name or "").strip().lower()
        if not target:
            return False
        for m in self.list_installed_models():
            if m["name"].lower() == target or m["clean_name"].lower() == target:
                return True
        return False

    def delete_model(self, model_name: str) -> Dict[str, Any]:
        """Delete an installed model from Ollama and return the freed disk space."""
        installed = self.list_installed_models()
        target_info = None
        for m in installed:
            if m["name"].lower() == model_name.lower() or m["clean_name"].lower() == model_name.lower():
                target_info = m
                break

        exact_name = target_info["name"] if target_info else model_name
        size_bytes = target_info["size_bytes"] if target_info else 0

        try:
            resp = requests.delete(
                f"{self.base_url}/api/delete",
                json={"name": exact_name},
                timeout=15.0,
            )
            if resp.status_code not in (200, 204):
                # Try without/with :latest if needed
                alt_name = f"{model_name}:latest" if ":" not in model_name else model_name
                resp = requests.delete(
                    f"{self.base_url}/api/delete",
                    json={"name": alt_name},
                    timeout=15.0,
                )
                if resp.status_code not in (200, 204):
                    return {
                        "success": False,
                        "error": f"Ollama returned status {resp.status_code}: {resp.text}",
                    }

            freed_human = format_bytes(size_bytes) if size_bytes > 0 else "disk space"
            return {
                "success": True,
                "model": model_name,
                "freed_bytes": size_bytes,
                "freed_human": freed_human,
                "message": f"Deleted {model_name} — freed {freed_human} of disk space.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -------------------------------------------------------------------------
    # Download Management (Start / Progress / Pause / Resume / Cancel+Cleanup)
    # -------------------------------------------------------------------------

    def start_download(self, model_name: str) -> Dict[str, Any]:
        """Start or resume pulling a model from Ollama."""
        model_name = (model_name or "").strip()
        if not model_name:
            return {"error": "Model name is required"}

        if not self.is_running():
            self.start_ollama_service()

        with self._downloads_lock:
            existing = self._downloads.get(model_name)
            if existing and existing.status in ("downloading", "starting"):
                return existing.to_dict()

            task = existing if (existing and existing.status == "paused") else DownloadTask(model_name)
            task._pause_requested.clear()
            task._cancel_requested.clear()
            task.status = "downloading"
            task.status_text = "Starting download..."
            task.error = None
            task.updated_at = time.time()
            self._downloads[model_name] = task

            t = threading.Thread(
                target=self._run_download_worker,
                args=(task,),
                daemon=True,
            )
            task._thread = t
            t.start()
            return task.to_dict()

    def _run_download_worker(self, task: DownloadTask) -> None:
        """Background worker that streams `/api/pull` progress from Ollama."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/pull",
                json={"name": task.model, "stream": True},
                stream=True,
                timeout=(10, 300),
            )
            with task._lock:
                task._active_response = resp

            if resp.status_code != 200:
                with task._lock:
                    task.status = "error"
                    task.error = f"Download failed (HTTP {resp.status_code}): {resp.text}"
                    task.status_text = task.error
                    task.updated_at = time.time()
                return

            for raw_line in resp.iter_lines(decode_unicode=True):
                if task._cancel_requested.is_set() or task._pause_requested.is_set():
                    break
                if not raw_line:
                    continue
                try:
                    data = json.loads(raw_line)
                except Exception:
                    continue

                if "error" in data:
                    with task._lock:
                        task.status = "error"
                        task.error = str(data["error"])
                        task.status_text = f"Error: {task.error}"
                        task.updated_at = time.time()
                    return

                status_msg = data.get("status", "Downloading...")
                digest = data.get("digest")
                total = data.get("total")
                completed = data.get("completed")

                with task._lock:
                    if digest:
                        clean_digest = digest.replace(":", "-")
                        task.seen_digests.add(clean_digest)
                        if total is not None:
                            layer = task.layers.setdefault(digest, {"total": 0, "completed": 0})
                            layer["total"] = max(layer["total"], int(total))
                            if completed is not None:
                                layer["completed"] = max(layer["completed"], int(completed))

                    # Sum across all known layers for smooth monotonic progress
                    agg_total = sum(l["total"] for l in task.layers.values())
                    agg_completed = sum(l["completed"] for l in task.layers.values())

                    if agg_total > 0:
                        task.total_bytes = agg_total
                        task.completed_bytes = min(agg_completed, agg_total)
                        task.percent = min(99.9, (task.completed_bytes / task.total_bytes) * 100.0)

                    # Friendly status text
                    if status_msg.startswith("pulling "):
                        if task.total_bytes > 0:
                            task.status_text = (
                                f"Downloading {format_bytes(task.completed_bytes)} / "
                                f"{format_bytes(task.total_bytes)}"
                            )
                        else:
                            task.status_text = "Preparing download layers..."
                    elif "verifying" in status_msg.lower():
                        task.status_text = "Verifying downloaded files..."
                        task.percent = max(task.percent, 99.0)
                    elif "writing manifest" in status_msg.lower():
                        task.status_text = "Finishing installation..."
                        task.percent = 99.5
                    elif status_msg == "success":
                        task.status = "completed"
                        task.percent = 100.0
                        if task.total_bytes > 0:
                            task.completed_bytes = task.total_bytes
                        task.status_text = "Download complete!"
                    else:
                        task.status_text = status_msg.capitalize()

                    task.updated_at = time.time()

            # Check exit reason
            if task._cancel_requested.is_set():
                self._cleanup_cancelled_task(task)
                return

            if task._pause_requested.is_set():
                with task._lock:
                    task.status = "paused"
                    task.status_text = (
                        f"Paused at {format_bytes(task.completed_bytes)} / "
                        f"{format_bytes(task.total_bytes)}"
                        if task.total_bytes > 0
                        else "Download paused"
                    )
                    task.updated_at = time.time()
                return

            # If stream ended without explicit "success" or pause/cancel
            with task._lock:
                if task.status not in ("completed", "error", "paused", "cancelled"):
                    if self.is_model_installed(task.model):
                        task.status = "completed"
                        task.percent = 100.0
                        task.completed_bytes = task.total_bytes
                        task.status_text = "Download complete!"
                    else:
                        task.status = "error"
                        task.error = "Connection closed before download finished. Click Resume to continue."
                        task.status_text = task.error
                    task.updated_at = time.time()

        except Exception as e:
            if task._cancel_requested.is_set():
                self._cleanup_cancelled_task(task)
            elif task._pause_requested.is_set():
                with task._lock:
                    task.status = "paused"
                    task.status_text = "Download paused"
                    task.updated_at = time.time()
            else:
                with task._lock:
                    task.status = "error"
                    task.error = str(e)
                    task.status_text = f"Download interrupted: {e}"
                    task.updated_at = time.time()
        finally:
            with task._lock:
                if task._active_response is not None:
                    try:
                        task._active_response.close()
                    except Exception:
                        pass
                    task._active_response = None

    def pause_download(self, model_name: str) -> Dict[str, Any]:
        """Pause an active model download while preserving partial data on disk."""
        with self._downloads_lock:
            task = self._downloads.get(model_name)
            if not task:
                return {"error": f"No active download for {model_name}"}

        task._pause_requested.set()
        with task._lock:
            task.status = "paused"
            task.status_text = (
                f"Paused ({format_bytes(task.completed_bytes)} / {format_bytes(task.total_bytes)})"
                if task.total_bytes > 0
                else "Paused"
            )
            task.updated_at = time.time()
            if task._active_response is not None:
                try:
                    task._active_response.close()
                except Exception:
                    pass
        return task.to_dict()

    def resume_download(self, model_name: str) -> Dict[str, Any]:
        """Resume a paused or interrupted model download."""
        return self.start_download(model_name)

    def cancel_download(self, model_name: str) -> Dict[str, Any]:
        """Stop/cancel a download and clean up partial download blobs from disk."""
        with self._downloads_lock:
            task = self._downloads.get(model_name)
            if not task:
                return {"model": model_name, "status": "cancelled", "status_text": "Cancelled"}

        task._cancel_requested.set()
        task._pause_requested.clear()
        with task._lock:
            if task._active_response is not None:
                try:
                    task._active_response.close()
                except Exception:
                    pass

        # Wait briefly for worker thread to exit or perform cleanup directly
        if task._thread and task._thread.is_alive():
            task._thread.join(timeout=2.0)

        self._cleanup_cancelled_task(task)
        return task.to_dict()

    def _cleanup_cancelled_task(self, task: DownloadTask) -> None:
        """Delete partial blob files and any incomplete manifest for a cancelled download."""
        cleaned_bytes = 0
        blobs_dir = os.path.join(get_ollama_models_dir(), "blobs")
        if os.path.isdir(blobs_dir):
            # Clean up partial files matching digests from this task, or any -partial files
            partial_patterns = [os.path.join(blobs_dir, "*-partial*")]
            for pattern in partial_patterns:
                for filepath in glob.glob(pattern):
                    fname = os.path.basename(filepath)
                    should_remove = False
                    if not task.seen_digests:
                        should_remove = True
                    else:
                        for d in task.seen_digests:
                            if fname.startswith(d):
                                should_remove = True
                                break
                    if should_remove:
                        try:
                            fsize = os.path.getsize(filepath)
                            os.remove(filepath)
                            cleaned_bytes += fsize
                        except Exception:
                            pass

        with task._lock:
            task.cleaned_bytes = max(task.cleaned_bytes, cleaned_bytes)
            task.status = "cancelled"
            cleaned_note = f" (cleaned up {format_bytes(task.cleaned_bytes)} partial data)" if task.cleaned_bytes > 0 else ""
            task.status_text = f"Download cancelled{cleaned_note}"
            task.percent = 0.0
            task.completed_bytes = 0
            task.layers.clear()
            task.updated_at = time.time()

    def get_downloads_status(self) -> Dict[str, Dict[str, Any]]:
        """Return status of all tracked downloads."""
        with self._downloads_lock:
            return {name: task.to_dict() for name, task in self._downloads.items()}

    def get_curated_catalog_with_status(self) -> List[Dict[str, Any]]:
        """Return curated models enriched with installed status and active download state."""
        installed = self.list_installed_models()
        installed_map = {}
        for m in installed:
            installed_map[m["name"].lower()] = m
            installed_map[m["clean_name"].lower()] = m

        downloads = self.get_downloads_status()
        catalog: List[Dict[str, Any]] = []
        for item in CURATED_MODELS:
            model_id = item["id"]
            inst = installed_map.get(model_id.lower())
            dl = downloads.get(model_id)
            entry = dict(item)
            supports_vision = bool(
                entry.get("supports_vision")
                or is_vision_model(model_id, {"family": inst.get("family", "")} if inst else None)
            )
            entry["supports_vision"] = supports_vision
            entry["vision_badge"] = (
                entry.get("vision_badge") or "Supports images and scanned documents"
                if supports_vision
                else None
            )
            entry["installed"] = inst is not None
            entry["installed_size_human"] = inst["size_human"] if inst else None
            entry["installed_size_bytes"] = inst["size_bytes"] if inst else 0
            entry["download"] = dl
            catalog.append(entry)
        return catalog

    def get_fallback_general_chat_model(self, preferred: Optional[str] = None) -> Optional[str]:
        """Return an installed general-purpose chat model (excluding embedding and OCR-only models)."""
        installed = self.list_installed_models()
        candidates = [
            m
            for m in installed
            if not m.get("is_embedding") and not is_ocr_specialized_model(m.get("name", ""))
        ]
        if not candidates:
            return None

        if preferred:
            pref_lower = preferred.strip().lower()
            for m in candidates:
                if pref_lower in (m.get("name", "").lower(), m.get("clean_name", "").lower()):
                    return m["clean_name"]

        preference_order = (
            "llama3.2:3b",
            "qwen2.5:7b",
            "llama3.2:1b",
            "abliterant/qwen3.5-rana:9b-q8_0",
        )
        for target in preference_order:
            for m in candidates:
                if target in (m.get("name", "").lower(), m.get("clean_name", "").lower()):
                    return m["clean_name"]

        return candidates[0].get("clean_name") or candidates[0].get("name")

    # -------------------------------------------------------------------------
    # Embeddings & Streaming Chat
    # -------------------------------------------------------------------------

    def get_embeddings(self, texts: List[str], model: str = EMBEDDING_MODEL) -> List[List[float]]:
        """
        Compute embeddings for a list of strings using Ollama's local embedding model.
        Uses a persistent HTTP session with zero redundant pre-flight checks.
        """
        if not texts:
            return []

        # Fast path: directly call batch /api/embed over persistent session
        try:
            batch_size = 16
            all_vectors: List[List[float]] = []
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                resp = self.session.post(
                    f"{self.base_url}/api/embed",
                    json={"model": model, "input": batch, "keep_alive": "30m"},
                    timeout=120,
                )
                if resp.status_code == 404 and "not found" in resp.text.lower():
                    # Embedding model not pulled yet; pull once automatically
                    self.session.post(
                        f"{self.base_url}/api/pull",
                        json={"name": model, "stream": False},
                        timeout=300,
                    )
                    resp = self.session.post(
                        f"{self.base_url}/api/embed",
                        json={"model": model, "input": batch, "keep_alive": "30m"},
                        timeout=120,
                    )
                if resp.status_code == 200:
                    vectors = resp.json().get("embeddings", [])
                    if len(vectors) == len(batch):
                        all_vectors.extend(vectors)
                        continue
                raise ValueError("Fallback to single embeddings endpoint")
            if len(all_vectors) == len(texts):
                return all_vectors
        except requests.exceptions.ConnectionError:
            self.start_ollama_service()
        except Exception:
            pass

        # Fallback to /api/embeddings one by one
        vectors = []
        for text in texts:
            resp = self.session.post(
                f"{self.base_url}/api/embeddings",
                json={"model": model, "prompt": text, "keep_alive": "30m"},
                timeout=60,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Ollama embedding error ({resp.status_code}): {resp.text}")
            vec = resp.json().get("embedding")
            if not vec:
                raise RuntimeError("Ollama returned an empty embedding vector.")
            vectors.append(vec)
        return vectors

    def stream_chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.4,
        num_ctx: int = 4096,
    ) -> Generator[Dict[str, Any], None, None]:
        """Stream chat tokens from Ollama `/api/chat` with low-latency connection pooling."""
        model_lower = (model or "").lower()

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {
                "temperature": float(temperature),
                "num_ctx": int(num_ctx),
            },
        }

        # Qwen3 models support `think: False` to skip 500-token reasoning latency on chat replies
        if "qwen3" in model_lower:
            payload["think"] = False

        try:
            with self.session.post(
                f"{self.base_url}/api/chat",
                json=payload,
                stream=True,
                timeout=(5, 300),
            ) as resp:
                if resp.status_code != 200:
                    err_text = resp.text
                    try:
                        err_json = json.loads(err_text)
                        err_text = err_json.get("error", err_text)
                    except Exception:
                        pass
                    yield {"type": "error", "error": f"Ollama error: {err_text}"}
                    return

                thinking_tokens: List[str] = []
                content_started = False
                strip_figure_tags = is_ocr_specialized_model(model)
                pending_tag_buf = ""

                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue

                    if "error" in chunk:
                        yield {"type": "error", "error": str(chunk["error"])}
                        return

                    msg = chunk.get("message", {}) or {}
                    think_tok = msg.get("thinking", "")
                    if think_tok and not content_started:
                        thinking_tokens.append(think_tok)
                        yield {"type": "thinking", "count": len(thinking_tokens)}

                    token = msg.get("content", "")
                    if token:
                        if strip_figure_tags:
                            combined = re.sub(r"</?figure>", "", pending_tag_buf + token, flags=re.IGNORECASE)
                            pending_tag_buf = ""
                            lt_idx = combined.rfind("<")
                            if lt_idx != -1:
                                tail = combined[lt_idx:]
                                tail_low = tail.lower()
                                if len(tail) < len("</figure>") and (
                                    "<figure>".startswith(tail_low) or "</figure>".startswith(tail_low)
                                ):
                                    pending_tag_buf = tail
                                    combined = combined[:lt_idx]
                            token = combined
                        if token:
                            content_started = True
                            yield {"type": "token", "content": token}

                    if chunk.get("done"):
                        if strip_figure_tags and pending_tag_buf:
                            rem = re.sub(r"</?figure>", "", pending_tag_buf, flags=re.IGNORECASE)
                            pending_tag_buf = ""
                            if rem:
                                content_started = True
                                yield {"type": "token", "content": rem}

                        # If a reasoning model put its entire output inside `thinking` and left `content` empty,
                        # provide a clean fallback answer from its conclusion
                        if not content_started and thinking_tokens:
                            joined_think = "".join(thinking_tokens).strip()
                            last_sentences = joined_think.split(".")[-2:]
                            fallback = ".".join(s.strip() for s in last_sentences if s.strip())
                            if fallback:
                                yield {"type": "token", "content": fallback}

                        yield {
                            "type": "done",
                            "eval_count": chunk.get("eval_count"),
                            "total_duration": chunk.get("total_duration"),
                        }
                        return
        except requests.exceptions.ConnectionError:
            if self.start_ollama_service():
                yield from self.stream_chat(model, messages, temperature, num_ctx)
                return
            yield {
                "type": "error",
                "error": "Could not connect to Ollama at http://127.0.0.1:11434. Please make sure Ollama is running.",
            }
        except Exception as e:
            yield {"type": "error", "error": str(e)}
