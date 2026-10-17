// 面板里两个可选清单的格子 id。
//
// 输入框用 aria-activedescendant 指过去，画格子的一边把同一套 id 挂在自己身上 ——
// 两边必须是同一份，所以不各写一遍。

export const optionId = (index: number) => `palette-option-${index}`
export const actionId = (index: number) => `palette-action-${index}`

/** 结果列表本身：输入框的 `aria-controls` 和那个 `role="listbox"` 说的是同一个。 */
export const resultsId = 'palette-results'
