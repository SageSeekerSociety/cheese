// Which browser on which system a sign-in came from, read off its User-Agent,
// so the device list names something a person recognises. Anything this does
// not recognise stays unnamed rather than guessed. The desktop app is the one
// client a User-Agent cannot name, so it is identified by its sign-in method.

const BROWSERS: [RegExp, string][] = [
  [/\bEdg(?:e|A|iOS)?\//, 'Edge'],
  [/\bOPR\//, 'Opera'],
  [/\b(?:Firefox|FxiOS)\//, 'Firefox'],
  [/\b(?:Chrome|CriOS)\//, 'Chrome'],
  [/\bVersion\/[\d.]+.*\bSafari\//, 'Safari'],
]

// iPhone and iPad before macOS: an iPad can say "Macintosh" too, but never
// "iPad" unless it is one.
const SYSTEMS: [RegExp, string][] = [
  [/\bWindows NT\b/, 'Windows'],
  [/\b(?:iPhone|iPad|iPod)\b/, 'iOS'],
  [/\bAndroid\b/, 'Android'],
  [/\bMac OS X\b/, 'macOS'],
  [/\bCrOS\b/, 'ChromeOS'],
  [/\bLinux\b/, 'Linux'],
]

function first(table: [RegExp, string][], userAgent: string): string | null {
  return table.find(([pattern]) => pattern.test(userAgent))?.[1] ?? null
}

/** The system alone, for a sign-in whose User-Agent does not name a browser. */
export function osOf(userAgent: string): string | null {
  return first(SYSTEMS, userAgent)
}

export function deviceOf(userAgent: string): { browser: string; os: string } | null {
  const browser = first(BROWSERS, userAgent)
  const os = osOf(userAgent)
  return browser && os ? { browser, os } : null
}

/**
 * The sign-in the desktop app makes for itself (backend/app/api/routes/app_sign_in.py).
 *
 * Its window is the system's own webview, which reports the browser it is built
 * on — Edge on Windows, WebKit on macOS — so the User-Agent names a browser the
 * person never opened. The method it signs in with is what tells the app apart
 * from that browser.
 */
export const DESKTOP_APP_SIGN_IN = 'app_sign_in'
