// 预览里「看 / 批注 / 改」三段模式（原型里那一段，快捷键 1 / 2 / 3）。
//
// 三种模式说的是「现在鼠标按下去算什么」：
//
// - `view` 看：什么都不指。拖选一段只是在读、在复制——这一段不弹任何东西。
// - `note` 批注：指一处就写一句话。这是原来唯一的行为，也是默认那一段。
// - `edit` 改：就地改正文。今天只有 markdown 那一条（`liveEdit`）开得起来，没开编辑
//   的档（PDF、源代码、网页、图片）这一段是灰的——和原型里「运行中应用请通过批注交由
//   芝士修改源码」同一条道理：能改的才给，不能改的不摆一颗按下去没反应的键。
//
// 为什么不只是「选中后多一步」：原型看模式里选中只在选区左下浮一颗「添加批注」，点了
// 才展开整张卡；批注模式里选中直接出卡。所以这个模式必须传进定位那几支，由它们决定
// 选中之后摆什么。见 `usePreviewLocator` 的 `armFirst` 和 `PreviewLocator`。
//
// 模式是这一格自己的事（和全屏、评论栏一样），不跨格、不落盘；**按文件各记一份**：
// 一份上切到「批注」，换去看另一份时是「看」（刚打开一份东西先是在读它），再换回来还是
// 离开时那一段（原型里也是 `Z.modes[fileId]`）。谁读它：`PanelPreviewView` 把 `view` 之外
// 的两段接上定位层，宿主按它禁不禁用「改」那一段。
import type { ComputedRef, InjectionKey } from 'vue'

import { computed, reactive } from 'vue'

/** 三段模式。名字照原型里的 `view / note / edit`。 */
export type PreviewMode = 'view' | 'note' | 'edit'

export interface PreviewModeApi {
  /** 现在这一段（按文件记，换文件跟着走）。 */
  mode: ComputedRef<PreviewMode>
  set: (mode: PreviewMode) => void
  /** 这一段能不能用：`edit` 只有这一份开得起来编辑时才给。灰的那一段按下去不生效。 */
  enabled: (mode: PreviewMode) => boolean
  /** 现在这一段能不能改正文：`edit` 且这一份开得起来。 */
  editing: ComputedRef<boolean>
  /** 现在能不能指一处写批注：`note` 那一段。 */
  annotating: ComputedRef<boolean>
}

export const PREVIEW_MODE: InjectionKey<PreviewModeApi> = Symbol('preview-mode')

/** 快捷键 1 / 2 / 3 对应的三段。原型里也是这三个数。 */
export const MODE_KEYS: Readonly<Record<string, PreviewMode>> = { '1': 'view', '2': 'note', '3': 'edit' }

/**
 * @param canEdit 这一份现在开不开得起来编辑（markdown 的协同会话、Office 的编辑器）。
 *   它可以是异步才为真的（协同会话要等连上），所以是个函数，每次问它。
 *   不允许 `edit` 时按了「改」不切过去——灰的键按下去悄没声地留在原处，比切过去再
 *   报错好。
 * @param currentPath 现在开着哪一份。模式按**文件**记（原型里也是 `Z.modes[fileId]`）：
 *   在一份 PDF 上切到「批注」，换去看另一份就该回到「看」——刚打开一份东西先是在读它。
 *   换文件时把记下的那一份切过去；没记过的那一份是「看」。
 */
export function usePreviewMode(canEdit: () => boolean, currentPath: () => string): PreviewModeApi {
  // 按路径记，用对象而不是 `Map`：`Map` 不是响应式的，`set` 进去读它的那个 computed
  // 不会重算——切了模式、界面还停在上一次那一段。
  const byPath = reactive<Record<string, PreviewMode | undefined>>({})
  const here = computed(() => currentPath())
  const mode = computed<PreviewMode>({
    get: () => byPath[here.value] ?? 'view',
    set: (candidate) => {
      byPath[here.value] = candidate
    },
  })
  const enabled = (candidate: PreviewMode): boolean => candidate !== 'edit' || canEdit()
  function set(candidate: PreviewMode) {
    if (!enabled(candidate)) return
    mode.value = candidate
  }
  return {
    mode,
    set,
    enabled,
    editing: computed(() => mode.value === 'edit' && canEdit()),
    annotating: computed(() => mode.value === 'note'),
  }
}
