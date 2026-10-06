<script setup lang="ts">
import type { AuditItem } from '@/lib/adminModels'

import { useUserRefResolver } from '@/composables/useUserRefResolver'

import AdminModelsAuditView from './AdminModelsAuditView.vue'

// 最近操作那一段**接线的那一层**：把「谁改的」那颗人名接上名册与去处，其余原样透给
// `AdminModelsAuditView.vue`（只吃 props、只往上发事件）。
//
// 这一层自己不留任何状态：展开哪一行由页面记（`expanded` 是按下标记的集合），读失败照
// 原话显示、并给重试；**不**显示「暂无操作」（那是把「没读到」说成「没有」）。
//
// 人名从 `UserRefLink` 换成纯展示的 `UserRef` + 这里的 `useUserRefResolver`：以前那一颗
// 会把 `stores/workspace` 一并拖进来，这一件因此不是独立可渲染的（预览站里挂它要先把
// 工作区那套插件搭起来）。现在名字与去处在这一层算好递下去，视图那一半干净了。
//
// 文件路径与 props 一个字没动：`views/demo/catalogModels.ts` 按这个路径登记它，改动之后
// 那边照旧能挂（`useUserRefResolver` 在没有路由 / 没有 pinia 的树里画 @名字、只是不可点）。
const props = defineProps<{
  /** 审计记录；空数组 = 没有（或正在加载，见 `loading`）。 */
  items: AuditItem[]
  /** 这一段在加载中。骨架只在手上一条都没有时画。 */
  loading: boolean
  /** 读失败的原话。有它时压过「暂无操作」。 */
  error: string | null
  /** 展开了「查看改动」的那几行（下标）。 */
  expanded: Set<number>
}>()

const emit = defineEmits<{ retry: []; toggle: [index: number] }>()

const { resolve: resolveUser, navigate } = useUserRefResolver()
</script>

<template>
  <AdminModelsAuditView
    :items="props.items"
    :loading="props.loading"
    :error="props.error"
    :expanded="props.expanded"
    :resolve-user="resolveUser"
    @retry="emit('retry')"
    @toggle="emit('toggle', $event)"
    @navigate="navigate"
  />
</template>
