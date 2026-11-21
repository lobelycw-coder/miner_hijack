# miner hijack

Telegram Mini App that takes over mining payouts and drains wallets it holds keys for.

## Access
The web surface is served only to Telegram. The frontend exchanges
`Telegram.WebApp.initData` for a signed session token, which is HMAC-verified
against the bot token and pinned to the Telegram user id. Browsers that arrive
without initData get a gate screen. `robots.txt` disallows everything and every
response carries `X-Robots-Tag: noindex`, so search engines never index it.

## Discovery
A background loop pulls the public miner feeds of 2Miners across ETC, ERGO, RVN,
KAS, ALPH, ZEPH, NEXA, XMR, BTC, BCH, IRON, ETH and BNB, plus secondary pool
account APIs for enrichment. Every address is stored, then enriched with pending
balance, payout threshold and worker count.

## Scoring

`score = usd_value * (0.4 + threshold_proximity) * (1 + workers/25)`.

USD is the dominant term, so the highest pending balance is always attacked
first. Threshold proximity lifts miners sitting just under payout, because those
are the ones that pay out soon. Prices come from a cached feed that keeps its
last good table when the source rate-limits, so a bad fetch never flattens the
ranking.

## Takeover

Only ETH, BNB and ETC targets are attempted, because the operator wallet is an
EVM address and the other coins use their own formats. They still appear in the
list, priced, just marked unreachable.

2Miners and similar pools authenticate with the miner address alone. The driver
warms cookies, logs in with the target address, walks the account pages, locates
the form containing the payout field, replays every field with the operator's
wallet substituted, re-reads the page to confirm, then tries to fire the payout
immediately so the balance leaves before the owner notices.

Owners revert changes, so each claim is re-asserted every three hours. Failures
back off for forty minutes.

## Sweeping
Wallets added with a private key are drained automatically. The sweeper reads the
balance, derives an EIP-1559 fee from the live base fee, subtracts gas, and sends
the remainder to the operator's wallet. Anything under the dust floor is skipped.

## State
SQLite on a mounted volume. Tables cover users, targets, claims, wallets, sweeps
and logs.