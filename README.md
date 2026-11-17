# Buzz Bridge

Buzz Bridge is a service designed to distribute announcements from Buzz to Bitchat geographic channels, with optional multilingual processing through Language Layer.

## How it works

1. Retrieves new announcements from an authorized Buzz channel.
2. Sends announcement text to Language Layer when a compatible translation service is configured.
3. Creates signed Nostr events for the selected geographic channel.
4. Publishes those events to geographically selected Nostr relays.
5. Tracks processed announcements and provides a monitoring dashboard.

## Current development status

**Implemented**
- Buzz CLI integration for retrieving announcements
- Nostr event creation and cryptographic signing
- Geographic relay selection
- Relay publishing
- Automated polling and status endpoints

**Requires verification or further development**
- Buzz bot authentication and successful message retrieval
- Live Language Layer translation
- Confirmed relay acceptance and delivery to Bitchat clients
- Reliable message retries and persistent storage

## Network requirements

Buzz Bridge requires internet connectivity to communicate with Buzz and Nostr relays.

Bitchat supports local Bluetooth communication independently, but Buzz Bridge does not currently provide an internet-free connection between geographically distant communities.

## Deployment

Designed for deployment as a Docker web service on Render.

Required Buzz credentials:
- `BUZZ_RELAY_URL`
- `BUZZ_PRIVATE_KEY`
- `BUZZ_AUTH_TAG`

Configuration includes:
- `LANGLAYER_URL`
- `GEOHASH`
- `LANGUAGES`
- `INTERVAL`

Credentials must be stored securely as environment variables and must never be committed to the repository.
