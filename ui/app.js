const $ = (id) => document.getElementById(id);

function setStatus(el, text, isError = false) {
  el.textContent = text;
  el.classList.toggle("error", isError);
}

function renderChapter(ch, root) {
  const { summary, rows } = ch;
  const details = document.createElement("details");
  details.className = "chapter-card";
  details.open = true;

  const sum = document.createElement("summary");
  const title = document.createElement("span");
  title.textContent = summary.title;
  const stats = document.createElement("span");
  stats.className = "chapter-stats";
  stats.textContent = `unique lemmas: ${summary.unique_lemmas} · unique forms: ${summary.unique_forms}`;
  sum.append(title, stats);

  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  thead.innerHTML = `
    <tr>
      <th>type</th>
      <th>lemma</th>
      <th>form</th>
      <th>parse</th>
      <th>parse occ</th>
      <th>lemma occ</th>
      <th>1st lemma this chap</th>
      <th>1st parse this chap</th>
      <th>last lemma this chap</th>
      <th>last parse this chap</th>
    </tr>
  `;
  // make headers sortable
  const ths = thead.querySelectorAll("th");
  const sortIndicator = document.createElement("div");
  sortIndicator.className = "sort-indicator muted";
  sortIndicator.textContent = "";
  ths.forEach((th, idx) => {
    th.classList.add("sortable");
    th.dataset.colIndex = String(idx);
    th.addEventListener("click", () => {
      const tableEl = table;
      const currentlyAsc = th.classList.contains("sort-asc");
      // clear other headers
      ths.forEach((t) => t.classList.remove("sort-asc", "sort-desc"));
      th.classList.add(currentlyAsc ? "sort-desc" : "sort-asc");
      const asc = !currentlyAsc;
      sortTable(tableEl, idx, asc);
      sortIndicator.textContent = `Sorted by ${th.textContent} (${asc ? "asc" : "desc"})`;
    });
  });
  const tbody = document.createElement("tbody");
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(r.type)}</td>
      <td class="greek">${escapeHtml(r.lemma)}</td>
      <td class="greek">${escapeHtml(r.form)}</td>
      <td class="greek">${escapeHtml(r.parse)}</td>
      <td>${r.parse_occ}</td>
      <td>${r.lemma_occ}</td>
      <td>${r.first_occ_lemma ? "yes" : ""}</td>
      <td>${r.first_occ_parse ? "yes" : ""}</td>
      <td>${r.last_occ_lemma ? "yes" : ""}</td>
      <td>${r.last_occ_parse ? "yes" : ""}</td>
    `;
    tbody.appendChild(tr);
  }
  table.append(thead, tbody);
  
  function getCellValue(row, i) {
    const cell = row.children[i];
    if (!cell) return "";
    return cell.textContent.trim();
  }

  const numericCols = new Set([4, 5]);
  const booleanCols = new Set([6, 7, 8, 9]);

  function sortTable(tbl, colIndex, asc = true) {
    const tbody = tbl.tBodies[0];
    const rows = Array.from(tbody.querySelectorAll("tr"));
    rows.sort((a, b) => {
      let va = getCellValue(a, colIndex);
      let vb = getCellValue(b, colIndex);
      if (numericCols.has(colIndex)) {
        va = parseInt(va) || 0;
        vb = parseInt(vb) || 0;
        return asc ? va - vb : vb - va;
      }
      if (booleanCols.has(colIndex)) {
        va = va.toLowerCase() === "yes" ? 1 : 0;
        vb = vb.toLowerCase() === "yes" ? 1 : 0;
        return asc ? va - vb : vb - va;
      }
      return asc ? va.localeCompare(vb, undefined, {sensitivity: "base"}) : vb.localeCompare(va, undefined, {sensitivity: "base"});
    });
    rows.forEach((r) => tbody.appendChild(r));
  }
  wrap.append(sortIndicator, table);
  details.append(sum, wrap);
  root.appendChild(details);
}

function renderReport(data) {
  const root = $("report-root");
  root.classList.remove("hidden");
  root.replaceChildren();

  // The backend always returns a MultiFileReport: one section per file.
  for (const fileRep of data.file_reports) {
    const fileDetails = document.createElement("details");
    fileDetails.className = "file-section";
    fileDetails.open = true;

    const fileSummary = document.createElement("summary");
    fileSummary.className = "file-title";
    fileSummary.textContent = fileRep.filename;

    const chapterContainer = document.createElement("div");
    chapterContainer.className = "chapters-container";

    for (const ch of fileRep.chapters) {
      renderChapter(ch, chapterContainer);
    }

    fileDetails.append(fileSummary, chapterContainer);
    root.appendChild(fileDetails);
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function main() {
  const fileInput = $("file-input");
  const fileName = $("file-name");
  const processBtn = $("process-btn");
  const status = $("status");
  const reportRoot = $("report-root");

  fileInput.addEventListener("change", () => {
    const files = Array.from(fileInput.files || []);
    fileName.textContent = files.length ? files.map((f) => f.name).join(", ") : "No files selected";
    processBtn.disabled = files.length === 0;
    reportRoot.classList.add("hidden");
    setStatus(status, "");
  });

  processBtn.addEventListener("click", async () => {
    const files = Array.from(fileInput.files || []);
    if (files.length === 0) return;
    processBtn.disabled = true;
    reportRoot.classList.add("hidden");

    // Elapsed-time timer: ticks alongside the latest status message so you can
    // ballpark how long a run takes; freezes the total when done or on error.
    const t0 = performance.now();
    const fmt = (ms) => {
      const s = ms / 1000;
      return s < 60
        ? `${s.toFixed(1)}s`
        : `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
    };
    let baseMsg = "Working… (Bedrock can take a minute)";
    const paint = () => setStatus(status, `${baseMsg} · ${fmt(performance.now() - t0)}`);
    paint();
    const ticker = setInterval(paint, 200);

    const fd = new FormData();
    for (const f of files) {
      fd.append("files", f, f.name);
    }

    try {
      const res = await fetch("/api/analyze", { method: "POST", body: fd });
      if (!res.ok) {
        // Validation failures (e.g. no/invalid files) come back as plain JSON.
        const body = await res.json().catch(() => ({}));
        const msg = body.detail || body.error || res.statusText || "Request failed";
        setStatus(status, `${msg} · ${fmt(performance.now() - t0)}`, true);
        return;
      }

      // Success path is a stream of newline-delimited JSON events.
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let finalData = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          let msg;
          try { msg = JSON.parse(line); } catch { continue; }

          if (msg.event === "progress") {
            const latest = msg.title ? ` — finished “${msg.title}”` : "";
            baseMsg = msg.total
              ? `Analyzing chapters… ${msg.done}/${msg.total}${latest}`
              : "Starting…";
            paint();
          } else if (msg.event === "result") {
            finalData = msg.data;
          } else if (msg.event === "error") {
            const detail = msg.detail ? `\n\n${msg.detail}` : "";
            setStatus(status, `${msg.error || "Request failed"} · ${fmt(performance.now() - t0)}` + detail, true);
            return;
          }
        }
      }

      const total = fmt(performance.now() - t0);
      if (finalData) {
        const names = finalData.file_reports?.map((f) => f.filename).join(", ") || "complete";
        setStatus(status, `Done in ${total}: ${names}`);
        renderReport(finalData);
      } else {
        setStatus(status, `No report returned. · ${total}`, true);
      }
    } catch (e) {
      setStatus(status, `${String(e)} · ${fmt(performance.now() - t0)}`, true);
    } finally {
      clearInterval(ticker);
      processBtn.disabled = false;
    }
  });
}

main();
