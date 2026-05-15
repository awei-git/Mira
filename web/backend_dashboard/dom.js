export const $ = (id) => document.getElementById(id);

export function el(tag, value = "", className = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? "" : String(value);
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

export function kv(key, value, mono = false) {
  const row = el("div", "", "kv");
  row.append(el("div", key, "k"), el("div", value, mono ? "v mono" : "v"));
  return row;
}

export function item(title, sub = "", href = "") {
  const row = href ? document.createElement("a") : el("div");
  row.className = "item";
  if (href) {
    row.href = href;
    row.target = "_blank";
    row.rel = "noopener";
  }
  const head = el("div", "", "item-title");
  head.append(el("span", title), href ? el("span", "Open", "open") : el("span", ""));
  row.append(head, el("div", sub || "", "item-sub mono"));
  return row;
}

export function list(node, rows, empty = "No records", scroll = false) {
  clear(node);
  if (!rows.length) {
    node.append(el("div", empty, "empty"));
    return;
  }
  const wrap = el("div", "", scroll ? "list scroll-list" : "list");
  rows.forEach((row) => wrap.append(row));
  node.append(wrap);
}

export function metric(label, value, note, className = "") {
  const card = el("div", "", `metric ${className}`.trim());
  card.append(el("div", label, "metric-label"), el("div", value, "metric-value"), el("div", note, "metric-note"));
  return card;
}

export function table(node, headers, rows, className = "wide") {
  clear(node);
  if (!rows.length) {
    node.append(el("div", "No records", "empty"));
    return;
  }
  const wrap = el("div", "", "table-wrap");
  const t = el("table", "", className);
  const thead = document.createElement("thead");
  const hrow = document.createElement("tr");
  headers.forEach((header) => hrow.append(el("th", header)));
  thead.append(hrow);
  const tbody = document.createElement("tbody");
  rows.forEach((cells) => {
    const tr = document.createElement("tr");
    cells.forEach((cell) => tr.append(el("td", cell)));
    tbody.append(tr);
  });
  t.append(thead, tbody);
  wrap.append(t);
  node.append(wrap);
}
