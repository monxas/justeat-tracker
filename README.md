# justeat-tracker

Tiny Docker container that tracks your Just Eat orders in real time and pushes
state to a Home Assistant sensor. Adaptive polling (10s when the rider is
arriving, 600s in terminal states), rotating OAuth refresh tokens, atomic state
persistence — runs on ~50MB RAM.

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

| Variable      | Default                      | Description |
| ------------- | ---------------------------- | ----------- |
| `HA_URL`      | _(required)_                 | Home Assistant base URL, no trailing slash |
| `HA_TOKEN`    | _(required)_                 | HA long-lived access token |
| `SENSOR_ID`   | `sensor.justeat_tracking`    | Override sensor entity_id |
| `STATE_PATH`  | `/data/state.json`           | Override state file location (inside container) |

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
- Country-locked: defaults to Spain (`just-eat.es`). See
  [docs/extract-refresh-token.md](docs/extract-refresh-token.md) for hints on
  porting to other markets.
- No PubNub WebSocket support yet (polling only). The Just Eat web app uses
  PubNub for sub-second updates — a PR to switch to push-based would be welcome.
- No metrics endpoint. PRs welcome to add `/metrics` for Prometheus.

## License

[MIT](LICENSE)
