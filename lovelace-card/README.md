# Just Eat tracking card

A Lovelace custom card that renders the `sensor.justeat_tracking` entity
pushed by [justeat-tracker](https://github.com/monxas/justeat-tracker).

Vanilla web component, **no dependencies**, ~10KB single file.

![card preview](https://github.com/monxas/justeat-tracker/raw/main/lovelace-card/preview.png)

## Features

- **Compact + expanded modes** — auto-toggle, or pin one
- **Auto-hide when idle** — disappears from the dashboard between orders
- **Adaptive theming** — uses HA's CSS variables, looks native in dark + light
- **Failed/Cancelled red theme** — clear visual when an order didn't make it
- **History timeline** — every status transition with timestamps

## Install

### HACS (recommended)

1. HACS → **Frontend** → ⋮ → Custom repositories
2. Add `https://github.com/monxas/justeat-tracker` with category **Lovelace**
3. Click **Install** on "Just Eat tracking card"
4. Reload the browser (Ctrl+F5)

### Manual

1. Copy `justeat-card.js` (from the repo root) to `<config>/www/justeat-card.js`
2. Settings → Dashboards → ⋮ → **Resources** → Add Resource
3. URL: `/local/justeat-card.js`, Type: **JavaScript Module**
4. Reload the browser

## Configure

You first need the [justeat-tracker](https://github.com/monxas/justeat-tracker)
container running and pushing `sensor.justeat_tracking`. Then add the card:

```yaml
type: custom:justeat-card
entity: sensor.justeat_tracking
```

### Full config

```yaml
type: custom:justeat-card
entity: sensor.justeat_tracking
mode: auto                  # auto | compact | expanded   (default: auto)
hide_when_idle: true        # default: true
show_history: true          # default: true
show_progress_bar: true     # default: true
```

| Option              | Default | Description |
| ------------------- | ------- | ----------- |
| `entity`            | _(req)_ | Sensor entity pushed by the tracker |
| `mode`              | `auto`  | `auto` = expanded for active orders, compact for terminal; or force `compact`/`expanded` |
| `hide_when_idle`    | `true`  | Hide the entire card when there's no active or recently-terminal order |
| `show_history`      | `true`  | Show the timeline of status transitions |
| `show_progress_bar` | `true`  | Show the gradient progress bar |

Tap the card to toggle between compact and expanded.

## Combine with other cards

Because of `hide_when_idle: true`, this card disappears from the dashboard
when no order is active. You can put it at the top of a view without taking
permanent real estate:

```yaml
type: vertical-stack
cards:
  - type: custom:justeat-card
    entity: sensor.justeat_tracking
  - type: weather-forecast
    entity: weather.home
  - # ...other cards
```

## Troubleshooting

- **Card says "Entity not found"** — the tracker hasn't pushed to HA yet. Check
  the container logs and confirm `HA_URL` + `HA_TOKEN` are correct.
- **Card never appears** — `hide_when_idle: true` is the default. Set to
  `false` to always show, even with no active order.
- **Card shows the wrong status emoji** — the tracker maps statuses to emoji
  in Spanish. Override via custom CSS or open an issue if your market uses
  different statuses.

## License

[MIT](../LICENSE)
