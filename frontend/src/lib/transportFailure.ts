// What the app did not answer.
//
// Every answer of ours is a JSON envelope, and so is every error of ours: a
// 500 from the backend still arrives as `{code, message, data}` with a
// sentence it wrote for the user (「暂时无法发起 GitHub 账号连接，请稍后重试」
// is one). Anything else in the envelope's place — nginx's 502 page,
// Cloudflare's 530 (error 1033: a tunnel that flapped), a captive portal's 200,
// the SPA's own fallback — is the edge speaking for an app that did not, and
// is neither a business error nor a success. Both API layers (`api.ts` on
// fetch, `network/` on axios) come here, so the user reads one sentence
// whichever layer the call went through.

// A page instead of an envelope: the body is not a JSON object. The status
// line does not decide this — the page comes with any of them (Cloudflare's
// 1033 says 530, a captive portal says 200) and so does the envelope (the
// backend's own 5xx carries one, and its sentence must reach the user).
export function isTransportFailure(body: unknown): boolean {
  return typeof body !== 'object' || body === null
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
