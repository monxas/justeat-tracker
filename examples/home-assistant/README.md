# Home Assistant integration

The tracker pushes order state to a sensor entity via HA REST API. No HA-side
integration code required — the sensor appears automatically the first time the
tracker pushes.

## Sensor shape

```yaml
sensor.justeat_tracking:
  state: DriverNearby          # or Processing / OnItsWay / Canceled / idle / refresh_failed
  attributes:
    isActive: true
    status: DriverNearby
    statusLabel: "📍 Rider cerca"
    isTerminal: false
    restaurant: "Pizza Place"
    orderId: pdbfgapahkiir84h0knimq
    eta: "5-10 min"
    dueDate: "2026-05-23T13:45:00+00:00"
    history:
      - { ts: "2026-05-23T13:00:00Z", value: "Processing", label: "🔄 Procesando" }
      - { ts: "2026-05-23T13:05:00Z", value: "DriverAssigned", label: "🛵 Rider asignado" }
    upcoming: ["DriverArrivingAtCustomer", "Delivered"]
    fetchedAt: "2026-05-23T13:30:00.123456+00:00"
    lastPushAt: "2026-05-23T13:30:00.124567+00:00"
    friendly_name: "Just Eat tracking"
    icon: mdi:moped
```

## Frontend options

Three ways to render the sensor, from easiest to most custom:

### 1. The bundled HACS card ★ recommended

The repo ships with [`justeat-card.js`](../../justeat-card.js) — install via
HACS, see [lovelace-card/README.md](../../lovelace-card/README.md).

```yaml
type: custom:justeat-card
entity: sensor.justeat_tracking
```

### 2. Mushroom template card

If you'd rather use [mushroom](https://github.com/piitaya/lovelace-mushroom):

```yaml
type: custom:mushroom-template-card
icon: mdi:moped
primary: "{{ state_attr('sensor.justeat_tracking', 'statusLabel') }}"
secondary: >-
  {% set d = state_attr('sensor.justeat_tracking', 'dueDate') %}
  {% if d %}ETA {{ as_timestamp(d) | timestamp_custom('%H:%M', true) }}{% endif %}
entity: sensor.justeat_tracking
tap_action:
  action: more-info
multiline_secondary: true
fill_container: true
```

Hide the card when `state == 'idle'`:

```yaml
visibility:
  - condition: state
    entity: sensor.justeat_tracking
    state_not:
      - idle
      - unavailable
```

## Automations

### Push notification when rider is near

```yaml
- alias: "Just Eat — rider cerca"
  trigger:
    - platform: state
      entity_id: sensor.justeat_tracking
      to: DriverNearby
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "🛵 Rider cerca"
        message: "{{ state_attr('sensor.justeat_tracking', 'restaurant') }}"
```

### Alert when refresh token fails

```yaml
- alias: "Just Eat — sesión caducada"
  trigger:
    - platform: state
      entity_id: sensor.justeat_tracking
      to: refresh_failed
      for: minutes: 5
  action:
    - service: persistent_notification.create
      data:
        title: "Just Eat tracker"
        message: "Re-login at just-eat.es and update data/state.json"
```

### 3. Custom standalone dashboard (advanced)

The `justeat-live.js` + `justeat-live.css` in this directory implement a
self-contained banner + tap-to-expand takeover for custom HTML dashboards
(not Lovelace). They require:

- `haGet(entityId)` helper in your dashboard
- DOM elements `#justeat-banner` and `#justeat-live`
- Body class `je-banner-active` toggled when banner visible

Use them as a reference if you're building a custom tablet dashboard. For
standard Home Assistant usage, prefer option 1 (the HACS card).

Use them as a reference, not a drop-in install.
