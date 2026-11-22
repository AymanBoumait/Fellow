/**
 * Fellow Offline Markdown Renderer & Code Syntax Highlighter
 * Supports:
 * - Headings (# to ######)
 * - Bold, italic, bold+italic, strikethrough, inline code
 * - Bullet lists & numbered lists (including multi-line & nested items)
 * - GitHub-Flavored Markdown tables with column alignment
 * - Blockquotes
 * - Fenced code blocks with language badge, offline syntax highlighting, and Copy button
 */
(function () {
  "use strict";

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * Lightweight offline syntax highlighter for fenced code blocks.
   * Operates on raw unescaped code string and returns safe HTML with token spans.
   */
  function highlightCode(rawCode, lang) {
    const language = (lang || "").toLowerCase().trim();
    const tokenRegex =
      /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/|<!--[\s\S]*?-->)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\b(def|class|return|if|elif|else|for|while|in|import|from|as|try|except|finally|with|lambda|yield|async|await|True|False|None|and|or|not|is|pass|break|continue|function|const|let|var|new|this|typeof|instanceof|switch|case|default|throw|catch|export|extends|static|public|private|protected|void|int|float|double|char|bool|boolean|string|struct|enum|interface|type|namespace|using|include|select|where|join|left|right|inner|outer|group|order|by|insert|into|values|update|set|delete|create|table|drop|alter|null|true|false|fn|mut|pub|impl|trait|match|use|mod|crate|package|func|defer|go|chan|map)\b|\b(\d+(?:\.\d+)?(?:e[+-]?\d+)?)\b|\b([A-Za-z_][A-Za-z0-9_]*)(?=\s*\()/gi;

    let result = "";
    let lastIndex = 0;
    let match;

    while ((match = tokenRegex.exec(rawCode)) !== null) {
      if (match.index > lastIndex) {
        result += escapeHtml(rawCode.slice(lastIndex, match.index));
      }
      const [full, comment, strLit, keyword, numLit, funcName] = match;
      if (comment) {
        result += `<span class="hl-comment">${escapeHtml(full)}</span>`;
      } else if (strLit) {
        result += `<span class="hl-string">${escapeHtml(full)}</span>`;
      } else if (keyword) {
        result += `<span class="hl-keyword">${escapeHtml(full)}</span>`;
      } else if (numLit) {
        result += `<span class="hl-number">${escapeHtml(full)}</span>`;
      } else if (funcName) {
        result += `<span class="hl-function">${escapeHtml(full)}</span>`;
      } else {
        result += escapeHtml(full);
      }
      lastIndex = tokenRegex.lastIndex;
    }

    if (lastIndex < rawCode.length) {
      result += escapeHtml(rawCode.slice(lastIndex));
    }
    return result;
  }

  /**
   * Format inline Markdown elements (inline code, bold, italic, links, strikethrough).
   */
  function parseInline(text) {
    if (!text) return "";

    // Extract inline code spans first so inner asterisks/underscores are preserved
    const inlineCodeSlots = [];
    let processed = text.replace(/`([^`\n]+)`/g, function (_, codeContent) {
      const idx = inlineCodeSlots.length;
      inlineCodeSlots.push(`<code class="md-inline-code">${escapeHtml(codeContent)}</code>`);
      return `\u0000INLINECODE_${idx}\u0000`;
    });

    processed = escapeHtml(processed);

    // Links: [label](https://...)
    processed = processed.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" class="md-link" data-external-url="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );

    // Bold + Italic: ***text***
    processed = processed.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");

    // Bold: **text** or __text__
    processed = processed.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    processed = processed.replace(/__([^_]+)__/g, "<strong>$1</strong>");

    // Italic: *text* or _text_
    processed = processed.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    processed = processed.replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,!?:;]|$)/g, "$1<em>$2</em>");

    // Strikethrough: ~~text~~
    processed = processed.replace(/~~([^~]+)~~/g, "<del>$1</del>");

    // Restore inline code spans
    processed = processed.replace(/\u0000INLINECODE_(\d+)\u0000/g, function (_, idx) {
      return inlineCodeSlots[Number(idx)] || "";
    });

    return processed;
  }

  /**
   * Parse GFM table block lines into an HTML table.
   */
  function parseTable(lines) {
    const parseRow = (rowStr) =>
      rowStr
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((c) => c.trim());

    const headers = parseRow(lines[0]);
    const alignRow = parseRow(lines[1]);
    const alignments = alignRow.map((cell) => {
      if (cell.startsWith(":") && cell.endsWith(":")) return "center";
      if (cell.endsWith(":")) return "right";
      return "left";
    });

    let html = '<div class="md-table-wrap"><table class="md-table"><thead><tr>';
    headers.forEach((h, i) => {
      const align = alignments[i] || "left";
      html += `<th style="text-align:${align}">${parseInline(h)}</th>`;
    });
    html += "</tr></thead><tbody>";

    for (let r = 2; r < lines.length; r++) {
      if (!lines[r].trim()) continue;
      const cells = parseRow(lines[r]);
      html += "<tr>";
      headers.forEach((_, i) => {
        const align = alignments[i] || "left";
        const cellText = cells[i] !== undefined ? cells[i] : "";
        html += `<td style="text-align:${align}">${parseInline(cellText)}</td>`;
      });
      html += "</tr>";
    }
    html += "</tbody></table></div>";
    return html;
  }

  /**
   * Render full Markdown text into safe, styled HTML.
   */
  function renderMarkdown(markdownText) {
    if (!markdownText) return "";

    let text = String(markdownText).replace(/\r\n/g, "\n");

    // Handle unclosed fenced code block during live token streaming
    const fenceCount = (text.match(/```/g) || []).length;
    if (fenceCount % 2 !== 0) {
      text += "\n```";
    }

    // 1. Extract fenced code blocks into placeholders
    const codeBlocks = [];
    text = text.replace(/```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g, function (_, lang, code) {
      const idx = codeBlocks.length;
      const cleanLang = (lang || "code").trim();
      const cleanCode = code.replace(/\n$/, "");
      const highlighted = highlightCode(cleanCode, cleanLang);
      const encodedRaw = encodeURIComponent(cleanCode);

      const blockHtml = `
        <div class="md-code-block">
          <div class="md-code-header">
            <span class="md-code-lang">${escapeHtml(cleanLang)}</span>
            <button type="button" class="md-code-copy-btn" data-code="${encodedRaw}" title="Copy code to clipboard">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
              <span>Copy</span>
            </button>
          </div>
          <pre class="md-pre"><code class="language-${escapeHtml(cleanLang)}">${highlighted}</code></pre>
        </div>
      `;
      codeBlocks.push(blockHtml);
      return `\n\u0000CODEBLOCK_${idx}\u0000\n`;
    });

    const lines = text.split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      // Empty line
      if (!trimmed) {
        i++;
        continue;
      }

      // Code block placeholder
      const codeMatch = trimmed.match(/^\u0000CODEBLOCK_(\d+)\u0000$/);
      if (codeMatch) {
        out.push(codeBlocks[Number(codeMatch[1])] || "");
        i++;
        continue;
      }

      // Horizontal Rule
      if (/^([-*_])\1{2,}$/.test(trimmed)) {
        out.push('<hr class="md-hr" />');
        i++;
        continue;
      }

      // Headings (# to ######)
      const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
      if (headingMatch) {
        const level = headingMatch[1].length;
        out.push(`<h${level} class="md-h${level}">${parseInline(headingMatch[2].trim())}</h${level}>`);
        i++;
        continue;
      }

      // Table detection: current line has | and next line is separator |---|---|
      if (
        line.includes("|") &&
        i + 1 < lines.length &&
        /^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$/.test(lines[i + 1])
      ) {
        const tableLines = [lines[i], lines[i + 1]];
        i += 2;
        while (i < lines.length && lines[i].trim().includes("|")) {
          tableLines.push(lines[i]);
          i++;
        }
        out.push(parseTable(tableLines));
        continue;
      }

      // Blockquote (> ...)
      if (/^\s*>\s?/.test(line)) {
        const quoteLines = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quoteLines.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        out.push(`<blockquote class="md-blockquote">${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
        continue;
      }

      // Unordered List (-, *, +)
      if (/^\s*[-*+]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          const itemText = lines[i].replace(/^\s*[-*+]\s+/, "");
          items.push(`<li>${parseInline(itemText)}</li>`);
          i++;
        }
        out.push(`<ul class="md-ul">${items.join("")}</ul>`);
        continue;
      }

      // Ordered List (1. 2. ...)
      if (/^\s*\d+[.)]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
          const itemText = lines[i].replace(/^\s*\d+[.)]\s+/, "");
          items.push(`<li>${parseInline(itemText)}</li>`);
          i++;
        }
        out.push(`<ol class="md-ol">${items.join("")}</ol>`);
        continue;
      }

      // Standard Paragraph (collect consecutive non-special lines)
      const paraLines = [];
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !/^\u0000CODEBLOCK_\d+\u0000$/.test(lines[i].trim()) &&
        !/^(#{1,6})\s+/.test(lines[i]) &&
        !/^\s*>\s?/.test(lines[i]) &&
        !/^\s*[-*+]\s+/.test(lines[i]) &&
        !/^\s*\d+[.)]\s+/.test(lines[i]) &&
        !/^([-*_])\1{2,}$/.test(lines[i].trim()) &&
        !(
          lines[i].includes("|") &&
          i + 1 < lines.length &&
          /^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$/.test(lines[i + 1])
        )
      ) {
        paraLines.push(parseInline(lines[i]));
        i++;
      }
      if (paraLines.length > 0) {
        out.push(`<p class="md-p">${paraLines.join("<br />")}</p>`);
      }
    }

    return out.join("\n");
  }

  // Global delegated handler for code copy buttons & external links
  document.addEventListener("click", function (e) {
    const copyBtn = e.target.closest(".md-code-copy-btn");
    if (copyBtn) {
      const encoded = copyBtn.getAttribute("data-code") || "";
      const rawCode = decodeURIComponent(encoded);
      const labelSpan = copyBtn.querySelector("span");
      const markCopied = () => {
        copyBtn.classList.add("copied");
        if (labelSpan) labelSpan.textContent = "Copied!";
        setTimeout(() => {
          copyBtn.classList.remove("copied");
          if (labelSpan) labelSpan.textContent = "Copy";
        }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(rawCode).then(markCopied).catch(() => {
          fallbackCopy(rawCode);
          markCopied();
        });
      } else {
        fallbackCopy(rawCode);
        markCopied();
      }
      return;
    }

    const extLink = e.target.closest("[data-external-url]");
    if (extLink) {
      e.preventDefault();
      const url = extLink.getAttribute("data-external-url");
      if (url) {
        fetch("/api/open-url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
        }).catch(() => {
          window.open(url, "_blank", "noopener,noreferrer");
        });
      }
    }
  });

  function fallbackCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch (_) {}
    document.body.removeChild(ta);
  }

  window.FellowMarkdown = {
    render: renderMarkdown,
    escapeHtml: escapeHtml,
  };
})();
