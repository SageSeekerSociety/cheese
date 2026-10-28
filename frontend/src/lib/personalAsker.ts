/** 预览里「问个人芝士」那一项有没有、按下去做什么。
 *
 *  单独拎出来是为了能对着它写用例：那一项列不列出来，只由「有没有个人芝士」决定，
 *  而这一条决定挂在页面上（面板只认「给了还是没给」）。写在这里，`useTopicActions`
 *  和它的用例用的是同一份判断。
 *
 *  没有的时候给 undefined——面板据此不列那一项；摆一条按下去什么都不做的，比不摆更糟。
 */
export function personalAsker(
  available: boolean,
  open: () => void,
  ask: (message: string) => void
): ((message: string) => void) | undefined {
  if (!available) return undefined
  return (message: string) => {
    open()
    ask(message)
  }
}
