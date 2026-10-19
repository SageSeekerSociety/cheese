// 一列行：右键一行，弹出那一行的操作菜单，弹在鼠标那一点上。行尾没有 ⋯——三个点
// 全去掉了，右键（手机上是长按）就是入口。同一时间只有一行的菜单开着，所以一列只要
// 一份状态。
//
//   const rowMenu = useRowMenu()
//   <li @contextmenu="rowMenu.open(file.path, $event)">
//     <AdaptiveMenu v-bind="rowMenu.bind(file.path)" :actions="…">
//
// 行没有能做的事时不要绑 open：右键就该是浏览器自己的那一份。
import { ref } from 'vue'

export function useRowMenu<K extends string | number>() {
  const openKey = ref<K | null>(null)
  const point = ref<[number, number] | null>(null)

  function open(key: K, event: MouseEvent) {
    // 右键在链接、输入框上，或者正选着一段字：要的是浏览器那一份（在新标签打开、复制、
    // 粘贴），不是这一行的操作。
    const target = event.target instanceof Element ? event.target : null
    if (target?.closest('a[href], input, textarea, [contenteditable="true"]')) return
    if (window.getSelection()?.isCollapsed === false) return
    event.preventDefault()
    // 键盘（Shift+F10 / 菜单键）发来的 contextmenu 没有鼠标坐标，落在 (0,0)：这时把
    // 菜单开在焦点所在那一行的左下角，而不是屏幕左上角。
    const keyboard = event.clientX === 0 && event.clientY === 0
    const rect = (event.currentTarget as HTMLElement | null)?.getBoundingClientRect()
    point.value = keyboard && rect ? [rect.left + 12, rect.top + rect.height] : [event.clientX, event.clientY]
    openKey.value = key
  }

  function bind(key: K) {
    return {
      modelValue: openKey.value === key,
      point: openKey.value === key ? point.value : null,
      'onUpdate:modelValue': (value: boolean) => {
        openKey.value = value ? key : null
        if (!value) point.value = null
      },
    }
  }

  return { open, bind }
}
