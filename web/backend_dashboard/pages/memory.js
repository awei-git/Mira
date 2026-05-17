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
  title.append(el("h2", "Memory Kernel"), el("div", "Experience ledger events, kernel commits, queued review items, and failure lessons.", "page-subtitle"));
  head.append(title, statusPill(status.overall || "gray", "memory"));

  const grid = el("div", "", "grid-3");
  const windowPanel = el("div", "", "panel");
  windowPanel.append(
    el("div", "Kernel window", "panel-title"),
    statusPill(status.overall || "gray", "memory"),
    kv("experience events", `${counts.ledger || (mem.ledger || []).length} recent V3 events in the ledger window`),
    kv("kernel commits", `${commits.length} accepted/rejected/quarantined memory proposals`),
    kv("queued review", `${queueCount} items waiting for review across memory queues`),
    kv("stored items", `${counts.items || 0} kernel store items`),
    kv("latest kernel commit", latestCommit ? `${latestCommit.status} - ${latestCommit.pipeline} - ${shortText(latestCommit.summary || (latestCommit.findings || []).join("; "), 90)}` : "none"),
    kv("latest failure lesson", latestLesson ? `${latestLesson.date} - ${shortText(latestLesson.incident, 90)}` : "none"),
    kv("ledger dates", `${(status.date_range || {}).first || "none"} -> ${(status.date_range || {}).last || "none"}`),
    kv("errors", (status.errors || []).join("; ") || "none")
  );

  const lessonsPanel = el("div", "", "panel");
  const lessonsRows = lessons.slice(-20).reverse().map((s) => item(shortText(s.incident, 80), `date=${s.date}\nchange=${shortText(s.behavioral_change, 150)}\nreinforced=${s.reinforcement_count}`));
  const lessonsBody = el("div", "", "panel-scroll");
  list(lessonsBody, lessonsRows, "No failure lessons recorded");
  lessonsPanel.append(el("div", "Scars / Failure Lessons", "panel-title"), lessonsBody);

  const commitsPanel = el("div", "", "panel");
  const commitRows = commits.slice(-20).reverse().map((c) => item(`${c.status} - ${c.pipeline}`, `time=${c.timestamp}\n${shortText(c.summary || (c.findings || []).join("; "), 160)}\ncommitted=${(c.committed_actions || []).length} rejected=${(c.rejected_actions || []).length} quarantined=${(c.quarantined_actions || []).length}`));
  const commitBody = el("div", "", "panel-scroll");
  list(commitBody, commitRows, "No recent memory commits");
  commitsPanel.append(el("div", "Kernel Commits", "panel-title"), commitBody);

  grid.append(windowPanel, lessonsPanel, commitsPanel);
  root.append(head, grid);
}
