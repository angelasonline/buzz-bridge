# Path B — hosted, always-online, publicly viewable bridge runner.
# Multi-stage: (1) compile the real Apache-2.0 buzz-cli from source for Linux,
# (2) a slim Python runtime with the bridge + runner. We keep the CLI (no NIP-OA
# reimplementation) per the decision. buzz-cli is a thin *client* (clap/reqwest/
# tokio/nostr/rustls-ring; no relay/db/axum/postgres/redis), but it is NOT a
# single-crate build: it depends on four in-repo crates (buzz-core, buzz-sdk,
# buzz-persona, buzz-ws-client). `cargo build -p buzz-cli` compiles just that
# subgraph — cheap next to the full workspace — provided the whole repo is present.

# ---- stage 1: build buzz-cli --------------------------------------------------
# 1.95 matches the repo's rust-toolchain.toml pin (channel = "1.95.0"). A lower
# base tag still works — rustup auto-installs 1.95 from the toolchain file — but
# it wastes a toolchain download, so we match the pin.
FROM rust:1.95-slim AS cli
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
# Pinned to the exact commit Fizz confirmed the build against (native arm64,
# `cargo build -p buzz-cli --release`, 1m23s clean). Override with a newer SHA/
# tag/branch when you want to move forward. depth-1 fetch of one ref keeps the
# clone small; GitHub allows fetch-by-SHA, so this works for a commit too.
ARG BUZZ_REF=e6c90bb7c430d1b2af16508b634f9a5283b7fa3b
RUN git init -q . \
 && git remote add origin https://github.com/block/buzz \
 && git fetch -q --depth 1 origin "${BUZZ_REF}" \
 && git checkout -q FETCH_HEAD
# buzz-cli pulls four in-repo crates (buzz-core/buzz-sdk/buzz-persona/buzz-ws-client),
# so `-p` needs the WHOLE tree present (the fetch above provides it). Default release
# profile is LTO-off / codegen-units=16 — the memory-friendly path. If Render's
# builder OOMs or times out on the Rust stage, uncomment to cap parallelism
# (lower peak RAM, longer build):
# ENV CARGO_BUILD_JOBS=2
# rustls+ring means no OpenSSL system dep. Build only the CLI crate.
RUN cargo build -p buzz-cli --release
# Binary name is `buzz` (crates/buzz-cli/Cargo.toml [[bin]] name = "buzz").
RUN install -m 755 target/release/buzz /usr/local/bin/buzz && buzz --help >/dev/null

# ---- stage 2: python runtime + bridge ----------------------------------------
FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=cli /usr/local/bin/buzz /usr/local/bin/buzz
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # coincurve + websockets ship manylinux wheels
COPY bridge.py translate_forward.py runner.py ./
COPY .scratch/online_relays_gps.csv .scratch/online_relays_gps.csv

# Credentials come from the host's SECRET STORE at runtime, never baked in:
#   BUZZ_RELAY_URL, BUZZ_PRIVATE_KEY (bot key), BUZZ_AUTH_TAG (bounded+scoped bot tag)
# Behavior via env: LANGLAYER_URL, GEOHASH, LANGUAGES, INTERVAL, PORT (host sets $PORT).
EXPOSE 8787
CMD ["python", "runner.py"]
