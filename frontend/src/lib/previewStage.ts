// 预览这一格的阶段：从打开到能读能指，中间经过哪几步、每步失败时说的是哪一件事。
//
// 文档那一档原先自己拼「转圈 / 缺服务 / 转不了」三行条件，帧那一档自己说「正在获取授权」
// 「正在加载新页面」——同一个处境在不同档上换了词。这里把阶段收成一处，说给读者的名词也
// 只有这一套（这个模块只做判断，文案在 i18n）。
//
// 判断的输入是各处已经算好的信号，这里不自己取数：一件事属于哪个阶段是这一个小模块的
// 事，取到没取到是取数那一层的事（`panelPreview/`）。
//
// 现在只有文档那一档接上它。帧那一档还没搬：它的「授权 / 导航」两步和这里的一一对应，
// 但它的文案要保留（「正在获取授权」和「正在加载新页面」是两句），搬过去要先想把这两
// 句放在哪儿——那是下一步，见文档《预览查看器契约与预览状态机》。

/** 预览此刻在哪一步。
 *
 *  收成一个可辨识联合：`stale` 只在 `updated` 上有，`failure` 只在 `failed` 上有，读的人
 *  不必先看 `stage` 再猜另一个字段在不在。 */
export type PreviewStageNow =
  /** 还没有东西可看、也没在报错：停在第一步等着。 */
  | { stage: 'auth' }
  /** 取字节、转格式。 */
  | { stage: 'navigate' }
  /** 正文到齐，能读能指。 */
  | { stage: 'ready' }
  /** 重取到了新一版、屏幕上摆的还是上一版：能读，但要知道看到的不是刚存的。 */
  | { stage: 'updated'; stale: true }
  /** 打不开，说得出是哪一种。 */
  | { stage: 'failed'; failure: 'rendererMissing' | 'cannotRender' }

export interface PreviewStageInput {
  /** 这一次取数还在路上。 */
  loading: boolean
  /** 屏幕上有东西可看（字节或渲染好的那一页；markdown 是它的正文）。 */
  shown: boolean
  /** 上一次重取失败，但屏幕上还留着上一版。 */
  stale: boolean
  /** 这个部署缺转换服务（换个文件也一样）。 */
  rendererMissing: boolean
  /** 这一份转不了（别的文件仍能看）。 */
  cannotRender: boolean
}

/** 此刻在第几步。顺序就是读者等的顺序：先在路上的那一步说话；都已经落定，才说结果。 */
export function previewStage(input: PreviewStageInput): PreviewStageNow {
  if (input.shown) {
    // 有东西可看：上一版还在，讲的是「看到的不是最新的」，不把它说成打不开。
    return input.stale ? { stage: 'updated', stale: true } : { stage: 'ready' }
  }
  if (input.loading) return { stage: 'navigate' }
  if (input.rendererMissing) return { stage: 'failed', failure: 'rendererMissing' }
  if (input.cannotRender) return { stage: 'failed', failure: 'cannotRender' }
  // 什么都没有、也没在报错：还没开始取。停在授权那一步等着。
  return { stage: 'auth' }
}
