import { el, kv, table } from "../dom.js";
import { cliObservationLine, fmtTokens, modelBreakdown, modelMixFamily } from "../format.js";
import { state } from "../state.js";

function renderTokenChart(root, daily) {
  const legend = el("div", "", "chart-legend");
  [["claude", "Claude"], ["deepseek", "DeepSeek"], ["gpt", "GPT/Codex"], ["gemini", "Gemini"], ["local", "Local"], ["mixed", "Mixed/unknown"]].forEach(([cls, label]) => {
    const row = el("span", "", "legend-item");
    row.append(el("span", "", `legend-dot ${cls}`), el("span", label));
    legend.append(row);
  });
  const max = Math.max(1, ...daily.map((d) => d.tokens || 0));
  const bars = el("div", "", "chart");
  daily.forEach((d) => {
    const family = modelMixFamily(d.models);
    const bar = el("div", "", `bar ${family}`);
    bar.style.height = `${Math.max(2, Math.round(((d.tokens || 0) / max) * 132))}px`;
    bar.title = `${d.date}: ${fmtTokens(d.tokens)} measured tokens, ${d.calls} measured calls\nmodels: ${modelBreakdown(d.models, d.tokens)}\nCLI observed: ${cliObservationLine(d.cli_observed)}`;
    bar.append(el("span", String(d.date || "").slice(8)));
    bars.append(bar);
  });
  root.append(legend, bars);
}

export function renderUsagePage(root, data) {
  root.replaceChildren();
  const jobs = data?.outputs?.jobs || {};
  const hist = jobs.usage_history || {};
  let daily = (hist.daily || []).slice(-30);
  if (!daily.length && (jobs.usage_totals || {}).tokens) {
    daily = [{ date: jobs.date || "today", tokens: jobs.usage_totals.tokens, calls: jobs.usage_totals.calls, cost_usd: jobs.usage_totals.cost_usd, models: {} }];
  }
  const head = el("div", "", "page-head");
  const title = el("div");
  title.append(el("h2", "Usage"), el("div", "Measured usage plus Codex CLI observations where token logs were missing.", "page-subtitle"));
  head.append(title);

  const grid = el("div", "", "grid-2");
  const chartPanel = el("div", "", "panel");
  const chart = el("div");
  renderTokenChart(chart, daily);
  chartPanel.append(el("div", "Measured Daily Tokens", "panel-title"), chart);

  const breakdown = el("div", "", "panel");
  const tabs = el("div", "", "range-tabs");
  ["today", "last_7d", "last_30d"].forEach((name) => {
    const btn = el("button", name.replace("_", " "));
    if (name === state.usageRange) btn.classList.add("active");
    btn.onclick = () => {
      state.usageRange = name;
      renderUsagePage(root, state.dashboard);
    };
    tabs.append(btn);
  });
  let total = (hist.totals || {})[state.usageRange] || {};
  if (!(total.tokens || 0) && (jobs.usage_totals || {}).tokens) total = { ...jobs.usage_totals, models: {} };
  if (!Object.keys(total.models || {}).length) {
    total.models = {};
    (jobs.recent || []).forEach((j) => Object.entries(((j.usage || {}).models) || {}).forEach(([model, stats]) => {
      const dest = total.models[model] || (total.models[model] = { tokens: 0, calls: 0, cost_usd: 0 });
      dest.tokens += stats.tokens || 0;
      dest.calls += stats.calls || 0;
      dest.cost_usd += stats.cost_usd || 0;
    }));
  }
  const modelTable = el("div");
  const totalTokens = Number(total.tokens || 0);
  table(modelTable, ["model", "share", "tokens", "calls", "cost"], Object.entries(total.models || {}).sort((a, b) => (b[1].tokens || 0) - (a[1].tokens || 0)).map(([model, stats]) => {
    const tokens = Number(stats.tokens || 0);
    const share = totalTokens > 0 ? `${Math.round((tokens / totalTokens) * 100)}%` : "0%";
    return [model, share, fmtTokens(tokens), stats.calls, `$${Number(stats.cost_usd || 0).toFixed(4)}`];
  }));
  const dailyTable = el("div");
  breakdown.append(
    el("div", "Measured Model Breakdown", "panel-title"),
    tabs,
    kv("measured total", `${fmtTokens(total.tokens || 0)} tokens - ${total.calls || 0} calls - $${Number(total.cost_usd || 0).toFixed(4)}`),
    kv("Codex CLI observed", cliObservationLine(total.cli_observed)),
    modelTable
  );

  grid.append(chartPanel, breakdown);
  const dailyPanel = el("div", "", "panel");
  table(dailyTable, ["date", "measured tokens", "measured calls", "measured models", "Codex CLI observed"], daily.slice().reverse().map((d) => [d.date, fmtTokens(d.tokens || 0), d.calls || 0, modelBreakdown(d.models, d.tokens), cliObservationLine(d.cli_observed)]));
  dailyPanel.append(el("div", "Daily Measured Mix + CLI Evidence", "panel-title"), hist.coverage_note ? kv("coverage", hist.coverage_note) : "", dailyTable);
  root.append(head, grid, dailyPanel);
}
