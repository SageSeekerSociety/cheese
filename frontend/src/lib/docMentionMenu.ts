// 文档里的 @ 和 #：打 @ 挑一个人，打 # 挑一个话题。写进正文的是引用本身
// （`<@账号>`、`<#话题编号>`，lib/refChip.ts），画出来是带名字的标签
// （lib/docDecorations.ts 的 createTokenChips）。
//
// 对话输入框写进去的是「@名字」，发送时才换成引用；文档没有发送这一步，正文直接
// 协同同步出去，所以挑中的那一刻就写引用。写成名字的话，它永远只是一串字。
//
// 和 slash 菜单一样建在 @tiptap/suggestion 上；菜单画在哪、停在第几行归宿主
// （composables/useDocRefMenu.ts）。
import type { SuggestionProps } from '@tiptap/suggestion'
import type { MentionItem, MentionPoolEntry } from '../composables/useRoomMentionPicker'
import type { Topic } from '../cx_types'

import { Extension } from '@tiptap/core'
import { PluginKey } from '@tiptap/pm/state'
import { Suggestion } from '@tiptap/suggestion'

import { t } from '@/i18n'

export type RefTrigger = '@' | '#'

/** 一项候选，连同挑中之后写进正文的引用。 */
export interface RefItem extends MentionItem {
  token: string
}

const LIMIT = 7

/** 打 @ 时的候选，按名字或账号搜。人在前、AI 队友在后：对话里 @ 芝士是叫它干活，
 *  所以它排第一；文档里的 @ 只是写下这是谁（负责人、找谁确认），写的多半是人。 */
export function peopleItems(pool: MentionPoolEntry[], query: string): RefItem[] {
  const q = query.toLowerCase()
  const found = pool.filter((p) => p.label.toLowerCase().includes(q) || p.handle.toLowerCase().includes(q))
  return [...found.filter((p) => !p.agent), ...found.filter((p) => p.agent)].slice(0, LIMIT).map((p) => ({
    label: p.label,
    kind: 'member',
    insert: p.handle,
    sub: p.handle,
    agent: p.agent,
    external: !!p.external,
    handle: p.handle,
    token: `<@${p.handle}>`,
  }))
}

/** 打 # 时的候选：项目里的话题（项目本身那间不算），按标题搜。 */
export function topicItems(topics: Topic[], query: string): RefItem[] {
  const q = query.toLowerCase()
  return topics
    .filter((tp) => tp.kind !== 'root' && tp.title.toLowerCase().includes(q))
    .slice(0, LIMIT)
    .map((tp) => ({
      label: tp.title,
      kind: 'topic',
      insert: tp.id,
      sub: tp.status === 'archived' ? t('work.room.mention.archived') : t('work.room.mention.inProgress'),
      agent: false,
      token: `<#${tp.id}>`,
    }))
}

export interface RefMenuHandlers {
  items: (trigger: RefTrigger, query: string) => RefItem[]
  onStart: (props: SuggestionProps<RefItem, RefItem>, trigger: RefTrigger) => void
  onUpdate: (props: SuggestionProps<RefItem, RefItem>, trigger: RefTrigger) => void
  onExit: () => void
  onKeyDown: (props: { event: KeyboardEvent }) => boolean
}

// 紧跟在字母、数字后面的 @ 是邮箱地址（a@b.com）、# 是编号的一部分（C#），不开菜单。
// 中文后面要开：「请@李雪看看」里没有空格。
const WORD_BEFORE = /[A-Za-z0-9_.+-]$/

export function createRefMenu(handlers: RefMenuHandlers) {
  return Extension.create({
    name: 'cheeseRefMenu',
    addProseMirrorPlugins() {
      return (['@', '#'] as const).map((trigger) =>
        Suggestion<RefItem, RefItem>({
          editor: this.editor,
          pluginKey: new PluginKey(`cheeseRefMenu${trigger === '@' ? 'At' : 'Hash'}`),
          char: trigger,
          allowedPrefixes: null,
          items: ({ query }) => handlers.items(trigger, query),
          allow: ({ state, range }) => {
            const $from = state.doc.resolve(range.from)
            if ($from.parent.type.spec.code) return false
            if ($from.marks().some((mark) => mark.type.spec.code)) return false
            const before = $from.parent.textBetween(0, $from.parentOffset, undefined, '￼')
            return !WORD_BEFORE.test(before)
          },
          // 写进去的是引用加一个空格，光标停在空格后面，接着往下打字。
          command: ({ editor, range, props: item }) => {
            editor
              .chain()
              .focus()
              .insertContentAt(range, [{ type: 'text', text: `${item.token} ` }])
              .run()
          },
          render: () => ({
            onStart: (p) => handlers.onStart(p, trigger),
            onUpdate: (p) => handlers.onUpdate(p, trigger),
            onExit: () => handlers.onExit(),
            onKeyDown: (p) => handlers.onKeyDown(p),
          }),
        })
      )
    },
  })
}
