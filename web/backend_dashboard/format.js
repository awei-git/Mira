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

export function modelFamily(model) {
  const value = String(model || "").toLowerCase();
  if (value.includes("claude")) return "claude";
  if (value.includes("deepseek")) return "deepseek";
  if (value.includes("gpt")) return "gpt";
  if (value.includes("gemini")) return "gemini";
  if (value.includes("omlx") || value.includes("gemma") || value.includes("qwen") || value.includes("local")) return "local";
  return "mixed";
}
