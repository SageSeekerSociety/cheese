// API 前缀 —— 网关的挂载点。构建时由 `.env` 的 `VITE_API_BASE_URL` 注入，本项目的
// 值就是 `/api`（docker-compose 的默认、scripts/dev/up.sh、e2e 都是它；旧的 api.ts
// 干脆写死成 `BASE = '/api'`）。
//
// 这里必须兜底：漏配时 `import.meta.env.VITE_API_BASE_URL` 是 JS 的 undefined，直接
// 拼进模板串会串出 `undefined/avatars/12` —— 一个**相对**地址。浏览器会拿它去拼当前
// 页面地址，在设置页就成了 `/users/settings/undefined/avatars/12`（dev 下这条还会被
// vite 的 /users 代理转给后端，于是回 404）。兜底成 `/api` 后，返回的永远是从根开始
// 的绝对地址，而且正好是配好时该有的那一个，不会随页面漂移。
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

export const getAvatarUrl = (avatar?: string | number) => {
  return avatar ? `${API_BASE}/avatars/${avatar}` : `${API_BASE}/avatars/default`
}

export const getFullAttachmentUrl = (attachmentUrl: string) => {
  if (attachmentUrl.startsWith('http')) {
    return attachmentUrl
  }
  return `${API_BASE}${attachmentUrl}`
}

export const formatFileSize = (byte: number) => {
  if (byte < 1024) {
    return `${byte} B`
  }
  if (byte < 1024 * 1024) {
    return `${(byte / 1024).toFixed(2)} KB`
  }
  if (byte < 1024 * 1024 * 1024) {
    return `${(byte / 1024 / 1024).toFixed(2)} MB`
  }
  return `${(byte / 1024 / 1024 / 1024).toFixed(2)} GB`
}
