# justeat-tracker

Tiny Docker container that tracks your Just Eat orders in real time and pushes
state to a Home Assistant sensor. Adaptive polling (10s when the rider is
arriving, 600s in terminal states), rotating OAuth refresh tokens, atomic state
persistence — runs on ~50MB RAM.

Ships with a **HACS-compatible Lovelace card** ([`justeat-card.js`](justeat-card.js))
that renders the sensor with progress bar, ETA, history, and auto-hides between
orders. See [lovelace-card/README.md](lovelace-card/README.md).

> **Unofficial.** Not affiliated with Just Eat Takeaway.com N.V. Uses the same
> public API that the just-eat.es web app uses. Your tokens never leave your
> network.

## What it does

Every N seconds (where N depends on order state):

1. Hits `i18n.api.just-eat.io/consumer/me/orders/es?status=active` to find your
   current order.
2. Hits `/consumer/me/orders/es/{orderId}/status` to pull full state + history.
3. POSTs to your Home Assistant `sensor.justeat_tracking` with everything
   shaped for easy templating.
4. Refreshes the OAuth access token automatically when it nears expiry.

Adaptive polling:

| Status                          | Interval |
| ------------------------------- | -------- |
| `Processing` / `Accepted`       | 120s     |
| `DriverAssigned`                | 60s      |
| `OutForDelivery` / `OnItsWay`   | 30s      |
| `DriverNearby`                  | 20s      |
| `DriverArrivingAtCustomer`      | **10s**  |
| Terminal (Delivered/Cancelled)  | 600s     |
| No active order                 | 300s     |

## Quick start

### 1. Clone

```bash
git clone https://github.com/monxas/justeat-tracker.git
cd justeat-tracker
```

### 2. Extract your initial refresh token

See [docs/extract-refresh-token.md](docs/extract-refresh-token.md) — paste a
one-liner in your browser console, copy the `refresh_token` value out.

### 3. Initialise state

```bash
mkdir -p data
cat > data/state.json <<EOF
{
  "refresh_token": "<paste your refresh_token here>",
  "expires_at": 0
}
EOF
chmod 600 data/state.json
sudo chown -R 1000:1000 data
```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env — set HA_URL and HA_TOKEN
```

`HA_TOKEN` is a long-lived access token from Home Assistant
(Profile → Security → Long-Lived Access Tokens). Best practice: create a
dedicated user `justeat-tracker` and use its token.

### 5. Start

```bash
docker compose up -d --build
docker compose logs -f
```

You should see:

```
INFO Just Eat tracker starting — sensor=sensor.justeat_tracking
INFO Refreshing access token...
INFO Token refreshed OK, new RT=..., expires_at=...
INFO order pdbfgap... status=Accepted next=120s
```

Open Home Assistant → Developer Tools → States → `sensor.justeat_tracking` to
confirm. See [examples/home-assistant/](examples/home-assistant/) for sensor
shape + sample Lovelace card + automations.

## Architecture

```
┌──────────────────┐    polling     ┌─────────────────────────────────┐
│ justeat-tracker  │ ────────────▶  │ Just Eat API                    │
│   (container)    │                │   auth.just-eat.es/connect/token │
│                  │                │   i18n.api.just-eat.io/.../status│
│   /data/state.json                └─────────────────────────────────┘
│   (RT rotating)  │
│                  │ POST sensor state
│                  │ ────────────▶  ┌─────────────────────────────────┐
└──────────────────┘                │ Home Assistant                  │
                                    │   sensor.justeat_tracking       │
                                    └─────────────────────────────────┘
```

- **State persistence**: `data/state.json` is a Docker volume. Container restart
  is safe — current refresh_token + access_token survive.
- **Atomic writes**: `tmp + fsync + rename` to survive container crash mid-save.
- **Token rotation**: Just Eat issues a new refresh_token on every `/connect/token`
  call and invalidates the old. The tracker persists the new one before
  acknowledging.

## Configuration

All via environment variables (`.env`):

| Variable          | Default                      | Description |
| ----------------- | ---------------------------- | ----------- |
| `HA_URL`          | _(required)_                 | Home Assistant base URL, no trailing slash |
| `HA_TOKEN`        | _(required)_                 | HA long-lived access token |
| `SENSOR_ID`       | `sensor.justeat_tracking`    | Override sensor entity_id |
| `COUNTRY`         | `es`                         | Market: `es`, `uk`, `ie`, `it`, `fr`, `dk`, `no`, `ch`, `at` (only `es` verified) |
| `AUTH_HOST`       | derived from `COUNTRY`       | Override OAuth host (e.g. `auth.just-eat.co.uk`) |
| `API_HOST`        | `i18n.api.just-eat.io`       | Override API host (shared across markets) |
| `STOREFRONT_URL`  | derived from `AUTH_HOST`     | Override Origin/Referer (e.g. `https://www.just-eat.es`) |
| `CLIENT_ID`       | `consumer_web_je`            | OAuth client_id (public web app client) |
| `STATE_PATH`      | `/data/state.json`           | Override state file location (inside container) |

### Multi-country usage

For markets other than Spain, set `COUNTRY` to the market code. The auth host is
auto-derived from the preset map. Example UK:

```bash
COUNTRY=uk
```

If your market is not in the preset map, or the derived `auth.just-eat.<tld>`
host doesn't work, set `AUTH_HOST` and `STOREFRONT_URL` explicitly.

If you successfully run the tracker in a market other than ES, please open a PR
moving your market from "untested" to "verified" in `tracker.py`.

## Failure modes

| Scenario                          | What happens |
| --------------------------------- | ------------ |
| Just Eat token rotation fails (401) | Container retries next iteration after marking `expires_at=0` |
| Refresh token expired             | Sensor goes to `state=refresh_failed`, container backs off 600s, you need to re-login and replace `data/state.json` |
| Home Assistant down               | Logs warning, container keeps polling Just Eat, retries on next iteration |
| Container crash mid-save          | `data/state.json.tmp` may exist; safe to delete (or restart — `os.replace` makes write atomic) |

## Security notes

- `data/state.json` and `.env` contain secrets. Both are in `.gitignore`.
- The HA token has near-full access to your HA. Create a dedicated user with
  minimum scope if your HA supports it.
- The refresh token is **as good as your Just Eat password** — anyone who has it
  can read your order history and trigger account actions. Treat it as such.
- The container runs as uid 1000 (non-root). The image is alpine, ~50MB.

## Limitations / non-goals

- Only one order tracked at a time (first active order returned).
- Markets other than Spain are untested but should follow the same OAuth +
  REST pattern. Override `AUTH_HOST` if the preset doesn't match. See
  [docs/extract-refresh-token.md](docs/extract-refresh-token.md) for the
  extraction workflow.
- No real-time WebSocket support yet (polling only). The Just Eat web app uses
  AWS IoT MQTT-over-WebSocket for sub-second updates. See
  [docs/realtime-mqtt-investigation.md](docs/realtime-mqtt-investigation.md) for
  the auth-flow research and how to contribute the implementation.
- No metrics endpoint. PRs welcome to add `/metrics` for Prometheus.

## License

[MIT](LICENSE)
