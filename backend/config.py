"""
Configuration, paths, RAM check logic, curated models, and persistent settings for Fellow.
"""
import json
import os
import sys
import threading
from typing import Any, Dict, List, Optional
import psutil

APP_NAME = "Fellow"
APP_VERSION = "1.0.0"
OLLAMA_BASE_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA_BASE_URL.startswith("http"):
    OLLAMA_BASE_URL = f"http://{OLLAMA_BASE_URL}"
# Avoid Windows 2-second IPv6 ::1 fallback penalty when connecting to local Ollama
OLLAMA_BASE_URL = OLLAMA_BASE_URL.replace("://localhost:", "://127.0.0.1:")

EMBEDDING_MODEL = "nomic-embed-text"
RECOMMENDED_OCR_VISION_MODEL = "Maternion/LightOnOCR-3:1b"

CURATED_MODELS: List[Dict[str, Any]] = [
    {
        "id": "llama3.2:1b",
        "title": "Llama 3.2 1B",
        "simple_label": "Fastest (older laptops)",
        "tier_name": "Fastest",
        "description": "Fastest, best for older or low-memory laptops",
        "approx_size": "~1.3 GB",
        "size_mb_est": 1320,
        "ram_needs": "Runs smoothly on 4 GB+ RAM (~2 GB free memory)",
        "min_ram_gb": 3,
        "is_default": False,
        "category": "chat",
        "supports_vision": False,
        "vision_badge": None,
    },
    {
        "id": "llama3.2:3b",
        "title": "Llama 3.2 3B",
        "simple_label": "Fast (default)",
        "tier_name": "Fast",
        "description": "Fast and well-rounded (default)",
        "approx_size": "~2.0 GB",
        "size_mb_est": 2020,
        "ram_needs": "Recommended for 5 to 10 GB RAM (~4 GB free memory)",
        "min_ram_gb": 5,
        "is_default": True,
        "category": "chat",
        "supports_vision": False,
        "vision_badge": None,
    },
    {
        "id": "qwen2.5:7b",
        "title": "Qwen2.5 7B",
        "simple_label": "Smarter (needs more memory)",
        "tier_name": "Smarter",
        "description": "Strong general reasoning, writing, and research help, needs 8+ GB free memory",
        "approx_size": "~4.7 GB",
        "size_mb_est": 4700,
        "ram_needs": "Needs 8+ GB free memory (10+ GB system RAM)",
        "min_ram_gb": 10,
        "is_default": False,
        "category": "chat",
        "supports_vision": False,
        "vision_badge": None,
    },
    {
        "id": "Maternion/LightOnOCR-3:1b",
        "title": "LightOnOCR-3 1B (Scans & Images)",
        "simple_label": "Scans & Images (LightOnOCR-3 1B)",
        "tier_name": "Vision & OCR",
        "description": "Compact 1B model specialized for scanned documents and pictures",
        "approx_size": "~1.0 GB",
        "size_mb_est": 1000,
        "ram_needs": "Runs smoothly on 4 GB+ RAM (supports images & scanned files)",
        "min_ram_gb": 4,
        "is_default": False,
        "category": "chat",
        "supports_vision": True,
        "vision_badge": "Supports images and scanned documents",
    },
    {
        "id": "abliterant/qwen3.5-rana:9b-q8_0",
        "title": "Qwen3.5 RANA 9B (Vision)",
        "simple_label": "Vision (images & scanned docs)",
        "tier_name": "Vision",
        "description": "Supports images and scanned documents — visual reading and strong reasoning",
        "approx_size": "~10.0 GB",
        "size_mb_est": 10000,
        "ram_needs": "Needs 10+ GB free memory (supports images & scanned PDFs)",
        "min_ram_gb": 10,
        "is_default": False,
        "category": "chat",
        "supports_vision": True,
        "vision_badge": "Supports images and scanned documents",
    },
]

DEFAULT_SYSTEM_PROMPT = (
    "You are Fellow, a warm, patient, and encouraging offline study and research companion "
    "for students and professors. You can answer general questions directly from your own knowledge "
    "in well-structured Markdown. If the user has uploaded documents relevant to their question, "
    "use them as additional context, but do not require a file to respond, and never ask the user "
    "to provide or upload a file unless they explicitly refer to a specific document that is not in the available context."
)

DEFAULT_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "study-qa",
        "name": "Study Q&A",
        "description": "Clear, encouraging explanations from general knowledge or course materials",
        "temperature": 0.4,
        "num_ctx": 4096,
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "builtin": True,
    },
    {
        "id": "essay-helper",
        "name": "Essay Helper",
        "description": "Brainstorm outlines, strengthen thesis statements, and polish academic clarity",
        "temperature": 0.6,
        "num_ctx": 4096,
        "system_prompt": (
            "You are Fellow, an academic writing tutor and essay companion. Help the student "
            "brainstorm outlines, sharpen thesis statements, organize arguments, and improve clarity. "
            "Answer directly from your own knowledge, and incorporate any uploaded readings if provided, "
            "without requiring a file to help. Guide constructively rather than writing the entire essay "
            "unless asked for illustrative example paragraphs."
        ),
        "builtin": True,
    },
    {
        "id": "code-explainer",
        "name": "Code Explainer",
        "description": "Step-by-step explanations of code, algorithms, and technical concepts",
        "temperature": 0.2,
        "num_ctx": 4096,
        "system_prompt": (
            "You are Fellow, a friendly computer science teaching assistant. Break down code snippets, "
            "algorithms, and formulas step-by-step in plain, approachable language. Always use fenced "
            "code blocks with the appropriate language tag and clear inline comments. Do not require any "
            "uploaded files to answer programming or conceptual questions."
        ),
        "builtin": True,
    },
    {
        "id": "lecture-prep",
        "name": "Lecture Prep",
        "description": "Create lecture outlines, seminar discussion prompts, and review questions",
        "temperature": 0.5,
        "num_ctx": 8192,
        "system_prompt": (
            "You are Fellow, an academic teaching and lecture preparation assistant for professors, "
            "instructors, and students. Help create structured lecture outlines, key takeaways, "
            "thought-provoking seminar discussion questions, and practice quiz questions from any topic "
            "or from uploaded readings when provided, without requiring a file to respond."
        ),
        "builtin": True,
    },
]


def get_resource_path(relative_path: str) -> str:
    """Resolve static/bundled resource paths in both dev and PyInstaller builds."""
    if hasattr(sys, "_MEIPASS"):
        base_path = getattr(sys, "_MEIPASS")
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def get_data_dir() -> str:
    """Return persistent directory for Fellow's user data (settings, chats, documents, vector store)."""
    custom_dir = os.environ.get("FELLOW_DATA_DIR")
    if custom_dir:
        os.makedirs(custom_dir, exist_ok=True)
        return custom_dir

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        data_dir = os.path.join(local_app_data, APP_NAME)
    elif sys.platform == "darwin":
        data_dir = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
    else:
        data_dir = os.path.expanduser(f"~/.local/share/{APP_NAME.lower()}")

    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def check_system_ram() -> Dict[str, Any]:
    """
    Detect system RAM using psutil and return a model tier recommendation in plain language.
    Tiers:
      - Under 5 GB: recommend Llama 3.2 1B (Fastest)
      - 5 to 10 GB: recommend Llama 3.2 3B (Fast - overall default)
      - 10 GB+: recommend Llama 3.2 3B as default & mention Qwen2.5 7B is available in Browse Models
    """
    vm = psutil.virtual_memory()
    total_gb_exact = round(vm.total / (1024 ** 3), 1)
    available_gb_exact = round(vm.available / (1024 ** 3), 1)
    # Friendly rounded display value (e.g. 7.9 GB -> 8 GB, 5.9 GB -> 6 GB)
    display_total_gb = int(round(vm.total / (1024 ** 3)))
    if display_total_gb < 1:
        display_total_gb = max(1, int(total_gb_exact))

    # Determine tier based on detected RAM capacity (using display_total_gb so an 8GB laptop
    # reporting 7.85GB usable sits squarely in the 5-to-10 GB tier)
    if display_total_gb < 5:
        tier_key = "under_5gb"
        recommended_model = "llama3.2:1b"
        recommended_title = "Llama 3.2 1B"
        recommended_tier_label = "Fastest"
        stronger_option = None
        plain_message = (
            f"Your computer has {display_total_gb} GB of memory ({available_gb_exact} GB currently free). "
            f"We recommend the Fastest assistant (Llama 3.2 1B), which will run smoothly on your laptop."
        )
    elif display_total_gb < 10:
        tier_key = "5_to_10gb"
        recommended_model = "llama3.2:3b"
        recommended_title = "Llama 3.2 3B"
        recommended_tier_label = "Fast"
        stronger_option = None
        plain_message = (
            f"Your computer has {display_total_gb} GB of memory ({available_gb_exact} GB currently free). "
            f"We recommend the Fast assistant (Llama 3.2 3B), which will run smoothly."
        )
    else:
        tier_key = "10gb_plus"
        recommended_model = "llama3.2:3b"
        recommended_title = "Llama 3.2 3B"
        recommended_tier_label = "Fast"
        stronger_option = "qwen2.5:7b"
        plain_message = (
            f"Your computer has {display_total_gb} GB of memory ({available_gb_exact} GB currently free). "
            f"We recommend the Fast assistant (Llama 3.2 3B) for quick setup, and Qwen2.5 7B "
            f"(the Smarter assistant) is also available as a stronger option in Browse Models!"
        )

    return {
        "total_bytes": vm.total,
        "available_bytes": vm.available,
        "total_gb": total_gb_exact,
        "display_total_gb": display_total_gb,
        "available_gb": available_gb_exact,
        "tier_key": tier_key,
        "recommended_model": recommended_model,
        "recommended_title": recommended_title,
        "recommended_tier_label": recommended_tier_label,
        "stronger_option": stronger_option,
        "plain_message": plain_message,
        "tiers": [
            {
                "model": "llama3.2:1b",
                "tier_label": "Fastest assistant",
                "simple_label": "Fastest (older laptops)",
                "title": "Llama 3.2 1B",
                "subtitle": "Under 5 GB memory • ~1.3 GB download",
                "recommended": recommended_model == "llama3.2:1b",
            },
            {
                "model": "llama3.2:3b",
                "tier_label": "Fast assistant (Default)",
                "simple_label": "Fast (default)",
                "title": "Llama 3.2 3B",
                "subtitle": "5 to 10 GB memory • ~2.0 GB download",
                "recommended": recommended_model == "llama3.2:3b",
            },
            {
                "model": "qwen2.5:7b",
                "tier_label": "Smarter assistant",
                "simple_label": "Smarter (needs more memory)",
                "title": "Qwen2.5 7B",
                "subtitle": "10+ GB memory • ~4.7 GB download",
                "recommended": False,
                "highlight_stronger": stronger_option == "qwen2.5:7b",
            },
        ],
    }


def get_friendly_model_info(model_name: str) -> Dict[str, str]:
    """Return friendly Simple-mode label and description for any model tag."""
    normalized = (model_name or "").strip().lower()
    for item in CURATED_MODELS:
        if item["id"].lower() == normalized or normalized.startswith(item["id"].lower()):
            return {
                "simple_label": item["simple_label"],
                "title": item["title"],
                "tier_name": item["tier_name"],
                "description": item["description"],
            }
    if "1b" in normalized:
        return {
            "simple_label": f"Fastest ({model_name})",
            "title": model_name,
            "tier_name": "Fastest",
            "description": "Compact model for low-memory laptops",
        }
    if "3b" in normalized or "4b" in normalized:
        return {
            "simple_label": f"Balanced ({model_name})",
            "title": model_name,
            "tier_name": "Fast",
            "description": "Balanced speed and capability",
        }
    if "7b" in normalized or "8b" in normalized:
        return {
            "simple_label": f"Smarter ({model_name})",
            "title": model_name,
            "tier_name": "Smarter",
            "description": "Larger model requiring 8+ GB free memory",
        }
    short_name = model_name.split("/")[-1] if "/" in (model_name or "") else model_name
    if len(short_name) > 22:
        short_name = short_name[:20] + "…"
    return {
        "simple_label": f"Custom ({short_name})",
        "title": model_name,
        "tier_name": "Custom",
        "description": "Custom user-added model",
    }


class SettingsManager:
    """Thread-safe persistent JSON settings store for Fellow."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or get_data_dir()
        self.settings_path = os.path.join(self.data_dir, "settings.json")
        self._lock = threading.Lock()
        self._settings = self._load_or_init()

    def _default_settings(self) -> Dict[str, Any]:
        ram_info = check_system_ram()
        return {
            "first_run_completed": False,
            "ram_check_completed": False,
            "ram_info": ram_info,
            "selected_model": ram_info["recommended_model"],
            "recommended_model": ram_info["recommended_model"],
            "embedding_model": EMBEDDING_MODEL,
            "mode": "simple",          # "simple" | "advanced"
            "theme": "light",          # "light" | "dark"
            "temperature": 0.4,
            "num_ctx": 4096,
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "active_preset": "study-qa",
            "presets": [dict(p) for p in DEFAULT_PRESETS],
            "use_documents": True,
            "top_k_chunks": 4,
        }

    def _load_or_init(self) -> Dict[str, Any]:
        defaults = self._default_settings()
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    defaults.update(loaded)
                    migrated = False

                    # Auto-migrate legacy system prompts that required/assumed documents
                    loaded_sys_prompt = defaults.get("system_prompt", "")
                    if (
                        "When document excerpts are provided in the context" in loaded_sys_prompt
                        or "cannot be answered from the uploaded documents" in loaded_sys_prompt
                    ):
                        defaults["system_prompt"] = DEFAULT_SYSTEM_PROMPT
                        migrated = True

                    # Refresh built-in presets while preserving user-created custom presets
                    builtin_by_id = {bp["id"]: dict(bp) for bp in DEFAULT_PRESETS}
                    refreshed_presets: List[Dict[str, Any]] = []
                    seen_ids = set()
                    for p in defaults.get("presets", []):
                        if not isinstance(p, dict):
                            continue
                        pid = p.get("id")
                        if pid in builtin_by_id and p.get("builtin", True):
                            refreshed_presets.append(builtin_by_id[pid])
                            seen_ids.add(pid)
                            if p.get("system_prompt") != builtin_by_id[pid]["system_prompt"]:
                                migrated = True
                        else:
                            refreshed_presets.append(p)
                            if pid:
                                seen_ids.add(pid)
                    for bp in DEFAULT_PRESETS:
                        if bp["id"] not in seen_ids:
                            refreshed_presets.append(dict(bp))
                            migrated = True

                    defaults["presets"] = refreshed_presets
                    if migrated:
                        self._write(defaults)
                    return defaults
            except Exception:
                pass
        self._write(defaults)
        return defaults

    def _write(self, data: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        tmp_path = self.settings_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.settings_path)

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def update(self, partial: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for k, v in partial.items():
                self._settings[k] = v
            self._write(self._settings)
            return dict(self._settings)

    def rerun_ram_check(self) -> Dict[str, Any]:
        ram_info = check_system_ram()
        with self._lock:
            self._settings["ram_info"] = ram_info
            self._settings["recommended_model"] = ram_info["recommended_model"]
            self._write(self._settings)
        return ram_info
