import { onBeforeUnmount, ref } from 'vue'

import { t } from '@/i18n'

// A refused or wrong credential comes back with a machine-readable reason and,
// when the next attempt has to wait, how long. The server's own message is
// English, so these screens say it themselves from those two fields.
interface Refusal {
  reason?: unknown
  retryAfterSeconds?: unknown
}

function refusalOf(error: unknown): Refusal {
  const data = (error as { error?: { data?: unknown } } | null)?.error?.data
  return data && typeof data === 'object' ? (data as Refusal) : {}
}

/** How long the server asked to wait before the next attempt, in seconds; 0 for no wait. */
export function attemptWaitSeconds(error: unknown): number {
  const { retryAfterSeconds } = refusalOf(error)
  return typeof retryAfterSeconds === 'number' && retryAfterSeconds > 0 ? retryAfterSeconds : 0
}

/** Seconds under a minute, whole minutes above; rounded up, so the wait is never understated. */
export function waitPhrase(seconds: number): string {
  const whole = Math.max(1, Math.ceil(seconds))
  return whole < 60 ? t('account.attempts.seconds', whole) : t('account.attempts.minutes', Math.ceil(whole / 60))
}

/** The sentence for a wrong credential or a refused attempt, or null for any other error. */
export function attemptMessage(error: unknown): string | null {
  const { reason } = refusalOf(error)
  const wait = attemptWaitSeconds(error)
  if (reason === 'invalid_email_code') return t('account.attempts.wrongEmailCode')
  if (reason === 'mail_limit_reached') return t('account.attempts.mailLimit')
  if (reason === 'email_code_too_soon') {
    return t('account.attempts.codeTooSoon', { wait: waitPhrase(wait) })
  }
  if (reason === 'invalid_credentials') {
    return wait
      ? t('account.attempts.wrongPasswordWait', { wait: waitPhrase(wait) })
      : t('account.attempts.wrongPassword')
  }
  if (reason === 'too_many_attempts' && wait) {
    return t('account.attempts.tooManyWait', { wait: waitPhrase(wait) })
  }
  return null
}

/** Holds a form's submit back until the wait the server asked for has passed. */
export function useAttemptWait() {
  const waiting = ref(false)
  let timer: ReturnType<typeof setTimeout> | undefined

  function waitFor(error: unknown) {
    const seconds = attemptWaitSeconds(error)
    if (!seconds) return
    clearTimeout(timer)
    waiting.value = true
    timer = setTimeout(() => (waiting.value = false), seconds * 1000)
  }

  onBeforeUnmount(() => clearTimeout(timer))
  return { waiting, waitFor }
}
