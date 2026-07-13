export const pages = [
  { id: "pipelines", label: "Pipelines", path: "/backend/pipelines" },
  { id: "memory", label: "Memory", path: "/backend/memory" },
  { id: "usage", label: "Usage", path: "/backend/usage" },
  { id: "outputs", label: "Outputs", path: "/backend/outputs" },
  { id: "influence", label: "Influence", path: "/backend/influence" },
  { id: "config", label: "Config", path: "/backend/config" },
  { id: "access", label: "Access", path: "/backend/access" },
];

export const state = {
  currentUser: localStorage.getItem("mira_profile") || "default",
  dashboard: null,
  currentPage: "pipelines",
  usageRange: "last_7d",
  agentSort: { key: "cost_30d", dir: "desc" },
  currentAgentStats: [],
  expandedPipelines: new Set(),
  selectedSteps: {},
};

export function pageFromLocation() {
  const match = window.location.pathname.match(/^\/backend\/([^/?#]+)/);
  const fromPath = match ? match[1] : "";
  const fromQuery = new URLSearchParams(window.location.search).get("page") || "";
  const candidate = fromPath || fromQuery || "pipelines";
  return pages.some((page) => page.id === candidate) ? candidate : "pipelines";
}

export function pathForPage(pageId) {
  return pages.find((page) => page.id === pageId)?.path || "/backend/pipelines";
}
