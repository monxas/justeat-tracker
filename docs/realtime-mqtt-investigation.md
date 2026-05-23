# Real-time updates: investigation notes

The Just Eat web app uses **AWS IoT Core MQTT over WebSocket** for sub-second
order updates. This document tracks what we know so far about the auth + topic
flow. **The tracker currently uses HTTP polling (10–600s adaptive); MQTT support
is a future feature.**

## What we know

### WebSocket endpoint

```
wss://amnvd3x0n6h2a-ats.iot.eu-west-1.amazonaws.com/mqtt
```

- Region: `eu-west-1`
- Account-specific endpoint (the `amnvd3x0n6h2a` prefix is Just Eat's IoT data endpoint)
- ALPN: `mqtt` (via `Sec-WebSocket-Protocol: mqtt`)
- Connection succeeds with HTTP 101 Switching Protocols

### Auth mechanism (inferred)

The WebSocket upgrade request has **no SigV4 query parameters** (no
`X-Amz-Algorithm`, `X-Amz-Credential`, etc.). This rules out the standard
SigV4-signed-URL flow.

The most likely auth mechanism is **AWS IoT Custom Authorizer**:

- A Lambda function on the AWS side validates a token sent inside the MQTT
  CONNECT packet (username/password fields).
- The token is probably the user's OAuth access token (the same JWT we already
  refresh in `tracker.py`).

### What triggers the connection (HAR ordering)

The WebSocket opens shortly after these chunks are loaded:

- `chunks/iot-core-sdk.4d2fd7a82dd42e45.js`
- `chunks/order-tracker-component.5d715f539366da83.js`
- `chunks/order-tracker-map.1868aaefc2271878.js`

The `iot-core-sdk` chunk is likely a wrapper around the official AWS IoT JS SDK
or `@aws-amplify/pubsub`.

### What we don't know

1. **MQTT CONNECT payload format** — username, password, client ID
2. **Topic name(s) subscribed** — likely templates like
   `orders/{consumerId}/{orderId}` or `consumer/{userId}/orders/+/status`
3. **Message payload structure** — probably similar to the REST `/status`
   response but possibly compacted

These are only visible in actual MQTT frames, which HAR does not capture.

## How to extract the missing pieces

### Option A — mitmproxy + Chrome DevTools

1. Run `mitmproxy` as a transparent proxy with TLS interception.
2. Install the mitmproxy CA in your OS and browser trust store.
3. Open Chrome with `--proxy-server=127.0.0.1:8080`.
4. Place a real order on just-eat.es.
5. Filter mitmproxy by `~h SEC-WebSocket-Protocol mqtt` and inspect frames.
6. The first frame after `WebSocket OPEN` is the MQTT CONNECT packet — decode
   the variable header to get username/password.

MQTT 3.1.1 CONNECT packet wire format:

```
| Fixed Header (1+VBI bytes)
| Protocol Name (e.g. "MQTT")
| Protocol Level (4 = MQTT 3.1.1, 5 = MQTT 5)
| Connect Flags (1 byte) — bits indicate password/username/will
| Keep Alive (2 bytes)
| Client ID (UTF-8 string)
| [Will Topic / Payload]
| Username (UTF-8 string)
| Password (binary)
```

### Option B — Chrome DevTools Protocol (CDP)

Chrome's Network panel actually CAN show WebSocket frames if you enable them:

1. Open Just Eat order page.
2. F12 → Network → filter "WS" → click the `mqtt` entry.
3. Switch to the **Messages** sub-tab. You'll see CONNECT, SUBSCRIBE, PUBLISH
   frames in real time.

### Option C — Read the SDK source

The `iot-core-sdk.4d2fd7a82dd42e45.js` chunk is small (~5KB), but probably
imports a vendored chunk via lazy-load. Inspect Chrome DevTools → Sources →
filter `iot-core-sdk` to find the actual SDK module.

If it's `aws-iot-device-sdk-v2` ported to JS, the auth signature scheme is in
their public docs. If it's a custom Just Eat shim, follow the bundle imports.

## Implementing once we have the pieces

Pseudo-code outline for `tracker.py`:

```python
import paho.mqtt.client as mqtt

IOT_ENDPOINT = 'amnvd3x0n6h2a-ats.iot.eu-west-1.amazonaws.com'
IOT_PORT = 443

def on_connect(client, userdata, flags, rc):
    client.subscribe(f'orders/{consumer_id}/+/status')

def on_message(client, userdata, msg):
    data = json.loads(msg.payload.decode())
    push_to_ha(data['status'], data)

client = mqtt.Client(
    client_id=f'web-{consumer_id}-{uuid.uuid4()}',
    transport='websockets',
    protocol=mqtt.MQTTv311,
)
client.username_pw_set(username='<TBD>', password=access_token)
client.tls_set()
client.ws_set_options(path='/mqtt', headers={'Sec-WebSocket-Protocol': 'mqtt'})
client.on_connect = on_connect
client.on_message = on_message
client.connect(IOT_ENDPOINT, IOT_PORT, keepalive=60)
client.loop_forever()
```

The blockers are:
1. Filling in the `username` field (probably `consumer-{userId}` or similar).
2. Subscribing to the right topic.

## Why we're not doing this yet

Current polling efficiency is high enough that real-time MQTT is a quality
improvement, not a necessity:

| Metric                    | Polling (current) | MQTT (theoretical) |
| ------------------------- | ----------------- | ------------------ |
| Requests per 45min order  | ~80               | 1 connect + sub    |
| Latency to update         | 10–60s            | sub-second         |
| Code complexity           | Low (stdlib only) | High (paho-mqtt + reconnect + auth refresh) |
| Failure modes             | 401 → refresh     | All polling failure modes + WebSocket disconnect + auth expiry mid-session |

If you've solved the auth flow above, please open a PR.
