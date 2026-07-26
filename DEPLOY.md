[DEPLOY.md](https://github.com/user-attachments/files/30394839/DEPLOY.md)
# Keeping the bridge online

The forwarder is a poller: each run reads new Buzz announcements and forwards them.
Staying online needs a supervisor loop that does not die, plus a status page so the
service can be observed. `runner.py` is both — a crash-proof forward loop and an HTTP
status surface (`/` dashboard, `/status` JSON, `/healthz`).

The bridge itself lives in
[langlayer/buzz](https://github.com/angelasonline/langlayer/tree/main/buzz). This repo is
the deployment.

## The credential gate — applies to both paths

The bridge reads the channel with `buzz messages get`, which needs three values:
`BUZZ_RELAY_URL`, `BUZZ_PRIVATE_KEY`, and `BUZZ_AUTH_TAG`.

**`BUZZ_PRIVATE_KEY` is a dedicated bot key, not the owner key.** A `bot`-role identity is
created and added to the announcements channel. The owner key is never used by the runner.

**`BUZZ_AUTH_TAG` is the NIP-OA owner-attestation tag, and it is the remaining gate.** Buzz
relay reads require this attestation on each request; channel membership alone is not
enough. Empirically: bot key with no tag returns `403 relay_membership_required`; an
established member with the tag removed returns the same; with the tag, reads succeed.

The tag is reusable — per-request auth expires by timestamp, but the tag itself persists.
Because an issued tag cannot be revoked, mint it **bounded** with a `created_at<` clause
and **scoped** with `kind=` clauses limited to what the bridge reads. It is minted through
Buzz Desktop agent-provisioning; there is no CLI command for it.

Until the tag is set in the runner's environment, the container builds and boots but 403s
on reads.

## Path B — Render Web Service (hosted, public URL)

Runs continuously with a public status page. `runner.py` binds `0.0.0.0:$PORT`, which
Render injects.

Render builds the image; no local Docker is required. The `Dockerfile` is multi-stage:

- **Stage 1** compiles the Apache-2.0 `buzz` CLI from source (`github.com/block/buzz`,
  `cargo build -p buzz-cli --release`) on `rust:1.95-slim`, matching the repo's
  `rust-toolchain.toml` pin, at a commit fixed by `ARG BUZZ_REF`. TLS is rustls plus ring,
  so there is no OpenSSL system dependency. If Render's builder runs out of memory or time
  on this stage, uncomment `ENV CARGO_BUILD_JOBS=2` in the Dockerfile.
- **Stage 2** is `python:3.12-slim`: installs `requirements.txt` (coincurve and
  websockets, both manylinux wheels), copies the `buzz` binary in beside `bridge.py`,
  `translate_forward.py`, and `runner.py`, and runs `runner.py`.

Build context, committed beside the `Dockerfile`:

- `Dockerfile`, `requirements.txt`
- `bridge.py`, `translate_forward.py`, `runner.py`
- `.scratch/online_relays_gps.csv` — the geo-relay directory the bridge reads

Never commit `.secrets/` or any `*.env`. Credentials go in as Render environment
variables and are not baked into the image.

| Purpose | Variables |
|---|---|
| Credentials | `BUZZ_RELAY_URL`, `BUZZ_PRIVATE_KEY`, `BUZZ_AUTH_TAG` |
| Behavior | `LANGLAYER_URL`, `GEOHASH`, `LANGUAGES`, `SOURCE_LANGUAGE`, `INTERVAL`, `RELAY_COUNT` |

`PORT` is injected by Render. Do not set it.

## Path A — local launchd (private)

Runs whenever the machine is on and awake, and survives logout and reboot. Viewable by
others only if the port is exposed through a tunnel, so in practice this is the private
option.

```bash
cd ~/.buzz/REPOS/buzz-ops-bridge
cp deploy/bridge.env.example .secrets/bridge.env   # fill in real values
chmod 600 .secrets/bridge.env
# edit the absolute-path placeholders in the plist to this repo's path:
cp deploy/com.buzzbridge.runner.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.buzzbridge.runner.plist
open http://127.0.0.1:8787
```

Same credential gate as above: the bot's `BUZZ_AUTH_TAG` must be in
`.secrets/bridge.env` before reads succeed.

## Status

`runner.py` is tested — all three endpoints serve, the loop survives per-iteration
errors, and it fails fast with a clear message when credentials are missing. The
`Dockerfile` builds on Render through both stages and boots.

The one remaining step before a live forward, on either path, is the bot's
`BUZZ_AUTH_TAG` from Desktop agent-provisioning.
