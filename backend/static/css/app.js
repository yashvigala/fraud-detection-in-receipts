// Shared client helpers for every page.
// Navbar rendering, fake-auth checks, toast notifications, fetch wrapper.

window.App = (function () {

  // --- Auth -------------------------------------------------
  function getCookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|;\\s*)' + name + '=([^;]*)'));
    if (!m) return null;
    let v = decodeURIComponent(m[1]);
    // FastAPI wraps values containing "=" / ";" etc. in double-quotes
    // (RFC 6265 cookie-octet escape). Strip them off.
    if (v.length >= 2 && v.startsWith('"') && v.endsWith('"')) {
      v = v.slice(1, -1);
    }
    return v;
  }
  function getUser() {
    const raw = getCookie('demo_user');
    if (!raw) return null;
    // Cookie is base64url-encoded JSON (set by the backend).
    try {
      const b64 = raw.replace(/-/g, '+').replace(/_/g, '/');
      return JSON.parse(atob(b64));
    } catch {
      // Legacy plain-JSON fallback
      try { return JSON.parse(raw); } catch { return null; }
    }
  }
  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    location.href = '/login';
  }
  function requireRole(...roles) {
    const u = getUser();
    if (!u) { location.href = '/login'; return null; }
    if (roles.length && !roles.includes(u.role)) {
      location.href = '/login?need=' + roles.join(',');
      return null;
    }
    return u;
  }

  // --- Navbar ------------------------------------------------
  function renderNav(active) {
    const u = getUser();
    if (!u) return '';
    const links = {
      employee: [
        ['Dashboard',  '/employee/dashboard', 'dashboard'],
        ['Submit claim','/employee/submit',    'submit'],
      ],
      manager: [
        ['Review queue', '/manager/queue', 'queue'],
        ['Analytics',    '/analytics',     'analytics'],
      ],
      admin: [
        ['Dashboard',  '/admin/dashboard',  'dashboard'],
        ['Review queue','/manager/queue',    'queue'],
        ['Rules',      '/admin/onboarding', 'onboarding'],
        ['Analytics',  '/analytics',        'analytics'],
      ],
    }[u.role] || [];
    const linkHtml = links.map(([label, href, key]) =>
      `<a class="nav-link ${key===active?'active':''}" href="${href}">${label}</a>`
    ).join('');
    return `
      <nav class="nav animate-fade">
        <a href="/" class="nav-brand">
          <div class="nav-brand-mark">E</div>
          ExpenseAI
        </a>
        <div class="nav-links">${linkHtml}</div>
        <div class="nav-user" onclick="App.logout()">
          <div class="avatar">${(u.email||'?')[0].toUpperCase()}</div>
          <div class="flex-col" style="text-align:right">
            <div class="text-sm" style="font-weight:500">${u.email}</div>
            <div class="role-pill">${u.role} · ${u.company_id}</div>
          </div>
        </div>
      </nav>
    `;
  }

  function mountNav(active) {
    const holder = document.getElementById('nav-holder');
    if (holder) holder.innerHTML = renderNav(active);
  }

  // --- Toast ------------------------------------------------
  function toast(msg, kind = '') {
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => {
      el.style.animation = 'fadeIn var(--dur-med) var(--ease-out) reverse';
      setTimeout(() => el.remove(), 250);
    }, 3200);
  }

  // --- Fetch helper ----------------------------------------
  async function api(path, opts = {}) {
    // `cache: 'no-store'` prevents the browser from serving cached
    // JSON (critical for /api/claims/mine and the manager queue —
    // users expect their freshly-submitted claim to appear immediately).
    const resp = await fetch(path, {
      credentials: 'include',
      cache: 'no-store',
      ...opts,
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || body.error || ('HTTP ' + resp.status));
    }
    return resp.json();
  }

  // --- Format helpers --------------------------------------
  function money(v, currency = '₹') {
    if (v == null || isNaN(v)) return '—';
    return currency + Number(v).toLocaleString('en-IN', {
      maximumFractionDigits: 2,
      minimumFractionDigits: 2,
    });
  }
  function date(v) {
    if (!v) return '—';
    const d = new Date(v);
    return d.toLocaleDateString('en-IN', { year: 'numeric', month: 'short', day: 'numeric' });
  }
  function datetime(v) {
    if (!v) return '—';
    const d = new Date(v);
    return d.toLocaleString('en-IN', { year: 'numeric', month: 'short', day: 'numeric',
                                        hour: '2-digit', minute: '2-digit' });
  }
  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
    }[c]));
  }
  function statusClass(s) {
    if (!s) return 'badge-muted';
    const k = s.toLowerCase();
    if (k === 'valid' || k === 'approved') return 'badge-valid';
    if (k === 'suspicious' || k === 'flagged') return 'badge-suspicious';
    if (k === 'rejected') return 'badge-rejected';
    if (k === 'fraudulent') return 'badge-fraudulent';
    return 'badge-muted';
  }

  // --- Count-up animation ----------------------------------
  function countUp(el, target, dur = 800) {
    const start = 0;
    const startT = performance.now();
    function step(now) {
      const p = Math.min(1, (now - startT) / dur);
      const ease = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.floor(start + (target - start) * ease).toLocaleString();
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = Number(target).toLocaleString();
    }
    requestAnimationFrame(step);
  }

  return {
    getUser, logout, requireRole, renderNav, mountNav,
    toast, api, money, date, datetime, escapeHtml, statusClass, countUp,
  };
})();
