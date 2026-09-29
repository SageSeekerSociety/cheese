// What changed in the desktop app, as written in desktop/CHANGELOG.md and
// published beside the installers (.github/workflows/desktop.yml). The file is
// a heading per day and a line per change:
//
//   ## 2026-09-29
//   - 关闭窗口后在后台运行
//
// The download page shows it; anything else in the file is left out.

export interface ChangelogDay {
  date: string
  changes: string[]
}

export function parseChangelog(text: string): ChangelogDay[] {
  const days: ChangelogDay[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    const heading = /^##\s+(\d{4}-\d{2}-\d{2})\s*$/.exec(line)
    if (heading) {
      days.push({ date: heading[1], changes: [] })
      continue
    }
    const change = /^[-*]\s+(.+)$/.exec(line)
    if (change && days.length) days[days.length - 1].changes.push(change[1])
  }
  return days.filter((d) => d.changes.length)
}

const RELEASE = '/downloads/desktop'

/** The published version and changelog, or nothing where a file is not there (a local build). */
export async function fetchDesktopRelease(): Promise<{ version: string | null; days: ChangelogDay[] }> {
  const [latest, changelog] = await Promise.all([
    fetch(`${RELEASE}/latest.json`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null),
    fetch(`${RELEASE}/CHANGELOG.md`)
      .then((r) => (r.ok && !r.headers.get('content-type')?.includes('text/html') ? r.text() : ''))
      .catch(() => ''),
  ])
  const version = typeof latest?.version === 'string' ? latest.version : null
  return { version, days: parseChangelog(changelog) }
}
