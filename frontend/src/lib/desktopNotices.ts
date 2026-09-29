// The desktop app keeps running with its window closed, and this page with it.
// The page is what is signed in, so it is the one that asks the server what a
// browser push would have said and has the app show that as a system
// notification, and that reports how many things wait, for the Dock or taskbar.
// Browser push itself never reaches the app's web view.

import { desktopBadge, desktopCan, desktopNotify } from './desktopApp'

import { listAwaitingMe } from '@/api'
import { NotificationsApi } from '@/network/api/notifications'

/** How often to ask. A push arrives within seconds; this is the app's version of that. */
export const DESKTOP_NOTICE_EVERY_MS = 30_000

// Where the last look stopped, per account, so a restart neither repeats what
// was shown nor starts over from everything.
const cursorKey = (userId: number) => `cheese.desktop.pushFeed.${userId}`

function readCursor(userId: number): number | null {
  try {
    const raw = localStorage.getItem(cursorKey(userId))
    return raw === null ? null : Number(raw)
  } catch {
    return null
  }
}

function writeCursor(userId: number, cursor: number): void {
  try {
    localStorage.setItem(cursorKey(userId), String(cursor))
  } catch {
    // Without storage the next launch starts from "now" again, which only skips.
  }
}

async function showNew(userId: number): Promise<void> {
  const { data } = await NotificationsApi.pushFeed(readCursor(userId))
  for (const item of data.items) desktopNotify(item)
  // Nothing has ever been pushed to this account: anything from now on is new.
  writeCursor(userId, data.latest ?? 0)
}

async function showWaiting(): Promise<void> {
  desktopBadge((await listAwaitingMe()).data.length)
}

/** Starts watching for the signed-in account; the returned function stops. */
export function watchForDesktopNotices(userId: number): () => void {
  const notices = desktopCan('notify')
  const waiting = desktopCan('badge')
  if (!notices && !waiting) return () => {}
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const look = async () => {
    await Promise.allSettled([notices ? showNew(userId) : null, waiting ? showWaiting() : null])
    if (!stopped) timer = setTimeout(look, DESKTOP_NOTICE_EVERY_MS)
  }
  void look()
  return () => {
    stopped = true
    clearTimeout(timer)
    // Signed out: nothing waits on anyone here any more.
    desktopBadge(0)
  }
}
