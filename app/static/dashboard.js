/* Trading console — single-page JS.
 *   refreshState()  - polls /api/state every 2s
 *   connectStream() - SSE activity feed
 *   bindings        - data-bind / data-bind-list driven DOM updates
 *   chart           - Chart.js cumulative PnL
 *   controls        - pause/halt buttons + master kill
 */

const POLL_MS = 2000;
const SSE_RECONNECT_MS = 3000;
const ACTIVITY_MAX = 50;

// ─── Formatters ──────────────────────────────────────────────────────

const fmt = {
  usd: (v) => v == null ? '—' : '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  usd0: (v) => v == null ? '—' : '$' + Math.round(Number(v)).toLocaleString('en-US'),
  'usd-signed': (v) => {
    if (v == null) return '—';
    const n = Number(v);
    const sign = n >= 0 ? '+' : '−';
    return sign + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  },
  'usd-colored': (v, el) => {
    if (v == null) return '—';
    const n = Number(v);
    el.classList.toggle('pos', n >= 0);
    el.classList.toggle('neg', n < 0);
    const sign = n >= 0 ? '+' : '−';
    const abs = Math.abs(n);
    if (abs >= 1000) return sign + '$' + (abs / 1000).toFixed(1) + 'k';
    return sign + '$' + abs.toFixed(2);
  },
  pct: (v) => v == null ? '—' : (Number(v) * 100).toFixed(1) + '%',
  'width-pct': (v, el) => { if (v != null) el.style.width = (Number(v) * 100).toFixed(1) + '%'; return null; },
  'width-inv-pct': (v, el) => { if (v != null) el.style.width = ((1 - Number(v)) * 100).toFixed(1) + '%'; return null; },
  int: (v) => v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US'),
  text: (v) => v == null ? '—' : String(v),
  pip: (v, el) => {
    el.classList.remove('bad', 'warn');
    if (v === false) el.classList.add('bad');
    return null;   // skip text update
  },
};

function format(value, fmtName, el) {
  const fn = fmt[fmtName];
  if (!fn) return String(value);
  const out = fn(value, el);
  return out;   // null means the formatter already handled the DOM
}

// ─── Path resolution for data-bind ──────────────────────────────────

function getPath(obj, path) {
  if (!obj || !path) return undefined;
  // supports a.b[0].c
  return path.split(/[.\[\]]+/).filter(Boolean).reduce((acc, key) => {
    if (acc == null) return undefined;
    const idx = parseInt(key, 10);
    return Number.isNaN(idx) ? acc[key] : acc[idx];
  }, obj);
}

// ─── State application ──────────────────────────────────────────────

function deriveExtra(state) {
  // Extra synthetic fields computed for the UI.
  const s0 = state.strategies?.[0] || {};
  const s1 = state.strategies?.[1] || {};
  state.meta = state.meta || {};
  state.meta.local_time = new Date(state.ts || Date.now()).toLocaleTimeString();
  state.meta.mode = state.mock ? 'paper · mock' : 'paper · armed';

  if (s0.win_rate_24h != null) s0.loss_rate_24h = 1 - s0.win_rate_24h;
  if (s0.capital_usd && s0.pnl_24h_usd != null) {
    s0.pnl_24h_pct = (s0.pnl_24h_usd / s0.capital_usd * 100).toFixed(1) + '%';
  }
  s0.status_tag = (s0.mode || 'paper').toUpperCase() +
    (s0.status === 'live-ready' ? ' · LIVE-READY' : '');
  if (s1.gate) s1.gate_label = 'GATED ' + s1.gate.cleared + '/' + s1.gate.total;
  // System containers label
  if (state.system?.containers) {
    state.system.containers.label =
      state.system.containers.active + ' / ' + state.system.containers.total;
  }
  return state;
}

function applyState(state) {
  state = deriveExtra(state);
  document.querySelectorAll('[data-bind]').forEach(el => {
    const path = el.dataset.bind;
    const fmtName = el.dataset.format || 'text';
    const v = getPath(state, path);
    const formatted = format(v, fmtName, el);
    if (formatted !== null && formatted !== undefined) el.textContent = formatted;
  });
  applyLists(state);
}

function applyLists(state) {
  document.querySelectorAll('[data-bind-list]').forEach(container => {
    const listPath = container.dataset.bindList;
    const tmplName = container.dataset.bindTemplate;
    const list = getPath(state, listPath) || [];
    const tmpl = document.getElementById('tmpl-' + tmplName);
    if (!tmpl) return;
    container.innerHTML = '';
    list.forEach(item => {
      const node = tmpl.content.cloneNode(true);
      const html = node.firstElementChild;
      if (!html) return;
      // Enrich with derived fields per type
      const enriched = enrichItem(tmplName, item);
      html.outerHTML = renderTemplate(html.outerHTML, enriched);
      container.insertAdjacentHTML('beforeend', renderTemplate(tmpl.innerHTML.trim(), enriched));
    });
  });
}

function enrichItem(kind, item) {
  const e = { ...item };
  if (kind === 'pos') {
    e.sourceLabel = (item.source || '').toUpperCase().slice(0, 4);
    e.size_usd_fmt = Number(item.size_usd || 0).toFixed(0);
    const pnl = Number(item.pnl_usd || 0);
    e.pnl_usd_fmt = (pnl >= 0 ? '+$' : '−$') + Math.abs(pnl).toFixed(2);
    e.pnl_pct_fmt = ((item.pnl_pct >= 0 ? '+' : '') + (Number(item.pnl_pct || 0) * 100).toFixed(1) + '%');
    e.pnlDirection = pnl >= 0 ? 'up' : 'dn';
    const sec = Number(item.age_seconds || 0);
    e.age_label = sec < 60 ? sec + 's' : sec < 3600 ? Math.round(sec / 60) + 'm' : Math.round(sec / 3600) + 'h';
  } else if (kind === 'gate-flag') {
    e.statusLabel = item.status === 'cleared' ? '✓ CLEARED' : 'PENDING';
  }
  return e;
}

function renderTemplate(html, vars) {
  return html.replace(/\{\{(\w+)\}\}/g, (_, key) => {
    const v = vars[key];
    return v == null ? '' : String(v);
  });
}

// ─── Polling loop ───────────────────────────────────────────────────

let pollTimer = null;
async function refreshState() {
  try {
    const res = await fetch('/api/state');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const state = await res.json();
    applyState(state);
    updateChart(state.pnl_series_30d || []);
  } catch (e) {
    console.warn('refreshState failed:', e);
  }
}

function startPolling() {
  refreshState();
  pollTimer = setInterval(refreshState, POLL_MS);
}

// ─── SSE activity stream ────────────────────────────────────────────

let sseSource = null;
const activityList = () => document.getElementById('activity-list');

function connectStream() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource('/api/stream');
  sseSource.addEventListener('activity', (e) => {
    try {
      const data = JSON.parse(e.data);
      pushActivity(data);
    } catch (err) {
      console.warn('activity parse failed:', err);
    }
  });
  sseSource.onerror = () => {
    if (sseSource) sseSource.close();
    sseSource = null;
    setTimeout(connectStream, SSE_RECONNECT_MS);
  };
}

function pushActivity(ev) {
  const list = activityList();
  if (!list) return;
  const row = document.createElement('div');
  row.className = 'activity-row';
  const ts = new Date(ev.ts || Date.now()).toLocaleTimeString();
  row.innerHTML = `
    <span class="ts">${ts}</span>
    <span class="tag ${ev.kind || 'sig'}">${(ev.kind || 'SIG').toUpperCase()}</span>
    <span class="msg">${(ev.strategy ? '[' + ev.strategy + '] ' : '') + (ev.message || '')}</span>
  `;
  list.insertBefore(row, list.firstChild);
  while (list.children.length > ACTIVITY_MAX) {
    list.removeChild(list.lastChild);
  }
}

// ─── Chart ──────────────────────────────────────────────────────────

let pnlChart = null;
let chartRange = '30D';

function updateChart(series) {
  const canvas = document.getElementById('pnl-chart');
  if (!canvas || typeof Chart === 'undefined') return;

  const labels = series.map(p => p.t.split('T')[0].slice(5));
  const combined = series.map(p => p.combined);
  const liq = series.map(p => p.liquidation);

  if (!pnlChart) {
    pnlChart = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Combined',
            data: combined,
            borderColor: '#ff7847',
            backgroundColor: 'rgba(255,120,71,0.15)',
            borderWidth: 2.5,
            tension: 0.3,
            fill: true,
            pointRadius: 0,
          },
          {
            label: 'Liquidation (paper)',
            data: liq,
            borderColor: '#00e5ff',
            borderDash: [4, 3],
            borderWidth: 1.8,
            tension: 0.2,
            fill: false,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#9aa6b6', font: { family: 'DM Mono', size: 10 } } },
        },
        scales: {
          x: {
            grid: { color: '#1c232e', drawBorder: false },
            ticks: { color: '#5b6677', font: { family: 'DM Mono', size: 10 } },
          },
          y: {
            grid: { color: '#1c232e', drawBorder: false },
            ticks: {
              color: '#5b6677',
              font: { family: 'DM Mono', size: 10 },
              callback: (v) => '$' + v,
            },
          },
        },
      },
    });
  } else {
    pnlChart.data.labels = labels;
    pnlChart.data.datasets[0].data = combined;
    pnlChart.data.datasets[1].data = liq;
    pnlChart.update('none');
  }
}

// ─── Range selector ─────────────────────────────────────────────────

function bindChartRanges() {
  document.querySelectorAll('#chart-ranges span').forEach(el => {
    el.addEventListener('click', () => {
      document.querySelectorAll('#chart-ranges span').forEach(s => s.classList.remove('act'));
      el.classList.add('act');
      chartRange = el.dataset.range;
      // Phase 5: re-fetch /api/pnl?range=... ; for Phase 1 we keep the 30d series
    });
  });
}

// ─── Controls ───────────────────────────────────────────────────────

const CONFIRM_TOKENS = {
  all: 'HALT',
  polymarket: 'PAUSE-POLY',
  liquidation: 'PAUSE-LIQ',
};

async function postKill(target, action) {
  const token = CONFIRM_TOKENS[target] || 'HALT';
  const path = action === 'pause' ? '/api/pause/' + target : (target === 'all' ? '/api/kill/all' : '/api/kill/' + target);
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'X-Confirm': token, 'Content-Type': 'application/json' },
      body: '{}',
    });
    const body = await res.json();
    pushActivity({
      ts: new Date().toISOString(),
      kind: res.ok ? 'warn' : 'err',
      strategy: target,
      message: (action || 'halt') + ' ' + target + ' → ' + (res.ok ? 'OK' : body.detail),
    });
    if (res.ok) refreshState();
  } catch (e) {
    pushActivity({ ts: new Date().toISOString(), kind: 'err', message: e.toString() });
  }
}

function showModal(title, body, onConfirm) {
  const back = document.getElementById('modal-backdrop');
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').textContent = body;
  back.classList.remove('hidden');
  const confirmBtn = document.getElementById('modal-confirm');
  const cancelBtn = document.getElementById('modal-cancel');
  const close = () => back.classList.add('hidden');
  confirmBtn.onclick = () => { close(); onConfirm(); };
  cancelBtn.onclick = close;
  back.onclick = (e) => { if (e.target === back) close(); };
}

function bindControls() {
  document.getElementById('master-kill-btn').addEventListener('click', () => {
    showModal(
      'Confirm master HALT',
      'This will disarm both strategies and flatten the paper book. Idempotent — safe to retry.',
      () => postKill('all', 'kill'),
    );
  });
  document.querySelectorAll('[data-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.action;
      const strategy = btn.dataset.strategy;
      const verb = action === 'pause' ? 'Pause' : 'Halt';
      showModal(
        verb + ' ' + strategy,
        'Confirm ' + verb.toLowerCase() + ' for ' + strategy + '. Idempotent.',
        () => postKill(strategy, action),
      );
    });
  });
}

// ─── Boot ───────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  bindControls();
  bindChartRanges();
  startPolling();
  connectStream();
});
