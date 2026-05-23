// ── justeat-live ──
// Reads sensor.justeat_tracking from HA (pushed by docker tracker on VM 208).
// Banner mode (always visible when active) + tap-to-expand fullscreen takeover.

import { haGet } from '../core.js';

const STAGE_PROGRESSION = [
  'AwaitingPayment', 'Processing', 'Accepted',
  'DriverAssigned', 'DriverArrivedAtRestaurant',
  'OnItsWay', 'OutForDelivery', 'DriverNearby', 'DriverArrivingAtCustomer',
  'Delivered', 'Completed',
];

const TERMINAL_STATES = new Set(['Delivered', 'Completed', 'Cancelled', 'Canceled', 'Rejected', 'Failed']);

let _expanded = false;
let _lastFetchedAt = 0;
let _lastTerminalAck = null;  // orderId we've already shown terminal-banner for

async function fetchData() {
  try {
    const sensor = await haGet('sensor.justeat_tracking');
    if (!sensor) return null;
    const a = sensor.attributes || {};
    // Tracker always pushes attributes; fall back to sensor.state if attrs missing
    if (!a.status && !a.isActive && a.error !== 'refresh_failed') {
      return { isActive: false, status: 'idle' };
    }
    return a;
  } catch (e) {
    // Sensor may not exist yet on first deploy
    return null;
  }
}

function fmtClock(iso) {
  if (!iso) return '--:--';
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
}

function fmtCountdown(targetIso) {
  if (!targetIso) return '';
  const min = Math.round((new Date(targetIso) - new Date()) / 60000);
  if (min < -120) return '';  // too old, hide
  if (min < 0) return `hace ${-min} min`;
  if (min === 0) return 'ahora';
  return `~${min} min`;
}

function progressPct(currentStatus) {
  if (TERMINAL_STATES.has(currentStatus) && currentStatus !== 'Delivered' && currentStatus !== 'Completed') {
    return 100;  // cancelled/failed → full red bar
  }
  const idx = STAGE_PROGRESSION.indexOf(currentStatus);
  if (idx < 0) return 0;
  return Math.round(((idx + 1) / STAGE_PROGRESSION.length) * 100);
}

function statusEmoji(label) {
  if (!label) return '🛵';
  // Label format: "🛵 Texto" → take first token
  const first = label.split(' ')[0];
  // If it's just text (no emoji), default to scooter
  return /\p{Emoji}/u.test(first) ? first : '🛵';
}

function statusText(label) {
  if (!label) return '';
  const parts = label.split(' ');
  return /\p{Emoji}/u.test(parts[0]) ? parts.slice(1).join(' ') : label;
}

function renderBanner(d) {
  const el = document.getElementById('justeat-banner');
  if (!el) return;
  const pct = progressPct(d.status);
  const isFailed = TERMINAL_STATES.has(d.status) && !['Delivered', 'Completed'].includes(d.status);

  el.innerHTML = `
    <div class="je-banner-row${isFailed ? ' je-failed' : ''}">
      <span class="je-banner-brand">🛵 JUST EAT</span>
      <span class="je-banner-icon">${statusEmoji(d.statusLabel)}</span>
      <span class="je-banner-status">${statusText(d.statusLabel)}</span>
      <span class="je-banner-restaurant">${d.restaurant ? '· ' + d.restaurant : ''}</span>
      <span class="je-banner-eta">${d.dueDate && !d.isTerminal ? `ETA ${fmtClock(d.dueDate)} · ${fmtCountdown(d.dueDate)}` : ''}</span>
      <span class="je-banner-expand">${_expanded ? '✕' : '↗'}</span>
    </div>
    <div class="je-banner-progress"><div class="je-banner-progress-fill${isFailed ? ' je-failed' : ''}" style="width:${pct}%"></div></div>
  `;
}

function renderExpanded(d) {
  const el = document.getElementById('justeat-live');
  if (!el) return;
  const pct = progressPct(d.status);
  const isFailed = TERMINAL_STATES.has(d.status) && !['Delivered', 'Completed'].includes(d.status);
  const isDone = ['Delivered', 'Completed'].includes(d.status);

  el.innerHTML = `
    <div class="je-takeover-inner${isFailed ? ' je-failed' : ''}">
      <button class="je-close-btn" id="je-close-btn">✕ Cerrar</button>
      <div class="je-brand">
        <span class="je-brand-orange"></span>
        <span class="je-brand-label">JUST EAT</span>
      </div>
      <div class="je-status-bigicon">${statusEmoji(d.statusLabel)}</div>
      <div class="je-status-label">${statusText(d.statusLabel) || d.status}</div>
      <div class="je-restaurant">${d.restaurant || ''}</div>
      <div class="je-progress">
        <div class="je-progress-bar"><div class="je-progress-fill${isFailed ? ' je-failed' : ''}" style="width:${pct}%"></div></div>
        <div class="je-progress-stages">
          ${STAGE_PROGRESSION.slice(0,9).map(s => {
            const reached = STAGE_PROGRESSION.indexOf(s) <= STAGE_PROGRESSION.indexOf(d.status);
            return `<div class="je-stage ${reached?'reached':''}"></div>`;
          }).join('')}
        </div>
      </div>
      ${d.dueDate || d.eta ? `<div class="je-eta-row">
        ${d.dueDate ? `<div class="je-eta-block">
          <div class="je-eta-label">ETA</div>
          <div class="je-eta-time">${fmtClock(d.dueDate)}</div>
          <div class="je-eta-sub">${fmtCountdown(d.dueDate)}</div>
        </div>` : ''}
        ${d.eta ? `<div class="je-eta-block">
          <div class="je-eta-label">RANGO</div>
          <div class="je-eta-time">${d.eta}</div>
        </div>` : ''}
      </div>` : ''}
      ${(d.history && d.history.length) ? `<div class="je-history">
        <div class="je-history-title">Historial (${d.history.length})</div>
        ${d.history.slice(-6).reverse().map(h => `
          <div class="je-history-row">
            <span class="je-history-time">${h.ts ? h.ts.slice(11,16) : ''}</span>
            <span class="je-history-label">${h.label || h.value}</span>
          </div>
        `).join('')}
      </div>` : ''}
      ${isDone ? '<div class="je-done-stamp">✓ Pedido finalizado</div>' : ''}
      ${isFailed ? '<div class="je-done-stamp je-failed-stamp">❌ Pedido no completado</div>' : ''}
    </div>
  `;
  const close = document.getElementById('je-close-btn');
  if (close) close.addEventListener('click', () => collapse());
}

function expand() {
  _expanded = true;
  const fs = document.getElementById('justeat-live');
  if (fs) fs.style.display = 'block';
}

function collapse() {
  _expanded = false;
  const fs = document.getElementById('justeat-live');
  if (fs) fs.style.display = 'none';
}

// Decide whether banner should be visible. Returns the data object if active, null otherwise.
function decideVisibility(d) {
  if (!d) return null;
  if (d.error === 'refresh_failed') return d;  // show error banner
  if (d.isActive) return d;
  // Show terminal state briefly (up to 10 min after fetchedAt) so user sees Delivered/Cancelled
  if (d.isTerminal && d.fetchedAt) {
    const age = (Date.now() - new Date(d.fetchedAt).getTime()) / 1000;
    if (age < 600) return d;
  }
  return null;
}

async function update() {
  const banner = document.getElementById('justeat-banner');
  const fullscreen = document.getElementById('justeat-live');
  if (!banner) return false;

  const raw = await fetchData();
  const d = decideVisibility(raw);

  if (!d) {
    banner.style.display = 'none';
    if (fullscreen) fullscreen.style.display = 'none';
    _expanded = false;
    return false;
  }

  if (d.error === 'refresh_failed') {
    banner.style.display = '';
    banner.innerHTML = `
      <div class="je-banner-row je-failed">
        <span class="je-banner-brand">🛵 JUST EAT</span>
        <span class="je-banner-status">⚠️ Sesión caducada — abre just-eat.es e inicia sesión</span>
      </div>`;
    return true;
  }

  banner.style.display = '';
  renderBanner(d);

  if (_expanded && fullscreen) {
    fullscreen.style.display = 'block';
    renderExpanded(d);
  } else if (fullscreen) {
    fullscreen.style.display = 'none';
  }

  return true;
}

function init() {
  const banner = document.getElementById('justeat-banner');
  if (banner) {
    banner.addEventListener('click', () => {
      if (_expanded) collapse(); else expand();
      update();
    });
  }
}

export default { init, update };
