// The demo's own records turned into the shapes the product's components take:
// a chat line becomes a Block, a 一件事 becomes a TaskLine with a TaskState.
import type { Block } from '@/cx_types'
import type { TaskLine } from '@/lib/channelTasks'
import type { RefNames } from '@/lib/refChip'
import type { TaskState } from '@/lib/taskState'
import type { ChatLine, Status, Thing } from './state'

import { ME, NAMES } from './data'

import { clockTime } from '@/lib/dateText'

export const REFS: RefNames = { mentionNames: NAMES, topicTitles: {}, notified: true }

export function block(line: ChatLine, conversation = 'demo'): Block {
  return {
    id: line.id,
    conversation_id: conversation,
    kind: 'message',
    author_type: 'participant',
    author: line.who,
    content: line.text,
    created_at: line.at,
    reactions: line.reactions,
  }
}

/** The clock on a message row: 09:05, in the viewer's zone. */
export const hhmm = (iso: string) => clockTime(Date.parse(iso))

export const STATUS_LABEL: Record<Status, string> = {
  talk: '讨论中',
  run: '在做',
  wait: '等你采纳',
  done: '完成',
}

export function stateOf(thing: Thing): TaskState {
  const base = { live: false, attention: null, line: '' }
  switch (thing.status) {
    case 'talk':
      return { ...base, kind: 'open', icon: 'mdi-forum-outline', tone: 'neutral', label: STATUS_LABEL.talk }
    case 'run':
      return { ...base, kind: 'running', icon: 'mdi-timelapse', tone: 'ok', label: STATUS_LABEL.run, live: true }
    case 'wait':
      return { ...base, kind: 'review', icon: 'mdi-file-eye-outline', tone: 'ink', label: STATUS_LABEL.wait }
    default:
      return { ...base, kind: 'closed', icon: 'mdi-check-circle-outline', tone: 'okMark', label: STATUS_LABEL.done }
  }
}

export function taskLine(thing: Thing): TaskLine {
  return {
    id: thing.id,
    title: thing.title,
    owner: thing.status === 'talk' ? null : thing.owner,
    creator: thing.owner,
    state: stateOf(thing),
    mine: thing.status === 'wait' && thing.owner === ME,
    accepted: null,
    at: thing.at,
    latest: thing.last,
  }
}

export function ownerName(thing: Thing): string | null {
  return thing.status === 'talk' ? null : NAMES[thing.owner] ?? thing.owner
}
