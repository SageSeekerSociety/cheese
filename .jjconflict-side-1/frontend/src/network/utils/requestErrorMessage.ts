import { BusinessError } from '@/network/types/error'

/** Show API-authored business feedback without exposing transport internals. */
export function requestErrorMessage(error: unknown, fallback: string): string {
  return error instanceof BusinessError ? error.message : fallback
}
