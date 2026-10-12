# deploy/metering-proxy/ — billing-sensitive changes

Read [`../../AGENTS.md`](../../AGENTS.md) first. This directory is the metering
proxy; its usage records are ingested by `backend/app/domain/usage`.

`CHEESE_TOKEN_CAP` defaults to `0`, which means the rolling cap is off. While it
is off, preserve normal-path behaviour byte for byte: put new logic behind the
cap or in an error/exception branch, and add a regression assertion that
preserves the off state. Changing the default or introducing new behaviour
while the cap is off requires a human decision first; a passing regression
test does not grant that approval. The cap counts completed usage plus an
in-flight estimate; do not make an off-by-default safety feature alter billing
or quota when disabled.

Cache-write records whose lifetime is unknown use the cheaper five-minute rate,
not the one-hour rate. A conservative undercharge is safer than charging an
unknown lifetime as the more expensive bucket; keep this policy aligned with
the ingestion code when changing either side.
