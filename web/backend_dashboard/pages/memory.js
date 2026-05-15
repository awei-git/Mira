import { el, item, kv, list } from "../dom.js";
import { shortText, statusPill } from "../format.js";

export function renderMemoryPage(root, data) {
  root.replaceChildren();
  const mem = data?.memory || {};
  const status = mem.status || {};
  const counts = status.counts || {};
  const commits = mem.commits || [];
  const lessons = (mem.kernel || {}).failure_lessons || [];
  const queues = mem.queues || {};
  const queueCount = Object.values(queues).reduce((n, rows) => n + rows.length, 0);
  const latestCommit = commits.slice(-1)[0];
  const latestLesson = lessons.slice(-1)[0];

  const head = el("div", "", "page-head");
  head.firstChild?.remove();
  const title = el("div");
  title.append(el("h2", "Memory"), el("div", "Memory window summary, failure lessons, and recent commits.", "page-subtitle"));
  head.append(title, statusPill(status.overall || "gray", "memory"));

  const grid = el("div", "", "grid-3");
  const windowPanel = el("div", "", "panel");
  windowPanel.append(
    el("div", "Memory window", "panel-title"),
    statusPill(status.overall || "gray", "memory"),
    kv("summary", `${counts.ledger || (mem.ledger || []).length} recent events - ${commits.length} commits - ${queueCount} queued - ${counts.items || 0} items`),
    kv("latest commit", latestCommit ? `${latestCommit.status} - ${latestCommit.pipeline} - ${shortText(latestCommit.summary || (latestCommit.findings || []).join("; "), 90)}` : "none"),
    kv("latest lesson", latestLesson ? `${latestLesson.date} - ${shortText(latestLesson.incident, 90)}` : "none"),
    kv("event dates", `${(status.date_range || {}).first || "none"} -> ${(status.date_range || {}).last || "none"}`),
    kv("errors", (status.errors || []).join("; ") || "none")
  );

  const lessonsPanel = el("div", "", "panel");
  const lessonsRows = lessons.slice(-20).reverse().map((s) => item(shortText(s.incident, 80), `date=${s.date}\nchange=${shortText(s.behavioral_change, 150)}\nreinforced=${s.reinforcement_count}`));
  const lessonsBody = el("div", "", "panel-scroll");
  list(lessonsBody, lessonsRows, "No failure lessons recorded");
  lessonsPanel.append(el("div", "Failure lessons", "panel-title"), lessonsBody);

  const commitsPanel = el("div", "", "panel");
  const commitRows = commits.slice(-20).reverse().map((c) => item(`${c.status} - ${c.pipeline}`, `time=${c.timestamp}\n${shortText(c.summary || (c.findings || []).join("; "), 160)}\ncommitted=${(c.committed_actions || []).length} rejected=${(c.rejected_actions || []).length} quarantined=${(c.quarantined_actions || []).length}`));
  const commitBody = el("div", "", "panel-scroll");
  list(commitBody, commitRows, "No recent memory commits");
  commitsPanel.append(el("div", "Recent commits", "panel-title"), commitBody);

  grid.append(windowPanel, lessonsPanel, commitsPanel);
  root.append(head, grid);
}
