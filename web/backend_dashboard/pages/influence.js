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

function valueText(value, fallback = "n/a") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function statRow(label, value, note = "") {
  const row = el("div", "", "influence-stat");
  row.append(el("div", label, "influence-stat-label"), el("div", valueText(value), "influence-stat-value"));
  if (note) row.append(el("div", note, "influence-stat-note"));
  return row;
}

function renderPlatformCard(name, status, rows, gaps = []) {
  const panel = el("div", "", "panel influence-platform");
  const head = el("div", "", "influence-lane-head");
  head.append(el("div", name, "influence-lane-title"), statusPill(status, status || "unknown"));
  const grid = el("div", "", "influence-stat-grid");
  rows.forEach((row) => grid.append(statRow(row.label, row.value, row.note || "")));
  const gapWrap = el("div", "", "influence-blockers");
  (gaps || []).forEach((gap) => gapWrap.append(el("div", gap, "influence-blocker")));
  if (!(gaps || []).length) gapWrap.append(el("div", "No visible data gap", "influence-clear"));
  panel.append(head, grid, el("div", "Data gaps", "panel-title"), gapWrap);
  return panel;
}

function renderPlatformMetrics(root, platforms) {
  const wrap = el("div", "", "influence-platforms");
  const substack = platforms?.substack || {};
  const relationship = substack.relationship_comments || {};
  wrap.append(renderPlatformCard("Substack Dashboard", substack.subscribers === null || substack.subscribers === undefined ? "yellow" : "green", [
    { label: "Subscribers", value: substack.subscribers, note: `+${substack.subscriber_delta_30d || 0} in 30d` },
    { label: "Paid", value: substack.paid_subscribers },
    { label: "Followers", value: substack.followers, note: substack.followers_status || "" },
    { label: "Active subscribers", value: substack.active_subscribers, note: `${substack.top_activity_subscribers || 0} high activity` },
    { label: "Article views", value: substack.article_views },
    { label: "Article likes", value: substack.article_likes },
    { label: "Article comments", value: substack.article_comments },
    { label: "Article restacks", value: substack.article_restacks },
    { label: "Notes posted", value: substack.notes_total, note: `${substack.notes_7d || 0} in 7d` },
    { label: "Note likes", value: substack.notes_likes },
    { label: "Note replies", value: substack.notes_replies },
    { label: "Note restacks", value: substack.notes_restacks },
    { label: "Outbound comments", value: relationship.outbound_comments_tracked },
    { label: "Author replies", value: relationship.author_replies, note: `rate ${valueText(relationship.author_reply_rate)}` },
    { label: "Other replies", value: relationship.other_replies },
    { label: "Follows attributed", value: relationship.follows_attributed },
  ], substack.data_gaps || []));

  const x = platforms?.x || {};
  wrap.append(renderPlatformCard("X / Articles", x.followers === null || x.followers === undefined ? "yellow" : "green", [
    { label: "Followers", value: x.followers, note: x.followers_status || "" },
    { label: "Posts 7d", value: x.posts_7d },
    { label: "Posts 30d", value: x.posts_30d },
    { label: "Article views", value: x.article_views },
    { label: "Article replies", value: x.article_replies },
    { label: "Article reposts", value: x.article_reposts },
  ], x.data_gaps || []));

  const podcast = platforms?.podcast || {};
  wrap.append(renderPlatformCard("Podcast", podcast.rss_ok ? "green" : "yellow", [
    { label: "Marginalia days", value: `${podcast.marginalia_completed_days || 0}/7`, note: podcast.marginalia_status || "" },
    { label: "RSS ok", value: podcast.rss_ok ? "yes" : "no" },
    { label: "Audio artifact", value: podcast.audio_artifact_present ? "present" : "not observed" },
    { label: "Plays", value: podcast.plays },
    { label: "Clicks", value: podcast.clicks },
  ], podcast.data_gaps || []));

  root.append(el("div", "Publisher Metrics", "panel-title"), wrap);
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

  const platformWrap = el("div");
  renderPlatformMetrics(platformWrap, influence.platforms || {});

  const lanes = el("div", "", "influence-lanes");
  (influence.lanes || []).forEach((lane) => lanes.append(renderLane(lane)));
  if (!lanes.children.length) lanes.append(el("div", "No influence lanes configured", "empty"));

  const recent = el("div", "", "panel");
  const recentList = el("div");
  list(recentList, recentRows(influence.recent || []), "No recent public artifacts", true);
  recent.append(el("div", "Recent public artifacts", "panel-title"), recentList);

  clear(root).append(head, scoreWrap, platformWrap, lanes, recent);
}
