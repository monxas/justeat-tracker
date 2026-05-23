# Extracting your initial refresh token from Just Eat

The tracker needs an initial `refresh_token` to bootstrap. Once running, it rotates
automatically (Just Eat issues a new RT on every refresh and invalidates the old).

## Step 1 — Log in to Just Eat in your browser

Open https://www.just-eat.es (or your country domain) and log in. Stay on the page.

## Step 2 — Open DevTools console

Press `F12` (or `Cmd+Option+I` on macOS) and switch to the **Console** tab.

## Step 3 — Paste this single-line snippet and press Enter

```javascript
(()=>{const d={timestamp:new Date().toISOString(),origin:location.origin,localStorage:{},sessionStorage:{},cookies_js_accessible:{}};for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);d.localStorage[k]=localStorage.getItem(k);}for(let i=0;i<sessionStorage.length;i++){const k=sessionStorage.key(i);d.sessionStorage[k]=sessionStorage.getItem(k);}document.cookie.split(/;\s*/).forEach(p=>{const[k,...v]=p.split('=');if(k)d.cookies_js_accessible[k]=v.join('=');});const j=JSON.stringify(d,null,2);console.log(j);if(navigator.clipboard)navigator.clipboard.writeText(j);return d;})();
```

It dumps localStorage + sessionStorage + JS-accessible cookies to the console and
copies the JSON to your clipboard. **Do not share this output publicly — it
contains your access tokens.**

## Step 4 — Locate the refresh token

Search the JSON for either of these:

- Cookie `je-rt` (43 chars, opaque)
- `localStorage["oidc.user:https://auth.just-eat.es:consumer_web_je"].refresh_token`

Both contain the same value. Example shape:

```json
"je-rt": "1KF5M4nyzzhbkJzd_iAyr0hNeH7vlprM8CfBqQ6tfOc"
```

## Step 5 — Initialise `data/state.json`

```json
{
  "refresh_token": "1KF5M4nyzzhbkJzd_iAyr0hNeH7vlprM8CfBqQ6tfOc",
  "expires_at": 0
}
```

Permissions: `chmod 600 data/state.json` and `chown 1000:1000 data/` (the
container runs as uid 1000).

## Step 6 — Start the container

```bash
docker compose up -d --build
docker compose logs -f
```

You should see:

```
INFO Refreshing access token (rt=1KF5M4..., exp_at=0, now=...)
INFO Token refreshed OK, new RT=vYTQvz..., expires_at=...
```

If you see `Refresh failed — RT may have expired`, your initial RT was already
consumed elsewhere (e.g., you reloaded the browser tab between extraction and
deployment). Repeat from step 1.

## Country / region variants

The defaults target `just-eat.es`. For other markets you'll need to adjust the
host in `tracker.py`:

| Country  | API host                                    |
| -------- | ------------------------------------------- |
| Spain    | `i18n.api.just-eat.io/consumer/me/orders/es` |
| UK       | `i18n.api.just-eat.io/consumer/me/orders/uk` *(guess, untested)* |
| Italy    | `i18n.api.just-eat.io/consumer/me/orders/it` *(guess, untested)* |

The token endpoint is shared across Just Eat group brands. The `auth.just-eat.{tld}`
host follows the storefront TLD. PRs welcome.
