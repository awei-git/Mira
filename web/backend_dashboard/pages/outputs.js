import { api, fmtTokens } from "../format.js";
import { clear, el, item, list } from "../dom.js";
import { state } from "../state.js";

async function openArtifactSection(root, section) {
  const sectionHref = section.href || `/api/${state.currentUser}/artifacts/${section.name}`;
  const rows = await api(sectionHref);
  const back = item("Back to artifact sections", "");
  back.onclick = () => {
    renderOutputsPage(root, state.dashboard);
    return false;
  };
  const entries = rows.map((r) => item(r.name, `${r.size || 0} bytes - ${r.modified || ""}`, `${sectionHref}/${encodeURIComponent(r.name.replace(/\/$/, ""))}`));
  list(root.querySelector("#artifacts-list"), [back, ...entries], "No files");
}

function renderAgentStats(root, jobs) {
  let stats = (jobs.agent_stats || []).length
    ? jobs.agent_stats
    : Object.entries(jobs.by_agent || {}).map(([agent, row]) => ({ agent, daily_avg: row.calls || 0, weekly_avg: (row.calls || 0) * 7, monthly_avg: (row.calls || 0) * 30, calls_30d: row.calls || 0, tokens_30d: row.tokens || 0, cost_30d: row.cost_usd || 0, top_model: "" }));
  if (!stats.length) {
    stats = (jobs.recent || []).filter((j) => j.status === "done" || j.dispatch_count || ((j.usage || {}).tokens || 0)).map((j) => ({ agent: j.agent || j.name, daily_avg: j.dispatch_count || ((j.usage || {}).calls || 0) || 1, weekly_avg: (j.dispatch_count || 1) * 7, monthly_avg: (j.dispatch_count || 1) * 30, calls_30d: j.dispatch_count || ((j.usage || {}).calls || 0) || 1, tokens_30d: (j.usage || {}).tokens || 0, cost_30d: (j.usage || {}).cost_usd || 0, top_model: Object.keys(((j.usage || {}).models) || {})[0] || "" }));
  }
  state.currentAgentStats = stats;
  const columns = [["agent", "agent", (v) => v, "180px"], ["daily_avg", "daily avg", (v) => v, "110px"], ["weekly_avg", "weekly avg", (v) => v, "110px"], ["monthly_avg", "monthly avg", (v) => v, "120px"], ["calls_30d", "30d calls", (v) => v, "110px"], ["tokens_30d", "30d tokens", (v) => fmtTokens(v), "120px"], ["cost_30d", "30d cost", (v) => `$${Number(v || 0).toFixed(4)}`, "120px"], ["top_model", "model", (v) => v || "", "260px"]];
  const rows = [...stats].sort((a, b) => {
    const av = a[state.agentSort.key];
    const bv = b[state.agentSort.key];
    const result = typeof av === "number" && typeof bv === "number" ? av - bv : String(av || "").localeCompare(String(bv || ""));
    return state.agentSort.dir === "asc" ? result : -result;
  });
  clear(root);
  if (!rows.length) {
    root.append(el("div", "No records", "empty"));
    return;
  }
  const wrap = el("div", "", "table-wrap");
  const table = el("table", "", "agent-table");
  const colgroup = document.createElement("colgroup");
  columns.forEach(([, , , width]) => {
    const col = document.createElement("col");
    col.style.width = width;
    colgroup.append(col);
  });
  const thead = document.createElement("thead");
  const hrow = document.createElement("tr");
  columns.forEach(([key, label]) => {
    const th = document.createElement("th");
    const btn = el("button", label);
    btn.onclick = () => {
      state.agentSort = { key, dir: state.agentSort.key === key && state.agentSort.dir === "desc" ? "asc" : "desc" };
      renderOutputsPage(document.getElementById("page"), state.dashboard);
    };
    th.append(btn);
    if (state.agentSort.key === key) th.append(el("span", state.agentSort.dir === "desc" ? "down" : "up", "sort-mark"));
    hrow.append(th);
  });
  thead.append(hrow);
  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    columns.forEach(([key, , fmt]) => tr.append(el("td", fmt(row[key]))));
    tbody.append(tr);
  });
  table.append(colgroup, thead, tbody);
  wrap.append(table);
  root.append(wrap);
}

export function renderOutputsPage(root, data) {
  root.replaceChildren();
  const jobs = data?.outputs?.jobs || {};
  const head = el("div", "", "page-head");
  const title = el("div");
  title.append(el("h2", "Outputs"), el("div", "Artifacts, recent items, jobs, and agent usage.", "page-subtitle"));
  head.append(title);

  const grid = el("div", "", "grid-3");
  const alerts = el("div", "", "panel");
  const alertBody = el("div");
  list(alertBody, (data.outputs.alert_items || []).map((i) => item(`${i.status} - ${i.title || i.id}`, `${i.id}\n${i.type} - ${(i.tags || []).join(", ")}\n${i.updated_at}`, i.href || `/api/${state.currentUser}/items/${i.id}`)), "No security alerts", true);
  alerts.append(el("div", "Security alerts", "panel-title"), alertBody);

  const artifacts = el("div", "", "panel");
  const artifactList = el("div");
  artifactList.id = "artifacts-list";
  list(artifactList, (data.outputs.artifacts || []).map((a) => {
    const row = item(a.name, `${a.count} item(s)`);
    row.onclick = () => {
      openArtifactSection(root, a);
      return false;
    };
    row.querySelector(".item-title").append(el("span", "Open", "open"));
    return row;
  }), "No artifact sections");
  artifacts.append(el("div", "Artifacts", "panel-title"), artifactList);

  const items = el("div", "", "panel");
  const itemBody = el("div");
  list(itemBody, (data.outputs.recent_items || []).map((i) => item(`${i.status} - ${i.title || i.id}`, `${i.id}\n${i.type} - ${(i.tags || []).join(", ")}\n${i.updated_at}`, i.href || `/api/${state.currentUser}/items/${i.id}`)), "No items", true);
  items.append(el("div", "Recent items", "panel-title"), itemBody);

  const jobPanel = el("div", "", "panel");
  const jobBody = el("div");
  list(jobBody, (jobs.recent || []).map((j) => item(`${j.status} - ${j.name}`, `$${Number((j.usage || {}).cost_usd || 0).toFixed(4)} - ${fmtTokens((j.usage || {}).tokens || 0)} tokens - dispatch ${j.dispatch_count || 0} - ${j.ran_at || ""}`)), "No jobs", true);
  jobPanel.append(el("div", "Jobs", "panel-title"), jobBody);
  grid.append(alerts, artifacts, items, jobPanel);

  const agentPanel = el("div", "", "panel");
  const agentTable = el("div");
  renderAgentStats(agentTable, jobs);
  agentPanel.append(el("div", "Agent usage", "panel-title"), agentTable);
  root.append(head, grid, agentPanel);
}
