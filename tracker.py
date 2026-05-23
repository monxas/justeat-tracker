#!/usr/bin/env python3
"""Just Eat order tracker.

Polls Just Eat API with adaptive interval based on order status, refreshes
OAuth access_token via rotating refresh_token (offline_access scope), and pushes
state to a Home Assistant sensor via REST API.

State is persisted in /data/state.json (refresh_token rotates — must survive
container restart). Logs to stdout (json-file driver picks up).
"""
import json, time, os, sys, logging, signal, threading
from datetime import datetime, timezone
from urllib import request, parse, error
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('je')

# ── Config (env) ──
STATE_PATH = os.environ.get('STATE_PATH', '/data/state.json')
HA_URL = os.environ['HA_URL'].rstrip('/')
HA_TOKEN = os.environ['HA_TOKEN']
SENSOR_ID = os.environ.get('SENSOR_ID', 'sensor.justeat_tracking')
# Binary sensor flipped on whenever there's an active order (or a terminal order
# seen <TERMINAL_GRACE_SECONDS ago, so the UI shows the final state briefly).
# Set BINARY_SENSOR_ID='' to disable.
BINARY_SENSOR_ID = os.environ.get('BINARY_SENSOR_ID', 'binary_sensor.justeat_order_active')
TERMINAL_GRACE_SECONDS = int(os.environ.get('TERMINAL_GRACE_SECONDS', '600'))

# Prometheus /metrics HTTP server. Set METRICS_PORT='' (or 0) to disable.
METRICS_PORT = int(os.environ.get('METRICS_PORT', '9100') or 0)

# ── Prometheus metrics (in-memory counters/gauges) ──
_metrics = {
    'refresh_total': 0,
    'refresh_failed_total': 0,
    'orders_fetch_total': 0,
    'orders_fetch_failed_total': 0,
    'status_fetch_total': 0,
    'status_fetch_failed_total': 0,
    'push_total': 0,
    'push_failed_total': 0,
    'loop_iterations_total': 0,
    'loop_errors_total': 0,
    'last_status_change_at': 0,        # unix ts of last state.status change
    'token_expires_at': 0,             # unix ts
    'last_push_at': 0,                 # unix ts
    'order_active': 0,                 # 0/1
    'consecutive_failures': 0,
}
_metrics_lock = threading.Lock()
_last_status_value = None

def _metric_inc(name, n=1):
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + n

def _metric_set(name, v):
    with _metrics_lock:
        _metrics[name] = v

# Country / region. Defaults to ES. Set COUNTRY=uk|it|fr|ie|... to override.
# For markets not in the preset map, override AUTH_HOST too.
COUNTRY = os.environ.get('COUNTRY', 'es').lower()

# Known auth hosts per market. Only ES is verified; the rest follow the TLD
# pattern but have NOT been tested. PRs welcome.
_AUTH_HOSTS = {
    'es': 'auth.just-eat.es',
    'uk': 'auth.just-eat.co.uk',
    'ie': 'auth.just-eat.ie',
    'it': 'auth.just-eat.it',
    'fr': 'auth.just-eat.fr',
    'dk': 'auth.just-eat.dk',
    'no': 'auth.just-eat.no',
    'ch': 'auth.just-eat.ch',
    'at': 'auth.just-eat.at',
}
AUTH_HOST = os.environ.get('AUTH_HOST') or _AUTH_HOSTS.get(COUNTRY, _AUTH_HOSTS['es'])
API_HOST = os.environ.get('API_HOST', 'i18n.api.just-eat.io')

# Storefront URL used for CORS-checked Origin/Referer headers. Derived from AUTH_HOST
# unless explicitly overridden — auth.just-eat.X → www.just-eat.X.
STOREFRONT_URL = os.environ.get('STOREFRONT_URL') or 'https://' + AUTH_HOST.replace('auth.', 'www.')

# ── Just Eat constants ──
CLIENT_ID = os.environ.get('CLIENT_ID', 'consumer_web_je')
TOKEN_URL = f'https://{AUTH_HOST}/connect/token'
STATUS_URL_TMPL = f'https://{API_HOST}/consumer/me/orders/{COUNTRY}/{{order_id}}/status'
ORDERS_URL = f'https://{API_HOST}/consumer/me/orders/{COUNTRY}?status=active'

# ── Polling intervals (seconds) per status ──
POLL_INTERVALS = {
    'AwaitingPayment': 60, 'Processing': 120, 'Accepted': 120,
    'DriverAssigned': 60, 'DriverArrivedAtRestaurant': 60,
    'OnItsWay': 30, 'OutForDelivery': 30, 'DriverNearby': 20,
    'DriverArrivingAtCustomer': 10,
    # Terminal states — kept long; we'll switch to ORDERS poll loop after one terminal push
    'Delivered': 600, 'Completed': 600,
    'Cancelled': 600, 'Canceled': 600, 'Rejected': 600, 'Failed': 600,
}
IDLE_POLL = 300  # no active order → check ORDERS list every 5 min
TERMINAL_STATES = {'Delivered', 'Completed', 'Cancelled', 'Canceled', 'Rejected', 'Failed'}

# Just Eat uses US spelling ('Canceled'); we map both to be safe.
STATUS_LABELS = {
    'AwaitingPayment': '💳 Esperando pago', 'Processing': '🔄 Procesando',
    'Accepted': '✅ Restaurante aceptó', 'DriverAssigned': '🛵 Rider asignado',
    'DriverArrivedAtRestaurant': '🍽️ Rider en restaurante',
    'OnItsWay': '🚚 En camino', 'OutForDelivery': '🚚 En camino',
    'DriverNearby': '📍 Rider cerca', 'DriverArrivingAtCustomer': '🚪 Rider llegando',
    'Delivered': '🎉 Entregado', 'Completed': '✓ Completado',
    'Cancelled': '❌ Cancelado', 'Canceled': '❌ Cancelado',
    'Rejected': '🚫 Rechazado', 'Failed': '⚠️ Fallido',
}

# ── Signal handling ──
_running = True
def _stop(signum, frame):
    global _running
    _running = False
    log.info('Shutdown signal %d received', signum)
signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


# ── State persistence ──
def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        log.error('state.json corrupt: %s — refusing to overwrite, aborting', e)
        sys.exit(2)

def save_state(s):
    """Atomic save via tmp + rename. fsync to disk before rename."""
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(s, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_PATH)


# ── HTTP helper ──
def http(method, url, headers=None, data=None, timeout=15):
    """Return (status_code, body_str). Raises urllib HTTPError on 4xx/5xx (caller handles)."""
    req = request.Request(url, method=method, headers=headers or {}, data=data)
    with request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode('utf-8', errors='replace')


# ── OAuth refresh ──
def refresh_access_token(refresh_tok):
    _metric_inc('refresh_total')
    body = parse.urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': refresh_tok,
        'client_id': CLIENT_ID,
    }).encode()
    try:
        st, body_text = http('POST', TOKEN_URL, headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': STOREFRONT_URL,
            'Referer': STOREFRONT_URL + '/',
        }, data=body)
    except error.HTTPError as e:
        log.error('Refresh HTTP %d: %s', e.code, e.read().decode('utf-8', errors='replace')[:200])
        _metric_inc('refresh_failed_total')
        return None
    except Exception as e:
        log.error('Refresh network error: %s', e)
        _metric_inc('refresh_failed_total')
        return None
    if st != 200:
        log.error('Refresh non-200: %d', st)
        _metric_inc('refresh_failed_total')
        return None
    try:
        d = json.loads(body_text)
    except json.JSONDecodeError:
        log.error('Refresh response not JSON: %s', body_text[:200])
        _metric_inc('refresh_failed_total')
        return None
    if 'access_token' not in d or 'refresh_token' not in d:
        log.error('Refresh response missing tokens: keys=%s', list(d.keys()))
        _metric_inc('refresh_failed_total')
        return None
    expires_at = int(time.time()) + int(d.get('expires_in', 3600))
    _metric_set('token_expires_at', expires_at)
    return {
        'access_token': d['access_token'],
        'refresh_token': d['refresh_token'],
        'expires_at': expires_at,
    }


def ensure_token(state):
    """Return valid access_token, refreshing if needed. Mutates+saves state."""
    now = time.time()
    if state.get('access_token') and state.get('expires_at', 0) > now + 90:
        return state['access_token']
    rt = state.get('refresh_token')
    if not rt:
        log.error('NO refresh_token in state — re-login required at just-eat.es')
        return None
    log.info('Refreshing access token (rt=%s..., exp_at=%d, now=%d)', rt[:6], state.get('expires_at', 0), int(now))
    new = refresh_access_token(rt)
    if not new:
        state['refresh_failed_at'] = int(time.time())
        save_state(state)
        log.error('Refresh failed — RT may have expired. Re-login at just-eat.es and update state.json')
        return None
    # Atomically rotate
    state['access_token'] = new['access_token']
    state['refresh_token'] = new['refresh_token']
    state['expires_at'] = new['expires_at']
    state.pop('refresh_failed_at', None)
    save_state(state)
    log.info('Token refreshed OK, new RT=%s..., expires_at=%d', new['refresh_token'][:6], new['expires_at'])
    return new['access_token']


# ── Just Eat fetches ──
def _je_headers(access_tok):
    return {
        'Authorization': f'Bearer {access_tok}',
        'Accept': 'application/json',
        'Accept-Version': '2019-05',
        'Origin': STOREFRONT_URL,
        'X-Jet-Application': 'OneWeb',
    }

def fetch_active_order(access_tok):
    """Return (order_id, http_status). order_id=None if no active orders."""
    _metric_inc('orders_fetch_total')
    try:
        st, body = http('GET', ORDERS_URL, headers=_je_headers(access_tok))
    except error.HTTPError as e:
        _metric_inc('orders_fetch_failed_total')
        return None, e.code
    except Exception as e:
        log.warning('orders list network error: %s', e)
        _metric_inc('orders_fetch_failed_total')
        return None, 0
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        log.warning('orders list non-JSON response')
        return None, st
    orders = d if isinstance(d, list) else d.get('orders', [])
    for o in orders:
        oid = o.get('orderId') or o.get('id')
        if oid:
            return oid, st
    return None, st

def fetch_order_status(access_tok, order_id):
    """Return (raw_dict, http_status). raw_dict=None on error."""
    _metric_inc('status_fetch_total')
    try:
        st, body = http('GET', STATUS_URL_TMPL.format(order_id=order_id), headers=_je_headers(access_tok))
    except error.HTTPError as e:
        _metric_inc('status_fetch_failed_total')
        return None, e.code
    except Exception as e:
        log.warning('status network error: %s', e)
        _metric_inc('status_fetch_failed_total')
        return None, 0
    try:
        return json.loads(body), st
    except json.JSONDecodeError:
        return None, st


# ── Data shaping ──
def transform_status(raw):
    s = raw.get('status', {})
    cur = s.get('value', '?')
    eta_obj = s.get('estimatedCompletionInMinutes') or {}
    eta_str = None
    if eta_obj.get('start') is not None:
        eta_str = f"{eta_obj['start']}-{eta_obj['end']} min"
    history = [
        {'ts': h.get('timestamp'), 'value': h.get('value'),
         'label': STATUS_LABELS.get(h.get('value'), h.get('value'))}
        for h in s.get('history', [])
    ]
    # Pin down when the order transitioned to its terminal state.
    # Use the FIRST history entry whose value is in TERMINAL_STATES.
    # (Falls back to current wall clock if history is empty but we're terminal.)
    terminal_since = None
    if cur in TERMINAL_STATES:
        for h in history:
            if h['value'] in TERMINAL_STATES and h['ts']:
                terminal_since = h['ts']
                break
        if not terminal_since:
            terminal_since = datetime.now(timezone.utc).isoformat()
    return {
        'isActive': bool(s.get('isActive')),
        'status': cur,
        'statusLabel': STATUS_LABELS.get(cur, cur),
        'isTerminal': cur in TERMINAL_STATES,
        'terminalSince': terminal_since,
        'restaurant': raw.get('restaurantName'),
        'orderId': raw.get('id'),
        'eta': eta_str,
        'dueDate': s.get('currentDueDate'),
        'history': history,
        'upcoming': [u.get('value') for u in s.get('upcoming', [])],
        'fetchedAt': datetime.now(timezone.utc).isoformat(),
    }


# ── HA push ──
def _post_state(entity_id, state_value, attrs):
    """Low-level POST to /api/states/{entity_id}. Returns True on 200/201."""
    payload = json.dumps({'state': state_value, 'attributes': attrs}).encode()
    try:
        st, _ = http('POST', f'{HA_URL}/api/states/{entity_id}', headers={
            'Authorization': f'Bearer {HA_TOKEN}',
            'Content-Type': 'application/json',
        }, data=payload, timeout=10)
        if st not in (200, 201):
            log.warning('HA push %s HTTP %d', entity_id, st)
            return False
        return True
    except error.HTTPError as e:
        log.warning('HA push %s HTTP %d: %s', entity_id, e.code, e.read().decode('utf-8', errors='replace')[:200])
    except Exception as e:
        log.warning('HA push %s failed: %s', entity_id, e)
    return False

def _is_order_active(attrs):
    """True if there's a live order, OR an order that just transitioned to
    terminal less than TERMINAL_GRACE_SECONDS ago (so the UI shows the result)."""
    if not attrs:
        return False
    if attrs.get('isActive'):
        return True
    if attrs.get('isTerminal'):
        # Prefer terminalSince (timestamp of the terminal transition in order
        # history) over fetchedAt (each refetch resets), so the grace window is
        # anchored to when the order ACTUALLY went terminal.
        anchor = attrs.get('terminalSince') or attrs.get('fetchedAt')
        if not anchor:
            return False
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(anchor)).total_seconds()
            return age < TERMINAL_GRACE_SECONDS
        except (ValueError, TypeError):
            return False
    return False

def push_to_ha(state_value, attributes):
    """Push to BOTH sensor.justeat_tracking AND binary_sensor.justeat_order_active."""
    global _last_status_value
    attrs = dict(attributes or {})
    attrs['lastPushAt'] = datetime.now(timezone.utc).isoformat()
    attrs.setdefault('friendly_name', 'Just Eat tracking')
    attrs.setdefault('icon', 'mdi:moped')

    # Metric: detect status change (used by Prometheus rate())
    cur_status = attrs.get('status') or state_value
    if cur_status != _last_status_value:
        _metric_set('last_status_change_at', int(time.time()))
        _last_status_value = cur_status
    _metric_set('last_push_at', int(time.time()))
    _metric_set('order_active', 1 if _is_order_active(attrs) else 0)

    ok = _post_state(SENSOR_ID, state_value, attrs)
    _metric_inc('push_total')
    if not ok:
        _metric_inc('push_failed_total')

    if BINARY_SENSOR_ID:
        active = _is_order_active(attrs)
        # Binary sensors in HA REST API: state must be literally 'on' or 'off'.
        bin_attrs = {
            'friendly_name': 'Just Eat order active',
            'icon': 'mdi:moped' if active else 'mdi:moped-off',
            'device_class': 'occupancy',
            # Helpful pass-through for templating without indirection:
            'status': attrs.get('status'),
            'statusLabel': attrs.get('statusLabel'),
            'restaurant': attrs.get('restaurant'),
            'orderId': attrs.get('orderId'),
            'dueDate': attrs.get('dueDate'),
            'isTerminal': attrs.get('isTerminal'),
            'lastPushAt': attrs['lastPushAt'],
        }
        _post_state(BINARY_SENSOR_ID, 'on' if active else 'off', bin_attrs)

    return ok


# ── Prometheus /metrics HTTP server ──
def _render_metrics():
    """Return Prometheus text format."""
    with _metrics_lock:
        snapshot = dict(_metrics)
    lines = ['# HELP justeat_tracker_info Tracker info',
             '# TYPE justeat_tracker_info gauge',
             f'justeat_tracker_info{{country="{COUNTRY}",sensor="{SENSOR_ID}"}} 1']
    # Counters
    for name, help_text in [
        ('refresh_total', 'OAuth refresh attempts'),
        ('refresh_failed_total', 'OAuth refresh failures'),
        ('orders_fetch_total', '/orders endpoint fetches'),
        ('orders_fetch_failed_total', '/orders fetches that failed'),
        ('status_fetch_total', '/status endpoint fetches'),
        ('status_fetch_failed_total', '/status fetches that failed'),
        ('push_total', 'HA push attempts'),
        ('push_failed_total', 'HA pushes that failed'),
        ('loop_iterations_total', 'Main loop iterations'),
        ('loop_errors_total', 'Main loop exceptions'),
    ]:
        lines.append(f'# HELP justeat_tracker_{name} {help_text}')
        lines.append(f'# TYPE justeat_tracker_{name} counter')
        lines.append(f'justeat_tracker_{name} {snapshot.get(name, 0)}')
    # Gauges
    for name, help_text in [
        ('last_status_change_at', 'Unix timestamp of last status change'),
        ('token_expires_at', 'Unix timestamp when current AT expires'),
        ('last_push_at', 'Unix timestamp of last HA push'),
        ('order_active', 'Whether an order is active (0/1)'),
        ('consecutive_failures', 'Consecutive iteration failures'),
    ]:
        lines.append(f'# HELP justeat_tracker_{name} {help_text}')
        lines.append(f'# TYPE justeat_tracker_{name} gauge')
        lines.append(f'justeat_tracker_{name} {snapshot.get(name, 0)}')
    return '\n'.join(lines) + '\n'


class _MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/metrics':
            body = _render_metrics().encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/health':
            with _metrics_lock:
                last_push = _metrics.get('last_push_at', 0)
            healthy = last_push > 0 and (time.time() - last_push) < 1800  # 30 min
            body = b'ok\n' if healthy else b'stale\n'
            self.send_response(200 if healthy else 503)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, fmt, *args):
        # Silence default access log
        pass


def _start_metrics_server():
    if not METRICS_PORT:
        log.info('METRICS_PORT=0 — /metrics disabled')
        return
    try:
        httpd = HTTPServer(('0.0.0.0', METRICS_PORT), _MetricsHandler)
    except OSError as e:
        log.error('Could not bind /metrics on :%d — %s', METRICS_PORT, e)
        return
    t = threading.Thread(target=httpd.serve_forever, daemon=True, name='metrics')
    t.start()
    log.info('Metrics server on :%d  → /metrics + /health', METRICS_PORT)


# ── Main loop ──
def main():
    log.info('Just Eat tracker starting — country=%s auth=%s api=%s sensor=%s HA=%s',
             COUNTRY, AUTH_HOST, API_HOST, SENSOR_ID, HA_URL)
    state = load_state()
    if 'refresh_token' not in state:
        log.error('FATAL: no refresh_token in %s. Initialize before starting.', STATE_PATH)
        sys.exit(1)

    # Seed gauges from persisted state so /metrics is meaningful before first iteration
    if state.get('expires_at'):
        _metric_set('token_expires_at', state['expires_at'])

    _start_metrics_server()

    consecutive_failures = 0

    while _running:
        _metric_inc('loop_iterations_total')
        _metric_set('consecutive_failures', consecutive_failures)
        delay = IDLE_POLL  # safe default
        try:
            at = ensure_token(state)
            if not at:
                push_to_ha('refresh_failed', {
                    'isActive': False,
                    'error': 'refresh_failed',
                    'message': 'Re-login at just-eat.es required',
                    'fetchedAt': datetime.now(timezone.utc).isoformat(),
                })
                delay = 600
                consecutive_failures += 1
            else:
                order_id, st = fetch_active_order(at)
                if st == 401:
                    log.warning('orders 401 — forcing refresh next iteration')
                    state['expires_at'] = 0
                    save_state(state)
                    delay = 5
                elif st in (0, 5, 502, 503, 504):
                    log.warning('orders transient error st=%d', st)
                    delay = 60
                    consecutive_failures += 1
                elif not order_id:
                    # No active order
                    if state.get('last_order_id'):
                        log.info('No active order (was tracking %s, now cleared)', state['last_order_id'])
                        state.pop('last_order_id', None)
                        save_state(state)
                    push_to_ha('idle', {
                        'isActive': False,
                        'fetchedAt': datetime.now(timezone.utc).isoformat(),
                    })
                    delay = IDLE_POLL
                    consecutive_failures = 0
                else:
                    # Active order — get full status
                    raw, st2 = fetch_order_status(at, order_id)
                    if st2 == 401:
                        state['expires_at'] = 0
                        save_state(state)
                        delay = 5
                    elif raw:
                        data = transform_status(raw)
                        push_to_ha(data['status'] if data['isActive'] else data['status'] + '_terminal', data)
                        state['last_order_id'] = order_id
                        save_state(state)
                        if data['isTerminal']:
                            log.info('order %s reached terminal state=%s — backing off', order_id, data['status'])
                            delay = POLL_INTERVALS.get(data['status'], 600)
                        else:
                            delay = POLL_INTERVALS.get(data['status'], 60)
                            log.info('order %s status=%s next=%ds', order_id[:8], data['status'], delay)
                        consecutive_failures = 0
                    else:
                        log.warning('status fetch st=%d', st2)
                        delay = 60
                        consecutive_failures += 1
        except Exception as e:
            log.exception('loop error: %s', e)
            consecutive_failures += 1
            _metric_inc('loop_errors_total')
            delay = min(60 * (2 ** min(consecutive_failures, 5)), 600)

        # Sleep in 1-sec chunks for fast shutdown
        for _ in range(delay):
            if not _running:
                break
            time.sleep(1)

    log.info('exited cleanly')


if __name__ == '__main__':
    main()
