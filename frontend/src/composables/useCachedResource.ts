// 一行接入的「先给缓存，再在背后刷新」（stale-while-revalidate）。
//
// 页面原来的写法是 onMounted(load) → loading=true → 请求 → 渲染，模板里
// `v-if="loading"` 把整屏换成一个转圈。第二次进这个页面和第一次一样贵，屏幕先
// 变空再变回来——用户的说法是「每进一个页面都要加载，感觉很麻烦」。
//
// 换成这个之后，`loading` 只在「这个 key 从来没成功取过」时为真。已经看过的页
// 面立刻画出来，请求照发，回来了再原地更新。
import type { MaybeRefOrGetter, Ref } from 'vue'

import { getCurrentInstance, onActivated, ref, toValue, watch } from 'vue'

import { fetchCachedPage, hasCachedPage, readCachedPage } from '@/lib/pageCache'

export interface CachedResource<T> {
  /** 当前 key 的内容：缓存里的，或刚取回来的。没有任何数据时是 undefined。 */
  data: Ref<T | undefined>
  /** 只在「没有任何数据可显示」时为真——它是「该不该转圈」的答案。 */
  loading: Ref<boolean>
  /** 有内容在显示，同时背后正在重取。想提示就用它，别拿它挡住内容。 */
  refreshing: Ref<boolean>
  /** 只在「没有缓存 + 请求失败」时有值。有内容时刷新失败不写这里。 */
  error: Ref<Error | null>
  /** 手动重取一次当前 key；有内容时走 refreshing，不转圈。 */
  refresh: () => Promise<void>
}

function asError(cause: unknown): Error {
  return cause instanceof Error ? cause : new Error(String(cause))
}

/**
 * @param key   这份数据的身份。项目 id / 成员 handle / 文档 kind 这些「换一个就
 *              是另一份数据」的东西都要拼进去。传 getter 就能跟着路由参数变。
 * @param fetcher 真正去取数的函数，拿到的是发起这次请求时的 key。
 */
export function useCachedResource<T>(
  key: MaybeRefOrGetter<string>,
  fetcher: (key: string) => Promise<T>
): CachedResource<T> {
  // 深层 ref 而不是 shallowRef：页面会就地改取回来的对象（把一条收件箱标成已
  // 读、从记忆列表里删一条），浅层的话那些改动不会重新渲染。
  const data = ref() as Ref<T | undefined>
  const loading = ref(false)
  const refreshing = ref(false)
  const error = ref<Error | null>(null)

  // 「现在屏幕上是哪个 key」。晚回来的响应拿它对一下——key 已经换了就说明用户
  // 走到别的项目/别的成员去了，这份数据现在是错的，丢掉。
  let currentKey = toValue(key)
  let hasData = false

  async function load(requestKey: string): Promise<void> {
    // 有东西在显示就绝不转圈：这一条是整个改动的目的。
    if (hasData) refreshing.value = true
    else loading.value = true
    try {
      const value = await fetchCachedPage(requestKey, () => fetcher(requestKey))
      if (requestKey !== currentKey) return
      data.value = value
      hasData = true
      error.value = null
    } catch (cause) {
      if (requestKey !== currentKey) return
      // 后台刷新失败不许砸掉页面：手里还有内容就继续显示它。一次网络抖动不该把
      // 用户正在看的东西换成一张错误页。
      if (!hasData) error.value = asError(cause)
    } finally {
      if (requestKey === currentKey) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  watch(
    () => toValue(key),
    (next) => {
      currentKey = next
      // 立刻切过去，不留上一个 key 的任何痕迹——哪怕新 key 什么都没有，显示空白
      // 加转圈也好过显示另一个项目的数据。
      hasData = hasCachedPage(next)
      data.value = hasData ? readCachedPage<T>(next) : undefined
      error.value = null
      loading.value = false
      refreshing.value = false
      void load(next)
    },
    { immediate: true }
  )

  async function refresh(): Promise<void> {
    await load(currentKey)
  }

  // keep-alive 之下组件不再重新挂载，所以「回到这一页」这件事只有 onActivated
  // 说得出来。首次挂载时它也会响一次，但那一发正好撞上上面 immediate 的那一发，
  // 被 fetchCachedPage 的去重合并掉，不会变成两个请求。
  if (getCurrentInstance()) onActivated(() => void refresh())

  return { data, loading, refreshing, error, refresh }
}
