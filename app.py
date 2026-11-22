"""
Fellow — Offline Study & Research Desktop Application
Main entry point: Flask backend server + PyWebView desktop window.
"""
import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, Response, jsonify, request, send_from_directory

from backend.chat_store import ChatStore
from backend.config import (
    APP_NAME,
    APP_VERSION,
    CURATED_MODELS,
    DEFAULT_SYSTEM_PROMPT,
    EMBEDDING_MODEL,
    RECOMMENDED_OCR_VISION_MODEL,
    SettingsManager,
    check_system_ram,
    get_data_dir,
    get_resource_path,
)
from backend.document_store import (
    IMAGE_EXTENSIONS,
    DocumentStore,
    EmptyDocumentError,
    ScannedDocumentError,
)
from backend.ollama_manager import OllamaManager, is_ocr_specialized_model, is_vision_model


ACTIVE_WEBVIEW_WINDOW = None


def save_file_with_dialog_or_downloads(source_bytes: bytes, default_filename: str) -> Dict[str, Any]:
    """
    Save a generated or exported file natively:
      - In PyWebView desktop window mode: open a native Save As dialog defaulting to ~/Downloads
      - In browser / headless fallback mode: write directly into ~/Downloads (auto-numbering collisions)
    """
    home_dir = os.path.expanduser("~")
    downloads_dir = os.path.join(home_dir, "Downloads")
    if not os.path.isdir(downloads_dir):
        try:
            os.makedirs(downloads_dir, exist_ok=True)
        except Exception:
            downloads_dir = home_dir

    safe_name = os.path.basename(default_filename).strip() or "Fellow_Export"

    if ACTIVE_WEBVIEW_WINDOW is not None:
        try:
            import webview
            dialog_type = (
                webview.FileDialog.SAVE
                if hasattr(webview, "FileDialog")
                else getattr(webview, "SAVE_DIALOG", 30)
            )
            res = ACTIVE_WEBVIEW_WINDOW.create_file_dialog(
                dialog_type,
                directory=downloads_dir,
                save_filename=safe_name,
            )
            if not res:
                return {"cancelled": True}
            target_path = res[0] if isinstance(res, (list, tuple)) else str(res)
            target_path = target_path.strip()
            if not target_path:
                return {"cancelled": True}
            os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
            with open(target_path, "wb") as f:
                f.write(source_bytes)
            return {
                "success": True,
                "saved_path": target_path,
                "filename": os.path.basename(target_path),
            }
        except Exception:
            # Fall through to direct ~/Downloads write if dialog throws
            pass

    stem, ext = os.path.splitext(safe_name)
    target_path = os.path.join(downloads_dir, safe_name)
    counter = 1
    while os.path.exists(target_path):
        target_path = os.path.join(downloads_dir, f"{stem} ({counter}){ext}")
        counter += 1

    with open(target_path, "wb") as f:
        f.write(source_bytes)

    return {
        "success": True,
        "saved_path": target_path,
        "filename": os.path.basename(target_path),
        "browser_fallback": True,
    }

STATIC_DIR = get_resource_path("static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024  # 128 MB upload limit

settings_manager = SettingsManager()
ollama_manager = OllamaManager()
document_store = DocumentStore(ollama=ollama_manager)
chat_store = ChatStore()


# -----------------------------------------------------------------------------
# Frontend Static Routes
# -----------------------------------------------------------------------------

@app.route("/")
def serve_index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/assets/<path:filename>")
def serve_assets(filename: str):
    assets_dir = get_resource_path("assets")
    return send_from_directory(assets_dir, filename)


# -----------------------------------------------------------------------------
# System, First-Run, RAM Check & Settings Routes
# -----------------------------------------------------------------------------

@app.route("/api/bootstrap", methods=["GET"])
def api_bootstrap():
    """Return initial state for Fellow on launch."""
    current_settings = settings_manager.get_all()
    if not current_settings.get("ram_info"):
        ram_info = settings_manager.rerun_ram_check()
        current_settings = settings_manager.get_all()
    else:
        ram_info = current_settings["ram_info"]

    ollama_status = ollama_manager.check_status(auto_start=True)
    curated = ollama_manager.get_curated_catalog_with_status()
    downloads = ollama_manager.get_downloads_status()
    docs = document_store.list_documents()
    chats = chat_store.list_chats()

    # If selected model is not among installed chat models, but another chat model is installed,
    # keep user's selection for first-run pull or fall back to an available chat model
    installed_chat_models = [
        m["clean_name"] for m in ollama_status["installed_models"] if not m["is_embedding"]
    ]
    selected = current_settings.get("selected_model") or ram_info["recommended_model"]
    if (
        current_settings.get("first_run_completed")
        and installed_chat_models
        and selected not in installed_chat_models
        and f"{selected}:latest" not in [m["name"] for m in ollama_status["installed_models"]]
    ):
        selected = installed_chat_models[0]
        current_settings = settings_manager.update({"selected_model": selected})

    # Pre-warm the selected model in the background so the first chat reply is fast
    if ollama_status.get("running") and selected:
        ollama_manager.warmup_model(selected)

    return jsonify({
        "app_name": APP_NAME,
        "version": APP_VERSION,
        "data_dir": get_data_dir(),
        "settings": current_settings,
        "ram_info": ram_info,
        "ollama": ollama_status,
        "curated_models": curated,
        "downloads": downloads,
        "documents": docs,
        "chats": chats,
    })


@app.route("/api/ram-check", methods=["POST"])
def api_ram_check():
    """Run or re-run the system RAM detection and model tier recommendation."""
    ram_info = settings_manager.rerun_ram_check()
    return jsonify({
        "ram_info": ram_info,
        "settings": settings_manager.get_all(),
    })


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(settings_manager.get_all())
    payload = request.get_json(silent=True) or {}
    updated = settings_manager.update(payload)
    if "selected_model" in payload and payload["selected_model"]:
        ollama_manager.warmup_model(str(payload["selected_model"]))
    return jsonify(updated)


@app.route("/api/ollama/status", methods=["GET", "POST"])
def api_ollama_status():
    auto_start = True
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        auto_start = bool(body.get("auto_start", True))
    status = ollama_manager.check_status(auto_start=auto_start)
    return jsonify(status)


@app.route("/api/open-url", methods=["POST"])
def api_open_url():
    """Open an external https:// link in the user's default system browser."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not (url.startswith("https://") or url.startswith("http://")):
        return jsonify({"error": "Only valid http/https URLs are permitted"}), 400
    try:
        webbrowser.open(url, new=2)
        return jsonify({"success": True, "url": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------------------------------
# Model Lineup, Download Control (Pause / Resume / Cancel) & Deletion
# -----------------------------------------------------------------------------

@app.route("/api/models", methods=["GET"])
def api_models():
    installed = ollama_manager.list_installed_models()
    curated = ollama_manager.get_curated_catalog_with_status()
    downloads = ollama_manager.get_downloads_status()
    current_settings = settings_manager.get_all()
    return jsonify({
        "installed_models": installed,
        "curated_models": curated,
        "downloads": downloads,
        "selected_model": current_settings.get("selected_model"),
        "embedding_model": EMBEDDING_MODEL,
    })


@app.route("/api/models/pull", methods=["POST"])
def api_models_pull():
    data = request.get_json(silent=True) or {}
    model_name = (data.get("model") or "").strip()
    if not model_name:
        return jsonify({"error": "Please enter a model name to download."}), 400
    task = ollama_manager.start_download(model_name)
    return jsonify(task)


@app.route("/api/models/pause", methods=["POST"])
def api_models_pause():
    data = request.get_json(silent=True) or {}
    model_name = (data.get("model") or "").strip()
    if not model_name:
        return jsonify({"error": "Model name is required"}), 400
    task = ollama_manager.pause_download(model_name)
    return jsonify(task)


@app.route("/api/models/resume", methods=["POST"])
def api_models_resume():
    data = request.get_json(silent=True) or {}
    model_name = (data.get("model") or "").strip()
    if not model_name:
        return jsonify({"error": "Model name is required"}), 400
    task = ollama_manager.resume_download(model_name)
    return jsonify(task)


@app.route("/api/models/cancel", methods=["POST"])
def api_models_cancel():
    data = request.get_json(silent=True) or {}
    model_name = (data.get("model") or "").strip()
    if not model_name:
        return jsonify({"error": "Model name is required"}), 400
    task = ollama_manager.cancel_download(model_name)
    return jsonify(task)


@app.route("/api/models/downloads", methods=["GET"])
def api_models_downloads():
    return jsonify({
        "downloads": ollama_manager.get_downloads_status(),
    })


@app.route("/api/models/delete", methods=["POST"])
def api_models_delete():
    data = request.get_json(silent=True) or {}
    model_name = (data.get("model") or "").strip()
    if not model_name:
        return jsonify({"error": "Model name is required"}), 400
    result = ollama_manager.delete_model(model_name)
    if not result.get("success"):
        return jsonify(result), 400

    # If the deleted model was the active model, switch to another installed chat model if available
    installed = [m for m in ollama_manager.list_installed_models() if not m["is_embedding"]]
    current = settings_manager.get_all().get("selected_model", "")
    if current.lower() == model_name.lower() and installed:
        settings_manager.update({"selected_model": installed[0]["clean_name"]})

    result["installed_models"] = ollama_manager.list_installed_models()
    result["curated_models"] = ollama_manager.get_curated_catalog_with_status()
    return jsonify(result)


# -----------------------------------------------------------------------------
# Named Presets (Advanced Mode)
# -----------------------------------------------------------------------------

@app.route("/api/presets/save", methods=["POST"])
def api_presets_save():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Preset name is required."}), 400

    preset_id = (data.get("id") or "").strip() or f"custom-{uuid.uuid4().hex[:8]}"
    temperature = float(data.get("temperature", 0.4))
    num_ctx = int(data.get("num_ctx", 4096))
    system_prompt = (data.get("system_prompt") or "").strip()
    description = (data.get("description") or "Custom study preset").strip()

    current = settings_manager.get_all()
    presets: List[Dict[str, Any]] = list(current.get("presets", []))

    updated_existing = False
    for p in presets:
        if p.get("id") == preset_id or p.get("name", "").lower() == name.lower():
            p["name"] = name
            p["description"] = description
            p["temperature"] = temperature
            p["num_ctx"] = num_ctx
            p["system_prompt"] = system_prompt
            preset_id = p["id"]
            updated_existing = True
            break

    if not updated_existing:
        presets.append({
            "id": preset_id,
            "name": name,
            "description": description,
            "temperature": temperature,
            "num_ctx": num_ctx,
            "system_prompt": system_prompt,
            "builtin": False,
        })

    new_settings = settings_manager.update({
        "presets": presets,
        "active_preset": preset_id,
        "temperature": temperature,
        "num_ctx": num_ctx,
        "system_prompt": system_prompt,
    })
    return jsonify(new_settings)


@app.route("/api/presets/delete", methods=["POST"])
def api_presets_delete():
    data = request.get_json(silent=True) or {}
    preset_id = (data.get("id") or "").strip()
    current = settings_manager.get_all()
    presets = [p for p in current.get("presets", []) if not (p.get("id") == preset_id and not p.get("builtin"))]
    active = current.get("active_preset")
    if active == preset_id:
        active = "study-qa"
    new_settings = settings_manager.update({
        "presets": presets,
        "active_preset": active,
    })
    return jsonify(new_settings)


# -----------------------------------------------------------------------------
# Document Upload, Scanned PDF Detection, and File Manager
# -----------------------------------------------------------------------------

@app.route("/api/documents", methods=["GET"])
def api_list_documents():
    return jsonify({"documents": document_store.list_documents()})


@app.route("/api/documents/upload", methods=["POST"])
def api_upload_document():
    if "file" not in request.files and "files" not in request.files:
        return jsonify({"error": "No file selected for upload."}), 400

    replace_doc_id = request.form.get("replace_doc_id") or None
    req_scope = (request.form.get("scope") or "").strip().lower()
    req_chat_id = (request.form.get("chat_id") or "").strip() or None
    processing_mode = (request.form.get("processing_mode") or "auto").strip().lower()

    created_chat = None
    if req_scope == "chat" and not req_chat_id:
        created_chat = chat_store.create_chat(title="New Study Session")
        req_chat_id = created_chat["id"]

    effective_scope = req_scope if req_scope in ("global", "chat") else None

    files = request.files.getlist("files") or [request.files["file"]]

    uploaded_records = []
    pending_scanned = []
    errors = []
    has_image_or_scan = False

    for f in files:
        if not f or not f.filename:
            continue
        filename = f.filename
        ext = os.path.splitext(filename)[1].lower()
        if ext in IMAGE_EXTENSIONS:
            has_image_or_scan = True
        file_bytes = f.read()
        try:
            record = document_store.add_document(
                filename=filename,
                file_bytes=file_bytes,
                replace_doc_id=replace_doc_id if len(files) == 1 else None,
                scope=effective_scope,
                chat_id=req_chat_id,
                processing_mode=processing_mode,
            )
            if record.get("read_mode") in ("ocr", "vision"):
                has_image_or_scan = True
            uploaded_records.append(record)
        except ScannedDocumentError as e:
            has_image_or_scan = True
            if processing_mode not in ("ocr", "vision"):
                staged = document_store.stage_scanned_pdf(
                    filename=filename,
                    file_bytes=file_bytes,
                    scope=effective_scope or "global",
                    chat_id=req_chat_id,
                    replace_doc_id=replace_doc_id if len(files) == 1 else None,
                )
                pending_scanned.append(staged)
            else:
                errors.append({
                    "filename": filename,
                    "error_type": "scanned_pdf",
                    "message": str(e),
                })
        except EmptyDocumentError as e:
            errors.append({
                "filename": filename,
                "error_type": "empty_document",
                "message": str(e),
            })
        except Exception as e:
            errors.append({
                "filename": filename,
                "error_type": "processing_error",
                "message": str(e),
            })

    status_code = 200 if (uploaded_records or pending_scanned) else 422
    response_payload: Dict[str, Any] = {
        "uploaded": uploaded_records,
        "pending_scanned": pending_scanned,
        "errors": errors,
        "documents": document_store.list_documents(),
    }
    if has_image_or_scan:
        active_model = settings_manager.get_all().get("selected_model", "")
        is_installed = ollama_manager.is_model_installed(RECOMMENDED_OCR_VISION_MODEL)
        is_active = active_model.lower() == RECOMMENDED_OCR_VISION_MODEL.lower()
        response_payload["ocr_model_recommendation"] = {
            "model": RECOMMENDED_OCR_VISION_MODEL,
            "installed": is_installed,
            "active": is_active,
            "message": (
                f"You uploaded a scanned file or picture. We recommend downloading "
                f"{RECOMMENDED_OCR_VISION_MODEL}, which is specialized for reading scanned "
                f"documents and images. Would you like to download it now and switch to it "
                f"automatically once done?"
            ),
        }
    if created_chat:
        response_payload["created_chat"] = created_chat
        response_payload["chats"] = chat_store.list_chats()
    return jsonify(response_payload), status_code


@app.route("/api/documents/process-scanned", methods=["POST"])
def api_process_scanned_document():
    """Process a staged scanned PDF using either OCR or Vision mode."""
    data = request.get_json(silent=True) or {}
    staging_id = (data.get("staging_id") or "").strip()
    mode = (data.get("mode") or "ocr").strip().lower()
    if not staging_id:
        return jsonify({"error": "staging_id is required."}), 400
    if mode not in ("ocr", "vision"):
        return jsonify({"error": "mode must be 'ocr' or 'vision'."}), 400

    try:
        record = document_store.process_staged_scanned_pdf(staging_id=staging_id, mode=mode)
        return jsonify({
            "uploaded": [record],
            "documents": document_store.list_documents(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 422


@app.route("/api/documents/cancel-scanned", methods=["POST"])
def api_cancel_scanned_document():
    data = request.get_json(silent=True) or {}
    staging_id = (data.get("staging_id") or "").strip()
    if staging_id:
        document_store.cancel_staged_scanned_pdf(staging_id)
    return jsonify({"success": True})


@app.route("/api/documents/<doc_id>/scope", methods=["PUT"])
def api_update_document_scope(doc_id: str):
    """Switch a document's scope between 'global' (Available in all chats) and 'chat' (Only in this chat)."""
    data = request.get_json(silent=True) or {}
    scope = (data.get("scope") or "global").strip().lower()
    chat_id = (data.get("chat_id") or "").strip() or None

    created_chat = None
    if scope == "chat" and not chat_id:
        created_chat = chat_store.create_chat(title="New Study Session")
        chat_id = created_chat["id"]

    updated = document_store.update_document_scope(doc_id=doc_id, scope=scope, chat_id=chat_id)
    if not updated:
        return jsonify({"error": "Document not found."}), 404

    payload: Dict[str, Any] = {
        "success": True,
        "document": updated,
        "documents": document_store.list_documents(),
    }
    if created_chat:
        payload["created_chat"] = created_chat
        payload["chats"] = chat_store.list_chats()
    return jsonify(payload)


@app.route("/api/documents/<doc_id>", methods=["DELETE"])
def api_delete_document(doc_id: str):
    removed = document_store.remove_document(doc_id)
    if not removed:
        return jsonify({"error": "Document not found."}), 404
    return jsonify({
        "success": True,
        "documents": document_store.list_documents(),
    })


# -----------------------------------------------------------------------------
# Chats: CRUD, Export to Markdown, and Streaming RAG Generation
# -----------------------------------------------------------------------------

@app.route("/api/chats", methods=["GET", "POST"])
def api_chats():
    if request.method == "GET":
        return jsonify({"chats": chat_store.list_chats()})
    data = request.get_json(silent=True) or {}
    title = data.get("title") or "New Study Session"
    chat = chat_store.create_chat(title=title)
    return jsonify({"chat": chat, "chats": chat_store.list_chats()})


@app.route("/api/chats/<chat_id>", methods=["GET", "PUT", "DELETE"])
def api_chat_detail(chat_id: str):
    if request.method == "GET":
        chat = chat_store.get_chat(chat_id)
        if not chat:
            return jsonify({"error": "Chat not found"}), 404
        return jsonify({"chat": chat})

    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        new_title = (data.get("title") or "").strip()
        updated = chat_store.rename_chat(chat_id, new_title)
        if not updated:
            return jsonify({"error": "Could not rename chat"}), 400
        return jsonify({"chat": updated, "chats": chat_store.list_chats()})

    if request.method == "DELETE":
        deleted = chat_store.delete_chat(chat_id)
        if not deleted:
            return jsonify({"error": "Chat not found"}), 404
        return jsonify({"success": True, "chats": chat_store.list_chats()})


@app.route("/api/chats/<chat_id>/export", methods=["GET"])
def api_export_chat(chat_id: str):
    exported = chat_store.export_markdown(chat_id)
    if not exported:
        return jsonify({"error": "Chat not found"}), 404
    filename, md_content = exported
    return Response(
        md_content,
        mimetype="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.route("/api/chats/<chat_id>/save-markdown", methods=["POST"])
def api_save_chat_markdown(chat_id: str):
    """Save exported chat Markdown natively via PyWebView Save dialog or ~/Downloads."""
    exported = chat_store.export_markdown(chat_id)
    if not exported:
        return jsonify({"error": "Chat not found"}), 404
    filename, md_content = exported
    result = save_file_with_dialog_or_downloads(md_content.encode("utf-8"), filename)
    return jsonify(result)


def _sanitize_chat_history(raw_messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], bool]:
    """
    Sanitize prior conversation turns before sending to Ollama:
    - Strip glitched tool_call blocks and Vision Tip warning turns so switching models
      mid-chat does not poison the newly selected model with an earlier model's refusal
    - Collapse any dangling/consecutive user messages so roles alternate cleanly
    - Track whether an earlier vision assistant message already described attached visuals
    """
    has_prior_vision_analysis = False
    clean_history: List[Dict[str, str]] = []
    poison_markers = (
        "SystemMetadata",
        "```tool_call",
        "> **Vision Tip:**",
        "does not support image inputs",
        "couldn't finish responding",
    )

    for pm in raw_messages:
        role = pm.get("role", "user")
        content = (pm.get("content") or "").strip()
        if not content:
            continue

        if role == "assistant":
            if any(marker in content for marker in poison_markers):
                if clean_history and clean_history[-1]["role"] == "user":
                    clean_history.pop()
                continue
            if is_vision_model(pm.get("model") or "") or "### Figure" in content:
                has_prior_vision_analysis = True
            clean_history.append({"role": "assistant", "content": content})
        elif role == "user":
            if clean_history and clean_history[-1]["role"] == "user":
                clean_history[-1] = {"role": "user", "content": content}
            else:
                clean_history.append({"role": "user", "content": content})

    if clean_history and clean_history[-1]["role"] == "user":
        clean_history.pop()

    return clean_history, has_prior_vision_analysis


@app.route("/api/chats/<chat_id>/stream", methods=["POST"])
def api_stream_chat(chat_id: str):
    """
    Retrieve relevant document context (when applicable), support Vision & OCR documents,
    and stream Fellow's response via SSE.
    """
    chat = chat_store.get_chat(chat_id)
    if not chat:
        chat = chat_store.create_chat()
        chat_id = chat["id"]

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Message cannot be empty."}), 400

    edit_message_id = (data.get("edit_message_id") or "").strip() or None
    edit_message_index = data.get("edit_message_index")
    if isinstance(edit_message_index, int) or edit_message_id:
        truncated = chat_store.truncate_from_message(
            chat_id=chat_id,
            message_id=edit_message_id,
            message_index=edit_message_index if isinstance(edit_message_index, int) else None,
        )
        if truncated is not None:
            chat = truncated
    else:
        # Remove any trailing unanswered user turn left behind by a prior failed/cancelled stream
        while chat.get("messages") and chat["messages"][-1].get("role") == "user":
            last_idx = len(chat["messages"]) - 1
            truncated = chat_store.truncate_from_message(
                chat_id=chat_id,
                message_id=chat["messages"][last_idx].get("id"),
                message_index=last_idx,
            )
            if truncated is None:
                break
            chat = truncated

    current_settings = settings_manager.get_all()
    model = (data.get("model") or current_settings.get("selected_model") or "llama3.2:3b").strip()
    temperature = float(data.get("temperature", current_settings.get("temperature", 0.4)))
    num_ctx = int(data.get("num_ctx", current_settings.get("num_ctx", 4096)))
    system_prompt = (data.get("system_prompt") or current_settings.get("system_prompt") or "").strip()
    use_documents = bool(data.get("use_documents", current_settings.get("use_documents", True)))
    top_k = int(current_settings.get("top_k_chunks", 4))

    # Persist user message immediately (preserving explicit message_id so errors can roll it back cleanly)
    added_user_msg_id = edit_message_id or str(uuid.uuid4())
    updated_chat = chat_store.add_message(
        chat_id,
        role="user",
        content=user_message,
        message_id=added_user_msg_id,
    )
    chat_title = updated_chat["title"] if updated_chat else chat["title"]

    raw_prior_messages = (updated_chat or chat).get("messages", [])[:-1][-12:]
    clean_history, has_prior_vision_analysis = _sanitize_chat_history(raw_prior_messages)

    # Skip RAG embedding lookup on simple greetings or model-identity questions for instant response
    normalized_q = user_message.lower().strip(" ?!.,")
    is_trivial_conversational = normalized_q in {
        "hi", "hello", "hey", "yo", "hola", "good morning", "good afternoon", "good evening",
        "thanks", "thank you", "who are you", "what model are you", "what ai are you",
        "which model are you", "are you there", "test",
    }

    applicable_docs = document_store.get_applicable_documents(chat_id) if use_documents else []
    retrieved_sources: List[Dict[str, Any]] = []
    vision_docs_to_use: List[Dict[str, Any]] = []
    vision_warning_note: Optional[str] = None

    if use_documents and len(applicable_docs) > 0:
        # 1. Text & OCR document retrieval (only returns plausibly relevant chunks)
        if not is_trivial_conversational:
            try:
                retrieved_sources = document_store.search(
                    user_message,
                    top_k=top_k,
                    current_chat_id=chat_id,
                )
            except Exception:
                retrieved_sources = []

        # 2. Vision document check (images & vision-mode scanned PDFs)
        all_vision_docs = document_store.get_vision_documents_for_chat(chat_id)
        if all_vision_docs:
            if is_vision_model(model):
                if not is_trivial_conversational or is_ocr_specialized_model(model):
                    vision_docs_to_use = all_vision_docs
            elif (
                not is_trivial_conversational
                and not has_prior_vision_analysis
                and document_store._query_references_documents(user_message, all_vision_docs)
            ):
                vision_warning_note = (
                    "Note: You have visual image/scanned documents attached in this chat, "
                    f"but your active model (`{model}`) does not support image inputs. "
                    f"Switch to a Vision-capable model (such as **{RECOMMENDED_OCR_VISION_MODEL}**) "
                    "in the top dropdown to read images directly, or re-process scanned PDFs using **OCR**."
                )

    has_doc_context = bool(retrieved_sources or vision_docs_to_use)

    # Construct final user message (with optional RAG excerpts and Vision page images)
    prompt_body = user_message

    collected_images_b64: List[str] = []
    vision_sources_meta: List[Dict[str, Any]] = []
    if vision_docs_to_use:
        for vdoc in vision_docs_to_use:
            for page_idx, b64_img in enumerate(vdoc.get("images_b64", []), start=1):
                if len(collected_images_b64) >= 4:
                    break
                collected_images_b64.append(b64_img)
                vision_sources_meta.append({
                    "chunk_id": f"vision-{vdoc['id']}-{page_idx}",
                    "doc_id": vdoc["id"],
                    "filename": vdoc["filename"],
                    "section": f"Page {page_idx} (Vision)",
                    "text": f"Visual image page {page_idx} sent directly to {model}.",
                    "read_mode": "vision",
                })

    if retrieved_sources:
        context_blocks = []
        for i, src in enumerate(retrieved_sources, start=1):
            context_blocks.append(
                f"[Excerpt {i} — Document: {src['filename']} | {src['section']}]\n{src['text']}"
            )
        joined_context = "\n\n---\n\n".join(context_blocks)
        final_user_content = (
            "Below are relevant excerpts from the user's uploaded study materials:\n\n"
            f"{joined_context}\n\n"
            "---\n"
            f"User Question: {prompt_body}\n\n"
            "Use the excerpts above when relevant and mention which document/section supports your points."
        )
    elif vision_docs_to_use:
        v_names = ", ".join(d["filename"] for d in vision_docs_to_use)
        final_user_content = (
            f"[Attached Visual Materials: {v_names}]\n"
            f"User Question: {prompt_body}\n\n"
            "Examine the attached image(s) carefully if relevant to the question; otherwise answer directly from your own knowledge."
        )
    else:
        final_user_content = prompt_body

    effective_stream_model = model
    ocr_text_only_notice: Optional[str] = None
    is_ocr_vision_turn = False

    if is_ocr_specialized_model(model):
        if collected_images_b64 and not is_trivial_conversational:
            # Dedicated single-turn OCR/vision reader requires a single user turn with no system prompt/history
            is_ocr_vision_turn = True
            single_line_prompt = (
                re.sub(r"\s+", " ", prompt_body).strip()
                or "Describe and transcribe this image in detail."
            )
            ollama_messages = [
                {
                    "role": "user",
                    "content": single_line_prompt,
                    "images": collected_images_b64,
                }
            ]
        else:
            fallback_chat_model = ollama_manager.get_fallback_general_chat_model(
                current_settings.get("recommended_model")
            )
            if fallback_chat_model:
                effective_stream_model = fallback_chat_model
                if is_trivial_conversational or not is_vision_model(effective_stream_model):
                    vision_docs_to_use = []
                    vision_sources_meta = []
                    collected_images_b64 = []
                    has_doc_context = bool(retrieved_sources)
                    if not retrieved_sources:
                        final_user_content = prompt_body
            else:
                ocr_text_only_notice = (
                    f"**`{model}`** is specialized for reading uploaded images and scanned documents. "
                    "Please upload an image or scanned PDF for it to read, or download a general study assistant "
                    "(such as **Llama 3.2 3B**) from **Browse Models** for text-only questions."
                )

    if not is_ocr_vision_turn:
        if not has_doc_context:
            if not system_prompt or system_prompt == DEFAULT_SYSTEM_PROMPT:
                base_sys = (
                    "You are Fellow, a warm, patient, and encouraging offline study and research companion "
                    "for students and professors. Answer the user's question directly and helpfully from "
                    "your own knowledge in well-structured Markdown."
                )
            else:
                base_sys = (
                    f"{system_prompt}\n\nAnswer directly from your own knowledge without requiring any file."
                )
            runtime_guardrail = (
                f"\n\n[Runtime Context: You are Fellow, running locally via Ollama using the model '{effective_stream_model}'. "
                f"You do NOT have external tools, functions, or SystemMetadata plugins enabled. "
                f"Never output tool_call blocks or JSON tool invocations; always answer directly in natural Markdown.]"
            )
        else:
            base_sys = system_prompt or DEFAULT_SYSTEM_PROMPT
            runtime_guardrail = (
                f"\n\n[Runtime Context: You are Fellow, currently running locally on the user's computer "
                f"via Ollama using the model '{effective_stream_model}'. You do NOT have external tools, functions, or "
                f"SystemMetadata plugins enabled. Never output tool_call blocks or JSON tool invocations; "
                f"always answer the user directly in natural, friendly Markdown.]"
            )

        ollama_messages = [{"role": "system", "content": (base_sys + runtime_guardrail).strip()}]
        ollama_messages.extend(clean_history)

        final_user_msg: Dict[str, Any] = {"role": "user", "content": final_user_content}
        if collected_images_b64 and is_vision_model(effective_stream_model) and not is_trivial_conversational:
            final_user_msg["images"] = collected_images_b64
        ollama_messages.append(final_user_msg)

    all_sources_for_display = retrieved_sources + vision_sources_meta

    def generate_sse():
        init_event = {
            "type": "sources",
            "sources": all_sources_for_display,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "model": effective_stream_model,
        }
        yield f"data: {json.dumps(init_event)}\n\n"

        if ocr_text_only_notice:
            yield f"data: {json.dumps({'type': 'token', 'content': ocr_text_only_notice})}\n\n"
            chat_store.add_message(
                chat_id=chat_id,
                role="assistant",
                content=ocr_text_only_notice,
                model=model,
                sources=all_sources_for_display,
            )
            done_payload = {
                "type": "done",
                "chat_id": chat_id,
                "chat_title": chat_title,
                "chat": chat_store.get_chat(chat_id),
                "chats": chat_store.list_chats(),
            }
            yield f"data: {json.dumps(done_payload)}\n\n"
            return

        if vision_warning_note and not is_ocr_vision_turn:
            warn_prefix = f"> **Vision Tip:** {vision_warning_note}\n\n"
            yield f"data: {json.dumps({'type': 'token', 'content': warn_prefix})}\n\n"

        full_response_parts: List[str] = (
            [f"> **Vision Tip:** {vision_warning_note}\n\n"]
            if (vision_warning_note and not is_ocr_vision_turn)
            else []
        )
        had_error = False

        for event in ollama_manager.stream_chat(
            model=effective_stream_model,
            messages=ollama_messages,
            temperature=temperature,
            num_ctx=num_ctx,
        ):
            ev_type = event.get("type")
            if ev_type == "thinking":
                yield f"data: {json.dumps({'type': 'thinking', 'count': event.get('count', 1)})}\n\n"
            elif ev_type == "token":
                token_str = event.get("content", "")
                full_response_parts.append(token_str)
                yield f"data: {json.dumps({'type': 'token', 'content': token_str})}\n\n"
            elif ev_type == "error":
                had_error = True
                err_msg = event.get("error", "An unexpected error occurred while talking to Ollama.")
                yield f"data: {json.dumps({'type': 'error', 'error': err_msg})}\n\n"
                break
            elif ev_type == "done":
                break

        full_text = "".join(full_response_parts).strip()

        # Retry once for specialized OCR model if first prompt produced empty or bare placeholder text
        if (
            is_ocr_vision_turn
            and not had_error
            and (not full_text or full_text == "![image](image_1.png)")
        ):
            retry_replace = bool(full_text)
            full_response_parts = []
            retry_msgs = [
                {
                    "role": "user",
                    "content": "Describe and transcribe this image in detail.",
                    "images": collected_images_b64,
                }
            ]
            for event in ollama_manager.stream_chat(
                model=effective_stream_model,
                messages=retry_msgs,
                temperature=temperature,
                num_ctx=num_ctx,
            ):
                ev_type = event.get("type")
                if ev_type == "token":
                    token_str = event.get("content", "")
                    full_response_parts.append(token_str)
                    if retry_replace:
                        yield f"data: {json.dumps({'type': 'replace', 'content': ''.join(full_response_parts)})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'token', 'content': token_str})}\n\n"
                elif ev_type == "error":
                    had_error = True
                    err_msg = event.get("error", "An unexpected error occurred while talking to Ollama.")
                    yield f"data: {json.dumps({'type': 'error', 'error': err_msg})}\n\n"
                    break
                elif ev_type == "done":
                    break
            full_text = "".join(full_response_parts).strip()

        if "SystemMetadata" in full_text or full_text.startswith("```tool_call"):
            replacement = (
                f"Hi! I'm **Fellow**, your offline study companion, currently running the "
                f"**`{effective_stream_model}`** model locally on your computer via Ollama."
            )
            full_text = replacement
            yield f"data: {json.dumps({'type': 'replace', 'content': replacement})}\n\n"

        if not full_text and not had_error:
            had_error = True
            yield f"data: {json.dumps({'type': 'error', 'error': 'Fellow did not receive a response from the model. Please try resending your message or check that the model is downloaded.'})}\n\n"

        if had_error and not full_text:
            chat_store.truncate_from_message(
                chat_id=chat_id,
                message_id=added_user_msg_id,
            )

        if full_text and not had_error:
            chat_store.add_message(
                chat_id=chat_id,
                role="assistant",
                content=full_text,
                model=effective_stream_model,
                sources=all_sources_for_display,
            )

        done_payload = {
            "type": "done",
            "chat_id": chat_id,
            "chat_title": chat_title,
            "chat": chat_store.get_chat(chat_id),
            "chats": chat_store.list_chats(),
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    return Response(
        generate_sse(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# -----------------------------------------------------------------------------
# Desktop Window / Server Launcher
# -----------------------------------------------------------------------------

def find_free_port(preferred: int = 5143) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_flask_server(host: str, port: int) -> None:
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


def main():
    parser = argparse.ArgumentParser(description="Fellow — Offline Study & Research Companion")
    parser.add_argument("--port", type=int, default=0, help="Local port to bind (default: auto)")
    parser.add_argument("--browser", action="store_true", help="Open Fellow in default web browser instead of PyWebView window")
    parser.add_argument("--no-window", action="store_true", help="Run backend server only without opening a GUI window")
    args = parser.parse_args()

    host = "127.0.0.1"
    port = args.port if args.port > 0 else find_free_port(5143)
    url = f"http://{host}:{port}"

    if args.no_window:
        print(f"Fellow server listening on {url}")
        run_flask_server(host, port)
        return

    # Start Flask server in a background daemon thread
    server_thread = threading.Thread(target=run_flask_server, args=(host, port), daemon=True)
    server_thread.start()

    # Wait briefly for Flask to accept connections
    for _ in range(30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((host, port)) == 0:
                break
        time.sleep(0.1)

    if args.browser:
        print(f"Opening Fellow in browser at {url}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return

    # Launch native desktop window via PyWebView
    global ACTIVE_WEBVIEW_WINDOW
    try:
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            title=APP_NAME,
            url=url,
            width=1240,
            height=820,
            min_size=(900, 620),
            text_select=True,
        )
        ACTIVE_WEBVIEW_WINDOW = window
        webview.start(debug=False)
    except Exception as e:
        ACTIVE_WEBVIEW_WINDOW = None
        print(f"PyWebView window unavailable ({e}); opening Fellow in default browser at {url}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        ACTIVE_WEBVIEW_WINDOW = None


if __name__ == "__main__":
    main()
