// 一列行，每行一颗 ⋯：右键一行弹的就是那一行的 ⋯ 菜单，弹在鼠标那一点上。点 ⋯
// 打开时照旧挂在 ⋯ 下面。同一时间只有一行的菜单开着，所以一列只要一份状态。
//
//   const rowMenu = useRowMenu()
//   <li @contextmenu="rowMenu.open(file.path, $event)">
//     <AdaptiveMenu v-bind="rowMenu.bind(file.path)" :actions="…">
//
// 行没有 ⋯ 的时候（没有能做的事）不要绑 open：右键就该是浏览器自己的那一份。
import { ref } from 'vue'

export function useRowMenu<K extends string | number>() {
  const openKey = ref<K | null>(null)
  const point = ref<[number, number] | null>(null)

  function open(key: K, event: MouseEvent) {
    event.preventDefault()
    point.value = [event.clientX, event.clientY]
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
