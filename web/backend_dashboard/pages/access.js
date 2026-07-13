import { el, kv } from "../dom.js";
import { statusPill } from "../format.js";

export function renderAccessPage(root, data) {
  root.replaceChildren();
  const hb = data?.service?.heartbeat || {};
  const head = el("div", "", "page-head");
  const title = el("div");
  title.append(el("h2", "Access"), el("div", "Connection, control plane, and security posture.", "page-subtitle"));
  head.append(title, statusPill(hb.busy ? "yellow" : "green", hb.busy ? `${hb.active_count || 0} active` : "online"));

  const grid = el("div", "", "grid-2");
  const service = el("div", "", "panel");
  service.append(
    el("div", "Connection", "panel-title"),
    kv("heartbeat", `status=${hb.status || ""} busy=${!!hb.busy} active=${hb.active_count || 0}`),
    kv("web", `${data.service.web.host}:${data.service.web.port} https=${data.service.web.https}`),
    kv("control", Object.entries(data.service.control || {}).map(([name, value]) => `${name}: ${value}`).join(" - "))
  );
  const security = el("div", "", "panel");
  const sec = data.security || {};
  security.append(el("div", "Security", "panel-title"), statusPill(sec.status || "gray", sec.summary || "security"));
  (sec.checks || []).forEach((check) => security.append(kv(check.name, `${check.status}: ${check.detail}`)));
  (sec.recommendations || []).forEach((rec) => security.append(kv("Fix", rec)));
  grid.append(service, security);
  root.append(head, grid);
}
