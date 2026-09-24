// Which browser on which system a sign-in came from, read off its User-Agent,
// so the device list names something a person recognises. Anything this does
// not recognise stays unnamed rather than guessed.

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

export function deviceOf(userAgent: string): { browser: string; os: string } | null {
  const browser = first(BROWSERS, userAgent)
  const os = first(SYSTEMS, userAgent)
  return browser && os ? { browser, os } : null
}
