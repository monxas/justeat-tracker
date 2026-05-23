/**
 * justeat-card — Lovelace custom card for sensor.justeat_tracking
 *
 * Reads attributes pushed by https://github.com/monxas/justeat-tracker
 * and renders an order tracker with status, progress bar, ETA, history.
 *
 * Config:
 *   type: custom:justeat-card
 *   entity: sensor.justeat_tracking   # required
 *   mode: auto                        # auto | compact | expanded   (default: auto)
 *   hide_when_idle: true              # default: true
 *   show_history: true                # default: true
 *   show_progress_bar: true           # default: true
 *
 * No build step — single-file vanilla web component, no dependencies.
 */

const STAGE_PROGRESSION = [
  'AwaitingPayment', 'Processing', 'Accepted',
  'DriverAssigned', 'DriverArrivedAtRestaurant',
  'OnItsWay', 'OutForDelivery', 'DriverNearby', 'DriverArrivingAtCustomer',
  'Delivered', 'Completed',
];
const TERMINAL_STATES = new Set(['Delivered', 'Completed', 'Cancelled', 'Canceled', 'Rejected', 'Failed']);
const FAILED_STATES = new Set(['Cancelled', 'Canceled', 'Rejected', 'Failed']);

const CARD_VERSION = '0.1.0';

class JustEatCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._expanded = null;          // null = auto, true = forced, false = forced
    this._lastState = null;
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error('Required: entity (e.g. sensor.justeat_tracking)');
    }
    this._config = {
      mode: 'auto',
      hide_when_idle: true,
      show_history: true,
      show_progress_bar: true,
      ...config,
    };
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 3;
  }

  _stateObj() {
    if (!this._hass || !this._config) return null;
    return this._hass.states[this._config.entity];
  }

  _isExpanded(attrs) {
    if (this._expanded !== null) return this._expanded;
    if (this._config.mode === 'compact') return false;
    if (this._config.mode === 'expanded') return true;
    // auto: expanded if non-terminal active order
    return attrs && attrs.isActive && !attrs.isTerminal;
  }

  _fmtClock(iso) {
    if (!iso) return '--:--';
    const d = new Date(iso);
    return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  }

  _fmtCountdown(iso) {
    if (!iso) return '';
    const min = Math.round((new Date(iso) - new Date()) / 60000);
    if (min < -120) return '';
    if (min < 0) return `${-min} min ago`;
    if (min === 0) return 'now';
    return `~${min} min`;
  }

  _progressPct(status) {
    if (FAILED_STATES.has(status)) return 100;
    const idx = STAGE_PROGRESSION.indexOf(status);
    if (idx < 0) return 0;
    return Math.round(((idx + 1) / STAGE_PROGRESSION.length) * 100);
  }

  _statusEmoji(label) {
    if (!label) return '🛵';
    const first = label.split(' ')[0];
    return /\p{Emoji}/u.test(first) ? first : '🛵';
  }

  _statusText(label) {
    if (!label) return '';
    const parts = label.split(' ');
    return /\p{Emoji}/u.test(parts[0]) ? parts.slice(1).join(' ') : label;
  }

  _toggleExpand() {
    const attrs = this._stateObj()?.attributes || {};
    const current = this._isExpanded(attrs);
    this._expanded = !current;
    this._render();
  }

  _render() {
    if (!this._config) return;
    const state = this._stateObj();
    if (!state) {
      this._renderUnknown();
      return;
    }

    const attrs = state.attributes || {};
    const value = state.state;

    // Hide-when-idle: also hide when state is unavailable/unknown
    if (this._config.hide_when_idle && (value === 'idle' || value === 'unavailable' || value === 'unknown' || (!attrs.isActive && !attrs.isTerminal))) {
      this.shadowRoot.innerHTML = `<style>:host { display: none; }</style>`;
      this.style.display = 'none';
      return;
    }
    this.style.display = '';

    const expanded = this._isExpanded(attrs);
    const failed = FAILED_STATES.has(attrs.status);
    const done = attrs.status === 'Delivered' || attrs.status === 'Completed';

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card class="${failed ? 'failed' : ''} ${done ? 'done' : ''}">
        ${expanded ? this._renderExpanded(attrs) : this._renderCompact(attrs)}
      </ha-card>
    `;

    this.shadowRoot.querySelector('ha-card')?.addEventListener('click', () => this._toggleExpand());
  }

  _renderCompact(a) {
    const pct = this._progressPct(a.status);
    return `
      <div class="row">
        <div class="icon">${this._statusEmoji(a.statusLabel)}</div>
        <div class="text">
          <div class="primary">${this._escape(this._statusText(a.statusLabel) || a.status || 'Tracking')}</div>
          <div class="secondary">${this._escape(a.restaurant || '')}${a.dueDate && !a.isTerminal ? ' · ETA ' + this._fmtClock(a.dueDate) : ''}</div>
        </div>
        <div class="arrow">›</div>
      </div>
      ${this._config.show_progress_bar ? `<div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div>` : ''}
    `;
  }

  _renderExpanded(a) {
    const pct = this._progressPct(a.status);
    const stages = STAGE_PROGRESSION.slice(0, 9);
    const reachedIdx = STAGE_PROGRESSION.indexOf(a.status);

    return `
      <div class="expanded">
        <div class="hero">
          <div class="big-icon">${this._statusEmoji(a.statusLabel)}</div>
          <div class="big-label">${this._escape(this._statusText(a.statusLabel) || a.status)}</div>
          <div class="restaurant">${this._escape(a.restaurant || '')}</div>
        </div>

        ${this._config.show_progress_bar ? `
          <div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div>
          <div class="stages">
            ${stages.map((s, i) => `<div class="stage ${i <= reachedIdx ? 'reached' : ''}"></div>`).join('')}
          </div>
        ` : ''}

        ${a.dueDate || a.eta ? `
          <div class="eta-row">
            ${a.dueDate ? `
              <div class="eta-block">
                <div class="eta-label">ETA</div>
                <div class="eta-time">${this._fmtClock(a.dueDate)}</div>
                <div class="eta-sub">${this._fmtCountdown(a.dueDate)}</div>
              </div>` : ''}
            ${a.eta ? `
              <div class="eta-block">
                <div class="eta-label">RANGE</div>
                <div class="eta-time small">${this._escape(a.eta)}</div>
              </div>` : ''}
          </div>` : ''}

        ${this._config.show_history && a.history && a.history.length ? `
          <div class="history">
            <div class="history-title">History (${a.history.length})</div>
            ${a.history.slice(-5).reverse().map(h => `
              <div class="history-row">
                <span class="history-time">${h.ts ? this._escape(h.ts.slice(11, 16)) : ''}</span>
                <span class="history-label">${this._escape(h.label || h.value || '')}</span>
              </div>
            `).join('')}
          </div>` : ''}

        ${a.status === 'Delivered' || a.status === 'Completed' ? '<div class="stamp">✓ Order completed</div>' : ''}
        ${FAILED_STATES.has(a.status) ? '<div class="stamp failed">❌ Order not completed</div>' : ''}
      </div>
    `;
  }

  _renderUnknown() {
    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="row">
          <div class="icon">🛵</div>
          <div class="text">
            <div class="primary">Just Eat tracking</div>
            <div class="secondary">Entity ${this._escape(this._config.entity)} not found</div>
          </div>
        </div>
      </ha-card>
    `;
  }

  _escape(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  _styles() {
    return `
      <style>
        :host { display: block; }
        ha-card {
          padding: 16px;
          cursor: pointer;
          transition: background 0.2s;
        }
        ha-card.failed { background: linear-gradient(135deg, var(--card-background-color, #1c1c1c) 60%, rgba(180,30,30,0.18)); }
        ha-card.done   { background: linear-gradient(135deg, var(--card-background-color, #1c1c1c) 60%, rgba(40,120,60,0.18)); }

        .row { display: flex; align-items: center; gap: 14px; }
        .icon { font-size: 2rem; flex: 0 0 auto; }
        .text { flex: 1; min-width: 0; }
        .primary { font-weight: 600; font-size: 1.05rem; color: var(--primary-text-color); }
        .secondary { font-size: 0.85rem; color: var(--secondary-text-color); margin-top: 2px;
                     overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .arrow { color: var(--secondary-text-color); font-size: 1.4rem; }

        .bar { height: 4px; background: rgba(127,127,127,0.18); border-radius: 2px; overflow: hidden; margin-top: 12px; }
        .bar-fill { height: 100%; background: linear-gradient(90deg, #ff8000, #ffa726);
                    transition: width 0.6s ease-out; }
        ha-card.failed .bar-fill { background: linear-gradient(90deg, #c62828, #e57373); }
        ha-card.done .bar-fill { background: linear-gradient(90deg, #2e7d32, #66bb6a); }

        .expanded { display: flex; flex-direction: column; gap: 14px; }
        .hero { text-align: center; padding: 8px 0; }
        .big-icon { font-size: 4rem; line-height: 1; filter: drop-shadow(0 4px 16px rgba(255,128,0,0.3)); }
        ha-card.failed .big-icon { filter: drop-shadow(0 4px 16px rgba(198,40,40,0.3)); }
        .big-label { font-size: 1.4rem; font-weight: 700; margin-top: 8px; color: var(--primary-text-color); }
        .restaurant { font-size: 0.95rem; color: var(--secondary-text-color); margin-top: 4px; }

        .stages { display: flex; justify-content: space-between; margin-top: 4px; }
        .stage { width: 8px; height: 8px; border-radius: 50%; background: rgba(127,127,127,0.25); }
        .stage.reached { background: #ff8000; box-shadow: 0 0 6px rgba(255,128,0,0.5); }
        ha-card.failed .stage.reached { background: #c62828; box-shadow: 0 0 6px rgba(198,40,40,0.5); }
        ha-card.done .stage.reached { background: #2e7d32; box-shadow: 0 0 6px rgba(46,125,50,0.5); }

        .eta-row { display: flex; gap: 20px; justify-content: center; }
        .eta-block { text-align: center; flex: 0 0 auto; }
        .eta-label { font-size: 0.65rem; letter-spacing: 2px; color: var(--secondary-text-color); text-transform: uppercase; }
        .eta-time { font-size: 1.8rem; font-weight: 800; color: var(--primary-text-color); font-variant-numeric: tabular-nums; margin: 2px 0; }
        .eta-time.small { font-size: 1.1rem; }
        .eta-sub { font-size: 0.75rem; color: var(--secondary-text-color); }

        .history { background: rgba(127,127,127,0.06); border-radius: 8px; padding: 10px 14px; }
        .history-title { font-size: 0.65rem; letter-spacing: 1.5px; color: var(--secondary-text-color);
                         text-transform: uppercase; margin-bottom: 6px; }
        .history-row { display: flex; gap: 10px; padding: 2px 0; font-size: 0.85rem; }
        .history-time { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; min-width: 42px; }

        .stamp { text-align: center; font-weight: 700; letter-spacing: 1px; padding: 6px 0; color: #66bb6a; }
        .stamp.failed { color: #ef5350; }
      </style>
    `;
  }
}

customElements.define('justeat-card', JustEatCard);

// Register with HA's custom-card picker
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'justeat-card',
  name: 'Just Eat tracking card',
  description: 'Order tracker UI for sensor.justeat_tracking (pushed by github.com/monxas/justeat-tracker)',
  preview: true,
  documentationURL: 'https://github.com/monxas/justeat-tracker/tree/main/lovelace-card',
});

console.info(
  `%c justeat-card %c v${CARD_VERSION} `,
  'background:#ff8000;color:#000;font-weight:700',
  'background:#222;color:#fff'
);
