<script setup lang="ts">
// 子树渲染出错时的兜底：这一段崩了，页面其余部分照常。
//
// 一个组件在 setup / 渲染里抛错，Vue 会把它整棵子树换成一段空注释——那一块就白
// 了，没有栈、没有提示，读的人只看到一块什么都没有的地方（真出事时它甚至不会进现
// 场：`app.config.errorHandler` 收得到，但屏幕上没有任何可点的东西）。这个组件接住
// 子树里第一次没人接的错：上报一次，把这一块换成一句话加一颗「重试」，并返回 false
// 让错误不再往上冒。
//
// 为什么不重复上报：`onErrorCaptured` 返回 false 之后 Vue 不再把这个错交给
// `app.config.errorHandler`（见 src/errorReporter.ts 里它的挂法），所以这里
// `reportError` 报的那一次就是唯一一次。也正因为这样，返 false 是这套设计的一半，不
// 是可有可无的一行。
//
// 只有真会把子树打没的错才接：异步组件加载失败（下面那个 info 分支）不算，它已经被
// Vue 单独隔离，收了反而会连累好着的兄弟。
//
// 子树在错误态下已经被 Vue 换掉了，重试重挂一次就是真的重新来过；resetKey 用在会换
// 内容的宿主上（例如话题 id），切走再回来时不用等人点重试，那一块自己活过来。
import { onErrorCaptured, ref, watch } from 'vue'

import { reportError } from '../../errorReporter'
import BaseButton from '../base/BaseButton.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /**
     * `section`（默认）用在整段对话这类占满一块的地方：兜底居中、撑满它替掉的那块，
     * 布局不跳。`compact` 用在面板里：一行、居左、贴着面板内容区的顶边，像面板自己
     * 的一条提示。
     */
    variant?: 'section' | 'compact'
    /**
     * 它变了就当作一次全新的子树：清掉错误、重新挂载。传当前话题 id，切话题时那
     * 一块自己恢复，不用等人点重试。null 表示这个宿主不会换内容。
     */
    resetKey?: string | number | null
  }>(),
  { variant: 'section', resetKey: null }
)

// 子树是否正处在错误态。
const failed = ref(false)
// 每次重挂 +1。只把 failed 放回 false 时，Vue 有可能复用那个已经坏了的实例（同类型
// 的 vnode 会被 patch 而不是重建）；换一个 key，这一截才真的重新 mount。
const nonce = ref(0)

function retry() {
  failed.value = false
  nonce.value += 1
}

onErrorCaptured((err, _instance, info) => {
  // 异步组件的加载失败（懒加载那块 chunk 挂了、或者它自己 import 的时候抛了）不算这
  // 棵子树的渲染/初始化错误：Vue 只会把那一格留空，兄弟节点照常渲染，`app.config` 的
  // errorHandler 也能记到它。这里要是也当成致命错，把整块换成兜底，反而会把本来好着
  // 的半边也一起收走——例如总览里的文档是异步装的，它挂了不该连进度、看板一起没。放
  // 它继续往上冒，上报由全局 errorHandler 照旧做一次，这里只兜住真会把子树打没的那种。
  if (info === 'async component loader') return
  // 已经进错误态就不再往上刷屏：同一棵子树在重画里可能反复抛，一次报告足够，真正的
  // 恢复靠重试或 resetKey。
  if (failed.value) return false
  const message = err instanceof Error ? err.message : String(err)
  const stack = err instanceof Error ? err.stack : undefined
  reportError(message, stack, `vue:${info}`)
  failed.value = true
  // 接住了：不再往上冒，界面上由下面这段兜底接手。
  return false
})

watch(
  () => props.resetKey,
  () => {
    if (failed.value) retry()
  }
)
</script>

<template>
  <!-- role="alert" so a reader is told the block died, not left with a silent hole. -->
  <div v-if="failed" class="error-boundary" :class="`error-boundary--${variant}`" role="alert">
    <span class="error-boundary__text">{{ t('global.errorBoundary.message') }}</span>
    <BaseButton kind="secondary" size="sm" @click="retry">{{ t('global.errorBoundary.retry') }}</BaseButton>
  </div>
  <!--
    The slot is the ONLY root when healthy: a Fragment renders its children in place,
    so no wrapper element is inserted and the wrapped layout is untouched. `:key`
    reaches renderSlot as a slot prop and sets the Fragment key, which is what forces
    a real remount on retry (not a patch of the broken instance).
  -->
  <slot v-else :key="nonce" />
</template>

<style scoped>
.error-boundary {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.error-boundary__text {
  color: var(--text);
}
/* 整段区域里的兜底：居中、撑满它替掉的那块，页面不跳。 */
.error-boundary--section {
  flex: 1 1 auto;
  justify-content: center;
  min-height: 0;
  padding: 24px 16px;
}
/* 面板里：一行，居左，贴顶，像面板自己的一条提示，不占满整块。 */
.error-boundary--compact {
  justify-content: flex-start;
  align-items: center;
  padding: 12px 16px;
}
</style>
