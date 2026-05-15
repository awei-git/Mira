import { api } from "../format.js";
import { el } from "../dom.js";
import { state } from "../state.js";

async function saveModelAssignment(agent, model, tokenBudget, statusNode) {
  statusNode.textContent = "saving";
  try {
    const res = await api(`/api/${state.currentUser}/backend-dashboard/models/${encodeURIComponent(agent)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, token_budget: Number(tokenBudget || 0) }),
    });
    state.dashboard.models = (res.config || {}).models || state.dashboard.models;
    state.dashboard.model_options = (res.config || {}).model_options || state.dashboard.model_options;
    state.dashboard.model_catalog = (res.config || {}).model_catalog || state.dashboard.model_catalog;
    statusNode.textContent = "saved";
  } catch (err) {
    statusNode.textContent = `failed: ${err.message}`;
  }
}

function catalogValueSet(catalog) {
  const values = new Set();
  (catalog?.groups || []).forEach((group) => {
    (group.models || []).forEach((model) => {
      if (model.value) values.add(model.value);
    });
  });
  return values;
}

function appendOption(select, value, label) {
  const opt = document.createElement("option");
  opt.value = value;
  opt.textContent = label || value;
  opt.title = value;
  select.append(opt);
}

function appendModelOptions(select, rowModel, catalog, fallbackOptions) {
  const catalogValues = catalogValueSet(catalog);
  if (rowModel && !catalogValues.has(rowModel)) {
    const current = document.createElement("optgroup");
    current.label = "Current";
    const opt = document.createElement("option");
    opt.value = rowModel;
    opt.textContent = `${rowModel} (current legacy value)`;
    current.append(opt);
    select.append(current);
  }
  if ((catalog?.groups || []).length) {
    (catalog.groups || []).forEach((group) => {
      const models = (group.models || []).filter((model) => model.value);
      if (!models.length) return;
      const optgroup = document.createElement("optgroup");
      optgroup.label = group.provider || "Models";
      models.forEach((model) => appendOption(optgroup, model.value, model.label || model.value));
      select.append(optgroup);
    });
    return;
  }
  [...new Set([rowModel, ...(fallbackOptions || [])].filter(Boolean))].forEach((value) => appendOption(select, value, value));
}

function modelCatalogSummary(catalog) {
  if (!catalog?.checked_at) return "";
  const providers = (catalog.sources || []).map((source) => source.provider).filter(Boolean);
  const suffix = providers.length ? ` from ${providers.join(", ")}` : "";
  return `Model catalog checked ${catalog.checked_at}${suffix}.`;
}

export function renderConfigPage(root, data) {
  root.replaceChildren();
  const head = el("div", "", "page-head");
  const title = el("div");
  title.append(el("h2", "Config"), el("div", "Agent model assignments and per-run token caps.", "page-subtitle"));
  head.append(title);
  const panel = el("div", "", "panel");
  panel.append(el("div", "Agent model assignments", "panel-title"));
  const rows = data.models || [];
  const options = data.model_options || [];
  const catalog = data.model_catalog || {};
  const summary = modelCatalogSummary(catalog);
  if (summary) panel.append(el("div", summary, "page-subtitle"));
  if (!rows.length) {
    panel.append(el("div", "No model assignments", "empty"));
    root.append(head, panel);
    return;
  }
  const wrap = el("div", "", "table-wrap");
  const table = el("table", "", "config-table");
  const thead = document.createElement("thead");
  const hrow = document.createElement("tr");
  ["agent", "model", "per-run cap", "state", ""].forEach((label) => hrow.append(el("th", label)));
  thead.append(hrow);
  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.className = "config-row";
    tr.append(el("td", row.agent || ""));
    const modelCell = document.createElement("td");
    const select = document.createElement("select");
    appendModelOptions(select, row.model, catalog, options);
    select.value = row.model || "";
    modelCell.append(select);
    const budgetCell = document.createElement("td");
    const budget = document.createElement("input");
    budget.type = "number";
    budget.min = "0";
    budget.step = "1000";
    budget.value = row.token_budget || 0;
    budgetCell.append(budget);
    const stateCell = document.createElement("td");
    const status = el("span", row.override ? "override" : "default", "save-state");
    stateCell.append(status);
    const actionCell = document.createElement("td");
    const saveButton = el("button", "Save");
    const markChanged = () => { status.textContent = "changed"; };
    select.onchange = markChanged;
    budget.oninput = markChanged;
    saveButton.onclick = () => saveModelAssignment(row.agent, select.value, budget.value, status);
    actionCell.append(saveButton);
    tr.append(modelCell, budgetCell, stateCell, actionCell);
    tbody.append(tr);
  });
  table.append(thead, tbody);
  wrap.append(table);
  panel.append(wrap);
  root.append(head, panel);
}
