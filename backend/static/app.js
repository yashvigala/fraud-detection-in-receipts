"use strict";

const $ = (id) => document.getElementById(id);
const status = $("status");
const setStatus = (msg, isError = false) => {
  status.textContent = msg;
  status.classList.toggle("error", isError);
};

let EMPLOYEES = [];

async function loadConfig() {
  const res = await fetch("/api/config");
  if (!res.ok) {
    setStatus(`Failed to load config: ${res.status}`, true);
    return;
  }
  const cfg = await res.json();
  EMPLOYEES = cfg.employees;

  const empSel = $("employee_id");
  empSel.innerHTML = "";
  cfg.employees.forEach((e) => {
    const opt = document.createElement("option");
    opt.value = e.employee_id;
    const comp = e.company_id ? ` · ${e.company_id}` : "";
    opt.textContent = `${e.employee_id} — ${e.department} · ${e.grade}${comp}`;
    opt.dataset.companyId = e.company_id || "";
    empSel.appendChild(opt);
  });
  syncEmployeeMeta();

  const catSel = $("override_category");
  cfg.categories.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    catSel.appendChild(opt);
  });
}

function syncEmployeeMeta() {
  const id = $("employee_id").value;
  const emp = EMPLOYEES.find((e) => e.employee_id === id);
  $("department").value = emp ? emp.department : "";
  $("grade").value = emp ? emp.grade : "";
}

async function submitForm(e) {
  e.preventDefault();
  setStatus("Uploading and processing... this may take a few seconds");
  $("submit-btn").disabled = true;

  const form = e.target;
  // Persist phone number across page reloads so the user only types it once.
  if (form.phone_number.value) {
    localStorage.setItem("phone_number", form.phone_number.value);
  }

  const fd = new FormData();
  fd.append("file", form.file.files[0]);
  fd.append("employee_id", form.employee_id.value);
  fd.append("department", form.department.value);
  fd.append("grade", form.grade.value);
  fd.append("override_category", form.override_category.value);
  fd.append("override_amount", form.override_amount.value);
  fd.append("override_vendor", form.override_vendor.value);
  fd.append("phone_number", form.phone_number.value);

  // Drools context-flag checkboxes — unchecked boxes don't submit at all,
  // so we only forward the checked ones (matching HTML form convention).
  ["receipt_attached","pre_approval_attached","is_business_trip",
   "is_per_diem","is_team_meal","attendee_list_attached"].forEach(name => {
    const el = form.querySelector(`input[name="${name}"]`);
    if (el && el.checked) fd.append(name, "on");
  });
  const just = form.querySelector("textarea[name=justification_text]");
  if (just && just.value) fd.append("justification_text", just.value);

  try {
    const res = await fetch("/api/submit", { method: "POST", body: fd });
    const data = await res.json();
    renderNotifications(data.notifications || []);
    if (!res.ok) {
      setStatus(`Error: ${data.detail || data.error || res.status}`, true);
      if (data.original_png_base64) renderImages(data);
      return;
    }
    setStatus("Done.");
    renderAll(data);
  } catch (err) {
    setStatus(`Request failed: ${err.message}`, true);
  } finally {
    $("submit-btn").disabled = false;
  }
}

function renderNotifications(notifications) {
  const el = $("notifications");
  el.innerHTML = "";
  if (!notifications.length) { el.hidden = true; return; }
  el.hidden = false;
  notifications.forEach((n) => {
    const div = document.createElement("div");
    div.className = "notif " + (n.placed ? "placed" : "failed");
    const icon = n.placed ? "📞" : "⚠️";
    const title = n.placed
      ? `Twilio call placed (${n.trigger})`
      : `Twilio call NOT placed (${n.trigger})`;
    const detail = n.placed
      ? `SID: ${n.sid}`
      : `Reason: ${escapeHtml(n.error || "unknown")}`;
    div.innerHTML =
      `<span class="icon">${icon}</span>` +
      `<div><div class="title">${escapeHtml(title)}</div>` +
      `<div class="detail">${detail}</div></div>`;
    el.appendChild(div);
  });
}

function showStage(id) { $(id).hidden = false; }

function renderImages(data) {
  showStage("stage-image");
  if (data.original_png_base64) {
    $("img-original").src = `data:image/png;base64,${data.original_png_base64}`;
  }
  if (data.preprocessed_png_base64) {
    $("img-preprocessed").src = `data:image/png;base64,${data.preprocessed_png_base64}`;
  }
  if (data.original_shape) {
    $("meta-original").textContent = `shape ${data.original_shape.join(" × ")}`;
  }
  if (data.preprocessed_shape) {
    $("meta-preprocessed").textContent = `shape ${data.preprocessed_shape.join(" × ")}`;
  }
}

function renderOcr(data) {
  showStage("stage-ocr");
  const kv = $("ocr-fields");
  kv.innerHTML = "";
  if (data.ocr_error) {
    kv.innerHTML = `<div style="color:var(--danger)">OCR error: ${data.ocr_error}</div>`;
    $("ocr-json").textContent = "";
    return;
  }
  const ocr = data.ocr || {};
  const keys = ["vendor", "date", "amount", "tax", "currency", "category", "confidence"];
  keys.forEach((k) => {
    const div = document.createElement("div");
    div.innerHTML = `<span class="k">${k}</span><span class="v">${formatValue(ocr[k])}</span>`;
    kv.appendChild(div);
  });
  $("ocr-json").textContent = JSON.stringify(ocr, null, 2);
}

function renderFeatures(data) {
  showStage("stage-features");
  const tbody = $("features-table").querySelector("tbody");
  tbody.innerHTML = "";
  const rows = data.engineered_features || {};
  Object.entries(rows).forEach(([k, v]) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${k}</td><td>${formatValue(v)}</td>`;
    tbody.appendChild(tr);
  });
}

function renderResult(data) {
  showStage("stage-result");
  const ar = data.anomaly_result;
  const verdict = $("verdict");
  verdict.textContent = ar.anomaly_label;
  verdict.className = "verdict " + ar.anomaly_label.toLowerCase();

  // Company banner — which tenant's rules produced this verdict.
  let companyEl = $("verdict-company");
  if (!companyEl) {
    companyEl = document.createElement("p");
    companyEl.id = "verdict-company";
    companyEl.className = "hint";
    companyEl.style.textAlign = "center";
    companyEl.style.marginTop = "-14px";
    companyEl.style.marginBottom = "18px";
    verdict.insertAdjacentElement("afterend", companyEl);
  }
  if (data.company) {
    companyEl.innerHTML =
      `Rules applied: <b>${escapeHtml(data.company.name)}</b> (${escapeHtml(data.company.id)})`;
  } else {
    companyEl.textContent = "";
  }

  // Reason codes — why this classification
  const reasonsEl = $("reasons");
  reasonsEl.innerHTML = "";
  (data.reasons || []).forEach((r) => {
    const li = document.createElement("li");
    li.className = "sev-" + r.severity;
    const techBlock = r.tech_detail
      ? `<div class="tech">${escapeHtml(r.tech_detail)}</div>`
      : "";
    li.innerHTML =
      `<span class="sev">${r.severity}</span>` +
      `<span class="source">${r.source}</span>` +
      `<span class="msg">${escapeHtml(r.message)}` +
        `<span class="reason-code">${r.code}</span>` +
        techBlock +
      `</span>`;
    reasonsEl.appendChild(li);
  });

  const pct = (v) => `${(v * 100).toFixed(1)}%`;
  $("bar-if").style.width = pct(ar.isolation_forest.anomaly_score);
  $("bar-ae").style.width = pct(ar.autoencoder.anomaly_score);
  $("bar-combined").style.width = pct(ar.combined_anomaly_score);

  $("val-if").textContent = ar.isolation_forest.anomaly_score.toFixed(3);
  $("val-ae").textContent = ar.autoencoder.anomaly_score.toFixed(3);
  $("val-combined").textContent = ar.combined_anomaly_score.toFixed(3);

  $("recon-err").textContent = ar.autoencoder.reconstruction_error.toFixed(4);

  const tf = $("top-features");
  tf.innerHTML = "";
  (ar.top_features || []).forEach((f) => {
    const li = document.createElement("li");
    li.textContent = `${f.name}  (residual ${f.residual.toFixed(3)})`;
    tf.appendChild(li);
  });
}

function renderAll(data) {
  renderImages(data);
  renderOcr(data);
  renderFeatures(data);
  renderPolicy(data);
  renderResult(data);
  renderDecision(data);
  // Smooth-scroll to the final verdict, which is the most important thing.
  setTimeout(() => $("stage-decision").scrollIntoView({ behavior: "smooth" }), 50);
}

function renderPolicy(data) {
  const pol = data.policy_result;
  if (!pol) return;
  showStage("stage-policy");

  const status = (pol.policy_engine_status || "").toLowerCase();
  $("policy-summary").innerHTML =
    `<div class="stat ${status}"><div class="label">status</div><div class="value">${escapeHtml(pol.policy_engine_status || "?")}</div></div>` +
    `<div class="stat"><div class="label">decision</div><div class="value">${escapeHtml(pol.policy_decision || "?")}</div></div>` +
    `<div class="stat"><div class="label">policy score</div><div class="value">${pol.policy_score ?? "?"}/100</div></div>` +
    `<div class="stat"><div class="label">violations</div><div class="value">${pol.violations_count ?? 0}</div></div>`;

  const ul = $("rule-hits");
  ul.innerHTML = "";
  const hits = pol.rule_hits || [];
  if (!hits.length) {
    ul.innerHTML = `<li style="grid-template-columns:1fr"><span class="msg">No rules fired — claim is fully policy-compliant.</span></li>`;
    return;
  }
  hits.forEach(h => {
    const li = document.createElement("li");
    li.className = (h.severity || "").toUpperCase();
    li.innerHTML =
      `<span class="rid">${escapeHtml(h.rule_id || "")}</span>` +
      `<span class="sev">${escapeHtml(h.severity || "")}</span>` +
      `<span class="msg">${escapeHtml(h.reason || "")}</span>`;
    ul.appendChild(li);
  });

  if (pol.service_available === false) {
    ul.insertAdjacentHTML("beforebegin",
      `<p class="hint" style="color:var(--warn)">⚠️ Drools service unreachable — showing ML-only verdict.</p>`);
  }
}

function renderDecision(data) {
  const d = data.decision;
  if (!d) return;
  showStage("stage-decision");

  const v = $("final-verdict");
  v.textContent = d.final_status || "?";
  v.className = "final-verdict " + (d.final_status || "").toLowerCase();

  $("final-action").textContent = d.action || "—";
  $("final-score").textContent = (d.final_score ?? 0).toFixed(3);

  const ul = $("final-reasons");
  ul.innerHTML = "";
  (d.decision_reason || []).forEach(r => {
    const li = document.createElement("li");
    li.textContent = r;
    ul.appendChild(li);
  });
}

function formatValue(v) {
  if (v === null || v === undefined) return "<null>";
  if (typeof v === "number") {
    return Number.isInteger(v) ? v : v.toFixed(4);
  }
  return v;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

document.addEventListener("DOMContentLoaded", () => {
  loadConfig();
  // Restore phone number from the last session.
  const stored = localStorage.getItem("phone_number");
  if (stored) $("phone_number").value = stored;
  $("employee_id").addEventListener("change", syncEmployeeMeta);
  $("submit-form").addEventListener("submit", submitForm);

  // Technical-details toggle: plain English by default; power users can
  // opt in to z-scores etc. Preference persisted across visits.
  const toggle = $("tech-toggle");
  if (localStorage.getItem("show_tech") === "1") {
    toggle.checked = true;
    document.body.classList.add("show-tech");
  }
  toggle.addEventListener("change", () => {
    document.body.classList.toggle("show-tech", toggle.checked);
    localStorage.setItem("show_tech", toggle.checked ? "1" : "0");
  });
});
