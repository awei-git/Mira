import { el } from "./dom.js";

export async function api(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function fmtTokens(n) {
  const value = Number(n || 0);
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

export function shortText(value, limit = 140) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 3)}...` : text;
}

export function timeAgo(iso) {
  const t = Date.parse(iso || "");
  if (!t) return "";
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 90) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 90) return `${min}m ago`;
  return `${Math.round(min / 60)}h ago`;
}

export function normalizeStatus(status) {
  const value = String(status || "").toLowerCase();
  if (["green", "ok", "done", "applied", "success", "succeeded", "completed", "verified"].includes(value)) return "green";
  if (["red", "error", "failed", "failure", "rejected", "quarantined"].includes(value)) return "red";
  if (["blue", "pending", "queued", "scheduled"].includes(value)) return "blue";
  if (["yellow", "running", "started", "active", "requires_human", "attention"].includes(value)) return "yellow";
  return "gray";
}

export function isRunningText(...values) {
  return /\b(running|started|active)\b/.test(values.map((value) => String(value || "").toLowerCase()).join(" "));
}

export function statusPill(status, label = "") {
  const value = normalizeStatus(status);
  const running = value === "yellow" && isRunningText(status, label);
  return el("span", label || value, `status-pill ${value}${running ? " running" : ""}`);
}

export function usageLine(usage) {
  const row = usage || {};
  if (!(row.calls || row.tokens || row.cost_usd)) return "usage not persisted";
  return `${row.calls || 0} calls - ${fmtTokens(row.tokens || 0)} tokens - $${Number(row.cost_usd || 0).toFixed(4)}`;
}

export function topModel(models) {
  let best = "";
  let bestTokens = -1;
  Object.entries(models || {}).forEach(([model, stats]) => {
    const tokens = Number((stats || {}).tokens || 0);
    if (tokens > bestTokens) {
      best = model;
      bestTokens = tokens;
    }
  });
  return best;
}

export function modelBreakdown(models, totalTokens = 0) {
  const rows = Object.entries(models || {})
    .map(([model, stats]) => [model, Number((stats || {}).tokens || 0), Number((stats || {}).calls || 0)])
    .filter((row) => row[1] > 0 || row[2] > 0)
    .sort((a, b) => b[1] - a[1]);
  if (!rows.length) return "no model usage recorded";
  const total = Number(totalTokens || 0) || rows.reduce((sum, row) => sum + row[1], 0);
  return rows
    .map(([model, tokens, calls]) => {
      const share = total > 0 ? `, ${Math.round((tokens / total) * 100)}%` : "";
      return `${model}: ${fmtTokens(tokens)} tokens${share}, ${calls} calls`;
    })
    .join(" | ");
}

export function cliObservationLine(cli) {
  const row = cli || {};
  const models = Object.entries(row.models || {})
    .sort((a, b) => Number((b[1] || {}).calls || 0) - Number((a[1] || {}).calls || 0))
    .map(([model, stats]) => `${model}: ${Number((stats || {}).calls || 0)} calls, ${Number((stats || {}).output_chars || 0)} output chars`);
  if (!Number(row.calls || 0)) return "none observed";
  return `${Number(row.calls || 0)} Codex CLI calls observed${models.length ? ` (${models.join(" | ")})` : ""}`;
}

export function modelMixFamily(models) {
  const rows = Object.entries(models || {})
    .map(([model, stats]) => [model, Number((stats || {}).tokens || 0)])
    .filter((row) => row[1] > 0)
    .sort((a, b) => b[1] - a[1]);
  if (!rows.length) return "mixed";
  const total = rows.reduce((sum, row) => sum + row[1], 0);
  if (rows.length > 1 && total > 0 && rows[0][1] / total < 0.95) return "mixed";
  return modelFamily(rows[0][0]);
}

export function modelFamily(model) {
  const value = String(model || "").toLowerCase();
  if (value.includes("claude")) return "claude";
  if (value.includes("deepseek")) return "deepseek";
  if (value.includes("gpt")) return "gpt";
  if (value.includes("gemini")) return "gemini";
  if (value.includes("omlx") || value.includes("gemma") || value.includes("qwen") || value.includes("local")) return "local";
  return "mixed";
}
