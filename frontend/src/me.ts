// 当前用户 (Phase 0 极简登录, spec §7.2): handle 即身份，无密码。The signed-in
// identity lives in localStorage so a reload keeps you in; App.vue gates the
// main content on `me`, so components can read myHandle() once at setup.
import { ref } from 'vue'
import { login as apiLogin } from './api'
import type { Me } from './types'

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

export async function signIn(handle: string, name = ''): Promise<Me> {
  const u = await apiLogin(handle.trim(), name.trim())
  me.value = u
  localStorage.setItem(KEY, JSON.stringify(u))
  return u
}

export function signOut(): void {
  me.value = null
  localStorage.removeItem(KEY)
}

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
