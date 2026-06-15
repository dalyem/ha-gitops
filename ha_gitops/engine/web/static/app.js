const BASE = window.BASE_PATH || "";

function $(sel, root = document) { return root.querySelector(sel); }
function $all(sel, root = document) { return [...root.querySelectorAll(sel)]; }

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", isError);
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 4000);
}
function overlay(on, msg = "Working…") {
  $("#overlay-msg").textContent = msg;
  $("#overlay").classList.toggle("hidden", !on);
}

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (body !== null) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(BASE + path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || res.statusText;
    throw new Error(detail);
  }
  return data;
}

// Generic data-driven POST actions: <button data-post="/api/deploy" data-confirm="...">
function wireActions() {
  $all("[data-post]").forEach((el) => {
    el.addEventListener("click", async () => {
      const path = el.dataset.post;
      if (el.dataset.confirm && !confirm(el.dataset.confirm)) return;
      let body = null;
      if (el.dataset.body) { try { body = JSON.parse(el.dataset.body); } catch (e) {} }
      if (el.dataset.bodyFrom) {
        const src = $(el.dataset.bodyFrom);
        body = { [el.dataset.bodyKey || "message"]: src ? src.value : "" };
      }
      overlay(true, el.dataset.busy || "Working…");
      try {
        const result = await api(path, "POST", body);
        const msg = (result && (result.message || result.note)) || el.dataset.ok || "Done.";
        toast(msg);
        setTimeout(() => location.reload(), 700);
      } catch (e) {
        overlay(false);
        toast(e.message, true);
      }
    });
  });

  const monToggle = $("#monitoring-toggle");
  if (monToggle) {
    monToggle.addEventListener("change", async () => {
      try { await api("/api/monitoring", "POST", { enabled: monToggle.checked }); toast("Monitoring " + (monToggle.checked ? "on" : "off")); }
      catch (e) { toast(e.message, true); monToggle.checked = !monToggle.checked; }
    });
  }
}

// ---- Setup wizard ---------------------------------------------------------
async function wireSetup() {
  const tokenForm = $("#token-form");
  if (tokenForm) {
    tokenForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      overlay(true, "Verifying token…");
      try {
        const r = await api("/api/token", "POST", { token: $("#token").value.trim() });
        toast("Token saved" + (r.login ? " for " + r.login : ""));
        await loadRepos();
      } catch (e) { toast(e.message, true); }
      finally { overlay(false); }
    });
  }

  const repoSel = $("#repo-select");
  if (repoSel) {
    if (window.HAS_TOKEN) loadRepos();
    repoSel.addEventListener("change", loadBranches);
  }

  const connectBtn = $("#connect-btn");
  if (connectBtn) {
    connectBtn.addEventListener("click", async () => {
      const repo = $("#repo-select").value;
      if (!repo) { toast("Pick a repository", true); return; }
      const [owner, name] = repo.split("/");
      overlay(true, "Connecting & analyzing…");
      try {
        await api("/api/connect", "POST", {
          owner, repo: name,
          branch: $("#branch-select").value,
          config_path: $("#config-path").value.trim(),
        });
        toast("Connected. Redirecting to readiness…");
        setTimeout(() => location.href = BASE + "/readiness", 700);
      } catch (e) { overlay(false); toast(e.message, true); }
    });
  }
}

async function loadRepos() {
  const sel = $("#repo-select");
  if (!sel) return;
  sel.innerHTML = "<option>Loading…</option>";
  try {
    const { repos } = await api("/api/repos");
    sel.innerHTML = "";
    repos.forEach((r) => {
      const o = document.createElement("option");
      o.value = r.full_name;
      o.textContent = r.full_name + (r.private ? " 🔒" : "") + (r.can_push ? "" : " (read-only)");
      o.dataset.default = r.default_branch;
      sel.appendChild(o);
    });
    $("#repo-step").classList.remove("hidden");
    await loadBranches();
  } catch (e) { sel.innerHTML = ""; toast(e.message, true); }
}

async function loadBranches() {
  const sel = $("#repo-select");
  const branchSel = $("#branch-select");
  if (!sel || !sel.value) return;
  const [owner, name] = sel.value.split("/");
  const def = sel.selectedOptions[0]?.dataset.default;
  branchSel.innerHTML = "<option>Loading…</option>";
  try {
    const { branches } = await api(`/api/branches?owner=${owner}&repo=${name}`);
    branchSel.innerHTML = "";
    if (branches.length === 0) {
      const o = document.createElement("option");
      o.value = def || "main"; o.textContent = (def || "main") + " (empty repo)";
      branchSel.appendChild(o);
    } else {
      branches.forEach((b) => {
        const o = document.createElement("option");
        o.value = b; o.textContent = b;
        if (b === def) o.selected = true;
        branchSel.appendChild(o);
      });
    }
  } catch (e) { branchSel.innerHTML = ""; toast(e.message, true); }
}

document.addEventListener("DOMContentLoaded", () => { wireActions(); wireSetup(); });
