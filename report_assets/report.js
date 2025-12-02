const state = {
  payload: null,
  workbookFilter: "__all__",
};

const fmt = (val, digits = 2) => {
  if (val === undefined || val === null || isNaN(val)) return "—";
  return Number(val).toFixed(digits);
};

const createCard = ({ label, value, subtext }) => {
  const card = document.createElement("div");
  card.className = "card";

  const labelEl = document.createElement("div");
  labelEl.className = "label";
  labelEl.textContent = label;
  card.appendChild(labelEl);

  const valueEl = document.createElement("div");
  valueEl.className = "value";
  valueEl.textContent = value;
  card.appendChild(valueEl);

  if (subtext) {
    const subtextEl = document.createElement("div");
    subtextEl.className = "subtext";
    subtextEl.textContent = subtext;
    card.appendChild(subtextEl);
  }

  return card;
};

const renderSummaryCards = (payload) => {
  const grid = document.getElementById("summary-grid");
  grid.innerHTML = "";
  let cards = [];

  if (payload.mode === "workbook" && payload.workbook) {
    const s = payload.workbook.summary || {};
    cards = [
      { label: "Overall Score", value: fmt(s.overall_score) },
      { label: "Worksheets", value: s.num_worksheets ?? "0" },
      { label: "Max Score", value: fmt(s.max_score) },
      { label: "Min Score", value: fmt(s.min_score) },
      { label: "Calculated Fields", value: s.total_calc_fields ?? "0" },
      {
        label: "Formula Complexity",
        value: fmt(s.formula_complexity_total),
        subtext: `Avg ${fmt(s.formula_complexity_avg)}`,
      },
    ];
  } else if (payload.mode === "directory" && payload.directory) {
    const c = payload.directory.corpus_summary || {};
    cards = [
      { label: "Avg Overall Score", value: fmt(c.overall_score_avg) },
      { label: "Total Workbooks", value: c.num_workbooks ?? "0" },
      { label: "Total Worksheets", value: c.total_worksheets ?? "0" },
      { label: "Max Workbook Score", value: fmt(c.overall_score_max) },
      { label: "Worksheets w/ Table Calcs", value: `${fmt(c.worksheets_with_table_calc_pct, 1)}%` },
      {
        label: "Formula Complexity",
        value: fmt(c.formula_complexity_total),
        subtext: `Avg ${fmt(c.formula_complexity_avg)}`,
      },
    ];
  }

  cards.forEach((card) => grid.appendChild(createCard(card)));
};

const getFilteredWorksheets = () => {
  const payload = state.payload;
  if (!payload) return [];
  if (payload.mode === "workbook" && payload.workbook) {
    return payload.workbook.worksheets || [];
  }
  if (payload.mode === "directory" && payload.directory) {
    const rows = payload.directory.all_worksheets || [];
    if (state.workbookFilter === "__all__") {
      return rows;
    }
    return rows.filter((row) => row.workbook === state.workbookFilter);
  }
  return [];
};

const renderWorksheetTable = (rows) => {
  const tbody = document.querySelector("#worksheet-table tbody");
  tbody.innerHTML = "";
  if (!rows || !rows.length) return;
  rows.forEach((ws) => {
    const tr = document.createElement("tr");

    const worksheetCell = document.createElement("td");
    worksheetCell.innerHTML = `<div class="worksheet-name">${ws.worksheet}</div>${ws.workbook ? `<div class="subtext">${ws.workbook}</div>` : ""}`;
    tr.appendChild(worksheetCell);

    const complexityCell = document.createElement("td");
    complexityCell.textContent = fmt(ws.complexity_score);
    tr.appendChild(complexityCell);

    const markCell = document.createElement("td");
    if (Array.isArray(ws.mark_types)) {
      ws.mark_types.forEach((m) => {
        const pill = document.createElement("span");
        pill.className = "pill";
        pill.textContent = m;
        markCell.appendChild(pill);
      });
    }
    tr.appendChild(markCell);

    const shelfCell = document.createElement("td");
    shelfCell.textContent = `${ws.shelf_density ?? 0} channels`;
    tr.appendChild(shelfCell);

    const calcCell = document.createElement("td");
    const calcCount = ws.num_calculated_fields_est ?? 0;
    calcCell.innerHTML = `<strong>${calcCount}</strong> fields<br>Avg complexity ${fmt(ws.calc_formula_complexity_avg)}`;
    tr.appendChild(calcCell);

    const metricCell = document.createElement("td");
    const dims = ws.num_dimensions_est ?? 0;
    const meas = ws.num_measures_est ?? 0;
    const tableCalc = ws.has_table_calc_ws ? "Yes" : "No";
    metricCell.innerHTML = `Dims: ${dims} · Meas: ${meas}<br>Table Calc: ${tableCalc}<br>Filters: ${ws.num_filters ?? 0}`;
    tr.appendChild(metricCell);

    tbody.appendChild(tr);
  });
};

const renderCalcAccordion = (worksheets) => {
  const container = document.getElementById("calc-accordion");
  container.innerHTML = "";
  if (!worksheets || !worksheets.length) {
    container.innerHTML = "<p class='subtext'>No calculated fields detected.</p>";
    return;
  }

  worksheets.forEach((ws) => {
    if (!Array.isArray(ws.calculated_fields) || !ws.calculated_fields.length) return;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `${ws.worksheet} · ${ws.calculated_fields.length} calculated field(s)`;
    details.appendChild(summary);

    ws.calculated_fields.forEach((calc) => {
      const row = document.createElement("div");
      row.className = "formula-row";
      const heading = document.createElement("div");
      heading.innerHTML = `<strong>${calc.field || "Calculation"}</strong> · Complexity ${fmt(calc.formula_complexity)}`;
      row.appendChild(heading);

      const pre = document.createElement("pre");
      pre.textContent = calc.formula || "";
      row.appendChild(pre);

      details.appendChild(row);
    });

    container.appendChild(details);
  });
};

const renderMarkChart = (summary) => {
  const chartContainer = document.getElementById("mark-chart");
  chartContainer.innerHTML = "";
  if (!summary || !Array.isArray(summary.top_mark_types) || !summary.top_mark_types.length) {
    chartContainer.classList.add("hidden");
    return;
  }
  chartContainer.classList.remove("hidden");
  const maxVal = summary.top_mark_types.reduce((max, [, count]) => Math.max(max, count), 0) || 1;
  const chartTitle = document.createElement("h3");
  chartTitle.textContent = "Top Mark Types";
  chartContainer.appendChild(chartTitle);
  const chart = document.createElement("div");
  chart.className = "bar-chart";
  summary.top_mark_types.slice(0, 8).forEach(([label, count]) => {
    const row = document.createElement("div");
    row.className = "bar-row";
    const labelEl = document.createElement("div");
    labelEl.className = "bar-label";
    labelEl.textContent = label;
    row.appendChild(labelEl);
    const track = document.createElement("div");
    track.className = "bar-track";
    const fill = document.createElement("div");
    fill.className = "bar-fill";
    fill.style.width = `${(count / maxVal) * 100}%`;
    track.appendChild(fill);
    row.appendChild(track);
    const value = document.createElement("div");
    value.className = "bar-value";
    value.textContent = count;
    row.appendChild(value);
    chart.appendChild(row);
  });
  chartContainer.appendChild(chart);
};

const renderDirectorySection = (payload) => {
  const section = document.getElementById("directory-section");
  const tableBody = document.querySelector("#workbook-table tbody");
  tableBody.innerHTML = "";
  if (payload.mode !== "directory" || !payload.directory) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const corpusCards = [];
  const c = payload.directory.corpus_summary || {};
  corpusCards.push(
    createCard({ label: "Total Workbooks", value: c.num_workbooks ?? "0" }),
    createCard({ label: "Total Worksheets", value: c.total_worksheets ?? "0" }),
    createCard({ label: "Avg Worksheet Complexity", value: fmt(c.worksheet_complexity_avg) })
  );
  const dirSummary = document.getElementById("directory-summary");
  dirSummary.innerHTML = "";
  corpusCards.forEach((card) => dirSummary.appendChild(card));
  renderMarkChart(c);

  (payload.directory.workbooks || []).forEach((wb) => {
    const tr = document.createElement("tr");
    const s = wb.summary || {};
    tr.innerHTML = `
      <td>${wb.workbook || "Workbook"}</td>
      <td>${fmt(s.overall_score)}</td>
      <td>${s.num_worksheets ?? 0}</td>
      <td>${fmt(s.max_score)}</td>
      <td>${fmt(s.min_score)}</td>
      <td>${s.total_calc_fields ?? 0}</td>
      <td>${fmt(s.formula_complexity_total)}</td>
    `;
    tableBody.appendChild(tr);
  });
};

const setupWorkbookFilter = (payload) => {
  const section = document.getElementById("filter-section");
  const select = document.getElementById("workbook-filter");
  select.innerHTML = "";
  if (payload.mode !== "directory" || !payload.directory) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const optionAll = document.createElement("option");
  optionAll.value = "__all__";
  optionAll.textContent = "All workbooks";
  select.appendChild(optionAll);
  (payload.directory.workbooks || []).forEach((wb) => {
    const opt = document.createElement("option");
    opt.value = wb.workbook;
    opt.textContent = wb.workbook;
    select.appendChild(opt);
  });
  select.value = state.workbookFilter;
  select.addEventListener("change", () => {
    state.workbookFilter = select.value;
    updateWorksheetSections();
  });
};

const updateWorksheetSections = () => {
  const rows = getFilteredWorksheets().slice(0, 300);
  renderWorksheetTable(rows);
  renderCalcAccordion(rows);
};

const initReport = () => {
  const dataEl = document.getElementById("report-data");
  if (!dataEl) return;
  const payload = JSON.parse(dataEl.textContent || "{}");
  state.payload = payload;
  state.workbookFilter = "__all__";
  document.getElementById("report-title").textContent = payload.title || "Workbook Report";
  document.getElementById("generated-at").textContent = payload.generated_at || "";

  renderSummaryCards(payload);
  renderDirectorySection(payload);
  setupWorkbookFilter(payload);
  updateWorksheetSections();
};

document.addEventListener("DOMContentLoaded", initReport);
