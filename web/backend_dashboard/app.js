import { $, clear, el, metric } from "./dom.js";
import { api, cliObservationLine, fmtTokens, modelBreakdown, modelMixFamily, timeAgo } from "./format.js";
import { pageFromLocation, pages, pathForPage, state } from "./state.js";
import { renderAccessPage } from "./pages/access.js";
import { renderConfigPage } from "./pages/config.js";
import { renderMemoryPage } from "./pages/memory.js";
import { renderOutputsPage } from "./pages/outputs.js";
import { renderPipelinesPage } from "./pages/pipelines.js";
import { renderUsagePage } from "./pages/usage.js";

const renderers = {
  pipelines: renderPipelinesPage,
  memory: renderMemoryPage,
  usage: renderUsagePage,
  outputs: renderOutputsPage,
  config: renderConfigPage,
  access: renderAccessPage,
};

function forceDark() {
  document.documentElement.style.colorScheme = "dark";
}

function miniUsageCard(daily) {
  const rows = (daily || []).slice(-30);
  const total = rows.reduce((sum, row) => sum + Number(row.tokens || 0), 0);
  const max = Math.max(1, ...rows.map((row) => Number(row.tokens || 0)));
  const card = metric("30d usage", fmtTokens(total), `${rows.length || 0} days - tokens`);
  const chart = el("div", "", "mini-chart");
  rows.forEach((row) => {
    const bar = el("div", "", `mini-bar ${modelMixFamily(row.models)}`);
    bar.style.height = `${Math.max(2, Math.round((Number(row.tokens || 0) / max) * 32))}px`;
    bar.title = `${row.date}: ${fmtTokens(row.tokens || 0)} measured tokens - $${Number(row.cost_usd || 0).toFixed(4)}\n${modelBreakdown(row.models, row.tokens)}\nCLI observed: ${cliObservationLine(row.cli_observed)}`;
    chart.append(bar);
  });
  card.append(chart);
  return card;
}

function linkMetric(card, pageId, title = "") {
  card.classList.add("clickable");
  card.tabIndex = 0;
  card.setAttribute("role", "link");
  card.title = title || card.textContent.trim();
  card.onclick = () => navigate(pageId);
  card.onkeydown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      navigate(pageId);
    }
  };
  return card;
}

function buildBrand() {
  const brand = el("div", "", "brand");
  const mark = el("div", "", "mark");
  const img = document.createElement("img");
  img.src = "/mira-icon.png";
  img.alt = "";
  mark.append(img);
  const copy = el("div");
  copy.append(el("div", "Mira", "brand-name"), el("div", "Backend", "brand-sub"));
  brand.append(mark, copy);
  return brand;
}

function buildShell() {
  const app = clear($("app"));
  const shell = el("div", "", "shell");
  const aside = document.createElement("aside");
  const profile = document.createElement("select");
  profile.id = "profile";
  const nav = document.createElement("nav");
  nav.id = "nav";
  const cards = el("div", "", "metrics");
  cards.id = "cards";
  const meta = el("div", "", "meta");
  const stamp = el("div");
  stamp.id = "stamp";
  const apiPath = el("div", "", "mono");
  apiPath.id = "apiPath";
  meta.append(stamp, apiPath);
  aside.append(buildBrand(), profile, nav, cards, meta);

  const main = document.createElement("main");
  const content = el("div", "", "content");
  const top = el("div", "", "top");
  const titleBlock = el("div");
  titleBlock.append(el("h1", "Mira backend"), el("div", "Operational status, memory changes, outputs, and usage.", "subtitle"));
  const actions = el("div", "", "actions");
  const status = el("span", "", "status");
  status.append(el("span", "", "dot"));
  const health = el("span", "Loading");
  health.id = "healthText";
  status.append(health);
  const refresh = el("button", "Refresh", "refresh");
  refresh.id = "refresh";
  actions.append(status, refresh);
  top.append(titleBlock, actions);
  const page = document.createElement("section");
  page.id = "page";
  content.append(top, page);
  main.append(content);
  shell.append(aside, main);
  app.append(shell);
}

function buildNav() {
  const nav = clear($("nav"));
  pages.forEach((page) => {
    const link = document.createElement("a");
    link.href = page.path;
    link.textContent = page.label;
    if (page.id === state.currentPage) link.classList.add("active");
    link.onclick = (event) => {
      event.preventDefault();
      navigate(page.id);
    };
    nav.append(link);
  });
}

function updateShell(data) {
  const hb = data.service.heartbeat || {};
  const jobs = data.outputs.jobs || {};
  const alerts = data.outputs.alert_items || [];
  const history = jobs.usage_history || {};
  const usage = (history.totals || {}).today || jobs.usage_totals || {};
  const queues = data.memory.queues || {};
  const memStatus = data.memory.status || {};
  const memCounts = memStatus.counts || {};
  const queueCount = Object.values(queues).reduce((n, rows) => n + rows.length, 0);
  const ledgerWindow = memCounts.ledger_window ?? (data.memory.ledger || []).length;
  const commitWindow = memCounts.commit_window ?? (data.memory.commits || []).length;
  const reviewQueue = memCounts.review_queue ?? queueCount;
  const redPipelines = (data.pipelines || []).filter((p) => p.status === "red").length;
  const scheduledPipelines = (data.pipelines || []).filter((p) => p.status === "blue").length;
  const yellowPipelines = (data.pipelines || []).filter((p) => p.status === "yellow").length;
  const grayPipelines = (data.pipelines || []).filter((p) => p.status === "gray").length;
  $("stamp").textContent = data.server_time;
  $("apiPath").textContent = `/api/${state.currentUser}/backend-dashboard`;
  $("healthText").textContent = hb.busy ? `${hb.active_count || 0} active` : `Online - ${timeAgo(hb.timestamp)}`;
  document.querySelector(".status")?.classList.toggle("online", !hb.busy);
  const securityNote = alerts[0]?.title || "none";
  const pipelineNote = `${scheduledPipelines} scheduled - ${yellowPipelines} attention - ${redPipelines} failed - ${grayPipelines} not observed`;
  const tokenNote = `${usage.calls || 0} calls - $${Number(usage.cost_usd || 0).toFixed(2)}`;
  const memoryNote = `${ledgerWindow} recent events - ${reviewQueue} review queued`;
  clear($("cards")).append(
    linkMetric(
      metric("Service", hb.busy ? `${hb.active_count || 0} active` : "Online", hb.status || "heartbeat", hb.busy ? "" : "online"),
      "access",
      `Service: ${hb.status || "heartbeat"}`
    ),
    linkMetric(
      metric("Security alerts", data.outputs.alert_count || alerts.length || 0, securityNote, alerts.length ? "red" : ""),
      "outputs",
      `Security alerts: ${securityNote}`
    ),
    linkMetric(metric("Pipelines", data.pipelines.length, pipelineNote), "pipelines", `Pipelines: ${pipelineNote}`),
    linkMetric(metric("Today tokens", fmtTokens(usage.tokens || 0), tokenNote), "usage", `Today tokens: ${tokenNote}`),
    linkMetric(miniUsageCard((history || {}).daily || []), "usage", "30 day usage chart"),
    linkMetric(metric("Memory commits", commitWindow, memoryNote), "memory", `Memory commits: ${memoryNote}`)
  );
}

function renderCurrentPage() {
  buildNav();
  const renderer = renderers[state.currentPage] || renderPipelinesPage;
  renderer($("page"), state.dashboard);
}

async function loadProfiles() {
  const data = await api("/api/profiles");
  const select = clear($("profile"));
  data.profiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = `${profile.display_name} - ${profile.agent_name}`;
    select.append(option);
  });
  select.value = state.currentUser;
  select.onchange = () => {
    state.currentUser = select.value;
    localStorage.setItem("mira_profile", state.currentUser);
    loadDashboard();
  };
}

export async function loadDashboard() {
  forceDark();
  state.dashboard = await api(`/api/${state.currentUser}/backend-dashboard`);
  updateShell(state.dashboard);
  renderCurrentPage();
}

function navigate(pageId, replace = false) {
  state.currentPage = pages.some((page) => page.id === pageId) ? pageId : "pipelines";
  const path = pathForPage(state.currentPage);
  if (replace) window.history.replaceState({ page: state.currentPage }, "", path);
  else window.history.pushState({ page: state.currentPage }, "", path);
  renderCurrentPage();
}

window.addEventListener("popstate", () => {
  state.currentPage = pageFromLocation();
  renderCurrentPage();
});

window.onload = async () => {
  forceDark();
  state.currentPage = pageFromLocation();
  buildShell();
  buildNav();
  $("refresh").onclick = loadDashboard;
  await loadProfiles();
  await loadDashboard();
  if (window.location.pathname === "/") navigate(state.currentPage, true);
  setInterval(loadDashboard, 30000);
};
