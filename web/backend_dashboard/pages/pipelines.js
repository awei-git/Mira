import { el, item, kv } from "../dom.js";
import { isRunningText, normalizeStatus, shortText, statusPill, usageLine } from "../format.js";
import { state } from "../state.js";

function pipelineStatusValue(p) {
  const text = String(p.status_text || "").toLowerCase();
  if (["scheduled", "pending", "queued"].some((value) => text.includes(value))) return "scheduled";
  return p.status || "gray";
}

function stepStatusValue(p, step) {
  const pipelineStatus = pipelineStatusValue(p);
  if (pipelineStatus === "scheduled" && normalizeStatus(step.status) === "yellow") return "scheduled";
  return step.status || pipelineStatus;
}

function modelLabel(step) {
  if (step.model) return `${step.model} (${step.model_recorded ? "recorded" : step.model_source || "configured"})`;
  if (step.model_source === "no LLM") return "no LLM step";
  return "not recorded";
}

function stepTooltip(p, step) {
  const usageRecorded = !!(step.usage_recorded || step.tokens || step.cost_usd);
  return [
    `${p.name} / ${step.label || step.name || ""}`,
    step.label && step.name ? `id: ${step.name}` : "",
    `status: ${normalizeStatus(stepStatusValue(p, step))}`,
    `model: ${modelLabel(step)}`,
    step.observed_at ? `observed: ${step.observed_at} (${step.timestamp_source || "pipeline"})` : "observed: no step timestamp",
    usageRecorded ? `tokens: ${step.tokens || 0}` : "tokens: not persisted",
    usageRecorded ? `cost: $${Number(step.cost_usd || 0).toFixed(4)}` : "cost: not persisted",
    step.usage_scope ? `usage scope: ${step.usage_scope}` : "",
    step.error ? `error: ${step.error}` : "",
  ].filter(Boolean).join("\n");
}

function dagSummary(p) {
  const output = (p.outputs || [])[0];
  const jobs = (p.current_jobs || []).join(", ");
  if (output) return `${output.status || "output"} - ${shortText(output.title || "", 72)}${output.updated_at ? ` - ${output.updated_at}` : ""}`;
  if (jobs) return `current jobs: ${jobs}${p.last_success_at ? ` - last success ${p.last_success_at}` : ""}`;
  if (p.status_detail) return shortText(p.status_detail, 120);
  return p.last_success_at ? `last success ${p.last_success_at}` : "no run evidence";
}

function renderOutputLinks(root, outputs) {
  (outputs || []).slice(0, 3).forEach((out) => {
    root.append(
      out.href
        ? item(`${out.status || "output"} - ${out.title || "untitled"}`, `${out.updated_at || ""}${out.error ? `\n${shortText(out.error, 180)}` : ""}`, out.href)
        : item(`${out.status || "output"} - ${out.title || "untitled"}`, out.updated_at || "")
    );
  });
}

function inlinePipelineDetail(p, step = null) {
  const root = el("div", "", "dag-detail");
  const pipe = el("div", "", "dag-detail-block");
  const stepBox = el("div", "", "dag-detail-block");
  root.onclick = (event) => event.stopPropagation();
  pipe.append(
    el("div", "Pipeline", "dag-detail-title"),
    kv("trigger", p.trigger || ""),
    kv("status", p.status_text || p.status || ""),
    kv("last run", p.last_run || "not observed"),
    kv("last success", p.last_success_at || "not observed"),
    kv("usage", usageLine(p.usage))
  );
  if ((p.current_jobs || []).length) pipe.append(kv("current jobs", (p.current_jobs || []).join(", ")));
  if (p.configured_model) pipe.append(kv("agent model", `${p.configured_agent || "agent"} -> ${p.configured_model}`));
  if (p.status_detail) pipe.append(kv("detail", p.status_detail));
  if ((p.outputs || []).length) {
    const outputs = el("div", "", "dag-outputs");
    renderOutputLinks(outputs, p.outputs);
    pipe.append(el("div", "Outputs", "dag-detail-title"), outputs);
  }
  stepBox.append(el("div", "Step details", "dag-detail-title"));
  if (step) {
    stepBox.append(
      kv("name", step.name || ""),
      step.label ? kv("label", step.label) : "",
      kv("status", normalizeStatus(stepStatusValue(p, step))),
      kv("model", modelLabel(step)),
      kv("observed", step.observed_at ? `${step.observed_at} (${step.timestamp_source || "pipeline"})` : "not instrumented"),
      kv("usage", step.usage_recorded ? `${step.tokens || 0} tokens - $${Number(step.cost_usd || 0).toFixed(4)}` : (step.usage_scope || "not persisted"))
    );
    if (step.configured_model) stepBox.append(kv("configured", step.configured_model));
    if (step.error) stepBox.append(kv("error", step.error));
  } else {
    stepBox.append(el("div", "Click a step node to inspect model, timestamp, usage, and error context.", "empty"));
  }
  root.append(pipe, stepBox);
  return root;
}

function renderDag(rows) {
  const wrap = el("div", "", "dag-list");
  rows.forEach((p) => {
    const selectedName = state.selectedSteps[p.name] || "";
    const selectedStep = (p.steps || []).find((s) => (typeof s === "string" ? s : s.name) === selectedName);
    const expanded = state.expandedPipelines.has(p.name);
    const dag = el("div", "", `dag${expanded ? " expanded" : ""}`);
    const side = el("div", "", "dag-side");
    side.append(el("div", p.name, "dag-name"));
    const statusCell = el("div", "", "dag-row-status");
    statusCell.append(statusPill(pipelineStatusValue(p), p.status_text || p.status || "not observed"));

    const flow = el("div", "", "dag-flow");
    flow.append(el("div", "Steps", "dag-flow-title"));
    const summary = selectedStep
      ? `selected: ${selectedStep.name || "step"} - ${normalizeStatus(stepStatusValue(p, selectedStep))} - ${modelLabel(selectedStep)}`
      : dagSummary(p);
    const summaryNode = el("div", summary, "dag-flow-summary");

    const graph = el("div", "", "dag-graph");
    (p.steps || []).forEach((s, idx) => {
      const step = typeof s === "string" ? { name: s, status: pipelineStatusValue(p) } : s;
      const stepStatus = normalizeStatus(stepStatusValue(p, step));
      const running = stepStatus === "yellow" && isRunningText(step.status, p.status, p.status_text);
      const btn = el("button", "", `dag-task ${stepStatus}${running ? " running" : ""}${step.name === selectedName ? " selected" : ""}`);
      btn.type = "button";
      btn.title = stepTooltip(p, step);
      btn.setAttribute("aria-label", `${p.name} ${step.name} ${stepStatus}`);
      btn.onclick = (event) => {
        event.stopPropagation();
        state.expandedPipelines.add(p.name);
        state.selectedSteps[p.name] = step.name || "";
        renderPipelinesPage(document.getElementById("page"), state.dashboard);
      };
      btn.append(el("span", "", "dag-node"));
      graph.append(btn);
      if (idx < (p.steps || []).length - 1) graph.append(el("span", "", "dag-edge"));
    });
    flow.append(graph, summaryNode);
    dag.onclick = () => {
      state.expandedPipelines.has(p.name) ? state.expandedPipelines.delete(p.name) : state.expandedPipelines.add(p.name);
      renderPipelinesPage(document.getElementById("page"), state.dashboard);
    };
    dag.append(side, statusCell, flow, inlinePipelineDetail(p, selectedStep));
    wrap.append(dag);
  });
  return wrap;
}

export function renderPipelinesPage(root, data) {
  root.replaceChildren();
  const rows = data?.pipelines || [];
  const head = el("div", "", "page-head");
  head.append(
    el("div", "", ""),
    el("div", `${rows.length} pipelines`, "page-subtitle")
  );
  head.firstChild.append(el("h2", "Pipelines"), el("div", "Compact overview. Click a pipeline to expand; click a node for step details.", "page-subtitle"));
  const legend = el("div", "", "pipeline-legend");
  [["green", "success"], ["blue", "scheduled / queued"], ["yellow", "running / attention"], ["red", "failed"], ["", "not observed"]].forEach(([cls, label]) => {
    const row = el("span", "", "legend-item");
    row.append(el("span", "", `legend-dot ${cls}`.trim()), el("span", label));
    legend.append(row);
  });
  root.append(head, legend, renderDag(rows));
}
