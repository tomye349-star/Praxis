// Shared helpers used by every page. Connection info (API URL + token) lives
// in localStorage so it carries across pages automatically — no page needs
// to ask for it more than once per device/browser.

const LS_URL = 'dashApiUrl';
const LS_TOKEN = 'dashApiToken';

const getApiUrl = () => localStorage.getItem(LS_URL) || '';
const getApiToken = () => localStorage.getItem(LS_TOKEN) || '';
const isConnected = () => !!(getApiUrl() && getApiToken());

function saveConnection() {
  const urlInput = document.getElementById('apiUrlInput');
  const tokenInput = document.getElementById('apiTokenInput');
  const url = urlInput.value.trim().replace(/\/$/, '');
  const token = tokenInput.value.trim();
  if (!url || !token) { alert('Please fill in both the API URL and the access token.'); return; }
  localStorage.setItem(LS_URL, url);
  localStorage.setItem(LS_TOKEN, token);
  const panel = document.getElementById('connectPanel');
  if (panel) panel.open = false;
  if (typeof loadState === 'function') loadState();
}

async function apiFetch(path, opts) {
  opts = opts || {};
  const url = getApiUrl();
  const token = getApiToken();
  if (!url || !token) { const e = new Error('not-connected'); throw e; }
  const res = await fetch(url + path, Object.assign({}, opts, {
    headers: Object.assign({
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + token,
    }, opts.headers || {}),
  }));
  if (!res.ok) throw new Error('API error (' + res.status + ')');
  return res.json();
}

function urgencyFor(dueDate) {
  if (!dueDate) return { band: 'good', label: 'On track' };
  const days = Math.ceil((new Date(dueDate + 'T00:00:00') - new Date()) / (1000 * 60 * 60 * 24));
  if (days <= 2) return { band: 'critical', label: 'Due in ' + Math.max(days, 0) + 'd' };
  if (days <= 5) return { band: 'serious', label: 'Due in ' + days + 'd' };
  if (days <= 9) return { band: 'warning', label: 'Due in ' + days + 'd' };
  return { band: 'good', label: days > 0 ? 'Due in ' + days + 'd' : 'On track' };
}

function fmtMinutes(mins) {
  mins = mins || 0;
  const h = Math.floor(mins / 60), m = mins % 60;
  return h ? (h + 'h ' + m + 'm') : (m + 'm');
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, function (s) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[s];
  });
}

// Highlights the current page's link in the shared .tabnav, based on the
// current filename (so the same nav markup works unchanged on every page).
function markActiveNav() {
  const here = (location.pathname.split('/').pop() || 'index.html');
  document.querySelectorAll('.tabnav a').forEach(function (a) {
    const target = a.getAttribute('href');
    if (target === here || (here === '' && target === 'index.html')) {
      a.classList.add('active');
    }
  });
}

// For detail pages (not the homepage): if there's no saved connection yet,
// show a short notice pointing back to the homepage instead of silently
// failing or duplicating the whole connect form on every page.
function requireConnectionOrNotice(containerId) {
  if (isConnected()) return true;
  const el = document.getElementById(containerId);
  if (el) {
    el.innerHTML = '<div class="notice">Not connected yet — go to <a href="index.html">Overview</a> and open "Connect your backend" first.</div>';
  }
  return false;
}

window.addEventListener('DOMContentLoaded', markActiveNav);
