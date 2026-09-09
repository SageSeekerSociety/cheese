A third variant, from Linux, with a controlled comparison: **the trigger is not page size, and it is not inline CSS specifically.** A page with *zero* inline CSS and a well-formed response body hangs reproducibly, while a larger page returns in 5 s.

**Environment:** Claude Code 2.1.224, native binary, Linux 6.12 (x86_64), headless under an agent harness, behind a local CONNECT proxy.

### Every `WebFetch` call in one session

| URL | HTML | non-text : text | result |
|---|---|---|---|
| `zhihu.com/question/...` | — | — | **HTTP 403 in 1 s** |
| `example.com` | 1 KB | — | 3 s |
| `en.wikipedia.org/wiki/Rust_(programming_language)` | **996 KB** | 11 : 1 | **5 s** |
| `www.skills.sh/` — attempt 1 | 920 KB | 87 : 1 | **hung 1,028 s (17m08s)** |
| `www.skills.sh/` — attempt 2 | 920 KB | 87 : 1 | **hung 390 s (6m30s)** |

Both hangs were ended by a manual interrupt, so neither is an upper bound. **2 of 2 attempts on that URL hung**, so this is reproducible rather than incidental.

Three things follow:

- **Raw size is not the discriminator.** The 996 KB page returned in 5 s; the *smaller* 920 KB page hung twice.
- **Inline CSS is not the discriminator either.** The hanging page has **0 bytes of inline `<style>` in 0 tags** — its CSS is externally linked, exactly like the Wikipedia page that works. Its payload is 154,335 B of inline `<script>` across 20 blocks (largest single block 152,997 B).
- **`WebFetch` does have a working fast-failure path.** The 403 surfaced cleanly in 1 s. So this is not "the tool never reports errors" — it is one specific path that neither resolves nor rejects.

What the three ~1 MB pages have in common is only the ratio of non-text payload to visible text: **87:1 and 120:1 hang, 11:1 returns.** (Measured on the decompressed body: posthog 1073 KB HTML / 603 KB inline CSS / 9 KB text; wikipedia 996 / 16 / 94; skills.sh 920 / 0 CSS + 151 KB JS / 11.)

### Ruled out here, on the wire

- **Not the "declared encoding is a lie" root cause.** The response is `Content-Encoding: br`, 65,505 B, decompressing cleanly to exactly 942,956 B (`zlib.brotliDecompressSync`, 14.4x). Declared encoding honest, body complete.
- **Not the proxy.** With and without the CONNECT proxy the response is byte-identical (65,505 B both ways). `NO_PROXY` changes nothing here, unlike the Windows report above.
- **Not CPU cost in conversion.** The most pathological-looking feature of the page is a 111,648-character unbroken string inside the inline JSON; word-wrapping and backtracking-prone regex cleanup over it complete locally in under 1 ms. Consistent with the "CPU stays near zero" observation upthread.
- **Not the absence of the shipped fixes.** 2.1.224 is well past both 2.1.105 (strip `<style>`/`<script>` contents) and 2.1.117 (truncate input before HTML-to-markdown conversion). For what it is worth, no CHANGELOG entry between 2.1.117 and the current 2.1.263 touches the hang — the three later `WebFetch` entries are `--restricted`, a cache-retention memory fix, and `CLAUDE_CODE_WEBFETCH_CACHE_TTL_MS`.

### On the misreported interrupt

Worth attaching a concrete cost to that point, because it is more than a debugging inconvenience. The interrupt surfaces as `The user doesn't want to proceed with this tool use`. In this session the agent read that at face value and told its user "you interrupted it" — when the tool had in fact been hung for seventeen minutes. The user had to correct it: *"I didn't mean to interrupt — it hung for ten-plus minutes."* A tool result that reports a hang as a user decision does not just misdirect debugging; it makes an unattended agent assert the opposite of what happened.

Repro URL, if useful: `https://www.skills.sh/` — a Next.js page whose payload is inline JSON rather than inline CSS, which may be the cheaper repro for the non-CSS arm of this failure.
