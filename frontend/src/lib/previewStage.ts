// 预览这一格的阶段：从打开到能读能指，中间经过哪几步、每步失败时说的是哪一件事。
//
// 以前这几样散在面板各处，各说各的：帧那一档说「正在获取授权」「正在加载新页面」，
// 文档那一档说「文档预览未启用」「无法显示这个文件」「刷新失败」——同一个处境在不同
// 档上换了词。这里把阶段收成一处，说给读者的名词也只有这一套（`lib/previewStage` 只
// 做判断，文案在 i18n）：授权、连接、导航、就绪、已更新、失败。
//
// 判断的输入是各处已经算好的信号，这里不自己取数：一件事属于哪个阶段是这一个小模块
// 的事，取到没取到是取数那一层的事（`panelPreview/`）。

/** 预览此刻在哪一步。 */
export type PreviewStage =
  /** 取访问这一份的凭证。 */
  | 'auth'
  /** 运行中的应用：等工作机在线、隧道通。 */
  | 'connect'
  /** 取字节、转格式。 */
  | 'navigate'
  /** 正文到齐，能读能指。 */
  | 'ready'
  /** 重取到了新一版，看的是它。 */
  | 'updated'
  /** 打不开，且没有旧的一版顶着。 */
  | 'failed'

/** 失败说得出是哪一种：缺服务、这一份转不了、上一版还在（所以没落 `failed`）。 */
export type PreviewFailure = 'rendererMissing' | 'cannotRender' | 'stale'

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
  /** 看的是运行中的应用：多一步「连接」。 */
  app: boolean
  /** 应用连上了（隧道通、帧报过在线）。 */
  connected: boolean
}

export interface PreviewStageNow {
  stage: PreviewStage
  /** 只在对应阶段有值：失败说明是哪一种。 */
  failure?: PreviewFailure
}

/** 此刻在第几步。顺序就是读者等的顺序：先在路上的那一步说话；都已经落定，才说结果。 */
export function previewStage(input: PreviewStageInput): PreviewStageNow {
  if (input.shown) {
    // 有东西可看：上一版还在，讲的是「看到的不是最新的」，不把它说成打不开。
    return input.stale ? { stage: 'updated', failure: 'stale' } : { stage: 'ready' }
  }
  if (input.loading) {
    // 应用比普通的文件多一步：要先连上工作机、隧道通，才谈得上取内容。
    if (input.app && !input.connected) return { stage: 'connect' }
    return { stage: 'navigate' }
  }
  if (input.rendererMissing) return { stage: 'failed', failure: 'rendererMissing' }
  if (input.cannotRender) return { stage: 'failed', failure: 'cannotRender' }
  // 什么都没有、也没在报错：还没开始取，或者这一步还没轮到。停在授权那一步等着。
  return { stage: 'auth' }
}
