const $ = (id) => document.getElementById(id);

function setStatus(el, text, isError = false) {
  el.textContent = text;
  el.classList.toggle("error", isError);
}

const ORDINAL_COL = 0;

// The "#" column is a running count of what is on screen, not a property of the
// row: it always reads 1..N top to bottom, so it is rewritten after every sort.
function renumber(tbody) {
  Array.from(tbody.rows).forEach((tr, i) => {
    tr.cells[ORDINAL_COL].textContent = String(i + 1);
  });
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
      <th class="row-num">#</th>
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
    // The ordinal column always counts 1..N down the visible rows, so there is
    // nothing in it to sort by — leave it out of the header wiring.
    if (idx === ORDINAL_COL) return;
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
      <td class="row-num"></td>
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
  renumber(tbody);
  
  function getCellValue(row, i) {
    const cell = row.children[i];
    if (!cell) return "";
    return cell.textContent.trim();
  }

  const numericCols = new Set([5, 6]);
  const booleanCols = new Set([7, 8, 9, 10]);

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
    renumber(tbody);
  }
  wrap.append(sortIndicator, table);
  details.append(sum, wrap);
  root.appendChild(details);
}

function renderReport(data, notice = null) {
  const root = $("report-root");
  root.classList.remove("hidden");
  root.replaceChildren();

  // A report re-derived from the store is indistinguishable from a fresh run
  // once rendered, so say which one is on screen. Without this, loading an old
  // run silently replaces the report you just paid Bedrock for.
  if (notice) {
    const banner = document.createElement("p");
    banner.className = "report-notice muted";
    banner.textContent = notice;
    root.appendChild(banner);
  }

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

// -- run history ------------------------------------------------------------
//
// Runs are identified by their timestamp — a wall-clock time is far more
// recognisable to the author than a uuid — so it is the first column and the
// label in every confirm. This is a display choice only: `store.list_runs`
// still breaks ties on id, because the clock is coarse enough that two runs
// can share a timestamp even if hand-driven authoring never produces them.
//
// It is also the weak point of the panel. Re-running the same file is the
// normal authoring loop, and nothing else distinguishes those rows; see the
// run-history section of CONTEXT.md.

function runLabel(run) {
  // Stored as UTC ISO; shown in local time, which is the identifier the author
  // will actually recognise.
  const d = new Date(run.created_at);
  return isNaN(d) ? run.created_at : d.toLocaleString();
}

const PAGE_SIZE = 10;

// Deleting is a selection-then-act flow only: tick the rows, confirm the list.
// There is no per-row delete button, so the destructive action is never one
// stray click away from the View button next to it.
function renderHistory(page, handlers) {
  const { runs, total, offset, limit } = page;
  const { onView, onDeleteMany, onPage } = handlers;
  const body = $("history-body");
  body.replaceChildren();

  if (!total) {
    const empty = document.createElement("p");
    empty.className = "history-empty muted";
    empty.textContent = "No stored runs yet.";
    body.appendChild(empty);
    return;
  }

  // Selection is per page and reset whenever the page changes: a bulk delete
  // must never remove a run the user cannot currently see.
  const selected = new Set();

  const toolbar = document.createElement("div");
  toolbar.className = "history-toolbar";
  const first = total ? offset + 1 : 0;
  const last = Math.min(offset + limit, total);
  toolbar.innerHTML = `
    <button type="button" class="btn-danger" data-act="delete-selected" disabled>
      Delete selected
    </button>
    <span class="history-range muted">${first}–${last} of ${total}</span>
    <span class="history-pager">
      <button type="button" class="btn-quiet" data-act="prev" ${offset <= 0 ? "disabled" : ""}>‹ Newer</button>
      <button type="button" class="btn-quiet" data-act="next" ${last >= total ? "disabled" : ""}>Older ›</button>
    </span>
  `;
  const bulkBtn = toolbar.querySelector('[data-act="delete-selected"]');
  toolbar.querySelector('[data-act="prev"]').addEventListener("click", () =>
    onPage(Math.max(0, offset - limit))
  );
  toolbar.querySelector('[data-act="next"]').addEventListener("click", () => onPage(offset + limit));

  const table = document.createElement("table");
  table.className = "history-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th class="history-check">
          <input type="checkbox" data-act="select-all" aria-label="Select all runs on this page" />
        </th>
        <th>run</th>
        <th>files</th>
        <th>tokens</th>
        <th>model</th>
        <th></th>
      </tr>
    </thead>
  `;
  const selectAll = table.querySelector('[data-act="select-all"]');

  function syncSelectionUi() {
    const n = selected.size;
    bulkBtn.disabled = n === 0;
    bulkBtn.textContent = n ? `Delete selected (${n})` : "Delete selected";
    selectAll.checked = n > 0 && n === runs.length;
    // Partial selection reads as neither on nor off, which is what the user has.
    selectAll.indeterminate = n > 0 && n < runs.length;
  }

  const tbody = document.createElement("tbody");
  for (const run of runs) {
    const label = runLabel(run);
    const tr = document.createElement("tr");
    // Model ids are long inference-profile ARNs; the trailing segment is the
    // part that distinguishes sonnet from haiku, which is all issue #1 needs.
    const model = String(run.model_id).split(".").pop();
    tr.innerHTML = `
      <td class="history-check">
        <input type="checkbox" data-act="select" aria-label="Select run from ${escapeHtml(label)}" />
      </td>
      <td class="history-when">${escapeHtml(label)}</td>
      <td class="history-files">${escapeHtml(run.filenames.join(", "))}</td>
      <td class="num">${run.token_count.toLocaleString()}</td>
      <td class="muted">${escapeHtml(model)}</td>
      <td class="history-actions">
        <button type="button" class="btn-quiet" data-act="view">View</button>
      </td>
    `;
    const box = tr.querySelector('[data-act="select"]');
    box.addEventListener("change", () => {
      box.checked ? selected.add(run.id) : selected.delete(run.id);
      tr.classList.toggle("selected", box.checked);
      syncSelectionUi();
    });
    tr.querySelector('[data-act="view"]').addEventListener("click", () => onView(run, label));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);

  selectAll.addEventListener("change", () => {
    const on = selectAll.checked;
    selected.clear();
    tbody.querySelectorAll("tr").forEach((tr, i) => {
      tr.querySelector('[data-act="select"]').checked = on;
      tr.classList.toggle("selected", on);
      if (on) selected.add(runs[i].id);
    });
    syncSelectionUi();
  });

  bulkBtn.addEventListener("click", () => {
    const chosen = runs.filter((r) => selected.has(r.id));
    if (chosen.length) onDeleteMany(chosen);
  });

  body.append(toolbar, table);
  syncSelectionUi();
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
  const history = $("history");

  // Which stored run is on screen, so deleting it can clear the report rather
  // than leave a table backed by rows that no longer exist.
  let shownRunId = null;
  let historyOffset = 0;

  async function viewRun(run, label) {
    setStatus(status, `Loading run from ${label}…`);
    try {
      const res = await fetch(`/api/runs/${run.id}/report`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setStatus(status, body.detail || "Could not load that run.", true);
        return;
      }
      const data = await res.json();
      shownRunId = run.id;
      renderReport(data, `Stored run from ${label} · ${run.filenames.join(", ")}`);
      setStatus(status, `Showing stored run from ${label}.`);
    } catch (e) {
      setStatus(status, String(e), true);
    }
  }

  // Clear the on-screen report if it was backed by a run that just went away.
  function forgetIfShown(ids) {
    if (shownRunId && ids.includes(shownRunId)) {
      reportRoot.replaceChildren();
      reportRoot.classList.add("hidden");
      shownRunId = null;
    }
  }

  async function deleteMany(runs) {
    const tokens = runs.reduce((n, r) => n + r.token_count, 0);
    // List them: the whole risk of a bulk delete is losing track of what is in
    // the selection, so the confirm shows every run rather than just a count.
    const lines = runs.map((r) => `  • ${runLabel(r)} — ${r.filenames.join(", ")}`).join("\n");
    const ok = window.confirm(
      `Delete ${runs.length} run${runs.length === 1 ? "" : "s"}?\n\n${lines}\n\n` +
        `${tokens.toLocaleString()} tokens total. This cannot be undone.`
    );
    if (!ok) return;

    setStatus(status, `Deleting ${runs.length} runs…`);
    try {
      const ids = runs.map((r) => r.id);
      const res = await fetch("/api/runs/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setStatus(status, body.detail || "Could not delete those runs.", true);
        return;
      }
      // `deleted` is what the server actually removed, which can be shorter
      // than `ids` if the selection had gone stale — report that, not the ask.
      const { deleted, count } = await res.json();
      forgetIfShown(deleted);
      setStatus(status, `Deleted ${count} run${count === 1 ? "" : "s"}.`);
      await loadHistory();
    } catch (e) {
      setStatus(status, String(e), true);
    }
  }

  async function loadHistory(offset = historyOffset) {
    // Only the fetch is guarded. Hiding the panel is the right answer to "there
    // is no history to show" and the wrong answer to "rendering threw", which
    // would otherwise make the whole feature vanish with nothing logged — so
    // the render below sits outside this try deliberately.
    let page;
    try {
      const res = await fetch(`/api/runs?limit=${PAGE_SIZE}&offset=${offset}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setStatus(status, body.detail || "Could not load run history.", true);
        return;
      }
      page = await res.json();
    } catch (e) {
      setStatus(status, `Could not load run history: ${e}`, true);
      return;
    }

    // Persistence off (LINGUA_PERSIST_RUNS=false) means there is no history to
    // offer — hide the section rather than show an empty one that never fills.
    // A store that failed to open answers 503 instead and is reported above.
    if (!page.persistence) {
      history.classList.add("hidden");
      return;
    }
    // Deleting the last page's contents can strand the offset past the end;
    // step back a page and re-fetch rather than render an empty table.
    if (!page.runs.length && page.total > 0 && offset > 0) {
      return loadHistory(Math.max(0, offset - PAGE_SIZE));
    }
    historyOffset = page.offset;
    history.classList.remove("hidden");
    $("history-count").textContent = page.total ? `${page.total} stored` : "";
    renderHistory(page, {
      onView: viewRun,
      onDeleteMany: deleteMany,
      onPage: (next) => loadHistory(next),
    });
  }

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
        shownRunId = finalData.run_id || null;
        renderReport(finalData);
        // The run just analyzed is now in the store and sorts to the top, so
        // jump back to the first page rather than leaving the user wherever
        // they had paged to.
        historyOffset = 0;
        loadHistory(0);
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

  loadHistory();
}

main();
