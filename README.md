# Fellow — Offline Study & Research Companion

**Fellow** is a warm, 100% offline desktop application designed for students and professors to chat with local AI models and study their documents, lecture slides, scanned PDFs, and images privately on their own computers—powered by [Ollama](https://ollama.com). It is engineered specifically to run comfortably on modest laptops (4 to 8 GB RAM, integrated graphics, no dedicated GPU) as well as higher-memory machines.

Created by **Ayman Boumait** ([GitHub](https://github.com/AymanBoumait) • [LinkedIn](https://www.linkedin.com/in/ayman-boumait)).

---

## Key Features

- **100% Offline & Private**: Connects locally to Ollama at `http://127.0.0.1:11434`. All UI assets, Markdown rendering, syntax highlighting, OCR, and FAISS vector search run locally with zero cloud services or external CDNs.
- **Automatic RAM Detection & Smart Defaults**: Uses `psutil` on first launch to inspect system memory and recommend the ideal model tier in plain language:
  - **Under 5 GB RAM**: Recommends **Llama 3.2 1B** (`llama3.2:1b`) — *Fastest*
  - **5 to 10 GB RAM**: Recommends **Llama 3.2 3B** (`llama3.2:3b`) — *Fast (default)*
  - **10+ GB RAM**: Recommends **Llama 3.2 3B** for quick setup and highlights **Qwen2.5 7B** (`qwen2.5:7b`) — *Smarter* in Browse Models
- **Optional Document Context (Answers Directly Anytime)**:
  - Ask Fellow general questions with **zero documents uploaded**—Fellow answers directly from its own knowledge without asking for a file.
  - Only retrieves document chunks when relevant uploaded materials exist for the active chat.
- **Document Scoping (Global vs. Private to Chat)**:
  - Choose whether each upload is **Available in all chats** (shared study library) or **Only in this chat** (isolated to the active conversation).
  - Uploads triggered inside an active chat default to **Only in this chat** and can be toggled between scopes with one click in the sidebar or chat document drawer.
- **Vision & Scanned PDF Support**:
  - Upload **PDF**, **DOCX**, **TXT**, and **Image** (`.png`, `.jpg`, `.jpeg`) files.
  - Automatically detects scanned PDFs with no selectable text and offers a choice between **OCR text extraction** (local RapidOCR / Tesseract, works with any text model) or **Visual reading** (Vision model).
  - When uploading a picture or scanned file, Fellow automatically recommends **`Maternion/LightOnOCR-3:1b`** (*LightOnOCR-3 1B — Scans & Images*), downloads it on confirmation, and switches to it automatically once complete. **`abliterant/qwen3.5-rana:9b-q8_0`** (*Qwen3.5 RANA 9B Vision*) is also included in Browse Models for heavier visual reasoning.
- **Edit & Resend Messages + Native Markdown Export**:
  - Edit any sent user message inline and regenerate the conversation cleanly from that point onward.
  - Export any study session cleanly to a formatted `.md` file via the **Export .md** button.
- **Two Modes (Simple & Advanced) + Light/Dark Themes**:
  - **Simple Mode**: Streamlined study UI with plain-language model labels and quick starters.
  - **Advanced Mode**: Adjust temperature, context length (`num_ctx`), system prompts, named presets (*Study Q&A*, *Essay Helper*, *Code Explainer*, *Lecture Prep*, plus custom presets), and inspect retrieved document chunks and match percentages.
  - Switch between warm **Light** and **Dark** themes in Settings.

---

## Step-by-Step Guide: How to Install & Use Fellow

### Step 1: Install Ollama
Fellow uses Ollama in the background to run AI models offline on your laptop.
- **Windows / macOS Packaged Users**:
  - Windows **`FellowSetup.exe`** bundles `OllamaSetup.exe` and installs it silently if missing.
  - macOS **`Fellow-macOS-arm64.dmg`** / **`Fellow-macOS-x86_64.dmg`** bundles Ollama right inside `Fellow.app`—no separate Ollama install needed.
- **Running from Source (All Platforms)**:
  1. Download and install Ollama from [ollama.com/download](https://ollama.com/download) ([macOS download](https://ollama.com/download/mac)).
  2. *(Optional)* Verify Ollama works in PowerShell / Terminal:
     ```bash
     ollama --version
     ```

### Step 2: Download Fellow & Install Dependencies
1. Clone this repository (or click **Code → Download ZIP** on GitHub and extract it):
   ```bash
   git clone https://github.com/AymanBoumait/Fellow.git
   cd Fellow
   ```
2. *(Recommended)* Create and activate a Python virtual environment (Python 3.10+):
   - **Windows (PowerShell):**
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     ```
   - **macOS (Terminal):**
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```
3. Install required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

### Step 3: Launch Fellow
Start Fellow as a native desktop window (PyWebView):
- **Windows:** `python app.py` (or `python app.py --browser`)
- **macOS:** `python3 app.py` (or `python3 app.py --browser`)

### Step 4: Complete First-Run Setup
1. On first launch, Fellow checks that Ollama is running and measures your computer's RAM.
2. Review Fellow's plain-language recommendation and click **Accept Recommendation & Set Up Fellow** to download your chat model and the local document search engine (`nomic-embed-text`) with live progress, Pause, Resume, and Cancel controls.

### Step 5: Study with Fellow
- **Ask general questions anytime**: Start typing in the composer immediately—no file required.
- **Upload study materials**:
  - Use the left sidebar to upload **Available in all chats** or **Only in this chat**, or click **+ Upload to This Chat** above the composer to attach a file strictly to the current session.
  - Supported formats: `.pdf`, `.docx`, `.txt`, `.png`, `.jpg`, `.jpeg`.
- **Working with pictures & scanned PDFs**:
  - Upload a picture or scanned PDF and accept Fellow's prompt to download & switch to **`Maternion/LightOnOCR-3:1b`**, or process scanned PDFs via local OCR so any text model can read them.
- **Switch models mid-chat, edit questions, and save notes**:
  - Switch models anytime using the top dropdown (or **Browse Models**).
  - Hover any sent user message and click **Edit** to revise and regenerate the answer below it.
  - Click **Export .md** in the top-right header to save the full conversation as Markdown.

---

## Step-by-Step Guide: How to Build a Standalone `.exe` (Windows)

Packaging Fellow as a Windows executable or setup installer lets students, professors, or anyone launch Fellow by double-clicking an icon—without opening a terminal or installing Python packages manually.

### Option A: Automated Build Script (`dist\Fellow\Fellow.exe` — App Only)
1. Ensure dependencies (including `pyinstaller`) are installed in your active environment:
   ```powershell
   pip install -r requirements.txt
   ```
2. Run the included PowerShell build script from the repository root:
   ```powershell
   .\build_exe.ps1
   ```
3. This script ensures `assets\icon.ico` exists (`generate_icon.py`), cleans old `build\` and `dist\` folders, and runs PyInstaller with `fellow.spec`.
4. Launch your app by double-clicking:
   ```text
   dist\Fellow\Fellow.exe
   ```
   *(Tip: Right-click `dist\Fellow\Fellow.exe` → **Show more options → Send to → Desktop (create shortcut)** for one-click access. Note: End users using Option A/B need [Ollama](https://ollama.com/download) installed locally; use **Option C** below to bundle Ollama into one installer.)*

### Option B: Single-File Portable `.exe` (`--onefile` — App Only)
If you prefer a single portable `.exe` file instead of a folder (note that `--onefile` takes a few extra seconds at startup to unpack bundled binaries):
```powershell
pyinstaller --noconfirm --clean --windowed --onefile `
  --name "Fellow" `
  --icon "assets/icon.ico" `
  --add-data "static;static" `
  --add-data "assets;assets" `
  --collect-all faiss `
  --collect-submodules webview `
  --collect-data webview `
  --collect-data rapidocr_onnxruntime `
  --collect-binaries onnxruntime `
  app.py
```
The standalone executable will be generated at `dist\Fellow.exe`.

### Option C: All-in-One Offline Windows Installer (`FellowSetup.exe` — Bundles Fellow + Ollama)
An Inno Setup script (`installer.iss`) and automated PowerShell builder (`build_installer.ps1`) package **both Fellow and OllamaSetup.exe** into one offline setup wizard (`installer_output\FellowSetup.exe`) so non-technical users do not need Python or Ollama pre-installed:

1. Install **Inno Setup 6**:
   ```powershell
   winget install JRSoftware.InnoSetup
   ```
   *(Or download directly from [jrsoftware.org/isdl.php](https://jrsoftware.org/isdl.php).)*
2. Run the all-in-one installer build script from the repository root:
   ```powershell
   .\build_installer.ps1
   ```
   This script automatically builds `dist\Fellow\Fellow.exe`, downloads the official `OllamaSetup.exe` into `installer_assets/` (excluded from Git via `.gitignore`), and compiles:
   ```text
   installer_output\FellowSetup.exe
   ```
   *(Optional flags: `.\build_installer.ps1 -SkipExeBuild` reuses an existing `dist\Fellow\Fellow.exe`; `-ForceDownloadOllama` refreshes `installer_assets\OllamaSetup.exe`.)*
3. **What end users experience**:
   - Double-clicking `FellowSetup.exe` installs Fellow normally and silently installs `OllamaSetup.exe` automatically only if Ollama is not already installed on their computer—no separate Ollama or Python setup required.
   - *(Note: `FellowSetup.exe` typically lands around ~1.2–1.5 GB because it bundles `OllamaSetup.exe`, which fits under GitHub Releases' 2 GB per-file upload limit. Unsigned `.exe` installers show a standard Windows SmartScreen prompt — click **More info → Run anyway** unless code-signed.)*

---

## Step-by-Step Guide: macOS App & `.dmg` (Apple Silicon & Intel)

Fellow supports both **Apple Silicon (`arm64`, M1–M4)** and **Intel (`x86_64`)** Macs, and packages **Ollama directly inside `Fellow.app`** so Mac users do not need to install Python or Ollama separately.

### Installing on macOS (End Users)
1. Download **`Fellow-macOS-arm64.dmg`** (for M1/M2/M3/M4 Macs) or **`Fellow-macOS-x86_64.dmg`** (for Intel Macs) from **Releases**.
2. Open the `.dmg` and drag **`Fellow.app`** into **Applications**.
3. **First launch (unsigned open-source app notice):** Because `Fellow.app` is not notarized with a paid Apple Developer ID, macOS Gatekeeper may warn *"Fellow cannot be opened because the developer cannot be verified"* or report the app is damaged. Allow it once via either:
   - Running this command in **Terminal**:
     ```bash
     xattr -cr /Applications/Fellow.app
     ```
   - Or opening **System Settings → Privacy & Security** and clicking **Open Anyway**.
4. Launch **Fellow**—the bundled Ollama runtime starts automatically in the background and shuts down cleanly when you quit.

### Building `.dmg` Automatically on GitHub Actions (Even Without a Mac)
A GitHub Actions workflow (`.github/workflows/build-macos.yml`) builds both `Fellow-macOS-arm64.dmg` (`macos-15`) and `Fellow-macOS-x86_64.dmg` (`macos-15-intel`) for free:
- **Automatic on Release:** Publishing any GitHub Release automatically builds both `.dmg` files and attaches them to that release.
- **Manual Run (Attach to an Existing Release):** Go to **Actions → Build macOS Installer (.dmg) → Run workflow**, optionally enter an existing tag such as `v1.0.0` in **`release_tag`**, and the workflow will build and upload both `.dmg` installers directly to that release.

### Building Locally on a Mac
On macOS (with Python 3.10+):
```bash
pip install -r requirements.txt pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-WebKit
chmod +x build_mac.sh
./build_mac.sh
```
This generates `assets/icon.icns`, bundles `dist/Fellow.app` with Ollama inside `Fellow.app/Contents/Resources/ollama/`, ad-hoc signs it, and outputs `dist/Fellow-macOS-$(uname -m).dmg`.

---

## Project Structure & Local Data Privacy

- `app.py` — Flask API backend, SSE streaming, RAG/Vision routing, and PyWebView desktop launcher
- `backend/config.py` — RAM detection, model catalog, presets, settings persistence
- `backend/ollama_manager.py` — Ollama lifecycle (system install or bundled sidecar), model downloads (pause/resume/cancel), embeddings, streaming chat
- `backend/document_store.py` — PDF/DOCX/TXT/Image ingestion, OCR & Vision handling, FAISS indexing, document scoping
- `backend/chat_store.py` — Local conversation persistence and `.md` export
- `static/` — Offline HTML/CSS/JS frontend, custom Markdown & syntax highlighter (`static/js/markdown.js`)
- `assets/` — Fellow owl logo and application icons (`logo.png`, `icon.png`, `icon.ico`, `logo_source.jpg`)

All user chats, settings, uploaded documents, FAISS indexes, and visual caches are stored privately on your machine in `%LOCALAPPDATA%\Fellow` on Windows or `~/Library/Application Support/Fellow` on macOS (or the directory specified by `FELLOW_DATA_DIR`), never inside the source repository.
