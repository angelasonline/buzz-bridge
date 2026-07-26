# buzz-bridge

Deployment for the Buzz to Bitchat announcement bridge.

Runs the forwarder unattended and serves a status page. The bridge itself lives in
[langlayer/buzz](https://github.com/angelasonline/langlayer/tree/main/buzz).

## What it does

An announcement posted in a Buzz channel is translated by Language Layer and
published to a Bitchat geohash channel, where it travels phone to phone over
Bluetooth mesh without internet.

## Deploy

Render Web Service, Docker. Requires `BUZZ_RELAY_URL`, `BUZZ_PRIVATE_KEY`,
`BUZZ_AUTH_TAG` as secrets, and `LANGLAYER_URL`, `GEOHASH`, `LANGUAGES`,
`INTERVAL` as configuration.
