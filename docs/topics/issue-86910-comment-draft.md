A third variant, from Linux: **a page with zero inline CSS and a well-formed response body still hangs forever.** Both of the mechanisms characterised above are ruled out on the wire here, which suggests "CSS-heavy" is narrower than the actual trigger.

**Environment:** Claude Code 2.1.224, native binary, Linux 6.12 (x86_64), running headless under an agent harness behind a local CONNECT proxy (`HTTPS_PROXY=http://127.0.0.1:<port>`).

**Timings, from the session transcript:**

| call | start | end | duration |
|---|---|---|---|
| `WebFetch https://www.skills.sh/` | 03:59:41.308Z | 04:16:49.229Z | **17m08s, never returned** — ended by a manual interrupt |
| `WebFetch https://example.com` (control, same session) | 04:18:07.010Z | 04:18:10.181Z | 3s |
| `curl` of the same URL, same host, same proxy | — | — | **2.0–2.3s**, HTTP 200 |

**Page composition**, measured by decompressing the exact response body, alongside the two pages from the original report:

| | www.skills.sh | posthog.com/docs/data/events | wikipedia Rust |
| --- | --- | --- | --- |
| HTML (decompressed) | 942,956 B | 1,061 KB | 983 KB |
| inline `<style>` | **0 B (0 tags)** | 598 KB | 20 KB |
| external stylesheets | 2 | — | — |
| inline `<script>` | 154,335 B in 20 blocks (largest 152,997 B) | — | — |
| visible text | 11,632 B | 9 KB | 92 KB |
| WebFetch | hangs | hangs | returns |

So the page carries no inline stylesheet at all — its CSS is externally linked, like the Wikipedia page that works. What it does have is the same extreme ratio of non-text payload to text (~80:1), reached through inline JS rather than inline CSS.

**Ruled out here, on the wire:**

- **Not the "declared encoding is a lie" root cause.** The response is `Content-Encoding: br`, 65,505 bytes, and decompresses cleanly to exactly 942,956 bytes (`zlib.brotliDecompressSync`, 14.4x). The declared encoding is honest and the body is complete.
- **Not the proxy.** Fetched with and without the CONNECT proxy, the response is byte-identical (65,505 B both ways, same leading bytes). `NO_PROXY` makes no difference here, unlike the Windows report above.
- **Not CPU cost in the conversion.** Consistent with the "CPU stays near zero" observation above: the most pathological-looking feature of this page is a 111,648-character unbroken string inside the inline JSON, and running word-wrapping and backtracking-prone regex cleanup over it locally completes in under 1 ms.
- **Not the two shipped fixes being absent.** 2.1.224 is well past both 2.1.105 (strip `<style>`/`<script>` contents) and 2.1.117 (truncate input before HTML-to-markdown conversion).

**On the misreported interrupt** — worth adding a concrete cost to that point, because it is not only a debugging inconvenience. The interrupt surfaced as `The user doesn't want to proceed with this tool use`, and the agent in this session read that at face value and told its user "you interrupted it", when in fact the tool had been hung for seventeen minutes. The user had to correct it ("I didn't mean to interrupt — it hung for ten-plus minutes"). A tool result that reports a hang as a user decision does not just misdirect debugging; it makes an unattended agent state the opposite of what happened.

**Limitation, stated plainly:** this is a single observation of this URL, not a repeated repro. I have not re-run it, precisely because there is no bounded way to — the call cannot be made to end on its own, which is the property this issue is about. Happy to run a controlled series if that would be useful.
