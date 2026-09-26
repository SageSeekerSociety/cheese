// What the topic page remembers across topic switches.
//
// The topic page is rebuilt for every topic (ProjectShell keys it on the topic id),
// so nothing one topic loaded can show up in the next. A few things are meant to
// outlive that: the reading mode the person chose, the files they left open in a
// room, and edits they have not saved yet. Those live here, provided by the project
// frame, which stays mounted while topics come and go.
//
// Where nothing provides it (a component mounted on its own), each caller gets a
// fresh memory — the component then behaves as if it had just been opened.
import type { InjectionKey, Ref } from 'vue'

import { inject, provide, reactive, ref } from 'vue'

export interface OpenFileTab {
  path: string
  pinned: boolean
}

export interface FileDraft {
  content: string
  saved: string
  version: string | null
}

export interface TopicMemory {
  /** 专注模式: session-only, and not something a topic switch should undo. */
  focusMode: Ref<boolean>
  /** 自由区 per room: the files left open there, restored on coming back. */
  filesByTopic: Map<string, OpenFileTab[]>
  /** Unsaved edits, keyed by source and path (see PanelChanges). */
  drafts: Map<string, FileDraft>
  /** The file last open in each source. */
  lastFiles: Map<string, string>
}

const MEMORY: InjectionKey<TopicMemory> = Symbol('topicMemory')

function createTopicMemory(): TopicMemory {
  return {
    focusMode: ref(false),
    filesByTopic: new Map(),
    drafts: reactive(new Map()),
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
