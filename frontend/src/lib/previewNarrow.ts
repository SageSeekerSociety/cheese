// 预览里「窄」的那一条线：一格比这还窄，就按手机的排法画（文件条收进「…」、Word 重排、
// 表格冻结首列、演示稿一页铺满）。
//
// 量的是这一格自己，不是窗口：平板上左边有侧栏、桌面上右栏被拖窄，窗口很宽而这一格只剩
// 四百来像素，读起来和手机是一回事。
import type { MaybeElementRef } from '@vueuse/core'

import { computed } from 'vue'
import { useElementSize } from '@vueuse/core'

export const NARROW_PX = 560

/** 这一格现在窄不窄。还没量到宽度（0）时算宽：先按宽处画，量到了再收。
 *
 *  `measured` 分开给：要把宽窄报给别处的那一层得等真量到了再报，否则挂上去那一下的「宽」
 *  会盖掉别处已经知道的「窄」。
 *
 *  量边框盒，不量内容盒：量到的这一格自己会按判定结果改内边距（`.pv--narrow`、
 *  `.is-narrow` 都把 16px 收到 8px），内容盒因此比边框盒窄出这 16px——判定成的「窄」
 *  又把内容盒撑宽，宽度落在临界那 16px 里就会来回翻。边框盒由外面那一级给，不受自己
 *  的内边距影响。 */
export function usePreviewNarrow(target: MaybeElementRef) {
  const { width } = useElementSize(target, { width: 0, height: 0 }, { box: 'border-box' })
  return {
    narrow: computed(() => width.value > 0 && width.value < NARROW_PX),
    measured: computed(() => width.value > 0),
  }
}
