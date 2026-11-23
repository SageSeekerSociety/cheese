// 频道页、任务页和私聊页在桌面上怎么摆：对话是一块，工作面板的每一格各是一块，都交给外壳的
// 布局区（layouts/pagePanels.ts）。摆法按页面的种类（频道 / 任务 / 私聊）记在这台设备上。
//
// 频道页：和任务页「对话」那一种摆法同一个规矩，一样东西一块，各带自己的标题栏，几块能同时开着。
// 进频道时旁边是概览：一栏窄的目录（宽度自己记着），没有标题栏（`bare`）；打开别的哪一块，概览
// 就让开。对话右边先是「支线」那一处（支线的列表或一条支线；在频道里打开的一件任务也开在这一处，
// 换掉它），再往右是「文件」那一处（文件的列表或打开的一份文件、文档、技能；例行事务也开在这一处）。
// 一处一次显示一样：新打开的换掉这一处原来的，换下去的是任务或文件时它就关掉了。最后一块关掉时
// 对话占满。
//
// 私聊页：页签可以拖去和别的并排、拖进对话那一格，格子可以收起、放大。对话那一块和工作面板固定
// 的那几格不能关（只能挪）；对话独占一格时不画页签栏。工作面板那几格同属 `work` 这一组：新打开的
// 一格落到这一组页签最多的那一格里，✕ 也只在那一格上。「收起」（✕、页头的开关）收的是只装着这一组
// 的那几格；挪进对话那一格的不跟着收。
//
// 任务页（「对话」那一种摆法，`kind` 为 'task'）：对话旁边是一块一块的，一样东西一块，各带自己的
// 标题栏（`titled`），没有页签、不能拖，分隔线照样能拖。进任务时旁边是概览；打开别的哪一块，概览就
// 让开（不和别的并排），页头上的「概览」把它叫回来；最后一块关掉时对话占满，概览不自己回来。几块能
// 同时开着，各在默认的位置上（`CHAT_ARRANGEMENT`）：文件、预览和从对话里打开的一份产出在对话右边
// 那一栏里上下排，终端在这一栏最下面、矮一些（高度自己记着）；改动、现场在再往右窄一些的格子里（两样都开
// 着时并排各一格）——对话右边那一栏里没有别的时，它们自己占那一栏。为此改动、现场各有两处（`side:` 那一处在右边），
// 此刻摆在哪一处由这里挑，工作面板只认它们的名字（`workLayout` 的 `outlet`、`visible` 替它对上）。
// 终端是一块（`TERMINAL_BLOCK`），一次显示一个，显示哪一个由工作面板挑。
//
// 任务页还有第二种摆法「编辑器」（页头上的开关，`kind` 为 'editor'，摆法另记一份）：同一件任务，
// 文件是主角。左边一栏是任务的文件树和改过的文件（`NAVIGATOR`，宽度自己记着，和「文件」那一格里
// 的树同宽）；中间是打开的文件，概览、现场、改动、预览打开时也进中间这一格，成为能关的页签（不
// 常摆着，`opened` 记着打开了哪几样）；终端在中间下面那一格；对话在右边一栏，宽度自己记着。三块
// 各自能藏起来（页头右边那三颗，`editorParts`），记在这台设备上。两种摆法里同一个 id 的那几块是
// 同一个实例：对话、打开的文件、终端换摆法时不重挂。
//
// 放大（页签栏或标题栏上的 ⤢）是布局层的；对话因此看不见的这段时间里有了新回复，那颗还原按钮
// 上带一个点（对话这一块的信号）。挪过摆法之后，「恢复默认摆法」在页头 ⋯ 和命令面板里。
import type { ComputedRef, Ref } from 'vue'
import type { LayoutNode } from '@/components/layout-area'
import type { WorkLayout, WorkTabMeta } from '@/components/work/workLayout'
import type { PagePanel, PagePanels, PagePanelsHandle } from '@/layouts/pagePanels'

import { computed, reactive, ref, watch } from 'vue'

import { useCommands } from '@/commands'
import { TASK_FILE_TREE_WIDTH } from '@/components/panels/taskFileTreeWidth'
import { NAVIGATOR, TERMINAL_BLOCK, workPaneId } from '@/components/work/workLayout'
import { t } from '@/i18n'
import { storageGetJson, storageSetJson } from '@/lib/browserStorage'

/** 对话那一块。 */
export const CONVERSATION = 'conversation'
/** 工作面板那几格同属的一组。 */
export const WORK_GROUP = 'work'
/** 编辑器那一种摆法：中间那一格里的一组（打开的文件，和打开在中间的概览之类）。 */
export const CENTRE_GROUP = 'centre'
/** 编辑器那一种摆法：中间下面那一格里的终端。 */
export const TERMINAL_GROUP = 'terminals'

/** 编辑器那一种摆法里三块各自在不在屏幕上（页头右边那三颗）。 */
export interface EditorParts {
  left: boolean
  terminal: boolean
  conversation: boolean
}
export type EditorPart = keyof EditorParts
const PARTS_KEY = 'cheesex.editorParts'
/** 编辑器里右边那一栏对话的宽度：拖过就记住。 */
const CONVERSATION_WIDTH = { initial: 400, min: 320, max: 640, storageKey: 'cheesex.editorConversationWidth' }

/** 「对话」那一种摆法里从对话打开的一份产出（文件、文档、技能）：一次一份，开在预览下面。 */
export const OPENED_GROUP = 'opened'
/** 改动、现场在窄栏里的那一处。 */
const SIDE_PREFIX = 'side:'
/** 有两处的那几样：别的块开着时在窄栏里，没有时自己占对话右边那一栏。 */
const SIDE_KINDS = ['changes', 'site']
/**
 * 窄栏的宽度是它自己的：拖过就记住，不和对话、对话右边那一栏按比例分。按比例分的话，它藏着的时候
 * 记下的那一份宽度会算在比例里，每刷新一次对话右边那一栏就窄一点。
 */
const SIDE_WIDTH = { initial: 340, min: 300, max: 560, storageKey: 'cheesex.taskSideWidth' }
/** 终端那一块的高度：拖过就记住。 */
const TERMINAL_HEIGHT = { initial: 220, min: 120, max: 640, storageKey: 'cheesex.taskTerminalHeight' }
/** 占对话右边那一栏的几样（另一栏只放改动、现场）。 */
function mainKind(key: string): boolean {
  return key === 'code' || key === 'preview' || key === TERMINAL_BLOCK || key.startsWith('file:')
}
/** 频道页的概览：一栏窄的目录，拖过就记住。 */
const OVERVIEW_WIDTH = { initial: 316, min: 260, max: 480, storageKey: 'cheesex.channelOverviewWidth' }
/** 频道页对话右边那一处：支线的列表或一条支线，或在频道里打开的一件任务。 */
export const TALK_GROUP = 'talk'
/** 频道页再往右那一处：文件的列表或打开的一份文件，或例行事务。 */
export const DOC_GROUP = 'doc'
/** 频道页上一样东西开在哪一处；概览和不认识的是 null。 */
function placeOf(key: string): typeof TALK_GROUP | typeof DOC_GROUP | null {
  if (key === 'threads' || key.startsWith('delivery:')) return TALK_GROUP
  if (key === 'files' || key === 'routines' || key.startsWith('file:')) return DOC_GROUP
  return null
}
/** 「对话」那一种摆法（和频道页）里，对话右边那几块此刻显示哪几样。 */
export interface ChatBlocks {
  /** 概览在（这时别的都让开）。 */
  overview: boolean
  /** 读者开着的那几块，按打开的先后。 */
  open: readonly string[]
}

/** 工作面板的一格，交给布局区时还要说的几样。 */
export interface RoomWorkTab extends WorkTabMeta {
  /** 随读者打开、关掉而来去的一格（一件打开的任务、一份文件）。 */
  transient?: boolean
  /** 里面有 iframe（预览）：挪到别的格子时不能重新加载。 */
  alwaysRender?: boolean
}

export interface RoomLayoutOptions {
  /**
   * 这一页的摆法记在哪个键下（同一种页面共用）；null 是这一档不交给外壳（手机、平板）。任务页的
   * 编辑器那一种摆法是 'editor'。
   */
  kind: () => 'room' | 'task' | 'editor' | 'dm' | null
  /** 工作面板此刻有哪几格，按页签顺序。 */
  tabs: () => RoomWorkTab[]
  /** 工作面板那一侧收着（或是浮层）：只装着工作面板的格子藏起来。 */
  workHidden: () => boolean
  /** 主区太窄、工作面板是压在对话上的浮层：它自己排页签，不进布局区。 */
  floating: () => boolean
  /** 收起工作面板那一侧：页签栏上 ✕ 的说法和动作；频道页上是标题栏上的 ✕。 */
  hide: { label: () => string; run: () => void }
  /** 读者在布局区里点了工作面板的一格；关掉了能关的一格；双击留下了一格。 */
  onActivated: (key: string) => void
  onClose: (key: string) => void
  /**
   * 频道页上一处换了别的东西，换下去的那一件任务或那一份文件随之关掉（不是读者关的）。不给时
   * 同 `onClose`。
   */
  onDrop?: (key: string) => void
  onKeep: (key: string) => void
  /** 读者在「+」里选了能开好几格的那一种：页面新开一格。 */
  onSpawn: (key: string) => void
  /** 换了一件任务、回到频道：「对话」那一种摆法和频道页回到刚进来的样子（只有概览）。 */
  scope?: () => string | null | undefined
}

export function useRoomLayout(
  usePanels: (spec: () => PagePanels | null) => PagePanelsHandle,
  opts: RoomLayoutOptions
): {
  panels: PagePanelsHandle
  /** 交给工作面板的那几样；不交给外壳时是 null。 */
  workLayout: ComputedRef<WorkLayout | null>
  /** 对话那一块此刻在屏幕上（没交给外壳时总是）。 */
  conversationInSight: ComputedRef<boolean>
  /** 对话看不见的这段时间里来了新回复。 */
  noteChatNews: () => void
  /** 页头开关开合的那几块的 id：交给外壳时是工作面板在屏幕上的那几格，否则是整块面板。 */
  panelControls: ComputedRef<string>
  /** 编辑器那一种摆法里三块各自在不在屏幕上；`toggleEditorPart` 藏起或摆回一块。 */
  editorParts: Readonly<EditorParts>
  toggleEditorPart: (part: EditorPart) => void
  /** 「对话」那一种摆法和频道页上此刻显示哪几块（工作面板那一侧收着时什么都不显示）。 */
  chatBlocks: ComputedRef<ChatBlocks>
  /** 关掉一块（页头上再点一下开着的那一颗）。 */
  closeBlock: (key: string) => void
} {
  const chatNews: Ref<boolean> = ref(false)

  const savedParts = storageGetJson<Partial<EditorParts>>(PARTS_KEY)
  const editorParts = reactive<EditorParts>({
    left: savedParts?.left !== false,
    terminal: savedParts?.terminal !== false,
    conversation: savedParts?.conversation !== false,
  })
  function toggleEditorPart(part: EditorPart) {
    editorParts[part] = !editorParts[part]
    storageSetJson(PARTS_KEY, { ...editorParts })
  }
  /** 编辑器里打开在中间的那几格固定的（概览、现场、改动、预览），按打开的先后。 */
  const opened = ref<string[]>([])
  /** 固定的那几格（不是打开的文件、终端，也不是「文件」那一格）：编辑器里打开时才进中间。 */
  const onDemand = (tab: RoomWorkTab) => !tab.transient && !tab.closable && !tab.spawns && !tab.terminal

  function editorSpec(): PagePanels {
    const hidden = opts.workHidden()
    const work: PagePanel[] = opts.tabs().flatMap((tab): PagePanel[] => {
      const base: PagePanel = {
        id: tab.key,
        title: tab.title,
        icon: tab.icon,
        faint: tab.faint,
        hint: tab.hint,
        signal: tab.signal,
        temporary: tab.temporary,
        alwaysRender: tab.alwaysRender,
        hidden,
      }
      if (tab.terminal) {
        return [
          {
            ...base,
            spawns: tab.spawns,
            transient: tab.transient,
            closable: !!tab.transient,
            group: TERMINAL_GROUP,
            below: 'code',
            hidden: hidden || !editorParts.terminal,
          },
        ]
      }
      // 打开的文件那一格：只有它一格时不画页签栏，打开了别的（概览、改动……）才出现。
      if (tab.key === 'code') return [{ ...base, group: CENTRE_GROUP, stripWhenShared: true, minWidth: 360 }]
      if (onDemand(tab)) {
        return opened.value.includes(tab.key) ? [{ ...base, group: CENTRE_GROUP, transient: true, closable: true }] : []
      }
      return [{ ...base, group: CENTRE_GROUP, transient: tab.transient, beside: tab.beside, closable: true }]
    })
    return {
      key: 'editor',
      panels: [
        {
          id: NAVIGATOR,
          title: t('work.task.form.left'),
          bare: true,
          width: TASK_FILE_TREE_WIDTH,
          hidden: hidden || !editorParts.left,
        },
        ...work,
        {
          id: CONVERSATION,
          title: t('work.room.tabs.chat'),
          icon: 'mdi-message-outline',
          minWidth: CONVERSATION_WIDTH.min,
          width: CONVERSATION_WIDTH,
          stripWhenShared: true,
          // 任务还没读到时只有对话可看：这时它不藏。
          hidden: !hidden && !editorParts.conversation,
          signal: chatNews.value ? { kind: 'dot' } : undefined,
        },
      ],
      arrangement: {
        direction: 'row',
        children: [{ panels: [NAVIGATOR] }, { panels: ['code'] }, { panels: [CONVERSATION] }],
      },
      onActivated: (id) => id !== CONVERSATION && opts.onActivated(id),
      onClose: (id) => {
        opened.value = opened.value.filter((key) => key !== id)
        opts.onClose(id)
      },
      onKeep: opts.onKeep,
      onSpawn: opts.onSpawn,
    }
  }

  // ---- 任务页「对话」那一种摆法，和频道页 ----
  const chat = reactive<{ overview: boolean; open: string[] }>({ overview: true, open: [] })
  watch(
    () => opts.scope?.(),
    () => {
      chat.overview = true
      chat.open = []
    }
  )
  /** 工作面板此刻有的那几样，加上终端那一块（有终端可开时）。 */
  function present(): Set<string> {
    // 单开在旁边的工作文件和一格一格的终端只在编辑器里有。
    const keys = new Set(
      opts
        .tabs()
        .filter((tab) => !tab.beside && !tab.terminal)
        .map((tab) => tab.key)
    )
    if (opts.tabs().some((tab) => tab.terminal)) keys.add(TERMINAL_BLOCK)
    return keys
  }
  // 从对话里打开的一份产出一出现就是读者刚打开的：它那一块和它一起摆出来（布局区要给它新开一格，
  // 摆出来时就该是看得见的，不是先藏着再翻出来）。
  watch(
    () =>
      opts
        .tabs()
        .filter((tab) => tab.transient && !tab.terminal && !tab.beside)
        .map((tab) => tab.key),
    (now, before) => {
      if (opts.kind() !== 'task' && opts.kind() !== 'room') return
      for (const key of now) if (!before?.includes(key)) openBlock(key)
    },
    { flush: 'sync' }
  )
  const chatBlocks = computed<ChatBlocks>(() => {
    if (opts.workHidden()) return { overview: false, open: [] }
    const keys = present()
    return chat.overview
      ? { overview: true, open: [] }
      : { overview: false, open: chat.open.filter((k) => keys.has(k)) }
  })
  /** 对话右边那一栏里有东西：改动、现场去窄栏。 */
  const mainOpen = computed(() => chatBlocks.value.open.some(mainKind))
  /** 工作面板的一样此刻摆在布局区里的哪一块（只有「对话」那一种摆法里两者不同）。 */
  function cellOf(key: string): string {
    if (opts.kind() !== 'task') return key
    if (key.startsWith('terminal:')) return TERMINAL_BLOCK
    return SIDE_KINDS.includes(key) && mainOpen.value ? SIDE_PREFIX + key : key
  }
  /** 布局区里的一块装的是工作面板的哪一样。 */
  const contentOf = (id: string) => (id.startsWith(SIDE_PREFIX) ? id.slice(SIDE_PREFIX.length) : id)
  /**
   * 「对话」那一种摆法和频道页上显示一块：概览让开别的，别的让开概览。频道页上一处一次一样：
   * 换下去的是一件任务或一份文件时，它随之关掉。
   */
  function openBlock(key: string) {
    if (key === 'overview') chat.overview = true
    else if (opts.kind() === 'room') {
      const place = placeOf(key)
      if (!place || !present().has(key)) return
      chat.overview = false
      const before = chat.open.find((one) => placeOf(one) === place)
      if (before === key) return
      const rest = chat.open.filter((one) => one !== before)
      chat.open = place === TALK_GROUP ? [key, ...rest] : [...rest, key]
      if (before && (before.startsWith('delivery:') || before.startsWith('file:')))
        (opts.onDrop ?? opts.onClose)(before)
    } else {
      const block = key.startsWith('terminal:') ? TERMINAL_BLOCK : key
      if (block !== TERMINAL_BLOCK && !present().has(block)) return
      chat.overview = false
      if (!chat.open.includes(block)) chat.open = [...chat.open, block]
    }
  }
  /**
   * 关掉一块（标题栏上的 ✕、页头上再点一下开着的那一颗）。工作面板先听到（它此刻在看的那一样
   * 换成还开着的一块）；关的是概览时，它让开的那几块回来。一块都不剩时对话占满：工作面板那一侧
   * 收起来，下一次打开从概览开始。
   */
  function closeBlock(key: string) {
    opts.onClose(key)
    if (key === 'overview') chat.overview = false
    else chat.open = chat.open.filter((one) => one !== key)
    const keys = present()
    if (chat.overview || chat.open.some((one) => keys.has(one))) return
    chat.overview = true
    chat.open = []
    opts.hide.run()
  }

  function chatSpec(): PagePanels {
    const shown = chatBlocks.value
    const inSight = (key: string) => (shown.overview ? key === 'overview' : shown.open.includes(key))
    const block = (tab: RoomWorkTab, id: string, hidden: boolean): PagePanel => ({
      id,
      title: tab.title,
      icon: tab.icon,
      faint: tab.faint,
      hint: tab.hint,
      signal: tab.signal,
      alwaysRender: tab.alwaysRender,
      titled: true,
      minWidth: SIDE_KINDS.includes(tab.key) ? 300 : 360,
      hidden,
    })
    const work: PagePanel[] = opts.tabs().flatMap((tab): PagePanel[] => {
      // 终端是一块（下面），单开在旁边的一份工作文件只在编辑器里有。
      if (tab.terminal || tab.beside) return []
      if (tab.transient) {
        return [{ ...block(tab, tab.key, !inSight(tab.key)), transient: true, group: OPENED_GROUP, below: 'preview' }]
      }
      if (SIDE_KINDS.includes(tab.key)) {
        return [
          block(tab, tab.key, !(inSight(tab.key) && !mainOpen.value)),
          { ...block(tab, SIDE_PREFIX + tab.key, !(inSight(tab.key) && mainOpen.value)), width: SIDE_WIDTH },
        ]
      }
      return [block(tab, tab.key, !inSight(tab.key))]
    })
    const terminal: PagePanel = {
      id: TERMINAL_BLOCK,
      title: t('work.room.tabs.terminal'),
      icon: 'mdi-console',
      titled: true,
      alwaysRender: true,
      height: TERMINAL_HEIGHT,
      hidden: !inSight(TERMINAL_BLOCK),
    }
    // 没拖过的人：窄桌面对半分，宽屏（≥ 1440px）把多出来的那块给工作面板；窄栏比对话右边那一栏窄。
    const wide = window.innerWidth >= 1440
    const declared = new Set([...work.map((panel) => panel.id), TERMINAL_BLOCK])
    const column = (ids: string[]): LayoutNode[] =>
      ids.filter((id) => declared.has(id)).map((id) => ({ panels: [id], size: id === 'overview' ? 4 : 2 }))
    return {
      key: 'task',
      panels: [
        {
          id: CONVERSATION,
          title: t('work.room.tabs.chat'),
          icon: 'mdi-message-outline',
          minWidth: 340,
          stripWhenShared: true,
          signal: chatNews.value ? { kind: 'dot' } : undefined,
        },
        ...work,
        terminal,
      ],
      arrangement: {
        direction: 'row',
        children: [
          { panels: [CONVERSATION], size: wide ? 45 : 50 },
          {
            direction: 'column',
            size: wide ? 55 : 50,
            children: column(['overview', 'code', 'preview', 'changes', 'site', TERMINAL_BLOCK]),
          },
          // 改动、现场在右边各占一格、并排，不叠成一栏：新开一格时 dockview 重新摆一遍，碰上一整栏都藏着的
          // 一栏会摆乱（页面宽度对不上）。
          ...SIDE_KINDS.map((key) => ({ panels: [SIDE_PREFIX + key] })),
        ],
      },
      onActivated: () => {},
      onClose: (id) => closeBlock(contentOf(id)),
      onKeep: opts.onKeep,
      onSpawn: opts.onSpawn,
    }
  }

  /** 频道页：概览一栏窄的目录，右边两处各显示一样（见文件头）。 */
  function channelSpec(): PagePanels {
    const shown = chatBlocks.value
    const inSight = (key: string) => (shown.overview ? key === 'overview' : shown.open.includes(key))
    const work: PagePanel[] = opts.tabs().map((tab): PagePanel => {
      const base: PagePanel = {
        id: tab.key,
        title: tab.title,
        icon: tab.icon,
        faint: tab.faint,
        hint: tab.hint,
        signal: tab.signal,
        alwaysRender: tab.alwaysRender,
        hidden: !inSight(tab.key),
        // 一块来了、走了，对话和开着的那几块回到默认的份额（概览留着读者拖出来的宽度）。
        evensOut: true,
      }
      if (tab.key === 'overview') return { ...base, bare: true, width: OVERVIEW_WIDTH }
      const place = placeOf(tab.key)
      return { ...base, titled: true, minWidth: 320, transient: tab.transient, group: place ?? undefined }
    })
    // 一处开着时它和对话对半分（文件那一处窄一些），两处都开着时三份；一块来了、走了都回到这几份。
    const declared = new Set(work.map((panel) => panel.id))
    const cell = (ids: string[], size?: number): LayoutNode[] => {
      const here = ids.filter((id) => declared.has(id))
      return here.length ? [{ panels: here, ...(size ? { size } : {}) }] : []
    }
    return {
      key: 'room',
      panels: [
        {
          id: CONVERSATION,
          title: t('work.room.tabs.chat'),
          icon: 'mdi-message-outline',
          minWidth: 340,
          stripWhenShared: true,
          signal: chatNews.value ? { kind: 'dot' } : undefined,
        },
        ...work,
      ],
      arrangement: {
        direction: 'row',
        children: [
          { panels: [CONVERSATION], size: 50 },
          ...cell(['overview']),
          ...cell(['threads'], 50),
          ...cell(['files', 'routines'], 40),
        ],
      },
      onActivated: () => {},
      onClose: closeBlock,
      onKeep: opts.onKeep,
      onSpawn: opts.onSpawn,
    }
  }

  function spec(): PagePanels | null {
    const kind = opts.kind()
    if (!kind) return null
    if (kind === 'editor') return editorSpec()
    if (kind === 'task') return chatSpec()
    if (kind === 'room') return channelSpec()
    // 私聊页：页签栏上的 ✕ 收起工作面板那一侧。
    const hideAction = { icon: 'mdi-close', label: opts.hide.label(), run: opts.hide.run }
    const work: PagePanel[] = opts.tabs().map((tab) => ({
      id: tab.key,
      title: tab.title,
      icon: tab.icon,
      faint: tab.faint,
      hint: tab.hint,
      signal: tab.signal,
      temporary: tab.temporary,
      transient: tab.transient,
      beside: tab.beside,
      alwaysRender: tab.alwaysRender,
      spawns: tab.spawns,
      group: WORK_GROUP,
      // 固定的几格不能关（挪去哪都行）；能关的是打开的文件。
      closable: !!tab.transient || !!tab.closable,
      minWidth: 360,
      hidden: opts.workHidden(),
      actions: [hideAction],
    }))
    const fixed = work.filter((panel) => !panel.closable && !panel.spawns).map((panel) => panel.id)
    // 没拖过的人：窄桌面对半分，宽屏（≥ 1440px）把多出来的那块给工作面板。
    const wide = window.innerWidth >= 1440
    const arrangement: LayoutNode = {
      direction: 'row',
      children: [
        { panels: [CONVERSATION], size: wide ? 45 : 50 },
        { panels: fixed, size: wide ? 55 : 50 },
      ],
    }
    return {
      key: kind,
      panels: [
        {
          id: CONVERSATION,
          title: t('work.room.tabs.chat'),
          icon: 'mdi-message-outline',
          minWidth: 480,
          // 对话独占一格时不画页签栏，有别的页签拖进来才出现。
          stripWhenShared: true,
          signal: chatNews.value ? { kind: 'dot' } : undefined,
        },
        ...work,
      ],
      arrangement,
      onActivated: (id) => id !== CONVERSATION && opts.onActivated(id),
      onClose: opts.onClose,
      onKeep: opts.onKeep,
      onSpawn: opts.onSpawn,
      // 私聊页没有页头那一行：对话自己的头和工作面板的页签栏并排，同高。
      tabStripHeight: 'var(--app-page-header-height)',
    }
  }

  const panels = usePanels(spec)

  /**
   * 编辑器里要显示一格固定的：先把它摆进中间（打开了才有它那一块），再翻到它。「对话」那一种摆法里
   * 打开它那一块（概览让开，或别的让开概览）。
   */
  function show(key: string) {
    const kind = opts.kind()
    if (kind === 'editor' && !opened.value.includes(key)) {
      const tab = opts.tabs().find((one) => one.key === key)
      if (tab && onDemand(tab)) opened.value = [...opened.value, key]
    }
    if (kind === 'task' || kind === 'room') {
      openBlock(key)
      return void panels.show(cellOf(key))
    }
    // 终端那一块只在「对话」那一种摆法里有。
    if (key !== TERMINAL_BLOCK) panels.show(key)
  }
  const workLayout = computed<WorkLayout | null>(() => {
    if (!panels.active.value || opts.floating()) return null
    const kind = opts.kind()
    if (kind === 'task') {
      return {
        outlet: (key) => panels.outlet(cellOf(key)),
        visible: new Set([...panels.visible.value].map(contentOf)),
        show,
        blocks: true,
        head: (key) => panels.headOutlet(cellOf(key)),
      }
    }
    return {
      outlet: (key) => panels.outlet(key),
      visible: panels.visible.value,
      show,
      blocks: kind === 'room',
      head: (key) => panels.headOutlet(key),
      editor: kind === 'editor',
    }
  })
  // 收着时说的是上一次在屏幕上的那几格（再打开回到的就是它们）；从没打开过时是第一格。
  let lastInSight: string[] = []
  const panelControls = computed(() => {
    if (!workLayout.value) return 'topic-panel'
    const keys = opts.tabs().map((tab) => tab.key)
    const inSight = keys.filter((key) => workLayout.value!.visible.has(key))
    if (inSight.length) lastInSight = inSight
    const named = inSight.length ? inSight : lastInSight.filter((key) => keys.includes(key))
    return (named.length ? named : keys.slice(0, 1)).map(workPaneId).join(' ')
  })
  const conversationInSight = computed(() => !panels.active.value || panels.visible.value.has(CONVERSATION))
  watch(conversationInSight, (inSight) => {
    if (inSight) chatNews.value = false
  })

  // 命令面板里放大 / 还原读者此刻在看的那一格工作面板。
  const maximizedNow = computed(() => panels.maximized.value)
  useCommands(() => {
    if (!workLayout.value) return []
    const reset = panels.rearranged.value
      ? [{ id: 'room.resetLayout', title: t('shell.layout.reset'), icon: 'mdi-restore', run: () => panels.reset() }]
      : []
    const front = opts.tabs().find((tab) => workLayout.value?.visible.has(tab.key))
    if (!front && !maximizedNow.value) return reset
    const restore = !!maximizedNow.value
    return [
      ...reset,
      {
        id: 'room.maximize',
        title: restore ? t('shell.layout.restore') : t('shell.layout.maximize'),
        icon: restore ? 'mdi-arrow-collapse' : 'mdi-arrow-expand',
        run: () => (restore ? panels.restore() : front && panels.maximize(cellOf(front.key))),
      },
    ]
  })

  return {
    panels,
    workLayout,
    conversationInSight,
    panelControls,
    noteChatNews: () => {
      if (!conversationInSight.value) chatNews.value = true
    },
    editorParts,
    toggleEditorPart,
    chatBlocks,
    closeBlock,
  }
}
