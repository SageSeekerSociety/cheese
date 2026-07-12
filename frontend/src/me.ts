// 当前用户身份。The 知是 login is the ONE sign-in; main.ts mirrors it into the
// `cheesex.me` localStorage entry this module reads, so components can read
// myHandle() once at setup.
import { ref } from 'vue'
import type { Me } from './cx_types'

const KEY = 'cheesex.me'

function load(): Me | null {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as Me) : null
  } catch {
    return null
  }
}

export const me = ref<Me | null>(load())

// The author handle components send with their actions. App.vue only mounts the
// signed-in UI, so this is always a real handle there.
export function myHandle(): string {
  return me.value?.handle ?? ''
}

// The signed session token (P1 真鉴权). api.ts attaches it as a Bearer header;
// empty when signed out or when an older (pre-token) identity is cached.
export function authToken(): string {
  return me.value?.token ?? ''
}
