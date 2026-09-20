import type { User } from '@/types/users'

function currentAccount(): User | null {
  try {
    return JSON.parse(localStorage.getItem('user') || 'null') as User | null
  } catch {
    return null
  }
}

// Sign-in can replace the account without reloading the SPA.
export function myHandle(): string {
  return currentAccount()?.username ?? ''
}

export function myId(): string {
  const id = currentAccount()?.id
  return id == null ? '' : String(id)
}
