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

`bridge.py`, `translate_forward.py`, and `runner.py` are vendored from
[langlayer/buzz](https://github.com/angelasonline/langlayer/tree/main/buzz). The Docker
build copies the versions in *this* repo, so a change made there does not reach the
deployed image until it is copied here.

Build context, committed beside the `Dockerfile`:

- `Dockerfile`, `requirements.txt`
- `bridge.py`, `translate_forward.py`, `runner.py`
- `.scratch/online_relays_gps.csv` — the geo-relay directory the bridge reads

Never commit `.secrets/` or any `*.env`. Credentials go in as Render environment
variables and are not baked into the image.

| Purpose | Variables |
|---|---|
| Credentials | `BUZZ_RELAY_URL`, `BUZZ_PRIVATE_KEY`, `BUZZ_AUTH_TAG` |
| Behavior | `LANGLAYER_URL`, `GEOHASH`, `LANGUAGES`, `INTERVAL` — plus optional `SOURCE_LANGUAGE` and `RELAY_COUNT`, both defaulted |

`PORT` is injected by Render. Do not set it.

## Path A — local launchd

For running the forwarder on a Mac instead of a host: launchd survives logout and reboot,
and the status page stays local unless you expose the port through a tunnel.

That flow depends on a `deploy/` kit (a plist, a wrapper script, and an env template) that
lives with the source checkout, not in this repo. See
[langlayer/buzz](https://github.com/angelasonline/langlayer/tree/main/buzz).

The credential gate above applies there too.

## Status

`runner.py` is tested — all three endpoints serve, the loop survives per-iteration
errors, and it fails fast with a clear message when credentials are missing. The
`Dockerfile` builds on Render through both stages and boots.

The one remaining step before a live forward, on either path, is the bot's
`BUZZ_AUTH_TAG` from Desktop agent-provisioning.
