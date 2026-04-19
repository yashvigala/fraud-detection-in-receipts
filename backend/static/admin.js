"use strict";

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const setStatus = (msg, kind = "") => {
  statusEl.textContent = msg;
  statusEl.className = "status " + kind;
};

const GRADES = ["Junior", "Mid", "Senior", "Director"];
// Known categories + departments — loaded from /api/config on boot.
let CATEGORIES = [];
let DEPARTMENTS = [];
let COMPANIES = [];
let CURRENT = null; // current CompanyRules object

async function boot() {
  const [cfg, companies] = await Promise.all([
    fetch("/api/config").then((r) => r.json()),
    fetch("/api/companies").then((r) => r.json()),
  ]);
  CATEGORIES = cfg.categories || [];
  // Unique departments from the synthetic employees.
  DEPARTMENTS = [...new Set(cfg.employees.map((e) => e.department))].sort();
  COMPANIES = companies.companies || [];

  const sel = $("company_select");
  sel.innerHTML = "";
  COMPANIES.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = `${c.name} (${c.id})`;
    sel.appendChild(opt);
  });
  sel.addEventListener("change", () => loadCompany(sel.value));
  $("save-btn").addEventListener("click", save);
  $("reset-btn").addEventListener("click", () => loadCompany(sel.value));

  if (COMPANIES.length) {
    await loadCompany(COMPANIES[0].id);
  }
}

async function loadCompany(id) {
  setStatus("Loading...");
  const res = await fetch(`/api/companies/${id}`);
  if (!res.ok) {
    setStatus(`Failed to load ${id}: HTTP ${res.status}`, "error");
    return;
  }
  CURRENT = await res.json();
  render();
  setStatus(`Loaded ${CURRENT.name}`, "success");
}

function render() {
  $("company_desc").value = CURRENT.description || "";

  // Grade limits (4 inputs)
  const gl = $("grade-limits");
  gl.innerHTML = "";
  GRADES.forEach((g) => {
    const value = CURRENT.grade_daily_limit[g] ?? 0;
    gl.insertAdjacentHTML(
      "beforeend",
      `<div class="field"><label>${g}</label><input type="number" data-grade="${g}" step="50" value="${value}"/></div>`,
    );
  });

  // Category limits (one per known category)
  const cl = $("category-limits");
  cl.innerHTML = "";
  CATEGORIES.forEach((cat) => {
    const value = CURRENT.category_daily_limit[cat] ?? 0;
    cl.insertAdjacentHTML(
      "beforeend",
      `<div class="field"><label>${cat}</label><input type="number" data-category="${cat}" step="100" value="${value}"/></div>`,
    );
  });

  // Category restrictions — one row per category, chips per department
  const cr = $("category-restrictions");
  cr.innerHTML = "";
  CATEGORIES.forEach((cat) => {
    const allowed = CURRENT.category_restrictions[cat]; // may be undefined
    const chips = DEPARTMENTS.map((d) => {
      const checked = allowed && allowed.includes(d) ? "checked" : "";
      return `<label class="chip"><input type="checkbox" data-cat="${cat}" data-dept="${d}" ${checked}/>${d}</label>`;
    }).join("");
    cr.insertAdjacentHTML(
      "beforeend",
      `<div class="cat-row">
         <div class="catname">${cat}</div>
         <div class="depts">${chips}</div>
       </div>`,
    );
  });

  $("round_threshold").value = CURRENT.round_number_threshold;
  $("sus_threshold").value = CURRENT.suspicious_threshold;
  $("anom_threshold").value = CURRENT.anomalous_threshold;
}

function collect() {
  const grade_daily_limit = {};
  document.querySelectorAll("#grade-limits input[data-grade]").forEach((el) => {
    grade_daily_limit[el.dataset.grade] = Number(el.value);
  });

  const category_daily_limit = {};
  document.querySelectorAll("#category-limits input[data-category]").forEach((el) => {
    category_daily_limit[el.dataset.category] = Number(el.value);
  });

  const category_restrictions = {};
  document.querySelectorAll("#category-restrictions input[type=checkbox]").forEach((el) => {
    if (!el.checked) return;
    const cat = el.dataset.cat;
    const dept = el.dataset.dept;
    if (!category_restrictions[cat]) category_restrictions[cat] = [];
    category_restrictions[cat].push(dept);
  });
  // Categories with zero boxes checked = unrestricted => remove the key.
  // (A category with an empty array would disallow every department.)
  Object.keys(category_restrictions).forEach((k) => {
    if (category_restrictions[k].length === 0) delete category_restrictions[k];
  });

  return {
    name: CURRENT.name,
    description: CURRENT.description,
    grade_daily_limit,
    category_daily_limit,
    category_restrictions,
    round_number_threshold: Number($("round_threshold").value),
    suspicious_threshold: Number($("sus_threshold").value),
    anomalous_threshold: Number($("anom_threshold").value),
  };
}

async function save() {
  setStatus("Saving...");
  const payload = collect();
  const res = await fetch(`/api/companies/${CURRENT.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    setStatus(`Save failed: ${body.detail || res.status}`, "error");
    return;
  }
  CURRENT = await res.json();
  render();
  setStatus(`Saved. Rules for ${CURRENT.name} updated.`, "success");
}

document.addEventListener("DOMContentLoaded", boot);
