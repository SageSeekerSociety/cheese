// What the topic page remembers across topic switches.
//
// The topic page is rebuilt for every topic (ProjectShell keys it on the topic id),
// so nothing one topic loaded can show up in the next. A few things are meant to
// outlive that: the files the person left open in a room, and the file last open
// in each source of a task's 改动. Those live here, provided by the project frame, which stays mounted
// while topics come and go.
//
// Where nothing provides it (a component mounted on its own), each caller gets a
// fresh memory — the component then behaves as if it had just been opened.
import type { InjectionKey } from 'vue'

import { inject, provide } from 'vue'

export interface OpenFileTab {
  path: string
  pinned: boolean
  /** 项目资料库里的一份文档（`path` 是 `doc:<编号>`），不是一份文件。 */
  document?: { id: string; title: string }
  /** AI 队友提议的一项技能（`path` 是 `skill:<编号>`）：在这里读完再决定存不存。 */
  skill?: { id: string; title: string }
  /** 频道里点开的项目文件（项目当前版本、只读）；带行号时是 chip 指着的那几行。 */
  project?: { lines: { start: number; end: number } | null }
}

export interface TopicMemory {
  /** 自由区 per room: the files left open there, restored on coming back. */
  filesByTopic: Map<string, OpenFileTab[]>
  /** The file last open in each source. */
  lastFiles: Map<string, string>
}

const MEMORY: InjectionKey<TopicMemory> = Symbol('topicMemory')

function createTopicMemory(): TopicMemory {
  return {
    filesByTopic: new Map(),
    lastFiles: new Map(),
  }
}

/** Called by the frame that outlives topic switches. */
export function provideTopicMemory(): void {
  provide(MEMORY, createTopicMemory())
}

export function useTopicMemory(): TopicMemory {
  return inject(MEMORY, createTopicMemory, true)
}
