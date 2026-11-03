// 预览这一格的阶段：屏幕上有没有一份可读的东西，以及正在取的那一份走到哪一步。
//
// 文档那一档原先自己拼「转圈 / 缺服务 / 转不了」三行条件，帧那一档自己说「正在获取授权」
// 「正在加载新页面」——同一个处境在不同档上换了词。这里把阶段收成一处，说给读者的名词也
// 只有这一套（这个模块只做判断，文案在 i18n）。
//
// 判断的输入是各处已经算好的信号，这里不自己取数：一件事属于哪个阶段是这一个小模块的
// 事，取到没取到是取数那一层的事（`panelPreview/`）。
//
// 「有没有东西可读」和「在不在取」是**两件事**，不是一件事的两个取值。帧那一档换一页时
// 刻意不把上一帧卸掉（`usePreviewFrames`：新导航不清掉已加载的那一帧），所以「摆着一份旧
// 的、同时在取新的」是它常态，那一档要同时说「能看」和「在加载」。文档那一档相反：它也
// 保着上一版不卸（重取时卸掉会丢滚动位置），但它不想在重取时再摆一个转圈——有东西可读就
// 够了。两档读的是同样这两件事，各取各的。把两件事压成一个互斥的枚举，就只能服务其中
// 一档：帧那一档那行「正在加载新页面」会在每次换页时静默消失（只有第一次导航还看得见）。

/** 取数走到哪一步。名字对着文档《预览查看器契约与预览状态机》那条时间线。
 *
 *  `auth` 是帧那一档的（先去拿一份会话，才谈得上取页面）；`navigate` 两档都有（取字节、
 *  转格式）。 */
export type PreviewStep = 'auth' | 'navigate'

/** 预览此刻的样子。
 *
 *  收成一个可辨识联合：`failure` 只在 `failed` 上有、`stale` 只在 `shown` 上有，读的人不必
 *  先看 `stage` 再猜另一个字段在不在。`loading` 在 `shown` 上是有的——那一档正是「摆着一份
 *  旧的、同时在取新的」，说得出在取哪一步；取完了就是 `null`。 */
export type PreviewStageNow =
  /** 打不开，说得出是哪一种。这时屏幕上没有可读的东西。 */
  | { stage: 'failed'; failure: 'rendererMissing' | 'cannotRender' }
  /** 屏幕上摆着一份可读的内容。`loading` 说同时还在取哪一步（`null`＝取完了），
   *  `stale` 说这一份不是最新的（这一版没取成，看到的是上一版）。 */
  | { stage: 'shown'; stale: boolean; loading: PreviewStep | null }
  /** 屏幕上是空的：停在 `step` 那一步等着。 */
  | { stage: 'waiting'; step: PreviewStep }
  /** 屏幕上是空的，也没在取、没报错：歇着。帧那一档没在导航时就是这样，不该显示任何一句
   *  「正在……」。 */
  | { stage: 'idle' }

export interface PreviewStageInput {
  /** 这一次取数走到哪一步；没在取就是 `null`。
   *
   *  分 `auth` 和 `navigate` 是要说得出卡在哪：一个在等授权，一个在取字节、转格式。 */
  loading: PreviewStep | null
  /** 屏幕上有东西可看（字节或渲染好的那一页；markdown 是它的正文）。 */
  shown: boolean
  /** 上一次重取没成，但屏幕上还留着上一版。 */
  stale: boolean
  /** 这个部署缺转换服务（换个文件也一样）。 */
  rendererMissing: boolean
  /** 这一份转不了（别的文件仍能看）。 */
  cannotRender: boolean
}

/** 此刻的样子。有东西可读时它说话最响：失败说的是「这一份取不到」，而读者手里明明有一份。 */
export function previewStage(input: PreviewStageInput): PreviewStageNow {
  if (input.shown) return { stage: 'shown', stale: input.stale, loading: input.loading }
  if (input.loading) return { stage: 'waiting', step: input.loading }
  if (input.rendererMissing) return { stage: 'failed', failure: 'rendererMissing' }
  if (input.cannotRender) return { stage: 'failed', failure: 'cannotRender' }
  // 空着、没在取、没报错：还没有要显示的东西，也没有在等的事。
  return { stage: 'idle' }
}
