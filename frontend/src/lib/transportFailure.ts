// What the app did not answer.
//
// Every answer of ours is a JSON envelope. Anything else in its place — nginx's
// 502 page, Cloudflare's 530 (error 1033: a tunnel that flapped), a captive
// portal's 200, the SPA's own fallback — is the edge speaking for an app that
// did not, and is neither a business error nor a success. Both API layers
// (`api.ts` on fetch, `network/` on axios) come here, so the user reads one
// sentence whichever layer the call went through.

// A server-side status, or a body that is not the envelope. Both are checked
// because the page comes with either: Cloudflare's 1033 says 530, a captive
// portal says 200.
export function isTransportFailure(status: number, body: unknown): boolean {
  return status >= 500 || typeof body !== 'object' || body === null
}

// The status stays on the error for whoever is debugging; the sentence is for
// the user, so it says what they can do and not what failed — no 网关, no 隧道
// (design-system §8.2). A write gets the longer one: whether it landed is the
// one thing they cannot know.
export function transportFailureMessage(method: string, status: number): string {
  const sentence =
    method.toUpperCase() === 'GET' ? '服务暂时不可达，请稍后重试' : '服务暂时不可达，刚才的操作没有送达，请稍后重试'
  return `${sentence}（HTTP ${status}）`
}
