/**
 * Fellow — Main Frontend Application Controller
 * Handles First-Run Setup, RAM Check, Simple/Advanced Modes, Model Downloads
 * (Pause/Resume/Cancel), Document Scoping (Global vs. Chat-only), and Streaming Chat.
 */
(function () {
  "use strict";

  const state = {
    settings: {
      first_run_completed: false,
      ram_check_completed: false,
      selected_model: "llama3.2:3b",
      embedding_model: "nomic-embed-text",
      mode: "simple",
      theme: "light",
      temperature: 0.4,
      num_ctx: 4096,
      system_prompt: "",
      active_preset: "study-qa",
      presets: [],
      use_documents: true,
    },
    ramInfo: null,
    ollama: {
      installed: true,
      running: true,
      installed_models: [],
    },
    curatedModels: [],
    downloads: {},
    documents: [],
    chats: [],
    currentChat: null,
    editingMessageIndex: null,
    welcomeSelectedModel: "llama3.2:3b",
    sidebarUploadScope: "global", // "global" (Available in all chats) | "chat" (Only in this chat)
    reuploadDocId: null,
    activeStreamController: null,
    downloadPollTimer: null,
    autoSwitchOnCompleteModel: null,
    pendingScannedList: [],
  };

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) => window.FellowMarkdown.escapeHtml(s);

  // =========================================================================
  // Initialization & Bootstrap
  // =========================================================================

  async function initApp() {
    bindEvents();
    await loadBootstrap();
    startDownloadPolling();
  }

  async function loadBootstrap() {
    try {
      const resp = await fetch("/api/bootstrap");
      const data = await resp.json();

      state.settings = data.settings || state.settings;
      state.ramInfo = data.ram_info || null;
      state.ollama = data.ollama || state.ollama;
      state.curatedModels = data.curated_models || [];
      state.downloads = data.downloads || {};
      state.documents = data.documents || [];
      state.chats = data.chats || [];

      if (state.ramInfo && state.ramInfo.recommended_model) {
        state.welcomeSelectedModel =
          state.settings.selected_model || state.ramInfo.recommended_model;
      }

      applyTheme(state.settings.theme || "light");
      applyMode(state.settings.mode || "simple");

      if (state.chats.length > 0 && !state.currentChat) {
        await selectChat(state.chats[0].id);
      }

      renderAll();

      if (!state.settings.first_run_completed) {
        openWelcomeWizard();
      }
    } catch (err) {
      showToast("Could not connect to Fellow backend: " + err.message, true);
    }
  }

  function renderAll() {
    renderModelDropdown();
    renderDocumentsList();
    renderChatDocumentPanel();
    renderChatsList();
    renderAdvancedPanel();
    renderModelsModalContent();
    renderSettingsModalContent();
  }

  // =========================================================================
  // Theme & Mode (Simple vs. Advanced)
  // =========================================================================

  function applyTheme(theme) {
    const cleanTheme = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", cleanTheme);
    state.settings.theme = cleanTheme;

    const btnLight = $("btn-theme-light");
    const btnDark = $("btn-theme-dark");
    if (btnLight && btnDark) {
      btnLight.classList.toggle("active", cleanTheme === "light");
      btnDark.classList.toggle("active", cleanTheme === "dark");
    }
  }

  async function setTheme(theme) {
    applyTheme(theme);
    await saveSettings({ theme: state.settings.theme });
  }

  function applyMode(mode) {
    const cleanMode = mode === "advanced" ? "advanced" : "simple";
    state.settings.mode = cleanMode;

    $("btn-mode-simple").classList.toggle("active", cleanMode === "simple");
    $("btn-mode-advanced").classList.toggle("active", cleanMode === "advanced");

    const sSimple = $("btn-settings-mode-simple");
    const sAdv = $("btn-settings-mode-advanced");
    if (sSimple && sAdv) {
      sSimple.classList.toggle("active", cleanMode === "simple");
      sAdv.classList.toggle("active", cleanMode === "advanced");
    }

    const advPanel = $("advanced-panel");
    if (advPanel) {
      advPanel.classList.toggle("hidden", cleanMode !== "advanced");
    }

    renderModelDropdown();

    if (state.currentChat) {
      renderCurrentChatMessages();
    }
  }

  async function setMode(mode) {
    applyMode(mode);
    await saveSettings({ mode: state.settings.mode });
  }

  async function saveSettings(partial) {
    try {
      const resp = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(partial),
      });
      if (resp.ok) {
        state.settings = await resp.json();
      }
    } catch (_) {}
  }

  // =========================================================================
  // First-Run Welcome Wizard (Ollama Check -> RAM Check -> Auto Setup)
  // =========================================================================

  function openWelcomeWizard() {
    const overlay = $("welcome-overlay");
    overlay.classList.remove("hidden");

    if (!state.ollama.installed || !state.ollama.running) {
      showWelcomeStep("ollama");
    } else {
      showWelcomeStep("ram");
      renderWelcomeRamStep();
    }
  }

  function showWelcomeStep(stepName) {
    $("welcome-step-ollama").classList.toggle("hidden", stepName !== "ollama");
    $("welcome-step-ram").classList.toggle("hidden", stepName !== "ram");
    $("welcome-step-download").classList.toggle("hidden", stepName !== "download");
  }

  function renderWelcomeRamStep() {
    if (!state.ramInfo) return;
    $("welcome-ram-detected-pill").textContent = `${state.ramInfo.display_total_gb} GB System Memory Detected`;
    $("welcome-ram-message").textContent = state.ramInfo.plain_message;

    const container = $("welcome-tier-cards");
    const tiers = state.ramInfo.tiers || [];
    container.innerHTML = tiers
      .map((t) => {
        const isSelected = state.welcomeSelectedModel === t.model;
        let badgeHtml = "";
        if (t.recommended) {
          badgeHtml = `<span class="tier-rec-tag">Recommended</span>`;
        } else if (t.highlight_stronger) {
          badgeHtml = `<span class="tier-rec-tag tertiary-tag">10+ GB Option</span>`;
        }
        return `
          <div class="tier-card ${isSelected ? "selected" : ""}" data-select-tier="${escapeHtml(t.model)}">
            ${badgeHtml}
            <div class="tier-card-title">${escapeHtml(t.tier_label)}</div>
            <div class="tier-card-model">${escapeHtml(t.title)}</div>
            <div class="tier-card-sub">${escapeHtml(t.subtitle)}</div>
          </div>
        `;
      })
      .join("");

    const acceptBtn = $("btn-accept-recommendation");
    const chosenTier = tiers.find((t) => t.model === state.welcomeSelectedModel);
    if (chosenTier && chosenTier.recommended) {
      acceptBtn.textContent = `Accept Recommendation (${chosenTier.title}) & Set Up Fellow`;
    } else if (chosenTier) {
      acceptBtn.textContent = `Set Up Fellow with ${chosenTier.title}`;
    }
  }

  async function startFirstRunModelSetup() {
    const chosenModel =
      state.welcomeSelectedModel ||
      (state.ramInfo ? state.ramInfo.recommended_model : "llama3.2:3b");

    await saveSettings({
      selected_model: chosenModel,
      ram_check_completed: true,
    });
    renderModelDropdown();

    showWelcomeStep("download");

    const installedNames = (state.ollama.installed_models || []).map((m) =>
      m.clean_name.toLowerCase()
    );
    const needChatModel = !installedNames.includes(chosenModel.toLowerCase());
    const needEmbedModel = !installedNames.includes("nomic-embed-text");

    if (!needChatModel && !needEmbedModel) {
      await completeFirstRun();
      return;
    }

    if (needEmbedModel) {
      await triggerModelPull("nomic-embed-text", false);
    }
    if (needChatModel) {
      await triggerModelPull(chosenModel, false);
    }
    renderWelcomeDownloadsProgress();
  }

  function renderWelcomeDownloadsProgress() {
    const wrap = $("welcome-downloads-container");
    if (!wrap) return;

    const chosenModel = state.settings.selected_model || state.welcomeSelectedModel || "llama3.2:3b";
    const requiredModels = [chosenModel, "nomic-embed-text"];
    const installedSet = new Set(
      (state.ollama.installed_models || []).flatMap((m) => [
        m.name.toLowerCase(),
        m.clean_name.toLowerCase(),
      ])
    );

    let allReady = true;
    const cardsHtml = requiredModels
      .map((modelName) => {
        const dl = state.downloads[modelName];
        const isInstalled =
          installedSet.has(modelName.toLowerCase()) ||
          (dl && dl.status === "completed");

        if (!isInstalled) {
          allReady = false;
        }

        const friendlyLabel =
          modelName === "nomic-embed-text"
            ? "Document Reading Engine (nomic-embed-text)"
            : `Fellow AI Assistant (${modelName})`;

        if (isInstalled) {
          return `
            <div class="dl-control-box">
              <div class="dl-info-row">
                <span>${escapeHtml(friendlyLabel)}</span>
                <span class="status-pill success-pill">✓ Ready (100%)</span>
              </div>
              <div class="dl-progress-track">
                <div class="dl-progress-fill" style="width:100%"></div>
              </div>
            </div>
          `;
        }

        return renderDownloadControlBox(modelName, friendlyLabel, dl);
      })
      .join("");

    wrap.innerHTML = cardsHtml;

    const finishBtn = $("btn-finish-welcome");
    if (allReady) {
      finishBtn.classList.remove("hidden");
      saveSettings({ first_run_completed: true });
    } else {
      finishBtn.classList.add("hidden");
    }
  }

  async function completeFirstRun() {
    await saveSettings({
      first_run_completed: true,
      ram_check_completed: true,
    });
    $("welcome-overlay").classList.add("hidden");
    await refreshModelsState();
  }

  // =========================================================================
  // Model Dropdown (Simple vs. Advanced Labels)
  // =========================================================================

  function renderModelDropdown() {
    const select = $("chat-model-select");
    if (!select) return;

    const isSimple = state.settings.mode !== "advanced";
    const installedChatModels = (state.ollama.installed_models || []).filter(
      (m) => !m.is_embedding
    );

    const seen = new Set();
    const optionsList = [];

    installedChatModels.forEach((m) => {
      seen.add(m.clean_name.toLowerCase());
      const visionNote = m.supports_vision ? " • Images & scans" : "";
      optionsList.push({
        value: m.clean_name,
        installed: true,
        simpleText: m.simple_label,
        advText: `${m.clean_name} (${m.size_human}${visionNote})`,
      });
    });

    (state.curatedModels || []).forEach((c) => {
      if (!seen.has(c.id.toLowerCase())) {
        seen.add(c.id.toLowerCase());
        const visionNote = c.supports_vision ? " • Images & scans" : "";
        optionsList.push({
          value: c.id,
          installed: Boolean(c.installed),
          simpleText: `${c.simple_label}${c.installed ? "" : " ⬇"}`,
          advText: `${c.id} (${c.approx_size}${visionNote})${c.installed ? "" : " ⬇"}`,
        });
      }
    });

    const currentSelected = state.settings.selected_model || "llama3.2:3b";
    if (!seen.has(currentSelected.toLowerCase())) {
      optionsList.unshift({
        value: currentSelected,
        installed: false,
        simpleText: `Custom (${currentSelected})`,
        advText: currentSelected,
      });
    }

    select.innerHTML = optionsList
      .map((opt) => {
        const label = isSimple ? opt.simpleText : opt.advText;
        const selectedAttr =
          opt.value.toLowerCase() === currentSelected.toLowerCase() ? "selected" : "";
        return `<option value="${escapeHtml(opt.value)}" ${selectedAttr}>${escapeHtml(label)}</option>`;
      })
      .join("");
  }

  // =========================================================================
  // Browse Models, Custom Model Pull, & Download Controls (Pause/Resume/Cancel)
  // =========================================================================

  function renderDownloadControlBox(modelName, titleText, dl) {
    const status = dl ? dl.status : "starting";
    const percent = dl ? dl.percent || 0 : 0;
    const completedMb = dl ? dl.completed_mb || 0 : 0;
    const totalMb = dl ? dl.total_mb || 0 : 0;
    const mbLabel =
      totalMb > 0
        ? `${completedMb.toFixed(1)} MB / ${totalMb.toFixed(1)} MB (${percent.toFixed(1)}%)`
        : `${percent.toFixed(1)}%`;

    const isPaused = status === "paused";
    const isError = status === "error";

    return `
      <div class="dl-control-box" data-dl-model="${escapeHtml(modelName)}">
        <div class="dl-info-row">
          <span>${escapeHtml(titleText || modelName)}</span>
          <span>${escapeHtml(mbLabel)}</span>
        </div>
        <div class="dl-progress-track">
          <div class="dl-progress-fill ${isPaused ? "paused" : ""}" style="width:${Math.min(100, Math.max(2, percent))}%"></div>
        </div>
        <div class="dl-info-row dl-status-subrow">
          <span>${escapeHtml(dl ? dl.status_text : "Preparing download...")}</span>
        </div>
        <div class="dl-buttons-row">
          ${
            isPaused || isError
              ? `<button type="button" class="btn btn-primary btn-xs" data-dl-action="resume" data-model="${escapeHtml(modelName)}">▶ Resume</button>`
              : `<button type="button" class="btn btn-secondary btn-xs" data-dl-action="pause" data-model="${escapeHtml(modelName)}">⏸ Pause</button>`
          }
          <button type="button" class="btn btn-danger-outline btn-xs" data-dl-action="cancel" data-model="${escapeHtml(modelName)}">
            ✕ Stop &amp; Cancel
          </button>
        </div>
      </div>
    `;
  }

  function renderModelsModalContent() {
    if (state.ramInfo) {
      $("models-ram-headline").textContent = `Detected ${state.ramInfo.display_total_gb} GB RAM:`;
      $("models-ram-detail").textContent = state.ramInfo.plain_message;
    }

    const grid = $("curated-models-grid");
    if (grid) {
      const activeModel = (state.settings.selected_model || "").toLowerCase();
      grid.innerHTML = (state.curatedModels || [])
        .map((m) => {
          const dl = state.downloads[m.id];
          const hasActiveDl =
            dl && ["starting", "downloading", "paused", "error"].includes(dl.status);
          const isInstalled = Boolean(m.installed) || (dl && dl.status === "completed");
          const isSelected = m.id.toLowerCase() === activeModel;
          const isRec =
            state.ramInfo &&
            state.ramInfo.recommended_model.toLowerCase() === m.id.toLowerCase();

          let bottomHtml = "";
          if (hasActiveDl) {
            bottomHtml = renderDownloadControlBox(m.id, m.title, dl);
          } else if (isInstalled) {
            bottomHtml = `
              <div class="curated-card-top">
                <span class="status-pill success-pill">✓ Installed ${m.installed_size_human ? `(${escapeHtml(m.installed_size_human)})` : ""}</span>
                <div>
                  ${
                    isSelected
                      ? `<span class="btn btn-subtle btn-sm">Active Assistant</span>`
                      : `<button type="button" class="btn btn-primary btn-sm" data-select-model="${escapeHtml(m.id)}">Use This Model</button>`
                  }
                </div>
              </div>
            `;
          } else {
            bottomHtml = `
              <div class="curated-card-top">
                <span class="curated-card-tag">Download size: ${escapeHtml(m.approx_size)}</span>
                <button type="button" class="btn btn-primary btn-sm" data-pull-model="${escapeHtml(m.id)}">
                  Download
                </button>
              </div>
            `;
          }

          return `
            <div class="curated-model-card">
              <div>
                <div class="curated-card-top">
                  <div>
                    <div class="curated-card-title">${escapeHtml(m.title)}</div>
                    <div class="curated-card-tag">${escapeHtml(m.id)}</div>
                  </div>
                  ${isRec ? `<span class="tier-rec-tag">Recommended for Your RAM</span>` : ""}
                </div>
                <p class="curated-card-desc">${escapeHtml(m.description)}</p>
                <div class="curated-card-specs">
                  <span class="spec-pill">Size: ${escapeHtml(m.approx_size)}</span>
                  <span class="spec-pill">${escapeHtml(m.ram_needs)}</span>
                  ${m.supports_vision ? `<span class="vision-model-badge">${escapeHtml(m.vision_badge || "Supports images and scanned documents")}</span>` : ""}
                </div>
              </div>
              ${bottomHtml}
            </div>
          `;
        })
        .join("");
    }

    const curatedIds = new Set((state.curatedModels || []).map((c) => c.id.toLowerCase()));
    const customDls = Object.values(state.downloads || {}).filter(
      (d) =>
        !curatedIds.has(d.model.toLowerCase()) &&
        ["starting", "downloading", "paused", "error"].includes(d.status)
    );
    const customWrap = $("custom-downloads-list");
    if (customWrap) {
      customWrap.innerHTML = customDls
        .map((d) => renderDownloadControlBox(d.model, d.model, d))
        .join("");
    }

    const installedWrap = $("installed-models-table-wrap");
    if (installedWrap) {
      const models = state.ollama.installed_models || [];
      if (models.length === 0) {
        installedWrap.innerHTML = `
          <div class="doc-empty-note">
            No models are currently installed on disk. Click <strong>Download</strong> on any assistant above to get started.
          </div>
        `;
      } else {
        const activeModel = (state.settings.selected_model || "").toLowerCase();
        installedWrap.innerHTML = models
          .map((m) => {
            const isSelected =
              m.name.toLowerCase() === activeModel ||
              m.clean_name.toLowerCase() === activeModel;
            const metaParts = [m.simple_label];
            if (m.parameter_size) metaParts.push(m.parameter_size);
            if (m.quantization_level) metaParts.push(m.quantization_level);

            return `
              <div class="installed-model-row">
                <div class="installed-model-main">
                  <div class="installed-model-name">
                    ${escapeHtml(m.title)}
                    <span class="curated-card-tag">(${escapeHtml(m.name)})</span>
                    ${m.supports_vision ? `<span class="vision-model-badge">${escapeHtml(m.vision_badge || "Supports images and scanned documents")}</span>` : ""}
                  </div>
                  <div class="installed-model-sub">${escapeHtml(metaParts.join(" • "))}</div>
                </div>
                <div class="installed-model-actions">
                  <span class="disk-size-badge" title="Disk space used">${escapeHtml(m.size_human)}</span>
                  ${
                    !m.is_embedding
                      ? isSelected
                        ? `<span class="status-pill success-pill">Active</span>`
                        : `<button type="button" class="btn btn-secondary btn-xs" data-select-model="${escapeHtml(m.clean_name)}">Select</button>`
                      : `<span class="status-pill neutral-pill">Document Search</span>`
                  }
                  <button
                    type="button"
                    class="btn btn-danger-outline btn-xs"
                    data-delete-model="${escapeHtml(m.name)}"
                    data-delete-size="${escapeHtml(m.size_human)}"
                  >
                    Delete
                  </button>
                </div>
              </div>
            `;
          })
          .join("");
      }
    }

    const activeDlCount = Object.values(state.downloads || {}).filter((d) =>
      ["starting", "downloading", "paused"].includes(d.status)
    ).length;
    const badge = $("active-download-badge");
    if (badge) {
      badge.textContent = String(activeDlCount);
      badge.classList.toggle("hidden", activeDlCount === 0);
    }
  }

  async function triggerModelPull(modelName, showNotification = true) {
    try {
      const resp = await fetch("/api/models/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelName }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        showToast(data.error || "Failed to start download", true);
        return;
      }
      state.downloads[modelName] = data;
      renderModelsModalContent();
      renderWelcomeDownloadsProgress();
      if (showNotification) {
        showToast(`Started downloading ${modelName}`);
      }
    } catch (err) {
      showToast("Error starting download: " + err.message, true);
    }
  }

  async function controlDownload(action, modelName) {
    try {
      const resp = await fetch(`/api/models/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelName }),
      });
      const data = await resp.json();
      if (action === "cancel") {
        delete state.downloads[modelName];
        showToast(data.status_text || `Cancelled download of ${modelName}`);
      } else {
        state.downloads[modelName] = data;
      }
      renderModelsModalContent();
      renderWelcomeDownloadsProgress();
    } catch (err) {
      showToast(`Could not ${action} download: ${err.message}`, true);
    }
  }

  function promptDeleteModel(modelName, sizeHuman) {
    openConfirmDialog({
      title: `Delete ${modelName}?`,
      message: `Are you sure you want to delete "${modelName}" from your computer? This will free up approximately ${sizeHuman} of disk space. You can re-download it anytime.`,
      confirmText: "Delete & Free Space",
      danger: true,
      onConfirm: async () => {
        try {
          const resp = await fetch("/api/models/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model: modelName }),
          });
          const data = await resp.json();
          if (!resp.ok || !data.success) {
            showToast(data.error || "Could not delete model", true);
            return;
          }
          state.ollama.installed_models = data.installed_models || [];
          state.curatedModels = data.curated_models || state.curatedModels;
          renderAll();

          const banner = $("models-feedback-banner");
          if (banner) {
            banner.innerHTML = `<strong>Disk space freed:</strong> ${escapeHtml(data.message)}`;
            banner.classList.remove("hidden");
          }
          showToast(data.message);
        } catch (err) {
          showToast("Delete failed: " + err.message, true);
        }
      },
    });
  }

  function startDownloadPolling() {
    if (state.downloadPollTimer) clearInterval(state.downloadPollTimer);
    state.downloadPollTimer = setInterval(async () => {
      const hasOngoing = Object.values(state.downloads || {}).some((d) =>
        ["starting", "downloading"].includes(d.status)
      );
      const welcomeVisible = !$("welcome-overlay").classList.contains("hidden");
      const modelsVisible = !$("models-modal").classList.contains("hidden");

      if (!hasOngoing && !welcomeVisible && !modelsVisible && !state.autoSwitchOnCompleteModel) {
        return;
      }

      try {
        const resp = await fetch("/api/models/downloads");
        if (!resp.ok) return;
        const data = await resp.json();
        const prevDownloads = state.downloads || {};
        const nextDownloads = data.downloads || {};

        let anyNewlyCompleted = false;
        let autoSwitchedModel = null;
        for (const [mName, task] of Object.entries(nextDownloads)) {
          if (
            task.status === "completed" &&
            (!prevDownloads[mName] || prevDownloads[mName].status !== "completed")
          ) {
            anyNewlyCompleted = true;
            if (
              state.autoSwitchOnCompleteModel &&
              mName.toLowerCase() === state.autoSwitchOnCompleteModel.toLowerCase()
            ) {
              autoSwitchedModel = mName;
            } else {
              showToast(`${mName} is downloaded and ready!`);
            }
          }
        }

        state.downloads = nextDownloads;

        if (autoSwitchedModel) {
          state.autoSwitchOnCompleteModel = null;
          await saveSettings({ selected_model: autoSwitchedModel });
          await refreshModelsState();
          const statusArea = $("upload-status-area");
          if (statusArea) {
            statusArea.classList.add("hidden");
            statusArea.innerHTML = "";
          }
          showToast(`Downloaded ${autoSwitchedModel} and switched to it automatically!`);
          return;
        }

        if (state.autoSwitchOnCompleteModel) {
          const autoTask = nextDownloads[state.autoSwitchOnCompleteModel];
          if (autoTask && autoTask.status === "cancelled") {
            state.autoSwitchOnCompleteModel = null;
            const statusArea = $("upload-status-area");
            if (statusArea) {
              statusArea.classList.add("hidden");
              statusArea.innerHTML = "";
            }
          } else if (autoTask) {
            renderInlineOcrDownloadProgress(state.autoSwitchOnCompleteModel, autoTask);
          }
        }

        if (anyNewlyCompleted) {
          await refreshModelsState();
        } else {
          renderModelsModalContent();
          renderWelcomeDownloadsProgress();
        }
      } catch (_) {}
    }, 1000);
  }

  async function refreshModelsState() {
    try {
      const resp = await fetch("/api/models");
      if (!resp.ok) return;
      const data = await resp.json();
      state.ollama.installed_models = data.installed_models || [];
      state.curatedModels = data.curated_models || [];
      state.downloads = data.downloads || {};
      renderAll();
      renderWelcomeDownloadsProgress();
    } catch (_) {}
  }

  // =========================================================================
  // Advanced Mode Panel (Presets, Temperature, num_ctx, System Prompt)
  // =========================================================================

  function renderAdvancedPanel() {
    const presets = state.settings.presets || [];
    const activeId = state.settings.active_preset || "study-qa";

    const chipsWrap = $("presets-chips");
    if (chipsWrap) {
      chipsWrap.innerHTML = presets
        .map((p) => {
          const isActive = p.id === activeId;
          return `
            <button
              type="button"
              class="preset-chip ${isActive ? "active" : ""}"
              data-preset-id="${escapeHtml(p.id)}"
              title="${escapeHtml(p.description || p.name)}"
            >
              ${escapeHtml(p.name)}
            </button>
          `;
        })
        .join("");
    }

    const activePresetObj = presets.find((p) => p.id === activeId);
    const delBtn = $("btn-delete-preset");
    if (delBtn) {
      delBtn.classList.toggle("hidden", !activePresetObj || Boolean(activePresetObj.builtin));
    }

    const tempSlider = $("adv-temperature");
    const tempVal = $("adv-temperature-val");
    if (tempSlider && tempVal) {
      tempSlider.value = state.settings.temperature ?? 0.4;
      tempVal.textContent = Number(state.settings.temperature ?? 0.4).toFixed(2);
    }

    const ctxSelect = $("adv-num-ctx");
    const ctxVal = $("adv-num-ctx-val");
    if (ctxSelect && ctxVal) {
      const numCtx = String(state.settings.num_ctx || 4096);
      ctxSelect.value = numCtx;
      ctxVal.textContent = `${numCtx} tokens`;
    }

    const sysPrompt = $("adv-system-prompt");
    if (sysPrompt && document.activeElement !== sysPrompt) {
      sysPrompt.value = state.settings.system_prompt || "";
    }

    const miniList = $("adv-installed-models-list");
    if (miniList) {
      const models = state.ollama.installed_models || [];
      if (models.length === 0) {
        miniList.innerHTML = `<div class="doc-empty-note">No models installed yet</div>`;
      } else {
        miniList.innerHTML = models
          .map(
            (m) => `
            <div class="adv-model-row">
              <div>
                <div class="adv-model-tag">${escapeHtml(m.name)}</div>
                <div class="adv-model-size">${escapeHtml(m.size_human)} ${m.parameter_size ? `• ${escapeHtml(m.parameter_size)}` : ""}</div>
              </div>
              <button
                type="button"
                class="btn-text-danger"
                data-delete-model="${escapeHtml(m.name)}"
                data-delete-size="${escapeHtml(m.size_human)}"
                title="Delete model"
              >
                Delete
              </button>
            </div>
          `
          )
          .join("");
      }
    }
  }

  async function selectPreset(presetId) {
    const preset = (state.settings.presets || []).find((p) => p.id === presetId);
    if (!preset) return;

    await saveSettings({
      active_preset: preset.id,
      temperature: preset.temperature,
      num_ctx: preset.num_ctx,
      system_prompt: preset.system_prompt,
    });
    renderAdvancedPanel();
    showToast(`Loaded "${preset.name}" preset`);
  }

  async function saveNewPreset() {
    const nameInput = $("new-preset-name");
    const name = (nameInput.value || "").trim();
    if (!name) {
      showToast("Please enter a name for your preset", true);
      nameInput.focus();
      return;
    }

    const temperature = parseFloat($("adv-temperature").value);
    const num_ctx = parseInt($("adv-num-ctx").value, 10);
    const system_prompt = $("adv-system-prompt").value.trim();

    try {
      const resp = await fetch("/api/presets/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          temperature,
          num_ctx,
          system_prompt,
        }),
      });
      if (resp.ok) {
        state.settings = await resp.json();
        nameInput.value = "";
        renderAdvancedPanel();
        showToast(`Saved preset "${name}"`);
      }
    } catch (err) {
      showToast("Failed to save preset: " + err.message, true);
    }
  }

  async function deleteActiveCustomPreset() {
    const activeId = state.settings.active_preset;
    const preset = (state.settings.presets || []).find((p) => p.id === activeId);
    if (!preset || preset.builtin) return;

    const resp = await fetch("/api/presets/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: activeId }),
    });
    if (resp.ok) {
      state.settings = await resp.json();
      renderAdvancedPanel();
      showToast(`Deleted preset "${preset.name}"`);
    }
  }

  // =========================================================================
  // Document Scoping, Upload (PDF, DOCX, TXT), & File Manager
  // =========================================================================

  function getChatTitleById(chatId) {
    if (!chatId) return "Current Chat";
    if (state.currentChat && state.currentChat.id === chatId) {
      return state.currentChat.title || "Current Chat";
    }
    const found = (state.chats || []).find((c) => c.id === chatId);
    return found ? found.title : "Saved Chat";
  }

  function getApplicableDocumentsForCurrentChat() {
    const docs = state.documents || [];
    const currentChatId = state.currentChat ? state.currentChat.id : null;
    const globalDocs = [];
    const chatDocs = [];

    docs.forEach((d) => {
      const scope = d.scope || "global";
      if (scope === "global") {
        globalDocs.push(d);
      } else if (scope === "chat" && currentChatId && d.chat_id === currentChatId) {
        chatDocs.push(d);
      }
    });

    return {
      globalDocs,
      chatDocs,
      allApplicable: [...chatDocs, ...globalDocs],
    };
  }

  function setSidebarUploadScope(scope) {
    state.sidebarUploadScope = scope === "chat" ? "chat" : "global";
    const btnGlobal = $("btn-sidebar-scope-global");
    const btnChat = $("btn-sidebar-scope-chat");
    if (btnGlobal && btnChat) {
      btnGlobal.classList.toggle("active", state.sidebarUploadScope === "global");
      btnChat.classList.toggle("active", state.sidebarUploadScope === "chat");
    }
    const hint = $("drop-zone-scope-hint");
    if (hint) {
      hint.textContent =
        state.sidebarUploadScope === "chat"
          ? "PDF, DOCX, TXT, PNG, JPG • Only in this chat"
          : "PDF, DOCX, TXT, PNG, JPG • Available in all chats";
    }
  }

  function renderReadModeBadge(d) {
    const rm = d.read_mode || "text";
    const label = d.read_mode_label || (rm === "ocr" ? "OCR" : rm === "vision" ? "Vision" : "Text extracted");
    return `<span class="doc-read-mode-badge mode-${escapeHtml(rm)}" title="Read method: ${escapeHtml(label)}">${escapeHtml(label)}</span>`;
  }

  function renderDocumentsList() {
    const docs = state.documents || [];
    $("doc-count-badge").textContent = String(docs.length);

    const listEl = $("documents-list");
    if (!listEl) return;

    if (docs.length === 0) {
      listEl.innerHTML = `<div class="doc-empty-note">No study documents uploaded yet</div>`;
      return;
    }

    listEl.innerHTML = docs
      .map((d) => {
        const scope = d.scope || "global";
        const isChatScoped = scope === "chat" && d.chat_id;
        const chatName = isChatScoped ? getChatTitleById(d.chat_id) : "";

        const badgeHtml = isChatScoped
          ? `<span class="doc-scope-badge scope-chat" title="Only used in chat: ${escapeHtml(chatName)}">Private to: ${escapeHtml(chatName)}</span>`
          : `<span class="doc-scope-badge scope-global" title="Available across every chat">Available in all chats</span>`;

        const readBadgeHtml = renderReadModeBadge(d);
        const toggleActionText = isChatScoped ? "Make global" : "Only this chat";
        const nextScope = isChatScoped ? "global" : "chat";

        return `
          <div class="doc-item" title="${escapeHtml(d.filename)} (${d.num_chunks} chunks)">
            <div class="doc-item-top">
              <div class="doc-item-info">
                <div class="doc-item-name">${escapeHtml(d.filename)}</div>
                <div class="doc-item-meta">
                  ${escapeHtml(d.file_type)} • ${d.num_sections} section${d.num_sections > 1 ? "s" : ""} • ${escapeHtml(d.file_size_human)}
                </div>
              </div>
              <div class="doc-item-actions">
                <button
                  type="button"
                  class="icon-btn-sm"
                  data-reupload-doc="${escapeHtml(d.id)}"
                  title="Re-upload / replace this document"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="23 4 23 10 17 10"></polyline>
                    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                  </svg>
                </button>
                <button
                  type="button"
                  class="icon-btn-sm"
                  data-remove-doc="${escapeHtml(d.id)}"
                  data-remove-name="${escapeHtml(d.filename)}"
                  title="Remove document"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="3 6 5 6 21 6"></polyline>
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                  </svg>
                </button>
              </div>
            </div>
            <div class="doc-item-scope-row">
              <div style="display:flex; align-items:center; gap:4px; min-width:0; flex-wrap:wrap;">
                ${badgeHtml}
                ${readBadgeHtml}
              </div>
              <button
                type="button"
                class="btn-flip-scope"
                data-toggle-doc-scope="${escapeHtml(d.id)}"
                data-target-scope="${nextScope}"
                title="Switch between 'Available in all chats' and 'Only in this chat'"
              >
                ${toggleActionText}
              </button>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderChatDocumentPanel() {
    const statusSpan = $("composer-doc-status");
    const pillsSpan = $("composer-doc-pills");
    const drawerList = $("chat-scoped-docs-list");
    if (!statusSpan || !pillsSpan || !drawerList) return;

    const { globalDocs, chatDocs, allApplicable } = getApplicableDocumentsForCurrentChat();
    const totalApplicable = allApplicable.length;

    if (totalApplicable === 0) {
      const totalAny = (state.documents || []).length;
      if (totalAny > 0) {
        statusSpan.textContent = `No documents active in this chat (${totalAny} private to other chats)`;
      } else {
        statusSpan.textContent = "Optional: upload a PDF, DOCX, TXT, or Image to study with files";
      }
      pillsSpan.innerHTML = "";
      drawerList.innerHTML = `
        <div class="doc-empty-note">
          No documents are active in this chat yet. Files are optional — you can ask Fellow anything right away, or click <strong>+ Upload to This Chat</strong> to add a file scoped only to this chat.
        </div>
      `;
      return;
    }

    if (!state.settings.use_documents) {
      statusSpan.textContent = `${totalApplicable} document${totalApplicable > 1 ? "s" : ""} available (Document search paused in settings)`;
      pillsSpan.innerHTML = "";
    } else {
      statusSpan.textContent = `Active in this chat (${totalApplicable}):`;
      const pillParts = [];
      if (chatDocs.length > 0) {
        pillParts.push(
          `<span class="doc-scope-badge scope-chat">${chatDocs.length} only in this chat</span>`
        );
      }
      if (globalDocs.length > 0) {
        pillParts.push(
          `<span class="doc-scope-badge scope-global">${globalDocs.length} in all chats</span>`
        );
      }
      pillsSpan.innerHTML = pillParts.join("");
    }

    // Populate the expandable Chat Document Scoping Drawer
    drawerList.innerHTML = allApplicable
      .map((d) => {
        const isChatOnly = d.scope === "chat";
        const readBadgeHtml = renderReadModeBadge(d);
        return `
          <div class="chat-doc-row">
            <div class="chat-doc-row-left">
              <span class="doc-scope-badge ${isChatOnly ? "scope-chat" : "scope-global"}">
                ${isChatOnly ? "Only in this chat" : "Available in all chats"}
              </span>
              ${readBadgeHtml}
              <span class="chat-doc-row-name" title="${escapeHtml(d.filename)}">${escapeHtml(d.filename)}</span>
            </div>
            <div class="chat-doc-row-right">
              <button
                type="button"
                class="btn btn-subtle btn-xs"
                data-toggle-doc-scope="${escapeHtml(d.id)}"
                data-target-scope="${isChatOnly ? "global" : "chat"}"
                title="Click to switch scope with one click"
              >
                Switch to "${isChatOnly ? "Available in all chats" : "Only in this chat"}"
              </button>
              <button
                type="button"
                class="icon-btn-sm"
                data-remove-doc="${escapeHtml(d.id)}"
                data-remove-name="${escapeHtml(d.filename)}"
                title="Remove document"
              >
                ✕
              </button>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderInlineOcrDownloadProgress(modelName, dlTask) {
    const statusArea = $("upload-status-area");
    if (!statusArea) return;
    statusArea.classList.remove("hidden");
    const boxHtml = renderDownloadControlBox(modelName, modelName, dlTask);
    statusArea.innerHTML = `
      <div class="scanned-choice-card">
        <div class="scanned-choice-title">Downloading ${escapeHtml(modelName)}</div>
        <div class="scanned-choice-desc">Fellow will automatically switch to this model once the download finishes.</div>
        ${boxHtml}
      </div>
    `;
  }

  function renderOcrRecommendationHtml(rec, hasPendingScanned) {
    if (!rec || rec.active) return "";
    const btnText = rec.installed
      ? `Switch to ${rec.model}`
      : `Download & Switch to ${rec.model}`;
    return `
      <div class="scanned-choice-card" data-ocr-rec-card="1">
        <div class="scanned-choice-title">Recommended for Scans &amp; Pictures</div>
        <div class="scanned-choice-desc">${escapeHtml(rec.message)}</div>
        <div class="scanned-choice-actions">
          <button
            type="button"
            class="btn btn-primary btn-xs"
            data-accept-ocr-model="${escapeHtml(rec.model)}"
          >
            ${escapeHtml(btnText)}
          </button>
          ${
            !hasPendingScanned
              ? `<button type="button" class="btn btn-subtle btn-xs" data-dismiss-ocr-rec="1">Keep Current Model</button>`
              : ""
          }
        </div>
      </div>
    `;
  }

  async function acceptOcrModelRecommendation(modelName) {
    // Process any pending scanned PDFs visually so they're ready as soon as the model is switched
    const pendingToProcess = [...(state.pendingScannedList || [])];
    state.pendingScannedList = [];

    for (const s of pendingToProcess) {
      try {
        const resp = await fetch("/api/documents/process-scanned", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ staging_id: s.staging_id, mode: "vision" }),
        });
        const data = await resp.json();
        if (resp.ok && data.documents) {
          state.documents = data.documents;
          renderDocumentsList();
          renderChatDocumentPanel();
        }
      } catch (_) {}
    }

    const installedSet = new Set(
      (state.ollama.installed_models || []).flatMap((m) => [
        m.name.toLowerCase(),
        m.clean_name.toLowerCase(),
      ])
    );

    if (installedSet.has(modelName.toLowerCase())) {
      await saveSettings({ selected_model: modelName });
      await refreshModelsState();
      const statusArea = $("upload-status-area");
      if (statusArea) {
        statusArea.classList.add("hidden");
        statusArea.innerHTML = "";
      }
      showToast(`Switched active model to ${modelName}`);
      return;
    }

    state.autoSwitchOnCompleteModel = modelName;
    await triggerModelPull(modelName, true);
    renderInlineOcrDownloadProgress(modelName, state.downloads[modelName]);
  }

  function renderScannedChoiceHtml(pendingList) {
    return pendingList
      .map(
        (s) => `
        <div class="scanned-choice-card" data-staging-card="${escapeHtml(s.staging_id)}">
          <div class="scanned-choice-title">Scanned PDF Detected: ${escapeHtml(s.filename)} (${s.num_pages} page${s.num_pages > 1 ? "s" : ""})</div>
          <div class="scanned-choice-desc">${escapeHtml(s.prompt_message)}</div>
          <div class="scanned-choice-actions">
            <button
              type="button"
              class="btn btn-primary btn-xs"
              data-process-scanned="${escapeHtml(s.staging_id)}"
              data-scanned-mode="ocr"
            >
              Process with OCR (works with any model)
            </button>
            <button
              type="button"
              class="btn btn-secondary btn-xs"
              data-process-scanned="${escapeHtml(s.staging_id)}"
              data-scanned-mode="vision"
            >
              Re-read visually with Current Vision Model
            </button>
            <button
              type="button"
              class="btn btn-subtle btn-xs"
              data-cancel-scanned="${escapeHtml(s.staging_id)}"
            >
              Cancel
            </button>
          </div>
        </div>
      `
      )
      .join("");
  }

  async function processPendingScanned(stagingId, mode) {
    state.pendingScannedList = (state.pendingScannedList || []).filter(
      (s) => s.staging_id !== stagingId
    );
    const statusArea = $("upload-status-area");
    if (statusArea) {
      statusArea.classList.remove("hidden");
      statusArea.innerHTML = `
        <div class="thinking-main-row">
          <div class="thinking-dots"><span></span><span></span><span></span></div>
          <span>${mode === "vision" ? "Preparing visual pages for Vision model..." : "Running OCR text extraction..."}</span>
        </div>
      `;
    }
    try {
      const resp = await fetch("/api/documents/process-scanned", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ staging_id: stagingId, mode }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        statusArea.innerHTML = `
          <div class="scanned-pdf-alert">
            <strong>Could not process scanned PDF</strong>
            ${escapeHtml(data.error || "Processing failed")}
          </div>
        `;
        return;
      }
      if (data.documents) {
        state.documents = data.documents;
        renderDocumentsList();
        renderChatDocumentPanel();
      }
      statusArea.classList.add("hidden");
      statusArea.innerHTML = "";

      const rec = (data.uploaded || [])[0];
      if (rec) {
        showToast(`Processed "${rec.filename}" using ${rec.read_mode_label || mode.toUpperCase()}`);
      }
    } catch (err) {
      statusArea.innerHTML = `
        <div class="scanned-pdf-alert">
          <strong>Processing error</strong>
          ${escapeHtml(err.message)}
        </div>
      `;
    }
  }

  async function cancelPendingScanned(stagingId) {
    state.pendingScannedList = (state.pendingScannedList || []).filter(
      (s) => s.staging_id !== stagingId
    );
    try {
      await fetch("/api/documents/cancel-scanned", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ staging_id: stagingId }),
      });
    } catch (_) {}
    const statusArea = $("upload-status-area");
    if (statusArea) {
      statusArea.classList.add("hidden");
      statusArea.innerHTML = "";
    }
  }

  async function uploadFiles(fileList, { replaceDocId = null, forcedScope = null, fromChat = false } = {}) {
    if (!fileList || fileList.length === 0) return;

    const statusArea = $("upload-status-area");
    statusArea.classList.remove("hidden");
    statusArea.innerHTML = `
      <div class="thinking-main-row">
        <div class="thinking-dots"><span></span><span></span><span></span></div>
        <span>Fellow is reading and indexing your document...</span>
      </div>
    `;

    // When uploading directly from inside an active chat composer, default to "chat" ("Only in this chat")
    const targetScope = forcedScope || (fromChat ? "chat" : state.sidebarUploadScope);
    const activeChatId = state.currentChat ? state.currentChat.id : "";

    const formData = new FormData();
    Array.from(fileList).forEach((f) => formData.append("files", f));
    if (replaceDocId) {
      formData.append("replace_doc_id", replaceDocId);
    }
    if (!replaceDocId && targetScope) {
      formData.append("scope", targetScope);
      if (targetScope === "chat" && activeChatId) {
        formData.append("chat_id", activeChatId);
      }
    }

    try {
      const resp = await fetch("/api/documents/upload", {
        method: "POST",
        body: formData,
      });
      const data = await resp.json();

      if (data.created_chat) {
        state.currentChat = data.created_chat;
        state.chats = data.chats || state.chats;
        renderChatsList();
        renderCurrentChatMessages();
      }

      if (data.documents) {
        state.documents = data.documents;
        renderDocumentsList();
        renderChatDocumentPanel();
      }

      const errors = data.errors || [];
      const pendingScanned = data.pending_scanned || [];
      const uploaded = data.uploaded || [];
      const ocrRec = data.ocr_model_recommendation || null;

      state.pendingScannedList = pendingScanned;

      const showRec = Boolean(ocrRec && !ocrRec.active);
      if (showRec || pendingScanned.length > 0 || errors.length > 0) {
        const recHtml = showRec ? renderOcrRecommendationHtml(ocrRec, pendingScanned.length > 0) : "";
        const pendingHtml = renderScannedChoiceHtml(pendingScanned);
        const errHtml = errors
          .map(
            (err) => `
            <div class="scanned-pdf-alert">
              <strong>Could not read ${escapeHtml(err.filename)}</strong>
              ${escapeHtml(err.message)}
            </div>
          `
          )
          .join("");
        statusArea.innerHTML = recHtml + pendingHtml + errHtml;
      } else {
        statusArea.classList.add("hidden");
        statusArea.innerHTML = "";
      }

      if (uploaded.length > 0) {
        const names = uploaded.map((u) => u.filename).join(", ");
        const firstScope = uploaded[0].scope || "global";
        const scopeDesc =
          firstScope === "chat" ? "Only in this chat" : "Available in all chats";
        const readModeLabel = uploaded[0].read_mode_label ? ` • ${uploaded[0].read_mode_label}` : "";

        if (fromChat) {
          $("chat-doc-drawer").classList.remove("hidden");
        }

        showToast(
          replaceDocId
            ? `Updated "${names}" in Fellow's library`
            : `Added "${names}" (${scopeDesc}${readModeLabel})`
        );
      }
    } catch (err) {
      statusArea.innerHTML = `
        <div class="scanned-pdf-alert">
          <strong>Upload failed</strong>
          ${escapeHtml(err.message)}
        </div>
      `;
    }
  }

  async function toggleDocumentScope(docId, targetScope) {
    const activeChatId = state.currentChat ? state.currentChat.id : null;
    try {
      const resp = await fetch(`/api/documents/${encodeURIComponent(docId)}/scope`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope: targetScope,
          chat_id: activeChatId,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        showToast(data.error || "Could not update document scope", true);
        return;
      }

      if (data.created_chat) {
        state.currentChat = data.created_chat;
        state.chats = data.chats || state.chats;
        renderChatsList();
        renderCurrentChatMessages();
      }

      state.documents = data.documents || state.documents;
      renderDocumentsList();
      renderChatDocumentPanel();

      const updatedDoc = data.document;
      if (updatedDoc) {
        const label =
          updatedDoc.scope === "chat"
            ? `Only in this chat (${getChatTitleById(updatedDoc.chat_id)})`
            : "Available in all chats";
        showToast(`"${updatedDoc.filename}" is now set to: ${label}`);
      }
    } catch (err) {
      showToast("Failed to update document scope: " + err.message, true);
    }
  }

  async function removeDocument(docId, filename) {
    try {
      const resp = await fetch(`/api/documents/${encodeURIComponent(docId)}`, {
        method: "DELETE",
      });
      const data = await resp.json();
      if (resp.ok) {
        state.documents = data.documents || [];
        renderDocumentsList();
        renderChatDocumentPanel();
        showToast(`Removed "${filename}"`);
      }
    } catch (err) {
      showToast("Could not remove document: " + err.message, true);
    }
  }

  // =========================================================================
  // Saved Chats Management (Create, Select, Rename, Delete, Export .md)
  // =========================================================================

  function renderChatsList() {
    const listEl = $("chats-list");
    if (!listEl) return;

    const chats = state.chats || [];
    if (chats.length === 0) {
      listEl.innerHTML = `<div class="doc-empty-note">No saved chats yet</div>`;
      return;
    }

    const activeId = state.currentChat ? state.currentChat.id : null;
    listEl.innerHTML = chats
      .map((c) => {
        const isActive = c.id === activeId;
        return `
          <div class="chat-item ${isActive ? "active" : ""}" data-select-chat="${escapeHtml(c.id)}">
            <span class="chat-item-title" title="${escapeHtml(c.title)}">${escapeHtml(c.title)}</span>
            <div class="chat-item-actions">
              <button
                type="button"
                class="icon-btn-sm"
                data-rename-chat="${escapeHtml(c.id)}"
                data-chat-title="${escapeHtml(c.title)}"
                title="Rename chat"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M12 20h9"></path>
                  <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
                </svg>
              </button>
              <button
                type="button"
                class="icon-btn-sm"
                data-export-chat="${escapeHtml(c.id)}"
                title="Export chat to Markdown (.md)"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                  <polyline points="7 10 12 15 17 10"></polyline>
                  <line x1="12" y1="15" x2="12" y2="3"></line>
                </svg>
              </button>
              <button
                type="button"
                class="icon-btn-sm"
                data-delete-chat="${escapeHtml(c.id)}"
                data-chat-title="${escapeHtml(c.title)}"
                title="Delete chat"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="3 6 5 6 21 6"></polyline>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
              </button>
            </div>
          </div>
        `;
      })
      .join("");
  }

  async function createNewChat() {
    try {
      const resp = await fetch("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New Study Session" }),
      });
      const data = await resp.json();
      state.currentChat = data.chat;
      state.editingMessageIndex = null;
      state.chats = data.chats || [];
      renderChatsList();
      renderDocumentsList();
      renderChatDocumentPanel();
      renderCurrentChatMessages();
      $("chat-input").focus();
    } catch (err) {
      showToast("Could not create new chat: " + err.message, true);
    }
  }

  async function selectChat(chatId) {
    try {
      const resp = await fetch(`/api/chats/${encodeURIComponent(chatId)}`);
      if (!resp.ok) return;
      const data = await resp.json();
      state.currentChat = data.chat;
      state.editingMessageIndex = null;
      renderChatsList();
      renderDocumentsList();
      renderChatDocumentPanel();
      renderCurrentChatMessages();
    } catch (_) {}
  }

  function promptRenameChat(chatId, currentTitle) {
    openPromptDialog({
      title: "Rename Study Chat",
      message: "Give this study conversation a memorable title:",
      initialValue: currentTitle || "",
      confirmText: "Save Title",
      onConfirm: async (newTitle) => {
        if (!newTitle.trim()) return;
        const resp = await fetch(`/api/chats/${encodeURIComponent(chatId)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: newTitle.trim() }),
        });
        if (resp.ok) {
          const data = await resp.json();
          state.chats = data.chats || [];
          if (state.currentChat && state.currentChat.id === chatId) {
            state.currentChat.title = data.chat.title;
            $("current-chat-title").textContent = data.chat.title;
          }
          renderChatsList();
          renderDocumentsList();
          renderChatDocumentPanel();
        }
      },
    });
  }

  function promptDeleteChat(chatId, chatTitle) {
    openConfirmDialog({
      title: "Delete Chat?",
      message: `Are you sure you want to delete "${chatTitle}"? This conversation cannot be recovered.`,
      confirmText: "Delete Chat",
      danger: true,
      onConfirm: async () => {
        const resp = await fetch(`/api/chats/${encodeURIComponent(chatId)}`, {
          method: "DELETE",
        });
        if (resp.ok) {
          const data = await resp.json();
          state.chats = data.chats || [];
          if (state.currentChat && state.currentChat.id === chatId) {
            if (state.chats.length > 0) {
              await selectChat(state.chats[0].id);
            } else {
              state.currentChat = null;
              renderCurrentChatMessages();
            }
          }
          renderChatsList();
          renderDocumentsList();
          renderChatDocumentPanel();
          showToast("Chat deleted");
        }
      },
    });
  }

  async function exportChatToMarkdown(chatId) {
    const targetId = chatId || (state.currentChat && state.currentChat.id);
    if (!targetId) {
      showToast("Start a conversation first before exporting to Markdown.", true);
      return;
    }
    try {
      const resp = await fetch(`/api/chats/${encodeURIComponent(targetId)}/save-markdown`, {
        method: "POST",
      });
      const data = await resp.json();
      if (!resp.ok) {
        showToast(data.error || "Failed to export Markdown", true);
        return;
      }
      if (data.cancelled) return;
      if (data.success) {
        showToast(`Saved "${data.filename}" to ${data.saved_path}`);
      }
    } catch (err) {
      showToast("Export failed: " + err.message, true);
    }
  }

  // =========================================================================
  // Chat Message Rendering & Streaming RAG Generation
  // =========================================================================

  function renderCurrentChatMessages(shouldScroll = true) {
    const titleEl = $("current-chat-title");
    const emptyEl = $("chat-empty-state");
    const messagesEl = $("chat-messages");

    if (!state.currentChat || (state.currentChat.messages || []).length === 0) {
      titleEl.textContent = state.currentChat ? state.currentChat.title : "New Study Session";
      emptyEl.classList.remove("hidden");
      messagesEl.innerHTML = "";
      return;
    }

    titleEl.textContent = state.currentChat.title || "Study Session";
    emptyEl.classList.add("hidden");

    messagesEl.innerHTML = state.currentChat.messages
      .map((msg, idx) => renderSingleMessageHtml(msg, idx))
      .join("");

    if (state.editingMessageIndex !== null) {
      const editTa = $("active-edit-textarea");
      if (editTa) {
        editTa.style.height = "auto";
        editTa.style.height = Math.min(240, Math.max(78, editTa.scrollHeight)) + "px";
        editTa.focus();
        editTa.setSelectionRange(editTa.value.length, editTa.value.length);
      }
    } else if (shouldScroll) {
      scrollToBottom();
    }
  }

  function renderSingleMessageHtml(msg, idx) {
    const isUser = msg.role === "user";
    const msgId = msg.id || `msg-${idx}`;

    if (isUser) {
      if (state.editingMessageIndex === idx) {
        return `
          <div class="msg-row msg-user" data-msg-index="${idx}" data-msg-id="${escapeHtml(msgId)}">
            <div class="msg-author-bar">
              <span>Editing your message</span>
            </div>
            <div class="msg-edit-box">
              <textarea
                id="active-edit-textarea"
                class="msg-edit-textarea"
                rows="3"
                aria-label="Edit message"
                data-edit-index="${idx}"
                data-edit-id="${escapeHtml(msgId)}"
              >${escapeHtml(msg.content)}</textarea>
              <div class="msg-edit-footer">
                <span class="msg-edit-hint">Saving will replace responses below with a fresh answer</span>
                <div class="msg-edit-actions">
                  <button
                    type="button"
                    class="btn btn-subtle btn-sm"
                    data-cancel-edit-msg="${idx}"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    class="btn btn-primary btn-sm"
                    data-save-edit-msg="${idx}"
                    data-edit-msg-id="${escapeHtml(msgId)}"
                  >
                    Save &amp; Resend
                  </button>
                </div>
              </div>
            </div>
          </div>
        `;
      }

      return `
        <div class="msg-row msg-user" data-msg-index="${idx}" data-msg-id="${escapeHtml(msgId)}">
          <div class="msg-author-bar">
            <span>You</span>
            <span>•</span>
            <span>${escapeHtml(msg.timestamp || "")}</span>
          </div>
          <div class="msg-user-body-wrap">
            <button
              type="button"
              class="msg-edit-btn"
              data-start-edit-msg="${idx}"
              title="Edit and resend this message"
              aria-label="Edit message"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <path d="M12 20h9"></path>
                <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
              </svg>
              <span>Edit</span>
            </button>
            <div class="msg-bubble">${escapeHtml(msg.content)}</div>
          </div>
        </div>
      `;
    }

    const renderedMd = window.FellowMarkdown.render(msg.content || "");
    const sourcesHtml = renderSourcesHtml(msg.sources || []);
    const modelBadge =
      state.settings.mode === "advanced" && msg.model
        ? `<span class="curated-card-tag">(${escapeHtml(msg.model)})</span>`
        : "";

    return `
      <div class="msg-row msg-fellow" data-msg-index="${idx}" data-msg-id="${escapeHtml(msgId)}">
        <div class="msg-author-bar">
          <span class="msg-fellow-avatar">
            <img src="/assets/logo.png" alt="Fellow" class="fellow-logo-img" />
          </span>
          <span>Fellow</span>
          ${modelBadge}
          <span>•</span>
          <span>${escapeHtml(msg.timestamp || "")}</span>
        </div>
        <div class="msg-bubble">
          <div class="msg-markdown-body">${renderedMd}</div>
          ${sourcesHtml}
        </div>
      </div>
    `;
  }

  function renderSourcesHtml(sources) {
    if (!sources || sources.length === 0) return "";

    const isAdvanced = state.settings.mode === "advanced";

    const uniqueMap = new Map();
    sources.forEach((s, idx) => {
      const key = `${s.filename}::${s.section}`;
      if (!uniqueMap.has(key)) {
        uniqueMap.set(key, { ...s, idx });
      }
    });

    const chipsHtml = Array.from(uniqueMap.values())
      .map(
        (s) => `
        <button
          type="button"
          class="source-chip"
          data-preview-source="${encodeURIComponent(JSON.stringify(s))}"
          title="Click to view passage from ${escapeHtml(s.filename)}"
        >
          📄 ${escapeHtml(s.filename)} (${escapeHtml(s.section)})
        </button>
      `
      )
      .join("");

    let inspectorHtml = "";
    if (isAdvanced) {
      const chunkCards = sources
        .map(
          (s, i) => `
          <div class="chunk-card">
            <div class="chunk-card-header">
              <span>Chunk #${i + 1}: ${escapeHtml(s.filename)} — ${escapeHtml(s.section)}</span>
              ${s.score_percent ? `<span class="chunk-score-badge">${s.score_percent}% match</span>` : ""}
            </div>
            <div class="chunk-card-text">${escapeHtml(s.text)}</div>
          </div>
        `
        )
        .join("");

      inspectorHtml = `
        <details class="chunks-inspector-details" open>
          <summary>Retrieved Document Chunks Used (${sources.length})</summary>
          <div class="chunks-inspector-list">${chunkCards}</div>
        </details>
      `;
    }

    return `
      <div class="msg-sources-area">
        <div class="sources-simple-bar">
          <span>Drawn from:</span>
          ${chipsHtml}
        </div>
        ${inspectorHtml}
      </div>
    `;
  }

  async function sendChatMessage(rawText, editOptions = null) {
    const message = (rawText || "").trim();
    if (!message) return;

    state.editingMessageIndex = null;

    if (!state.currentChat) {
      const resp = await fetch("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New Study Session" }),
      });
      const data = await resp.json();
      state.currentChat = data.chat;
      state.chats = data.chats || [];
    }

    const chatId = state.currentChat.id;
    if (!editOptions) {
      const inputEl = $("chat-input");
      inputEl.value = "";
      inputEl.style.height = "auto";
    }

    $("chat-empty-state").classList.add("hidden");

    // If editing an earlier message, remove that message and all messages after it locally first
    if (editOptions && typeof editOptions.editMessageIndex === "number") {
      state.currentChat.messages = (state.currentChat.messages || []).slice(
        0,
        editOptions.editMessageIndex
      );
    }

    const nowStr = new Date().toTimeString().slice(0, 5);
    const userMsg = {
      id: (editOptions && editOptions.editMessageId) || `msg-${Date.now()}`,
      role: "user",
      content: message,
      timestamp: nowStr,
    };
    state.currentChat.messages.push(userMsg);
    renderCurrentChatMessages();

    const messagesEl = $("chat-messages");
    const streamRow = document.createElement("div");
    streamRow.className = "msg-row msg-fellow";
    streamRow.innerHTML = `
      <div class="msg-author-bar">
        <span class="msg-fellow-avatar">
          <img src="/assets/logo.png" alt="Fellow" class="fellow-logo-img" />
        </span>
        <span>Fellow</span>
        <span>•</span>
        <span>Now</span>
      </div>
      <div class="msg-bubble">
        <div id="live-thinking-box" class="thinking-box">
          <div class="thinking-main-row">
            <div class="thinking-dots"><span></span><span></span><span></span></div>
            <span id="live-thinking-label">Fellow is thinking...</span>
          </div>
          <div id="live-slow-device-note" class="thinking-slow-note hidden">
            Still thinking, this can take a bit longer on your device. Fellow is working offline right on your computer.
          </div>
        </div>
        <div id="live-markdown-target" class="msg-markdown-body"></div>
        <div id="live-sources-target"></div>
      </div>
    `;
    messagesEl.appendChild(streamRow);
    scrollToBottom();

    const slowTimer = setTimeout(() => {
      const slowNote = $("live-slow-device-note");
      if (slowNote) slowNote.classList.remove("hidden");
    }, 5500);

    $("btn-send-msg").classList.add("hidden");
    $("btn-stop-stream").classList.remove("hidden");

    const abortController = new AbortController();
    state.activeStreamController = abortController;

    let accumulatedText = "";
    let retrievedSources = [];
    let syncedFromServerDone = false;
    let hadStreamError = false;

    const reqPayload = {
      message,
      model: state.settings.selected_model,
      temperature: state.settings.temperature,
      num_ctx: state.settings.num_ctx,
      system_prompt: state.settings.system_prompt,
      use_documents: state.settings.use_documents,
    };
    if (editOptions) {
      if (editOptions.editMessageId) {
        reqPayload.edit_message_id = editOptions.editMessageId;
      }
      if (typeof editOptions.editMessageIndex === "number") {
        reqPayload.edit_message_index = editOptions.editMessageIndex;
      }
    }

    try {
      const resp = await fetch(`/api/chats/${encodeURIComponent(chatId)}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reqPayload),
        signal: abortController.signal,
      });

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const block of parts) {
          const line = block.trim();
          if (!line.startsWith("data:")) continue;
          const jsonStr = line.slice(5).trim();
          if (!jsonStr) continue;

          let ev;
          try {
            ev = JSON.parse(jsonStr);
          } catch (_) {
            continue;
          }

          if (ev.type === "sources") {
            retrievedSources = ev.sources || [];
            if (ev.chat_title && state.currentChat) {
              state.currentChat.title = ev.chat_title;
              $("current-chat-title").textContent = ev.chat_title;
              renderDocumentsList();
            }
            if (retrievedSources.length > 0) {
              const lbl = $("live-thinking-label");
              if (lbl) {
                lbl.textContent = `Fellow is reading ${retrievedSources.length} passage${retrievedSources.length > 1 ? "s" : ""} from your documents and thinking...`;
              }
              const srcTarget = $("live-sources-target");
              if (srcTarget) {
                srcTarget.innerHTML = renderSourcesHtml(retrievedSources);
              }
            }
          } else if (ev.type === "thinking") {
            const lbl = $("live-thinking-label");
            if (lbl && !accumulatedText) {
              lbl.textContent = "Fellow is reasoning through your question...";
            }
          } else if (ev.type === "token") {
            accumulatedText += ev.content || "";
            const thinkingBox = $("live-thinking-box");
            if (thinkingBox && accumulatedText.trim().length > 0) {
              thinkingBox.classList.add("hidden");
            }
            const mdTarget = $("live-markdown-target");
            if (mdTarget) {
              mdTarget.innerHTML = window.FellowMarkdown.render(accumulatedText);
            }
            scrollToBottom();
          } else if (ev.type === "replace") {
            accumulatedText = ev.content || "";
            const thinkingBox = $("live-thinking-box");
            if (thinkingBox) thinkingBox.classList.add("hidden");
            const mdTarget = $("live-markdown-target");
            if (mdTarget) {
              mdTarget.innerHTML = window.FellowMarkdown.render(accumulatedText);
            }
            scrollToBottom();
          } else if (ev.type === "error") {
            hadStreamError = true;
            const mdTarget = $("live-markdown-target");
            const thinkingBox = $("live-thinking-box");
            if (thinkingBox) thinkingBox.classList.add("hidden");
            if (mdTarget) {
              mdTarget.innerHTML = `
                <div class="scanned-pdf-alert">
                  <strong>Fellow couldn't finish responding</strong>
                  ${escapeHtml(ev.error)}
                </div>
              `;
            }
          } else if (ev.type === "done") {
            if (ev.chat) {
              state.currentChat = ev.chat;
              syncedFromServerDone = true;
            }
            if (ev.chats) {
              state.chats = ev.chats;
              renderChatsList();
              renderDocumentsList();
            }
          }
        }
      }

      if (syncedFromServerDone && !hadStreamError && accumulatedText.trim().length > 0) {
        renderCurrentChatMessages(false);
      } else if (accumulatedText.trim()) {
        if (!hadStreamError) {
          state.currentChat.messages.push({
            id: `msg-${Date.now()}`,
            role: "assistant",
            content: accumulatedText,
            timestamp: new Date().toTimeString().slice(0, 5),
            model: state.settings.selected_model,
            sources: retrievedSources,
          });
          renderCurrentChatMessages(false);
        }
      } else if (!hadStreamError) {
        const thinkingBox = $("live-thinking-box");
        if (thinkingBox) thinkingBox.classList.add("hidden");
        const mdTarget = $("live-markdown-target");
        if (mdTarget) {
          mdTarget.innerHTML = `
            <div class="scanned-pdf-alert">
              <strong>Fellow couldn't finish responding</strong>
              Fellow didn't receive a response from the model. Please try again.
            </div>
          `;
        }
      }
    } catch (err) {
      if (err.name === "AbortError") {
        if (accumulatedText.trim()) {
          state.currentChat.messages.push({
            role: "assistant",
            content: accumulatedText + "\n\n*(Response stopped)*",
            timestamp: new Date().toTimeString().slice(0, 5),
            model: state.settings.selected_model,
            sources: retrievedSources,
          });
        }
        renderCurrentChatMessages();
      } else {
        showToast("Connection error: " + err.message, true);
      }
    } finally {
      clearTimeout(slowTimer);
      state.activeStreamController = null;
      $("btn-send-msg").classList.remove("hidden");
      $("btn-stop-stream").classList.add("hidden");
      const thinkingBox = $("live-thinking-box");
      if (thinkingBox) thinkingBox.classList.add("hidden");
    }
  }

  function scrollToBottom() {
    const wrap = $("chat-messages-wrap");
    if (wrap) {
      wrap.scrollTop = wrap.scrollHeight;
    }
  }

  // =========================================================================
  // Settings Modal Content & RAM Re-Check
  // =========================================================================

  function renderSettingsModalContent() {
    $("toggle-use-documents").checked = Boolean(state.settings.use_documents ?? true);

    if (state.ramInfo) {
      $("settings-ram-message").textContent = state.ramInfo.plain_message;
      const tiersWrap = $("settings-ram-tiers");
      if (tiersWrap) {
        const currentModel = (state.settings.selected_model || "").toLowerCase();
        tiersWrap.innerHTML = (state.ramInfo.tiers || [])
          .map((t) => {
            const isCurrent = t.model.toLowerCase() === currentModel;
            return `
              <button
                type="button"
                class="btn ${isCurrent ? "btn-primary" : "btn-secondary"} btn-xs"
                data-select-model="${escapeHtml(t.model)}"
              >
                ${escapeHtml(t.tier_label)} (${escapeHtml(t.title)}) ${t.recommended ? "★" : ""}
              </button>
            `;
          })
          .join("");
      }
    }
  }

  async function rerunRamCheck() {
    try {
      const resp = await fetch("/api/ram-check", { method: "POST" });
      if (resp.ok) {
        const data = await resp.json();
        state.ramInfo = data.ram_info;
        state.settings = data.settings || state.settings;
        renderSettingsModalContent();
        renderModelsModalContent();
        renderWelcomeRamStep();
        showToast(state.ramInfo.plain_message);
      }
    } catch (err) {
      showToast("Could not run memory check: " + err.message, true);
    }
  }

  // =========================================================================
  // Dialog Helpers & Toasts
  // =========================================================================

  function openConfirmDialog({ title, message, confirmText = "Confirm", danger = false, onConfirm }) {
    $("dialog-title").textContent = title;
    $("dialog-message").textContent = message;
    $("dialog-input-wrap").classList.add("hidden");
    $("btn-dialog-cancel").classList.remove("hidden");

    const confirmBtn = $("btn-dialog-confirm");
    confirmBtn.textContent = confirmText;
    confirmBtn.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;

    confirmBtn.onclick = async () => {
      $("dialog-modal").classList.add("hidden");
      if (onConfirm) await onConfirm();
    };
    $("dialog-modal").classList.remove("hidden");
  }

  function openPromptDialog({ title, message, initialValue = "", confirmText = "Save", onConfirm }) {
    $("dialog-title").textContent = title;
    $("dialog-message").textContent = message;
    const inputWrap = $("dialog-input-wrap");
    const inputEl = $("dialog-input");
    inputWrap.classList.remove("hidden");
    inputEl.value = initialValue;

    $("btn-dialog-cancel").classList.remove("hidden");
    const confirmBtn = $("btn-dialog-confirm");
    confirmBtn.textContent = confirmText;
    confirmBtn.className = "btn btn-primary";

    confirmBtn.onclick = async () => {
      const val = inputEl.value;
      $("dialog-modal").classList.add("hidden");
      if (onConfirm) await onConfirm(val);
    };
    $("dialog-modal").classList.remove("hidden");
    setTimeout(() => inputEl.focus(), 50);
  }

  function openSourcePreviewDialog(src) {
    $("dialog-title").textContent = `${src.filename} — ${src.section}`;
    $("dialog-message").textContent = src.text || "";
    $("dialog-input-wrap").classList.add("hidden");
    $("btn-dialog-cancel").classList.add("hidden");

    const confirmBtn = $("btn-dialog-confirm");
    confirmBtn.textContent = "Close";
    confirmBtn.className = "btn btn-primary";
    confirmBtn.onclick = () => {
      $("dialog-modal").classList.add("hidden");
    };
    $("dialog-modal").classList.remove("hidden");
  }

  function showToast(message, isError = false) {
    const container = $("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast ${isError ? "toast-error" : ""}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.remove();
    }, 4200);
  }

  // =========================================================================
  // Event Listeners Binding
  // =========================================================================

  function bindEvents() {
    $("btn-recheck-ollama").addEventListener("click", async () => {
      const resp = await fetch("/api/ollama/status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auto_start: true }),
      });
      const status = await resp.json();
      state.ollama = status;
      if (status.running) {
        showToast("Ollama is running and connected!");
        showWelcomeStep("ram");
        renderWelcomeRamStep();
      } else {
        showToast("Ollama isn't responding yet. Make sure the installer finished.", true);
      }
    });

    $("btn-skip-ollama-check").addEventListener("click", () => {
      $("welcome-overlay").classList.add("hidden");
    });

    $("btn-accept-recommendation").addEventListener("click", () => {
      startFirstRunModelSetup();
    });

    $("btn-skip-setup").addEventListener("click", () => {
      completeFirstRun();
    });

    $("btn-finish-welcome").addEventListener("click", () => {
      completeFirstRun();
    });

    $("btn-welcome-background").addEventListener("click", () => {
      completeFirstRun();
    });

    // Mode Toggle (Simple / Advanced)
    $("btn-mode-simple").addEventListener("click", () => setMode("simple"));
    $("btn-mode-advanced").addEventListener("click", () => setMode("advanced"));
    $("btn-settings-mode-simple").addEventListener("click", () => setMode("simple"));
    $("btn-settings-mode-advanced").addEventListener("click", () => setMode("advanced"));

    // Theme Toggle (Light / Dark)
    $("btn-theme-light").addEventListener("click", () => setTheme("light"));
    $("btn-theme-dark").addEventListener("click", () => setTheme("dark"));

    // Sidebar Upload Scope Selector ("Available in all chats" vs "Only in this chat")
    $("btn-sidebar-scope-global").addEventListener("click", () => setSidebarUploadScope("global"));
    $("btn-sidebar-scope-chat").addEventListener("click", () => setSidebarUploadScope("chat"));

    // Chat Document Drawer Toggle
    $("btn-toggle-chat-doc-drawer").addEventListener("click", () => {
      $("chat-doc-drawer").classList.toggle("hidden");
    });
    $("btn-close-chat-doc-drawer").addEventListener("click", () => {
      $("chat-doc-drawer").classList.add("hidden");
    });

    // Model Switcher Dropdown in Chat Header
    $("chat-model-select").addEventListener("change", async (e) => {
      const chosen = e.target.value;
      if (state.activeStreamController) {
        state.activeStreamController.abort();
      }

      const installedNames = (state.ollama.installed_models || []).flatMap((m) => [
        m.name.toLowerCase(),
        m.clean_name.toLowerCase(),
      ]);
      if (!installedNames.includes(chosen.toLowerCase())) {
        // Keep current installed model active until user confirms and download completes
        e.target.value = state.settings.selected_model || "llama3.2:3b";
        openConfirmDialog({
          title: `Download ${chosen}?`,
          message: `The assistant "${chosen}" isn't downloaded on your computer yet. Would you like Fellow to download it now?`,
          confirmText: "Download Now",
          onConfirm: async () => {
            state.autoSwitchOnCompleteModel = chosen;
            $("models-modal").classList.remove("hidden");
            await triggerModelPull(chosen);
          },
        });
      } else {
        await saveSettings({ selected_model: chosen });
        renderModelsModalContent();
        showToast(`Switched active assistant to ${chosen}`);
      }
    });

    // Open Modals (Browse Models, Settings, About)
    const openModelsModal = () => {
      $("models-feedback-banner").classList.add("hidden");
      refreshModelsState();
      $("models-modal").classList.remove("hidden");
    };
    $("btn-sidebar-models").addEventListener("click", openModelsModal);
    $("btn-header-browse-models").addEventListener("click", openModelsModal);
    $("btn-adv-open-models").addEventListener("click", openModelsModal);

    $("btn-sidebar-settings").addEventListener("click", () => {
      renderSettingsModalContent();
      $("settings-modal").classList.remove("hidden");
    });

    $("btn-about-icon").addEventListener("click", () => {
      $("about-modal").classList.remove("hidden");
    });
    $("btn-settings-open-about").addEventListener("click", () => {
      $("settings-modal").classList.add("hidden");
      $("about-modal").classList.remove("hidden");
    });

    $("btn-models-rerun-ram").addEventListener("click", rerunRamCheck);
    $("btn-settings-rerun-ram").addEventListener("click", rerunRamCheck);

    $("toggle-use-documents").addEventListener("change", async (e) => {
      await saveSettings({ use_documents: e.target.checked });
      renderDocumentsList();
      renderChatDocumentPanel();
    });

    $("custom-model-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = $("custom-model-input");
      const modelTag = (input.value || "").trim();
      if (!modelTag) {
        showToast("Please enter an Ollama model tag or hf.co/ reference", true);
        return;
      }
      input.value = "";
      await triggerModelPull(modelTag);
    });

    $("btn-new-chat").addEventListener("click", createNewChat);
    $("btn-rename-current-chat").addEventListener("click", () => {
      if (state.currentChat) {
        promptRenameChat(state.currentChat.id, state.currentChat.title);
      }
    });
    $("current-chat-title").addEventListener("click", () => {
      if (state.currentChat) {
        promptRenameChat(state.currentChat.id, state.currentChat.title);
      }
    });
    $("btn-export-chat").addEventListener("click", () => {
      exportChatToMarkdown(state.currentChat ? state.currentChat.id : null);
    });

    // Document Drag & Drop + File Inputs (Sidebar Global Uploader vs. In-Chat Scoped Uploader)
    const dropZone = $("drop-zone");
    const fileInput = $("file-input");
    const chatFileInput = $("chat-file-input");
    const reuploadInput = $("reupload-file-input");

    dropZone.addEventListener("click", (e) => {
      if (e.target !== fileInput && e.target !== chatFileInput && e.target !== reuploadInput) {
        fileInput.click();
      }
    });

    // Uploading directly from within the active chat defaults scope to "Only in this chat"
    $("btn-attach-file").addEventListener("click", () => chatFileInput.click());
    $("btn-composer-upload").addEventListener("click", () => chatFileInput.click());

    fileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length > 0) {
        uploadFiles(e.target.files, {
          forcedScope: state.sidebarUploadScope,
          fromChat: false,
        });
        e.target.value = "";
      }
    });

    chatFileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length > 0) {
        uploadFiles(e.target.files, {
          forcedScope: "chat",
          fromChat: true,
        });
        e.target.value = "";
      }
    });

    reuploadInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length > 0) {
        uploadFiles(e.target.files, {
          replaceDocId: state.reuploadDocId,
          fromChat: false,
        });
        state.reuploadDocId = null;
        e.target.value = "";
      }
    });

    ["dragenter", "dragover"].forEach((evName) => {
      dropZone.addEventListener(evName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach((evName) => {
      dropZone.addEventListener(evName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.remove("drag-over");
      });
    });
    dropZone.addEventListener("drop", (e) => {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        uploadFiles(e.dataTransfer.files, {
          forcedScope: state.sidebarUploadScope,
          fromChat: false,
        });
      }
    });

    // Chat Composer Form & Auto-growing Textarea
    const chatInput = $("chat-input");
    chatInput.addEventListener("input", () => {
      chatInput.style.height = "auto";
      chatInput.style.height = Math.min(160, chatInput.scrollHeight) + "px";
    });
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        $("chat-form").requestSubmit();
      }
    });

    $("chat-form").addEventListener("submit", (e) => {
      e.preventDefault();
      if (state.activeStreamController) return;
      sendChatMessage(chatInput.value);
    });

    $("btn-stop-stream").addEventListener("click", () => {
      if (state.activeStreamController) {
        state.activeStreamController.abort();
      }
    });

    // Advanced Panel Controls
    $("adv-temperature").addEventListener("input", (e) => {
      $("adv-temperature-val").textContent = Number(e.target.value).toFixed(2);
    });
    $("adv-temperature").addEventListener("change", async (e) => {
      await saveSettings({ temperature: parseFloat(e.target.value) });
    });

    $("adv-num-ctx").addEventListener("change", async (e) => {
      const val = parseInt(e.target.value, 10);
      $("adv-num-ctx-val").textContent = `${val} tokens`;
      await saveSettings({ num_ctx: val });
    });

    $("btn-apply-adv-params").addEventListener("click", async () => {
      await saveSettings({
        temperature: parseFloat($("adv-temperature").value),
        num_ctx: parseInt($("adv-num-ctx").value, 10),
        system_prompt: $("adv-system-prompt").value.trim(),
      });
      const badge = $("adv-saved-indicator");
      badge.classList.remove("hidden");
      setTimeout(() => badge.classList.add("hidden"), 1800);
      showToast("Updated Fellow's parameters");
    });

    $("btn-save-preset").addEventListener("click", saveNewPreset);
    $("btn-delete-preset").addEventListener("click", deleteActiveCustomPreset);

    // Delegated Click Handlers
    document.addEventListener("click", async (e) => {
      const closeBtn = e.target.closest("[data-close-modal]");
      if (closeBtn) {
        const modalId = closeBtn.getAttribute("data-close-modal");
        $(modalId).classList.add("hidden");
        return;
      }

      const starterCard = e.target.closest(".starter-card");
      if (starterCard) {
        const prompt = starterCard.getAttribute("data-prompt");
        if (prompt) sendChatMessage(prompt);
        return;
      }

      const tierCard = e.target.closest("[data-select-tier]");
      if (tierCard) {
        state.welcomeSelectedModel = tierCard.getAttribute("data-select-tier");
        renderWelcomeRamStep();
        return;
      }

      const pullBtn = e.target.closest("[data-pull-model]");
      if (pullBtn) {
        const model = pullBtn.getAttribute("data-pull-model");
        await triggerModelPull(model);
        return;
      }

      const dlBtn = e.target.closest("[data-dl-action]");
      if (dlBtn) {
        const action = dlBtn.getAttribute("data-dl-action");
        const model = dlBtn.getAttribute("data-model");
        await controlDownload(action, model);
        return;
      }

      const selectModelBtn = e.target.closest("[data-select-model]");
      if (selectModelBtn) {
        const model = selectModelBtn.getAttribute("data-select-model");
        if (state.activeStreamController) {
          state.activeStreamController.abort();
        }
        await saveSettings({ selected_model: model });
        renderAll();
        showToast(`Active assistant set to ${model}`);
        return;
      }

      const deleteModelBtn = e.target.closest("[data-delete-model]");
      if (deleteModelBtn) {
        const model = deleteModelBtn.getAttribute("data-delete-model");
        const size = deleteModelBtn.getAttribute("data-delete-size") || "disk space";
        promptDeleteModel(model, size);
        return;
      }

      const presetChip = e.target.closest("[data-preset-id]");
      if (presetChip) {
        await selectPreset(presetChip.getAttribute("data-preset-id"));
        return;
      }

      // One-click Document Scope Toggle ("global" <-> "chat")
      const scopeToggleBtn = e.target.closest("[data-toggle-doc-scope]");
      if (scopeToggleBtn) {
        const docId = scopeToggleBtn.getAttribute("data-toggle-doc-scope");
        const targetScope = scopeToggleBtn.getAttribute("data-target-scope");
        await toggleDocumentScope(docId, targetScope);
        return;
      }

      const reuploadBtn = e.target.closest("[data-reupload-doc]");
      if (reuploadBtn) {
        state.reuploadDocId = reuploadBtn.getAttribute("data-reupload-doc");
        $("reupload-file-input").click();
        return;
      }

      const removeDocBtn = e.target.closest("[data-remove-doc]");
      if (removeDocBtn) {
        const docId = removeDocBtn.getAttribute("data-remove-doc");
        const docName = removeDocBtn.getAttribute("data-remove-name");
        removeDocument(docId, docName);
        return;
      }

      const renameChatBtn = e.target.closest("[data-rename-chat]");
      if (renameChatBtn) {
        e.stopPropagation();
        promptRenameChat(
          renameChatBtn.getAttribute("data-rename-chat"),
          renameChatBtn.getAttribute("data-chat-title")
        );
        return;
      }

      const exportChatBtn = e.target.closest("[data-export-chat]");
      if (exportChatBtn) {
        e.stopPropagation();
        exportChatToMarkdown(exportChatBtn.getAttribute("data-export-chat"));
        return;
      }

      const deleteChatBtn = e.target.closest("[data-delete-chat]");
      if (deleteChatBtn) {
        e.stopPropagation();
        promptDeleteChat(
          deleteChatBtn.getAttribute("data-delete-chat"),
          deleteChatBtn.getAttribute("data-chat-title")
        );
        return;
      }

      const selectChatEl = e.target.closest("[data-select-chat]");
      if (selectChatEl) {
        await selectChat(selectChatEl.getAttribute("data-select-chat"));
        return;
      }

      const sourceChip = e.target.closest("[data-preview-source]");
      if (sourceChip) {
        try {
          const src = JSON.parse(decodeURIComponent(sourceChip.getAttribute("data-preview-source")));
          openSourcePreviewDialog(src);
        } catch (_) {}
        return;
      }

      // Scanned PDF OCR / Vision processing buttons
      const processScannedBtn = e.target.closest("[data-process-scanned]");
      if (processScannedBtn) {
        const stagingId = processScannedBtn.getAttribute("data-process-scanned");
        const mode = processScannedBtn.getAttribute("data-scanned-mode") || "ocr";
        await processPendingScanned(stagingId, mode);
        return;
      }

      const cancelScannedBtn = e.target.closest("[data-cancel-scanned]");
      if (cancelScannedBtn) {
        const stagingId = cancelScannedBtn.getAttribute("data-cancel-scanned");
        await cancelPendingScanned(stagingId);
        return;
      }

      // Accept Maternion/LightOnOCR-3:1b recommendation (download + auto-switch)
      const acceptOcrBtn = e.target.closest("[data-accept-ocr-model]");
      if (acceptOcrBtn) {
        const modelName = acceptOcrBtn.getAttribute("data-accept-ocr-model");
        if (modelName) {
          await acceptOcrModelRecommendation(modelName);
        }
        return;
      }

      const dismissOcrBtn = e.target.closest("[data-dismiss-ocr-rec]");
      if (dismissOcrBtn) {
        const statusArea = $("upload-status-area");
        if (statusArea) {
          statusArea.classList.add("hidden");
          statusArea.innerHTML = "";
        }
        return;
      }

      // Start editing a user message
      const startEditBtn = e.target.closest("[data-start-edit-msg]");
      if (startEditBtn) {
        if (state.activeStreamController) return;
        const msgIdx = parseInt(startEditBtn.getAttribute("data-start-edit-msg"), 10);
        if (!Number.isNaN(msgIdx)) {
          state.editingMessageIndex = msgIdx;
          renderCurrentChatMessages(false);
        }
        return;
      }

      // Cancel editing a user message
      const cancelEditBtn = e.target.closest("[data-cancel-edit-msg]");
      if (cancelEditBtn) {
        state.editingMessageIndex = null;
        renderCurrentChatMessages(false);
        return;
      }

      // Save & Resend an edited user message
      const saveEditBtn = e.target.closest("[data-save-edit-msg]");
      if (saveEditBtn) {
        if (state.activeStreamController) return;
        const msgIdx = parseInt(saveEditBtn.getAttribute("data-save-edit-msg"), 10);
        const msgId = saveEditBtn.getAttribute("data-edit-msg-id") || null;
        const editTa = $("active-edit-textarea");
        const updatedText = editTa ? editTa.value.trim() : "";
        if (!updatedText) {
          showToast("Message cannot be empty.", true);
          if (editTa) editTa.focus();
          return;
        }
        await sendChatMessage(updatedText, {
          editMessageId: msgId,
          editMessageIndex: msgIdx,
        });
        return;
      }
    });

    // Keyboard shortcuts inside the inline message edit textarea (Escape to cancel, Ctrl/Cmd+Enter to Save & Resend)
    document.addEventListener("keydown", async (e) => {
      if (e.target && e.target.id === "active-edit-textarea") {
        if (e.key === "Escape") {
          e.preventDefault();
          state.editingMessageIndex = null;
          renderCurrentChatMessages(false);
        } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          if (state.activeStreamController) return;
          const editTa = e.target;
          const msgIdx = parseInt(editTa.getAttribute("data-edit-index"), 10);
          const msgId = editTa.getAttribute("data-edit-id") || null;
          const updatedText = editTa.value.trim();
          if (!updatedText) {
            showToast("Message cannot be empty.", true);
            return;
          }
          await sendChatMessage(updatedText, {
            editMessageId: msgId,
            editMessageIndex: msgIdx,
          });
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", initApp);
})();
