const $ = (id) => document.getElementById(id);

function setStatus(el, text, isError = false) {
  el.textContent = text;
  el.classList.toggle("error", isError);
}

// -- sortable tables --------------------------------------------------------
//
// One table implementation, shared by the chapter table and the text table.
// The previous one sorted by reading cell text back out of the DOM, so every
// numeric or boolean column had to be registered in a hard-coded set of column
// indexes — which silently mis-sorts the moment a column is inserted, and issue
// #4 inserts four. Columns now declare their own accessor and kind and the sort
// runs over the data, so adding a column is one entry in a list.
//
// `get` is the sort key and, unless `text` overrides it, the displayed value.

function cellText(col, row) {
  if (col.text) return col.text(row);
  const v = col.get(row);
  if (col.type === "bool") return v ? "yes" : "";
  return v === null || v === undefined ? "" : String(v);
}

function compareBy(col, a, b) {
  const va = col.get(a);
  const vb = col.get(b);
  if (col.type === "num") return (va || 0) - (vb || 0);
  if (col.type === "bool") return (va ? 1 : 0) - (vb ? 1 : 0);
  return String(va ?? "").localeCompare(String(vb ?? ""), undefined, { sensitivity: "base" });
}

function buildTable(columns, rows, { className = "", rowClass = null } = {}) {
  // Three levels, not two: the indicator sits *outside* the scrolling wrap.
  // Inside it, it scrolls sideways with a wide table, and — where the wrap also
  // scrolls vertically, as the combination table does — it pushes the sticky
  // header down by its own height and lets rows paint in the gap above it.
  const block = document.createElement("div");
  block.className = "table-block";

  const wrap = document.createElement("div");
  wrap.className = "table-wrap";

  const indicator = document.createElement("div");
  indicator.className = "sort-indicator muted";

  const table = document.createElement("table");
  if (className) table.className = className;
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");

  // The ordinal is a running count of what is on screen, not a property of the
  // row: it always reads 1..N top to bottom, so there is nothing in it to sort
  // by and it is re-derived on every render.
  const ordinal = document.createElement("th");
  ordinal.className = "row-num";
  ordinal.textContent = "#";
  headRow.appendChild(ordinal);

  // Alignment follows `type` unless a column overrides it. The one case that
  // needs the override is a text column sorted by a hidden numeric key — the
  // form-combination column, which displays a label but sorts by paradigm
  // position so that re-sorting on it lays the conjugation out in order.
  const alignOf = (col) => (col.align !== undefined ? col.align : col.type === "num" ? "num" : "");

  const ths = columns.map((col) => {
    const th = document.createElement("th");
    th.textContent = col.label;
    th.className = "sortable";
    if (col.title) th.title = col.title;
    if (alignOf(col) === "num") th.classList.add("num");
    headRow.appendChild(th);
    return th;
  });
  thead.appendChild(headRow);

  const tbody = document.createElement("tbody");

  function paint(ordered) {
    tbody.replaceChildren();
    const frag = document.createDocumentFragment();
    ordered.forEach((row, i) => {
      const tr = document.createElement("tr");
      if (rowClass) {
        const cls = rowClass(row);
        if (cls) tr.className = cls;
      }
      const cells = [`<td class="row-num">${i + 1}</td>`];
      for (const col of columns) {
        const classes = [col.cls, alignOf(col) === "num" ? "num" : null].filter(Boolean).join(" ");
        const title = col.cellTitle ? ` title="${escapeHtml(col.cellTitle(row))}"` : "";
        cells.push(`<td${classes ? ` class="${classes}"` : ""}${title}>${escapeHtml(cellText(col, row))}</td>`);
      }
      tr.innerHTML = cells.join("");
      frag.appendChild(tr);
    });
    tbody.appendChild(frag);
  }

  let current = rows.slice();
  paint(current);

  ths.forEach((th, i) => {
    th.addEventListener("click", () => {
      const asc = !th.classList.contains("sort-asc");
      ths.forEach((t) => t.classList.remove("sort-asc", "sort-desc"));
      th.classList.add(asc ? "sort-asc" : "sort-desc");
      const col = columns[i];
      current = rows.slice().sort((a, b) => (asc ? 1 : -1) * compareBy(col, a, b));
      paint(current);
      indicator.textContent = `Sorted by ${col.label} (${asc ? "asc" : "desc"})`;
    });
  });

  table.append(thead, tbody);
  wrap.appendChild(table);
  block.append(indicator, wrap);
  return block;
}

// -- grammatical form summary (issues #7 / #14) -----------------------------
//
// Rendered as one card per feature dimension rather than one long list, because
// the question is always asked within a dimension ("how many aorists", "how
// many datives") and a single ranked list would interleave them.
//
// Zero rows are kept on purpose — "0 futures in this chapter" is the answer to
// issue #7's question, and dropping the row would make it unaskable. The
// backend supplies the expected vocabulary for exactly this reason.

function pct(x) {
  return `${(x * 100).toFixed(x === 1 || x === 0 ? 0 : 1)}%`;
}

// Grammar counts are only as good as the share of labels the normalizer could
// read, so this rides along with every profile. Without it an unparsed label is
// indistinguishable from grammar the text does not contain — which is the exact
// trap that made counting off the raw parse string unsafe.
function coverageNote(cov) {
  const el = document.createElement("p");
  el.className = "coverage-note muted";
  if (!cov) return el;
  const parts = [`${pct(cov.understood_share)} of morphology decoded`];
  if (cov.needs_attention) parts.push(`${cov.needs_attention} labels need attention`);
  if (cov.verb_forms) {
    parts.push(
      cov.verbs_missing_voice
        ? `voice missing on ${cov.verbs_missing_voice} of ${cov.verb_forms} verb forms`
        : `voice stated on all ${cov.verb_forms} verb forms`
    );
  }
  el.textContent = parts.join(" · ");
  if (cov.needs_attention || cov.voice_gap_share > 0.05) el.classList.add("warn");
  return el;
}

// -- form combinations ------------------------------------------------------
//
// The complement to the per-dimension cards. Those say "212 aorists"; this says
// which aorists — "aor. act. ind. 3sg" as one row and "aor. mid. part. nom. sg.
// masc." as another. A learner meets whole forms, not features, so two cells of
// the paradigm are two things to introduce even though both count as one
// aorist each above.
//
// Two properties the cards do not have:
//   - These rows PARTITION. Every token carries exactly one combination, so the
//     column sums to the tokens carrying morphology. A syncretic form lands in
//     one row that says so ("nom./acc.") rather than being counted twice.
//   - They cannot be zero-filled against the language. Greek's full cross
//     product is thousands of cells almost none of which any text contains, so
//     the row set is what the *corpus* attests. A chapter therefore carries an
//     explicit zero for a form the text uses elsewhere, which is the "no aorist
//     participles here" reading; forms the whole text lacks have no row at all.

function combinationColumns(scope, labels) {
  const cols = [
    {
      label: "form",
      // Displays the label, sorts by paradigm position — so clicking this
      // header lays the conjugation out in order instead of alphabetising it
      // into "aor." before "impf." before "pres.".
      get: (r) => r.order,
      text: (r) => r.form,
      type: "num",
      align: "",
      title: "the whole feature combination; sorts in paradigm order",
    },
  ];
  if (scope === "chapter") {
    cols.push(
      { label: "here", get: (r) => r.occ, type: "num" },
      { label: "cum.", get: (r) => r.cumulative, type: "num", title: "occurrences from the start of the text through this chapter" },
      { label: "new", get: (r) => r.isNew, type: "bool", title: "this form appears for the first time in this chapter" }
    );
  } else {
    cols.push(
      { label: "total", get: (r) => r.occ, type: "num" },
      { label: "# chs", get: (r) => r.chapter_count, type: "num", title: "distinct chapters this form appears in" },
      {
        label: "1st ch",
        get: (r) => r.first_chapter,
        type: "num",
        text: (r) => labels.short(r.first_chapter),
        cellTitle: (r) => labels.long(r.first_chapter),
        title: "chapter this form is introduced in",
      },
      {
        label: "last ch",
        get: (r) => r.last_chapter,
        type: "num",
        text: (r) => labels.short(r.last_chapter),
        cellTitle: (r) => labels.long(r.last_chapter),
      }
    );
  }
  return cols;
}

// The chapter view's table, rebuilt from the corpus inventory plus this
// chapter's two numbers per row. The inventory travels once because every other
// field on a row — the label, its paradigm position, the chapters it spans — is
// corpus-wide and cannot vary by chapter; see `CombinationCount`. Group and
// table totals are re-derived rather than sent, because these rows partition:
// the tokens in a class are exactly the sum of its `occ`.
function chapterCombinations(inventory, counts) {
  if (!inventory || !inventory.groups.length) return null;
  const byOrder = new Map((counts || []).map((c) => [c.order, c]));
  const groups = inventory.groups.map((g) => {
    // A row with no entry is one the text has not reached yet, which the
    // server omits rather than sending a pair of zeros for.
    const rows = g.rows.map((r) => {
      const c = byOrder.get(r.order);
      return { ...r, occ: c ? c.occ : 0, cumulative: c ? c.cumulative : 0 };
    });
    return {
      ...g,
      rows,
      tokens: rows.reduce((n, r) => n + r.occ, 0),
      tokens_cumulative: rows.reduce((n, r) => n + r.cumulative, 0),
    };
  });
  return {
    groups,
    tokens: groups.reduce((n, g) => n + g.tokens, 0),
    tokens_cumulative: groups.reduce((n, g) => n + g.tokens_cumulative, 0),
  };
}

function renderCombinations(table, scope, chapterIndex, labels) {
  const section = document.createElement("div");
  section.className = "combinations";
  if (!table || !table.groups.length) return section;

  // One table per form class. A single ranked list interleaves paradigms that
  // were never meant to be compared — "which tenses am I using" and "which
  // cases am I using" are different questions, and a participle answers to
  // neither, which is why it gets a table of its own.
  const groups = table.groups.map((g) => ({
    ...g,
    rows: g.rows.map((r) => ({ ...r, isNew: r.first_chapter === chapterIndex && r.occ > 0 })),
  }));
  const presentCount = groups.reduce((n, g) => n + g.rows.filter((r) => r.occ > 0).length, 0);
  const absentCount = groups.reduce((n, g) => n + g.rows.filter((r) => !r.occ).length, 0);

  const head = document.createElement("div");
  head.className = "combinations-head";
  head.innerHTML = `
    <h4>Form combinations</h4>
    <span class="muted">${presentCount} here · ${table.tokens.toLocaleString()} tokens</span>
  `;

  const grid = document.createElement("div");
  grid.className = "combinations-grid";

  // One control for all three tables: "should I see the forms this chapter
  // lacks" is a single question, and three checkboxes would make it look like
  // three. Absent rows are the "no aorist participles in this chapter"
  // reading, and on a long text they outnumber the present ones — so they are
  // available rather than default, and the control names its own count so
  // nothing is hidden silently.
  let showAbsent = false;
  if (absentCount > 0) {
    const toggle = document.createElement("label");
    toggle.className = "combinations-toggle muted";
    toggle.innerHTML = `<input type="checkbox" /> show ${absentCount} form${absentCount === 1 ? "" : "s"} the text uses elsewhere`;
    head.appendChild(toggle);
    toggle.querySelector("input").addEventListener("change", (e) => {
      showAbsent = e.target.checked;
      draw();
    });
  }

  const slots = groups.map((group) => {
    const card = document.createElement("div");
    card.className = "combinations-card";
    const label = document.createElement("div");
    label.className = "combinations-card-head";
    label.innerHTML = `
      <span class="combinations-class"${group.hint ? ` title="${escapeHtml(group.hint)}"` : ""}>${escapeHtml(group.label)}</span>
      <span class="muted"></span>
    `;
    const slot = document.createElement("div");
    card.append(label, slot);
    grid.appendChild(card);
    return { group, slot, count: label.querySelector(".muted") };
  });

  function draw() {
    for (const { group, slot, count } of slots) {
      const rows = showAbsent ? group.rows : group.rows.filter((r) => r.occ > 0);
      count.textContent = `${rows.length} form${rows.length === 1 ? "" : "s"} · ${group.tokens.toLocaleString()} tokens`;
      if (!rows.length) {
        // The card stays. Which classes exist is decided corpus-wide, so a
        // chapter with no participles says so rather than quietly losing the
        // table and leaving the author to notice it is missing.
        const empty = document.createElement("p");
        empty.className = "combinations-empty muted";
        empty.textContent = "None in this chapter.";
        slot.replaceChildren(empty);
        continue;
      }
      // Frequency first, paradigm order to break ties — the same default the
      // vocabulary table uses, and every header re-sorts.
      const ordered = rows.slice().sort((a, b) => b.occ - a.occ || a.order - b.order);
      slot.replaceChildren(
        buildTable(combinationColumns(scope, labels), ordered, {
          className: "table-narrow",
          // Same treatment the dimension cards give a zero: present, because
          // "not in this chapter" is an answer, but quiet, because the rows
          // carrying a count are the ones being read.
          rowClass: (r) => (r.occ ? "" : "zero"),
        })
      );
    }
  }
  draw();

  section.append(head, grid);
  return section;
}

// `scope` is "chapter" (this chapter's count beside the running total) or
// "text" (corpus totals plus where each form lives). `combinations` is the
// combination table for this scope, passed in rather than read off `source`
// because only the text report carries one — a chapter's is rebuilt from it.
function renderGrammar(source, scope, { chapterIndex = null, labels = null, combinations = null } = {}) {
  const groups = source.grammar;
  const section = document.createElement("section");
  section.className = "grammar";

  const head = document.createElement("div");
  head.className = "grammar-head";
  const title = document.createElement("h3");
  title.textContent = "Grammatical forms";
  head.append(title, coverageNote(source.coverage));
  section.appendChild(head);

  if (!groups || !groups.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No morphology was decoded here.";
    section.appendChild(empty);
    return section;
  }

  const grid = document.createElement("div");
  grid.className = "grammar-grid";

  for (const group of groups) {
    const card = document.createElement("div");
    card.className = "grammar-card";

    const isChapter = scope === "chapter";
    // Not the sum of the rows below it: a syncretic nom./acc. is counted under
    // both readings, so the rows can add up past the tokens involved. This is
    // the honest denominator.
    const stated = isChapter
      ? `${group.stated} here · ${group.stated_cumulative} so far`
      : `${group.stated} tokens`;

    const rows = group.stats
      .map((s) => {
        const isNew = isChapter && s.occ > 0 && s.first_chapter === chapterIndex;
        const right = isChapter
          ? `<td class="num">${s.occ}</td><td class="num muted">${s.cumulative}</td>`
          : `<td class="num">${s.occ}</td><td class="num muted">${s.chapter_count}</td>`;
        return `<tr class="${s.occ ? "" : "zero"}">
          <td>${escapeHtml(s.label)}${isNew ? ' <span class="badge-new" title="first appears in this chapter">new</span>' : ""}</td>
          ${right}
        </tr>`;
      })
      .join("");

    card.innerHTML = `
      <div class="grammar-card-head">
        <span class="grammar-dimension">${escapeHtml(group.label)}</span>
        <span class="muted">${escapeHtml(stated)}</span>
      </div>
      <table class="grammar-table">
        <thead><tr>
          <th>${escapeHtml(group.label.toLowerCase())}</th>
          <th class="num">${isChapter ? "here" : "total"}</th>
          <th class="num">${isChapter ? "cum." : "chs"}</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
    grid.appendChild(card);
  }

  section.append(grid, renderCombinations(combinations, scope, chapterIndex, labels));
  return section;
}

// -- chapter lens (issue #14) -----------------------------------------------

// `occ` is what is in this chapter, `cum` what the reader has met by the end of
// it. Shown as a pair rather than one or the other because the repetition
// question needs both: 3 occurrences of a word met 40 times already is a very
// different chapter from 3 occurrences of a word met twice.
const CHAPTER_COLUMNS = [
  { label: "type", get: (r) => r.type },
  { label: "lemma", get: (r) => r.lemma, cls: "greek" },
  { label: "form", get: (r) => r.form, cls: "greek" },
  { label: "parse", get: (r) => r.parse },
  { label: "parse occ", get: (r) => r.parse_occ, type: "num", title: "occurrences of this lemma+parse in this chapter" },
  { label: "parse cum", get: (r) => r.parse_cum, type: "num", title: "occurrences from the start of the text through this chapter" },
  { label: "lemma occ", get: (r) => r.lemma_occ, type: "num", title: "occurrences of this lemma in this chapter, across all its parses" },
  { label: "lemma cum", get: (r) => r.lemma_cum, type: "num", title: "occurrences from the start of the text through this chapter" },
  { label: "1st lemma", get: (r) => r.first_occ_lemma, type: "bool", title: "this lemma appears for the first time in this chapter" },
  { label: "1st parse", get: (r) => r.first_occ_parse, type: "bool", title: "this lemma+parse appears for the first time in this chapter" },
  { label: "last lemma", get: (r) => r.last_occ_lemma, type: "bool", title: "this lemma never appears again after this chapter" },
  { label: "last parse", get: (r) => r.last_occ_parse, type: "bool", title: "this lemma+parse never appears again after this chapter" },
];

// `inventory` is the corpus-wide combination table off the text report; this
// chapter's counts are joined onto it. Null when a run has no text report, in
// which case the chapter simply shows its dimension cards without them.
function renderChapter(ch, root, inventory) {
  const { summary, rows } = ch;
  const details = document.createElement("details");
  details.className = "chapter-card";
  details.open = true;

  const sum = document.createElement("summary");
  const title = document.createElement("span");
  title.textContent = summary.title;
  const stats = document.createElement("span");
  stats.className = "chapter-stats";
  stats.textContent =
    `${summary.token_count.toLocaleString()} tokens · ` +
    `unique lemmas: ${summary.unique_lemmas} · unique forms: ${summary.unique_forms}`;
  sum.append(title, stats);

  const body = document.createElement("div");
  body.className = "chapter-body";
  body.append(
    renderGrammar(ch, "chapter", {
      chapterIndex: summary.chapter_index,
      combinations: chapterCombinations(inventory, ch.combination_counts),
    }),
    buildTable(CHAPTER_COLUMNS, rows)
  );

  details.append(sum, body);
  root.appendChild(details);
}

// -- charts (issue #15) -----------------------------------------------------
//
// Hand-rolled SVG, no charting library. The package is dependency-free on
// purpose and two charts do not pay for a bundle, a build step and a second
// theming system; what a library would give us here is a legend and an axis,
// both of which are twenty lines.
//
// Both charts follow the same three rules:
//   - Marks carry the colour, text never does. Labels, values and legends stay
//     in the page's own ink, so a pale fill is never asked to be readable type.
//   - Segments and cells are separated by a 2px gap in the surface, not by a
//     stroke around them. A border adds ink that is not data and, at these
//     sizes, thickens every small cell by a third.
//   - Every chart has a table beside it. A tooltip is an enhancement, never the
//     only way to read a number, which also keeps the figures reachable to a
//     reader who never sees the chart at all.

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(name, attrs = {}, text = null) {
  const el = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) el.setAttribute(key, String(value));
  }
  if (text !== null) el.textContent = text;
  return el;
}

// A rounded data-end and a square baseline: the top of a column is the value
// and gets the radius, the bottom is the axis and has to sit flat on it.
function columnPath(x, y, w, h, radius = 4) {
  const r = Math.max(0, Math.min(radius, w / 2, h));
  if (r === 0) return `M${x} ${y}h${w}v${h}h${-w}Z`;
  return (
    `M${x} ${y + h}` +
    `V${y + r}` +
    `Q${x} ${y} ${x + r} ${y}` +
    `H${x + w - r}` +
    `Q${x + w} ${y} ${x + w} ${y + r}` +
    `V${y + h}` +
    "Z"
  );
}

// Ticks land on 1/2/2.5/5 x 10^n so the reader divides by round numbers.
// Without it the top tick reads 1,347 and every gridline below it is
// arithmetic.
function niceScale(max, count = 4) {
  if (!(max > 0)) return { max: 1, ticks: [0, 1] };
  const magnitude = 10 ** Math.floor(Math.log10(max / count));
  const step =
    [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= max / count) ?? 10 * magnitude;
  const top = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = 0; v <= top + step / 1000; v += step) ticks.push(Math.round(v * 1000) / 1000);
  return { max: top, ticks };
}

// One tooltip per chart, positioned against the chart's own box. Marks are
// hovered *and* focused: a keyboard reader gets the same readout, which is the
// reason the hit targets are elements rather than one mousemove handler over
// the plot.
function attachTip(host) {
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  host.appendChild(tip);
  return {
    show(html, target) {
      tip.innerHTML = html;
      tip.hidden = false;
      const box = host.getBoundingClientRect();
      const mark = target.getBoundingClientRect();
      const clamp = (v, max) => Math.max(4, Math.min(v, max - 4));

      // Above the mark by preference. When there is no room — a tall column, a
      // cell near the top of the treemap — it goes *beside* the mark rather
      // than below it: dropping it down would cover the thing being read.
      let left = mark.left - box.left + mark.width / 2 - tip.offsetWidth / 2;
      let top = mark.top - box.top - tip.offsetHeight - 8;
      if (top < 0) {
        const room = host.clientWidth - (mark.right - box.left);
        left =
          room >= tip.offsetWidth + 12
            ? mark.right - box.left + 8
            : mark.left - box.left - tip.offsetWidth - 8;
        top = mark.top - box.top;
      }
      tip.style.left = `${clamp(left, host.clientWidth - tip.offsetWidth)}px`;
      tip.style.top = `${clamp(top, host.clientHeight - tip.offsetHeight)}px`;
    },
    hide() {
      tip.hidden = true;
    },
  };
}

// Both charts are laid out in pixels rather than in a scaled viewBox, so that
// an 11px label is 11px whatever the panel is doing. That means they need a
// width before they can be drawn — and a panel is built detached, so there is
// none at build time. Draw once at a sensible default, then redraw when the
// element learns how wide it is and whenever that changes.
function autosize(host, draw, fallback = 820) {
  draw(fallback);
  if (typeof ResizeObserver !== "function") return;
  let drawn = fallback;
  new ResizeObserver(([entry]) => {
    const width = Math.round(entry.contentRect.width);
    // A few pixels either way is a scrollbar appearing, not a resize worth
    // rebuilding a few hundred cells for.
    if (!width || Math.abs(width - drawn) < 8) return;
    drawn = width;
    draw(width);
  }).observe(host);
}

function chartCard(title, subtitle) {
  const card = document.createElement("section");
  card.className = "chart-card";
  const head = document.createElement("div");
  head.className = "chart-head";
  head.innerHTML =
    `<h4>${escapeHtml(title)}</h4>` +
    `<p class="chart-sub muted">${escapeHtml(subtitle)}</p>`;
  card.appendChild(head);
  return { card, head };
}

// The legend is not optional and not a fallback. Two fills that differ only by
// hue are one channel, and a reader who cannot separate those hues has nothing
// left; identity always has a written form on screen.
function chartLegend(entries) {
  const el = document.createElement("ul");
  el.className = "chart-legend";
  el.innerHTML = entries
    .map(
      (e) =>
        `<li><span class="chart-swatch series-${e.slot}"></span>${escapeHtml(e.label)}` +
        (e.note ? ` <span class="muted">${escapeHtml(e.note)}</span>` : "") +
        "</li>"
    )
    .join("");
  return el;
}

function share(part, whole) {
  return whole ? part / whole : 0;
}

// -- new vs. repeated vocabulary per chapter --------------------------------
//
// The progression half of the text lens: how much of each chapter the reader
// has already met. "New" is the chapter a key's first occurrence falls in — the
// same test as the `1st lemma` / `1st parse` badges on the chapter table, so
// these bars are that column added up.
//
// Bars stack **types**, not tokens. The question the chart exists for is
// vocabulary load — how many words this chapter asks the reader to learn — and
// tokens answer a different one, how much of the running text those words
// account for. Both travel on every point and the table view shows the pair;
// only the bar height had to choose.
//
// Already-met sits at the baseline and new stacks on top: the vocabulary the
// chapter can assume is the base it builds on, and the new words are what it
// adds. The cost is that new no longer starts from a common baseline, so
// comparing it across chapters means comparing segment lengths rather than
// reading off the gridlines — the tooltip and the table view carry the exact
// counts for when that is the question being asked.

const PROGRESS_HEIGHT = 260;
const PROGRESS_SLOT_MIN = 34; // also the hover target width, so it clears 24px
const PROGRESS_SLOT_MAX = 200;

// Chapters divide the panel between them until they would be narrower than a
// hover target; past that the plot keeps its slot and scrolls. A four-chapter
// text should not be a thumbnail in the corner of the card, and a sixty-chapter
// one cannot be squeezed into it without putting the bars below a pixel.
function progressSlot(n, available) {
  if (!n) return PROGRESS_SLOT_MIN;
  return Math.max(PROGRESS_SLOT_MIN, Math.min(PROGRESS_SLOT_MAX, Math.floor(available / n)));
}

function progressColumns(labels) {
  return [
    {
      label: "chapter",
      get: (r) => r.chapter_index,
      type: "num",
      align: "",
      text: (r) => labels.short(r.chapter_index),
      cellTitle: (r) => labels.long(r.chapter_index),
    },
    { label: "new", get: (r) => r.new_types, type: "num", title: "keys appearing here for the first time" },
    { label: "already met", get: (r) => r.repeated_types, type: "num" },
    {
      label: "total",
      get: (r) => r.new_types + r.repeated_types,
      type: "num",
      title: "distinct keys in this chapter — the height of the bar",
    },
    { label: "new tokens", get: (r) => r.new_tokens, type: "num", title: "occurrences here of the new keys" },
    { label: "repeated tokens", get: (r) => r.repeated_tokens, type: "num" },
    {
      label: "tokens",
      get: (r) => r.new_tokens + r.repeated_tokens,
      type: "num",
      title: "the chapter's token count — the two halves partition it exactly",
    },
    {
      label: "% new",
      get: (r) => share(r.new_types, r.new_types + r.repeated_types),
      type: "num",
      text: (r) => pct(share(r.new_types, r.new_types + r.repeated_types)),
    },
  ];
}

function progressPlot(points, labels, grainNoun, available) {
  const host = document.createElement("div");
  host.className = "chart-plot";

  const n = points.length;
  const margin = { top: 10, right: 10, bottom: 34, left: 46 };
  const slot = progressSlot(n, available - margin.left - margin.right);
  const width = margin.left + margin.right + Math.max(n, 1) * slot;
  const height = PROGRESS_HEIGHT;
  const baseline = height - margin.bottom;
  const plotHeight = baseline - margin.top;

  const totals = points.map((p) => p.new_types + p.repeated_types);
  const scale = niceScale(Math.max(0, ...totals));
  const y = (value) => baseline - (value / scale.max) * plotHeight;

  const root = svgEl("svg", {
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    class: "chart-svg",
    role: "img",
    "aria-label": `New versus already-met ${grainNoun} per chapter`,
  });

  for (const tick of scale.ticks) {
    const ty = y(tick);
    root.appendChild(
      svgEl("line", { x1: margin.left, x2: width - margin.right, y1: ty, y2: ty, class: "chart-grid" })
    );
    root.appendChild(
      svgEl(
        "text",
        { x: margin.left - 8, y: ty + 4, class: "chart-axis", "text-anchor": "end" },
        tick.toLocaleString()
      )
    );
  }

  // Label every chapter until they would collide, then every k-th; the tooltip
  // and the table carry the ones the axis drops.
  const labelEvery = Math.ceil(n / 24);
  const tip = attachTip(host);
  // Thin, and thinner than its band: the leftover is the air between columns,
  // so neighbours never read as one block.
  const barWidth = Math.max(6, Math.min(32, slot - 10, Math.round(slot * 0.45)));

  points.forEach((point, i) => {
    const slotX = margin.left + i * slot;
    const x = slotX + (slot - barWidth) / 2;
    const total = point.new_types + point.repeated_types;
    const metTop = y(point.repeated_types);
    const stackTop = y(total);
    // What the readout is anchored to. The hit target is the full height of the
    // plot, so hanging the tooltip off that would park it at the top of the
    // chart however short the column is; the top of the bar is where the reader
    // is looking. Each branch reassigns it in draw order, so it ends up being
    // whichever segment is on top.
    let top = null;

    if (point.repeated_types > 0) {
      const h = baseline - metTop;
      // Square top when the new segment stacks on it; the radius belongs to the
      // end of the whole bar, not to the seam between two segments.
      const d =
        point.new_types > 0
          ? columnPath(x, metTop, barWidth, h, 0)
          : columnPath(x, metTop, barWidth, h);
      top = svgEl("path", { d, class: "series-2" });
      root.appendChild(top);
    }
    if (point.new_types > 0) {
      // The 2px surface gap between the segments, taken off the upper one so
      // the lower keeps its baseline and the stack keeps its top edge: the gap
      // eats a pixel of ink, never a pixel of scale.
      const bottom = point.repeated_types > 0 ? metTop - 2 : baseline;
      top = svgEl("path", { d: columnPath(x, stackTop, barWidth, bottom - stackTop), class: "series-1" });
      root.appendChild(top);
    }

    if (i % labelEvery === 0) {
      root.appendChild(
        svgEl(
          "text",
          { x: slotX + slot / 2, y: baseline + 16, class: "chart-axis", "text-anchor": "middle" },
          labels.short(point.chapter_index)
        )
      );
    }

    const tokens = point.new_tokens + point.repeated_tokens;
    const readout =
      `<strong>${escapeHtml(labels.long(point.chapter_index))}</strong>` +
      `<span><span class="chart-swatch series-1"></span>${point.new_types.toLocaleString()} new` +
      ` <span class="muted">· ${point.new_tokens.toLocaleString()} tokens</span></span>` +
      `<span><span class="chart-swatch series-2"></span>${point.repeated_types.toLocaleString()} already met` +
      ` <span class="muted">· ${point.repeated_tokens.toLocaleString()} tokens</span></span>` +
      `<span class="muted">${total.toLocaleString()} ${escapeHtml(grainNoun)} · ` +
      `${tokens.toLocaleString()} tokens` +
      (total ? ` · ${pct(share(point.new_types, total))} new` : "") +
      "</span>";

    const hit = svgEl("rect", {
      x: slotX,
      y: margin.top,
      width: slot,
      height: plotHeight,
      class: "chart-hit",
      tabindex: "0",
      role: "button",
      "aria-label":
        `${labels.long(point.chapter_index)}: ${point.new_types} new, ` +
        `${point.repeated_types} already met, ${total} ${grainNoun}`,
    });
    const show = () => tip.show(readout, top || hit);
    hit.addEventListener("mouseenter", show);
    hit.addEventListener("focus", show);
    hit.addEventListener("mouseleave", () => tip.hide());
    hit.addEventListener("blur", () => tip.hide());
    root.appendChild(hit);
  });

  root.appendChild(
    svgEl("line", {
      x1: margin.left,
      x2: width - margin.right,
      y1: baseline,
      y2: baseline,
      class: "chart-axis-line",
    })
  );
  root.appendChild(
    svgEl(
      "text",
      {
        x: margin.left + (width - margin.left - margin.right) / 2,
        y: height - 4,
        class: "chart-axis",
        "text-anchor": "middle",
      },
      "chapter"
    )
  );

  const scroll = document.createElement("div");
  scroll.className = "chart-scroll";
  scroll.appendChild(root);
  host.appendChild(scroll);
  return host;
}

function renderProgressionChart(textReport, labels) {
  const { card, head } = chartCard(
    "New vs. repeated vocabulary",
    "Distinct words per chapter, split by whether the reader has met them before."
  );

  const controls = document.createElement("div");
  controls.className = "chart-controls";
  controls.innerHTML = `
    <span class="segmented" role="group" aria-label="Row grain">
      <button type="button" class="btn-quiet" data-grain="lemma">lemma</button>
      <button type="button" class="btn-quiet" data-grain="parse">lemma + parse</button>
    </span>
    <button type="button" class="btn-quiet chart-view"></button>
  `;
  head.append(
    chartLegend([
      { slot: 1, label: "new here" },
      { slot: 2, label: "already met" },
    ]),
    controls
  );

  const body = document.createElement("div");
  body.className = "chart-body";
  card.appendChild(body);

  const viewButton = controls.querySelector(".chart-view");
  let grain = "lemma";
  let view = "chart";
  let width = 820;

  function note(text) {
    return Object.assign(document.createElement("p"), { className: "muted", textContent: text });
  }

  function draw() {
    const points = grain === "parse" ? textReport.parse_progress : textReport.lemma_progress;
    const grainNoun = grain === "parse" ? "lemma+parse pairs" : "lemmas";
    controls.querySelectorAll("[data-grain]").forEach((b) => {
      b.classList.toggle("active", b.dataset.grain === grain);
    });
    viewButton.textContent = view === "chart" ? "Table" : "Chart";
    if (!points.length) {
      body.replaceChildren(note("This run has no chapters to chart."));
      return;
    }
    if (view === "table") {
      body.replaceChildren(buildTable(progressColumns(labels), points, { className: "table-narrow" }));
      return;
    }
    // A single chapter is a single bar, and a single bar is a sentence with
    // axes drawn around it: everything in the first chapter is new by
    // definition, so there is no progression to plot yet.
    if (points.length === 1) {
      const only = points[0];
      body.replaceChildren(
        note(
          `One chapter, so all ${only.new_types.toLocaleString()} ${grainNoun} in it are new — ` +
            `${only.new_tokens.toLocaleString()} tokens. A second chapter is what makes this a progression.`
        )
      );
      return;
    }
    body.replaceChildren(progressPlot(points, labels, grainNoun, width));
  }

  controls.querySelectorAll("[data-grain]").forEach((b) => {
    b.addEventListener("click", () => {
      grain = b.dataset.grain;
      draw();
    });
  });
  viewButton.addEventListener("click", () => {
    view = view === "chart" ? "table" : "chart";
    draw();
  });

  autosize(body, (available) => {
    width = available;
    draw();
  });
  return card;
}

// -- the form treemap -------------------------------------------------------
//
// The other half of "at a glance": which grammar the text is made of. Area is
// occurrences and the four form classes are the four blocks, so "am I writing a
// participle-heavy text" is answered by looking rather than by adding up a
// column.
//
// A treemap is the right form here for one reason: these rows partition. Every
// token carrying morphology sits in exactly one cell, so the areas are a whole
// and dividing a rectangle up is an honest picture of it. The per-dimension
// cards below could not be drawn this way — a syncretic `nom./acc.` is counted
// under both its readings there, and area would double-count it.
//
// Squarified layout (Bruls, Huizing & van Wijk, 2000): cells are laid in rows
// along the shorter side, and a row closes when adding another cell would make
// the aspect ratio worse. The naive slice-and-dice alternative turns a few
// hundred cells into unreadable, unhoverable slivers.

function squarify(items, x, y, w, h) {
  const out = [];
  const total = items.reduce((sum, it) => sum + it.value, 0);
  if (!items.length || total <= 0 || w <= 0 || h <= 0) return out;

  const queue = items.slice();
  const scale = (w * h) / total; // constant: each row consumes exactly its area
  let area = { x, y, w, h };
  let row = [];

  const worst = (candidate) => {
    if (!candidate.length) return Infinity;
    const side = Math.min(area.w, area.h);
    const sum = candidate.reduce((s, it) => s + it.value, 0) * scale;
    const areas = candidate.map((it) => it.value * scale);
    return Math.max(
      (side * side * Math.max(...areas)) / (sum * sum),
      (sum * sum) / (side * side * Math.min(...areas))
    );
  };

  const place = (finished) => {
    const side = Math.min(area.w, area.h);
    const depth = (finished.reduce((s, it) => s + it.value, 0) * scale) / side;
    const downward = area.w >= area.h; // the strip runs down the left edge of a wide area
    let offset = 0;
    for (const item of finished) {
      const length = (item.value * scale) / depth;
      out.push(
        downward
          ? { item, x: area.x, y: area.y + offset, w: depth, h: length }
          : { item, x: area.x + offset, y: area.y, w: length, h: depth }
      );
      offset += length;
    }
    area = downward
      ? { x: area.x + depth, y: area.y, w: area.w - depth, h: area.h }
      : { x: area.x, y: area.y + depth, w: area.w, h: area.h - depth };
  };

  while (queue.length) {
    if (!row.length || worst([...row, queue[0]]) <= worst(row)) {
      row.push(queue.shift());
    } else {
      place(row);
      row = [];
    }
  }
  if (row.length) place(row);
  return out;
}

const TREEMAP = { minWidth: 420, height: 400, header: 18, gap: 2 };

// Slot 4 is a neutral grey rather than a fourth hue, on purpose. "Other" is the
// bucket the classifier could not place, not a class anyone compares the others
// against, and a fourth saturated hue would put yellow beside orange — a pair
// full-colour readers separate by less than the floor these fills are held to.
const FORM_CLASS_SLOT = { verb: 1, participle: 2, nominal: 3, other: 4 };

// Roughly, at the weights these labels are set in. Deliberately generous: the
// cost of over-estimating is a label this drops, and the cost of
// under-estimating is one that spills over the edge of its cell.
function fitsLabel(text, w, h, size) {
  return h >= size + 6 && w >= text.length * size * 0.56 + 8;
}

function treemapPlot(table, width) {
  const host = document.createElement("div");
  host.className = "chart-plot";
  const tip = attachTip(host);

  const root = svgEl("svg", {
    width,
    height: TREEMAP.height,
    viewBox: `0 0 ${width} ${TREEMAP.height}`,
    class: "chart-svg",
    role: "img",
    "aria-label": "Treemap of grammatical forms, sized by occurrences",
  });

  const blocks = squarify(
    table.groups.map((g) => ({ group: g, value: g.tokens })),
    0,
    0,
    width,
    TREEMAP.height
  );

  for (const block of blocks) {
    const group = block.item.group;
    const slot = FORM_CLASS_SLOT[group.key] || 4;
    const inner = {
      x: block.x + TREEMAP.gap / 2,
      y: block.y + TREEMAP.gap / 2,
      w: block.w - TREEMAP.gap,
      h: block.h - TREEMAP.gap,
    };

    // The class name rides on the block itself, not only in the legend: it is
    // the identity channel that survives a reader who cannot separate the
    // fills, and it costs one strip of surface per block.
    //
    // Both parts are measured first. A narrow class — participles are a tenth
    // of a text — has room for its name and not for its count, and letting the
    // count run on would print it across the *next* block's name, which reads
    // as one label belonging to the wrong colour.
    const labelWidth = group.label.length * 6.8;
    const count = `${group.tokens.toLocaleString()} tokens`;
    if (inner.w >= labelWidth + 4) {
      root.appendChild(
        svgEl("text", { x: inner.x + 1, y: inner.y + 11, class: "chart-block-label" }, group.label)
      );
      if (inner.w >= labelWidth + count.length * 6 + 12) {
        root.appendChild(
          svgEl(
            "text",
            { x: inner.x + 1, y: inner.y + 11, dx: labelWidth + 8, class: "chart-axis" },
            count
          )
        );
      }
    }

    const rows = group.rows
      .filter((r) => r.occ > 0)
      .slice()
      .sort((a, b) => b.occ - a.occ);
    const cells = squarify(
      rows.map((r) => ({ row: r, value: r.occ })),
      inner.x,
      inner.y + TREEMAP.header,
      inner.w,
      inner.h - TREEMAP.header
    );

    for (const cell of cells) {
      const row = cell.item.row;
      const w = cell.w - TREEMAP.gap;
      const h = cell.h - TREEMAP.gap;
      if (w <= 0.5 || h <= 0.5) continue; // thinner than the gap: nothing to draw
      const x = cell.x + TREEMAP.gap / 2;
      const y = cell.y + TREEMAP.gap / 2;

      const rect = svgEl("rect", {
        x,
        y,
        width: w,
        height: h,
        rx: Math.min(2, w / 2, h / 2),
        class: `series-${slot} chart-cell`,
        tabindex: "0",
        role: "button",
        "aria-label": `${row.form}, ${group.label}: ${row.occ} occurrences`,
      });
      const readout =
        `<strong>${escapeHtml(row.form)}</strong>` +
        `<span><span class="chart-swatch series-${slot}"></span>${escapeHtml(group.label)}</span>` +
        `<span>${row.occ.toLocaleString()} occurrence${row.occ === 1 ? "" : "s"}` +
        ` <span class="muted">· ${pct(share(row.occ, table.tokens))} of the morphology</span></span>` +
        `<span class="muted">chapter${row.chapter_count === 1 ? "" : "s"} ` +
        `${row.first_chapter + 1}–${row.last_chapter + 1} · in ${row.chapter_count} of them</span>`;
      const show = () => tip.show(readout, rect);
      rect.addEventListener("mouseenter", show);
      rect.addEventListener("focus", show);
      rect.addEventListener("mouseleave", () => tip.hide());
      rect.addEventListener("blur", () => tip.hide());
      root.appendChild(rect);

      // Labels are measured before they are drawn. A clipped one is worse than
      // none — it crops exactly the features that identify the cell — and every
      // label here is a row in the form table below regardless.
      if (fitsLabel(row.form, w, h, 11)) {
        root.appendChild(
          svgEl("text", { x: x + 4, y: y + 13, class: `chart-cell-label ink-${slot}` }, row.form)
        );
        const count = String(row.occ);
        if (h >= 30 && fitsLabel(count, w, h - 14, 10)) {
          root.appendChild(
            svgEl("text", { x: x + 4, y: y + 25, class: `chart-cell-count ink-${slot}` }, count)
          );
        }
      }
    }
  }

  const scroll = document.createElement("div");
  scroll.className = "chart-scroll";
  scroll.appendChild(root);
  host.appendChild(scroll);
  return host;
}

function renderFormTreemap(table) {
  const { card, head } = chartCard(
    "Grammatical forms in the whole text",
    "Every paradigm cell the text attests, sized by how often it occurs."
  );
  if (!table || !table.groups.length || !table.tokens) {
    card.appendChild(
      Object.assign(document.createElement("p"), {
        className: "muted",
        textContent: "No morphology was decoded in this run, so there is nothing to break down.",
      })
    );
    return card;
  }

  head.appendChild(
    chartLegend(
      table.groups.map((g) => ({
        slot: FORM_CLASS_SLOT[g.key] || 4,
        label: g.label,
        note: `${g.tokens.toLocaleString()} · ${pct(share(g.tokens, table.tokens))}`,
      }))
    )
  );

  const body = document.createElement("div");
  body.className = "chart-body";
  card.appendChild(body);
  autosize(body, (available) =>
    body.replaceChildren(treemapPlot(table, Math.max(TREEMAP.minWidth, available)))
  );

  const note = document.createElement("p");
  note.className = "chart-note muted";
  note.textContent =
    `${table.tokens.toLocaleString()} tokens carry morphology, across ` +
    `${table.groups.reduce((n, g) => n + g.rows.length, 0).toLocaleString()} distinct forms. ` +
    "Every cell is a row in the form tables below, where it can be sorted and read exactly.";
  card.appendChild(note);
  return card;
}

// -- text lens (issues #5 / #15) --------------------------------------------
//
// One table, one grain toggle — the same lemma/parse toggle the chart above it
// carries. The two grains answer different questions — "how much of this word is
// in the text" versus "how much of this word *in this form*" — and a lemma with
// eight parses is eight rows in one view and one in the other.
//
// `chapter_count` is deliberately not derivable from first/last: a lemma in
// chapters 1 and 20 spans twenty and appears in two, and that gap is the
// repetition question.

function chapterLabels(textReport) {
  const byIndex = new Map();
  for (const ch of textReport.chapters || []) byIndex.set(ch.chapter_index, ch);
  return {
    short: (i) => `${i + 1}`,
    // Chapter indexes are 0-based corpus-wide; the label a human recognises is
    // the title, so it rides along as a tooltip rather than widening the column.
    long: (i) => {
      const ch = byIndex.get(i);
      return ch ? `${i + 1}. ${ch.title}${ch.filename ? ` (${ch.filename})` : ""}` : `${i + 1}`;
    },
  };
}

function textColumns(grain, labels) {
  const cols = [
    { label: "type", get: (r) => r.type },
    { label: "lemma", get: (r) => r.lemma, cls: "greek" },
  ];
  if (grain === "parse") cols.push({ label: "parse", get: (r) => r.parse });
  cols.push(
    { label: "form", get: (r) => r.form, cls: "greek", title: "most frequent surface form — a representative, not a key" },
    { label: "forms", get: (r) => r.form_count, type: "num", title: "distinct surface forms this row covers" },
    { label: "total", get: (r) => r.total, type: "num", title: "occurrences across the whole text" },
    {
      label: "1st ch",
      get: (r) => r.first_chapter,
      type: "num",
      text: (r) => labels.short(r.first_chapter),
      cellTitle: (r) => labels.long(r.first_chapter),
      title: "chapter it is introduced in",
    },
    {
      label: "last ch",
      get: (r) => r.last_chapter,
      type: "num",
      text: (r) => labels.short(r.last_chapter),
      cellTitle: (r) => labels.long(r.last_chapter),
      title: "chapter it last appears in",
    },
    { label: "# chs", get: (r) => r.chapter_count, type: "num", title: "distinct chapters it appears in — not last minus first" }
  );
  return cols;
}

function renderTextPanel(textReport) {
  const panel = document.createElement("div");
  panel.className = "tab-panel";

  if (!textReport) {
    panel.innerHTML = `<p class="muted">This report has no text-wide data. Re-run the analysis to build it.</p>`;
    return panel;
  }

  const s = textReport.summary;
  const head = document.createElement("p");
  head.className = "text-summary";
  head.textContent =
    `${s.chapter_count} chapter${s.chapter_count === 1 ? "" : "s"} · ` +
    `${s.token_count.toLocaleString()} tokens · ` +
    `${s.unique_lemmas.toLocaleString()} lemmas · ` +
    `${s.unique_parses.toLocaleString()} lemma+parse pairs · ` +
    `${s.unique_forms.toLocaleString()} surface forms`;
  panel.appendChild(head);

  const labels = chapterLabels(textReport);

  // Charts first: the tab's job is "at a glance", and the two questions it
  // answers at a glance are how the vocabulary accumulates and what grammar the
  // text is made of. The tables under them are the same numbers exactly, for
  // when a glance is not enough.
  const charts = document.createElement("div");
  charts.className = "chart-stack";
  charts.append(
    renderProgressionChart(textReport, labels),
    renderFormTreemap(textReport.form_combinations)
  );
  panel.appendChild(charts);

  const grammar = document.createElement("details");
  grammar.className = "grammar-details";
  grammar.open = true;
  const gsum = document.createElement("summary");
  gsum.textContent = "Grammar profile — whole text";
  grammar.append(
    gsum,
    renderGrammar(textReport, "text", { labels, combinations: textReport.form_combinations })
  );
  panel.appendChild(grammar);
  const toolbar = document.createElement("div");
  toolbar.className = "grain-toolbar";
  toolbar.innerHTML = `
    <span class="grain-label muted">Vocabulary by</span>
    <span class="segmented" role="group" aria-label="Row grain">
      <button type="button" class="btn-quiet" data-grain="lemma">lemma</button>
      <button type="button" class="btn-quiet" data-grain="parse">lemma + parse</button>
    </span>
    <span class="grain-count muted"></span>
  `;
  const count = toolbar.querySelector(".grain-count");
  const tableSlot = document.createElement("div");

  function show(grain) {
    const rows = grain === "parse" ? textReport.parse_rows : textReport.lemma_rows;
    toolbar.querySelectorAll("[data-grain]").forEach((b) => {
      b.classList.toggle("active", b.dataset.grain === grain);
    });
    count.textContent = `${rows.length.toLocaleString()} rows · sorted by total, click any header to re-sort`;
    tableSlot.replaceChildren(buildTable(textColumns(grain, labels), rows));
  }
  toolbar.querySelectorAll("[data-grain]").forEach((b) => {
    b.addEventListener("click", () => show(b.dataset.grain));
  });

  panel.append(toolbar, tableSlot);
  show("lemma");
  return panel;
}

// -- CSV export (issue #3) --------------------------------------------------
//
// Exporting is a per-run action, so it lives on the run in the history table
// rather than on the report: you can pull the CSV for any stored run without
// first rendering it, and without disturbing whatever is already on screen.
//
// Built in the browser from `GET /api/runs/{id}/report` rather than from a
// dedicated `export.csv` route. That response *is* the displayed grain, so
// there is nothing extra to derive server-side, and re-deriving it is free —
// no provider call. The cost of this placement is that a report reachable
// without a stored run behind it (LINGUA_PERSIST_RUNS=false hides history
// entirely) has no export path; re-enable persistence to export.
//
// Grain is one row per chapter × lemma × parse: the rows the table shows, with
// the file/chapter they belong to spliced in so the whole corpus lands in a
// single flat file that pivots. Order is report order (first appearance in the
// chapter), not whatever the on-screen sort happens to be — the sort is a
// reading aid, and a spreadsheet re-sorts anyway.

const EXPORT_HEADER = [
  "file",
  "chapter_index",
  "chapter_id",
  "chapter_title",
  "type",
  "lemma",
  "form",
  "parse",
  "lemma_occ",
  "parse_occ",
  "form_occ",
  "first_occ_lemma",
  "first_occ_parse",
  "last_occ_lemma",
  "last_occ_parse",
  "lemma_first_chapter",
  "lemma_last_chapter",
  "parse_first_chapter",
  "parse_last_chapter",
];

// `chapter_index` and the four `*_chapter` columns are the model's 0-based
// corpus-wide indexes, left raw so they compare against each other: a row is a
// lemma's first appearance exactly when lemma_first_chapter == chapter_index.
// The booleans alongside them are that comparison already done, because "is
// this the first time?" is the question the author actually asks.
function exportRows(data) {
  const rows = [];
  for (const fileRep of data.file_reports || []) {
    for (const ch of fileRep.chapters) {
      for (const r of ch.rows) {
        rows.push([
          fileRep.filename,
          ch.summary.chapter_index,
          ch.summary.id,
          ch.summary.title,
          r.type,
          r.lemma,
          r.form,
          r.parse,
          r.lemma_occ,
          r.parse_occ,
          r.form_occ,
          r.first_occ_lemma,
          r.first_occ_parse,
          r.last_occ_lemma,
          r.last_occ_parse,
          r.lemma_first_chapter,
          r.lemma_last_chapter,
          r.parse_first_chapter,
          r.parse_last_chapter,
        ]);
      }
    }
  }
  return rows;
}

function csvCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  const s = String(value);
  return /["\r\n,]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

function toCsv(header, rows) {
  // CRLF per RFC 4180; Excel is the likeliest destination and is happiest with it.
  return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

function exportFilename(data) {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
  const names = (data.file_reports || []).map((f) => f.filename.replace(/\.docx$/i, ""));
  // One source file names the export after it; several would make an unreadable
  // filename, so they fall back to a count.
  const base =
    names.length === 1
      ? names[0].replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "")
      : `${names.length}-files`;
  return `lingua-calc-${base || "report"}-${stamp}.csv`;
}

function downloadCsv(filename, text) {
  // Every lemma and form in here is Greek, and Excel reads a BOM-less UTF-8 CSV
  // as the local codepage — i.e. as mojibake. The leading U+FEFF is what makes
  // the file openable by double-click rather than through the import wizard.
  // Spelled as a char code so the BOM cannot be lost to an editor or a tool
  // that strips it from source on save.
  const blob = new Blob([String.fromCharCode(0xfeff), text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function renderChaptersPanel(data) {
  const panel = document.createElement("div");
  panel.className = "tab-panel";

  // The backend always returns a MultiFileReport: one section per file. File
  // grouping stays inside this tab only — chapter indexes are corpus-wide and
  // files are ordered before indexing, so every cumulative figure already spans
  // the whole upload regardless of which file a chapter came from.
  const inventory = data.text_report ? data.text_report.form_combinations : null;

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
      renderChapter(ch, chapterContainer, inventory);
    }

    fileDetails.append(fileSummary, chapterContainer);
    panel.appendChild(fileDetails);
  }
  return panel;
}

function renderLemmaPanel() {
  const panel = document.createElement("div");
  panel.className = "tab-panel";
  panel.innerHTML = `<p class="muted">Not built yet.</p>`;
  return panel;
}

// Which lens is on screen. Module-level so switching between stored runs keeps
// you in the tab you were reading rather than snapping back to Chapters.
let activeTab = "chapters";

const TABS = [
  { key: "chapters", label: "Chapters", build: (data) => renderChaptersPanel(data) },
  { key: "text", label: "Text", build: (data) => renderTextPanel(data.text_report) },
  { key: "lemma", label: "Lemma", build: () => renderLemmaPanel() },
];

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

  const bar = document.createElement("div");
  bar.className = "tab-bar";
  bar.setAttribute("role", "tablist");

  const slot = document.createElement("div");
  slot.className = "tab-slot";

  // Panels are built on first view and cached. The text table can be a couple
  // of thousand rows, and building all three up front would make opening a
  // report pay for lenses the author may not look at.
  const built = new Map();

  function select(key) {
    activeTab = key;
    bar.querySelectorAll("[data-tab]").forEach((b) => {
      const on = b.dataset.tab === key;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", String(on));
    });
    if (!built.has(key)) built.set(key, TABS.find((t) => t.key === key).build(data));
    slot.replaceChildren(built.get(key));
  }

  for (const tab of TABS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tab";
    btn.dataset.tab = tab.key;
    btn.setAttribute("role", "tab");
    btn.textContent = tab.label;
    btn.addEventListener("click", () => select(tab.key));
    bar.appendChild(btn);
  }

  root.append(bar, slot);
  select(TABS.some((t) => t.key === activeTab) ? activeTab : "chapters");
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
// stray click away from the View and Export buttons next to it.
function renderHistory(page, handlers) {
  const { runs, total, offset, limit } = page;
  const { onView, onExport, onDeleteMany, onPage } = handlers;
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
        <button type="button" class="btn-quiet" data-act="export">Export CSV</button>
      </td>
    `;
    const box = tr.querySelector('[data-act="select"]');
    box.addEventListener("change", () => {
      box.checked ? selected.add(run.id) : selected.delete(run.id);
      tr.classList.toggle("selected", box.checked);
      syncSelectionUi();
    });
    tr.querySelector('[data-act="view"]').addEventListener("click", () => onView(run, label));

    // Export has to fetch the run's report first, which on a large run is not
    // instant — hold the button down for the round trip so a second click
    // cannot start a second download of the same thing.
    const exportBtn = tr.querySelector('[data-act="export"]');
    exportBtn.addEventListener("click", async () => {
      exportBtn.disabled = true;
      try {
        await onExport(run, label);
      } finally {
        exportBtn.disabled = false;
      }
    });
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

  // Deliberately does not call renderReport: exporting a run is not a request
  // to look at it, and swapping the report out from under the reader would be a
  // surprising side effect of asking for a download.
  async function exportRun(run, label) {
    setStatus(status, `Building CSV for ${label}…`);
    try {
      const res = await fetch(`/api/runs/${run.id}/report`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setStatus(status, body.detail || "Could not load that run.", true);
        return;
      }
      const data = await res.json();
      const rows = exportRows(data);
      if (!rows.length) {
        setStatus(status, `That run has no rows to export.`, true);
        return;
      }
      downloadCsv(exportFilename(data), toCsv(EXPORT_HEADER, rows));
      setStatus(status, `Exported ${rows.length.toLocaleString()} rows from ${label}.`);
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
      onExport: exportRun,
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
