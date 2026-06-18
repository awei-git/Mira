import { clear, el, item, list, metric } from "../dom.js";
import { shortText, statusPill } from "../format.js";

function scoreValue(row) {
  const value = row?.value;
  return typeof value === "number" ? String(value) : (value || "0");
}

function renderScorecard(root, rows) {
  const cards = el("div", "", "influence-scorecard");
  (rows || []).forEach((row) => {
    cards.append(metric(row.label || "metric", scoreValue(row), row.note || ""));
  });
  if (!cards.children.length) cards.append(el("div", "No influence scorecard yet", "empty"));
  root.append(cards);
}

function signalRows(lane) {
  return (lane.signals || [])
    .filter((row) => row && (row.value || row.label))
    .map((row) => item(row.label || "signal", shortText(row.value || "", 180), row.href || ""));
}

function renderLane(lane) {
  const panel = el("div", "", "panel influence-lane");
  const head = el("div", "", "influence-lane-head");
  const title = el("div");
  title.append(el("div", lane.name || lane.id || "lane", "influence-lane-title"));
  if (lane.updated_at) title.append(el("div", lane.updated_at, "influence-updated mono"));
  head.append(title, statusPill(lane.status, lane.status || "unknown"));

  const metrics = el("div", "", "influence-lane-metrics");
  metrics.append(el("div", lane.primary_metric || "not observed", "influence-primary"));
  metrics.append(el("div", lane.secondary_metric || "", "influence-secondary"));

  const signals = el("div");
  list(signals, signalRows(lane), "No signals");

  const blockers = el("div", "", "influence-blockers");
  (lane.blockers || []).forEach((blocker) => blockers.append(el("div", blocker, "influence-blocker")));
  if (!(lane.blockers || []).length) blockers.append(el("div", "No visible blocker", "influence-clear"));

  panel.append(head, metrics, el("div", "Signals", "panel-title"), signals, el("div", "Blockers", "panel-title"), blockers);
  if (lane.href) {
    const open = item("Open surface", lane.href, lane.href);
    panel.append(open);
  }
  return panel;
}

function recentRows(rows) {
  return (rows || []).map((row) => item(
    `${row.surface || "surface"} - ${row.title || "untitled"}`,
    row.updated_at || "",
    row.href || ""
  ));
}

export function renderInfluencePage(root, data) {
  root.replaceChildren();
  const influence = data?.public_influence || {};
  const head = el("div", "", "page-head");
  const title = el("div");
  title.append(
    el("h2", "Public Influence"),
    el("div", `${influence.north_star || "Qualified Agent Attention"} - Substack, X, podcast, and GitHub surfaces.`, "page-subtitle")
  );
  head.append(title, el("div", influence.updated_at || "", "page-subtitle mono"));

  const scoreWrap = el("div");
  renderScorecard(scoreWrap, influence.scorecard || []);

  const lanes = el("div", "", "influence-lanes");
  (influence.lanes || []).forEach((lane) => lanes.append(renderLane(lane)));
  if (!lanes.children.length) lanes.append(el("div", "No influence lanes configured", "empty"));

  const recent = el("div", "", "panel");
  const recentList = el("div");
  list(recentList, recentRows(influence.recent || []), "No recent public artifacts", true);
  recent.append(el("div", "Recent public artifacts", "panel-title"), recentList);

  clear(root).append(head, scoreWrap, lanes, recent);
}
