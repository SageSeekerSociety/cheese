/**
 * 「要离开这块正在标注的图了，先问一句」的那根线。
 *
 * 画到一半的标注只活在图那一层（`DesignImage`）里，而「关掉这一格 / 换到别的页签 /
 * 换到别的文件」都发生在上面几层（`WorkPanel`）。中间隔着好几层组件，逐层往上透传
 * 一个回调不值当，所以用这个模块级的小登记处：屏幕上的那块标注把自己登记进来，
 * 导航那一层在动之前问一句。
 *
 * 只登记「此刻在看着的、且有没发出去的笔画」的那一块——收起来的页签（`v-show` 留着）
 * 也在跑，但它们没在看，不该拦住别人。
 */
export interface AnnotationDiscardGuard {
  /** 弹一下确认。true = 可以走（笔画该丢就丢），false = 留下别动。 */
  confirmDiscard: () => Promise<boolean>
}

let guard: AnnotationDiscardGuard | null = null

export function setAnnotationGuard(next: AnnotationDiscardGuard): void {
  guard = next
}

/** 只清自己那一份：后登记的盖过先登记的，撤销时别把别人的撤掉。 */
export function clearAnnotationGuard(current: AnnotationDiscardGuard): void {
  if (guard === current) guard = null
}

export function hasUnsentAnnotations(): boolean {
  return guard !== null
}

/**
 * 动之前问一句。没有在标注的东西就直接放行；有的话听它怎么说。
 *
 * 确认通过时，登记处由那一块自己同步撤掉（`confirmDiscard` 里做，见 `DesignImage`），
 * 所以紧接着的第二次调用不会再弹一次。
 */
export function confirmAnnotationDiscard(): Promise<boolean> {
  const current = guard
  if (!current) return Promise.resolve(true)
  return current.confirmDiscard()
}
