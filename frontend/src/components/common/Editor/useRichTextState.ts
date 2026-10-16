// 工具栏 provide 给子件的那一份：编辑器现在什么状态、哪些命令跑得成。
//
// 编辑器的状态不是响应式的：每一笔事务后数一下（revision），按钮的选中态、可用态跟着
// 重算。数事务的那一处 watch 与原来在 RichTextToolbar.vue 里的完全一样，只是搬到了
// 这份 composable 上——工具栏只有一个，子件都从它这一份读。

import type { ChainedCommands, Editor } from '@tiptap/core'

import { computed, type ComputedRef, type InjectionKey, type Ref, ref, toRaw, watch } from 'vue'

import { ALIGNMENTS, HEADING_LEVELS } from './richTextOptions'

/** 工具栏按钮与弹层要看的那几样编辑器状态。 */
export interface RichTextToolbarState {
  editor: Editor | null
  editable: boolean
  heading: (typeof HEADING_LEVELS)[number] | null
  align: (typeof ALIGNMENTS)[number]['value'] | null
  fontFamily: string | null
  fontSize: string | null
  color: string | null
  highlight: string | null
  inTable: boolean
  link: boolean
}

/** 工具栏 provide 下去的那一份。 */
export interface RichTextToolbar {
  /** 读编辑器状态的地方都从这里拿，好让它跟着事务重算。 */
  state: ComputedRef<RichTextToolbarState>
  active: (name: string, attrs?: Record<string, unknown>) => boolean
  /** 现在这条命令做不做得成：做不成的按钮置灰。 */
  can: (command: (c: ChainedCommands) => ChainedCommands) => boolean
  run: (command: (c: ChainedCommands) => ChainedCommands) => void
}

/** 一个弹层：工具栏那一份，加自己的开关。 */
export interface RichTextMenu extends RichTextToolbar {
  open: Ref<boolean>
  /** 选中一项：先关掉弹层再执行。 */
  pick: (command: (c: ChainedCommands) => ChainedCommands) => void
}

export const RICH_TEXT_TOOLBAR: InjectionKey<RichTextToolbar> = Symbol('richTextToolbar')

/** 工具栏本体用：收一份编辑器状态，provide 给子件。 */
export function useRichTextState(editor: () => Editor | null | undefined): RichTextToolbar {
  const revision = ref(0)
  watch(
    () => toRaw(editor()),
    (current, _previous, cleanup) => {
      if (!current) return
      const update = () => revision.value++
      current.on('transaction', update)
      cleanup(() => current.off('transaction', update))
    },
    { immediate: true }
  )

  const state = computed<RichTextToolbarState>(() => {
    void revision.value
    const current = toRaw(editor()) ?? null
    const textStyle = current?.getAttributes('textStyle') ?? {}
    return {
      editor: current,
      editable: !!current?.isEditable,
      heading: HEADING_LEVELS.find((level) => current?.isActive('heading', { level })) ?? null,
      align: ALIGNMENTS.find((a) => current?.isActive({ textAlign: a.value }))?.value ?? null,
      fontFamily: (textStyle.fontFamily as string | null | undefined) ?? null,
      fontSize: (textStyle.fontSize as string | null | undefined) ?? null,
      color: (textStyle.color as string | null | undefined) ?? null,
      highlight: (current?.getAttributes('highlight').color as string | null | undefined) ?? null,
      inTable: !!current?.isActive('table'),
      link: !!current?.isActive('link'),
    }
  })

  function active(name: string, attrs?: Record<string, unknown>) {
    void revision.value
    return !!toRaw(editor())?.isActive(name, attrs)
  }

  function can(command: (c: ChainedCommands) => ChainedCommands) {
    void revision.value
    const current = toRaw(editor())
    return !!current?.isEditable && command(current.can().chain()).run()
  }

  function run(command: (c: ChainedCommands) => ChainedCommands) {
    const current = toRaw(editor())
    if (!current?.isEditable) return
    command(current.chain().focus()).run()
  }

  return { state, active, can, run }
}

/**
 * 工具栏子件要的那一份。`toolbar` 由调用处 `inject(RICH_TEXT_TOOLBAR)` 传进来，不在
 * 这里 inject：分级脚本读的是组件自己的 `<script>` 块，藏在 composable 里的 inject 会
 * 让按钮组和弹层看起来能单独挂起来（它们不能——离开工具栏就没有编辑器可用）。这个
 * 参数也让「挂错了地方」在这里就报出来，而不是静默不响应。
 */
export function useRichTextControls(toolbar: RichTextToolbar | undefined): RichTextToolbar {
  if (!toolbar) {
    throw new Error('RichTextToolbar action groups and menus only work inside it: it provides the editor state.')
  }
  return toolbar
}

/** 一个弹层子件：上面那一份，加自己的开关和「选中后先关再执行」。 */
export function useRichTextMenu(toolbar: RichTextToolbar | undefined): RichTextMenu {
  const controls = useRichTextControls(toolbar)
  const open = ref(false)
  function pick(command: (c: ChainedCommands) => ChainedCommands) {
    open.value = false
    controls.run(command)
  }
  return { ...controls, open, pick }
}
