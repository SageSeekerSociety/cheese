import i18n from '@/i18n'

/** Local HH:mm next to the name on the first of a run (not raw UTC). In the
 *  app's language, not the browser's: a Chinese screen on an en-US system
 *  otherwise reads 「07:20 PM」, which also no longer fits the gutter that a
 *  continued message shows its time in. */
export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(i18n.global.locale.value, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
