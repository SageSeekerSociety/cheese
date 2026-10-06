<script setup lang="ts">
import type { SpaceApplication } from '@/network/api/spaces/types'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useUserRefResolver } from '@/composables/useUserRefResolver'

import AdminSpacesPageView from './AdminSpacesPageView.vue'

import { SpacesApi } from '@/network/api/spaces'

// 管理后台的「空间申请」（`/admin/spaces`）：待平台管理员过目的开版申请。
//
// 这一屏只有一件事：**一条申请一眼看完，然后按通过或驳回**。所以每一行给的是
// 「谁、申请了什么版、什么时候、说了什么」，动作就在那一行右边 —— 三个状态页签
// 分开了「还没看 / 看过了」，行内不再需要状态徽章之外的说明。
//
// 容器：取数、写入、分页、人名与去处都在这儿；画的那一半在 `AdminSpacesPageView.vue`。
//
// 这一版换的是**壳**（做法不变）：
//
//   1. 页头与状态筛选改用后台共用的 `AdminPage` + `AdminTabs`（原来是一个
//      `v-select` 撑满整行 —— 三个选项的筛选器占 1070px，是「控件在替内容占地方」）。
//   2. 从 `v-card` 一卡一条改成**紧凑列表**：一页十几条时，卡与卡之间的空隙比正文还高。
//   3. `intro` 与 `description` 一起画时常常是同一句话（建版时同一次填的），所以
//      `blurb()` 只画一遍，两句不一样时才两句都画。
//   4. 空 / 读失败改用 `BaseEmptyState`（原来是一句裸文字），读失败还带重试。
//   5. 分页的「还有没有下一页」改成**多要一条**：后台这条路由既不给总数也不给
//      `has_more`（`{"items": [...]}` 就是全部），所以每次要 51 条，回来超过 50 条
//      就说明后面还有。原来那句 `items.length < 50` 在「正好五十条」时会把下一页
//      灰掉 —— 而那一页可能恰好还有一条。
defineOptions({ name: 'AdminSpacesPage' })

/** 一页五十条。多要的那一条只用来判断「后面还有」，不画出来。 */
const PAGE = 50

const { t } = useI18n()
const { resolve: resolveUser, navigate } = useUserRefResolver()
const status = ref('PENDING')
const items = ref<SpaceApplication[]>([])
const loading = ref(false)
/** 读这一页失败时是**服务端原话**（原话取不到就空串）；`null` 表示没失败。
 *  它说的是「这一页没读到」，所以画在列表自己的位置上（不是页顶横幅）。
 *  失败与否和原话是两件事：原话为空时仍要给出错态，不能落进「暂无申请」那个空态。 */
const loadError = ref<string | null>(null)
/** 通过 / 驳回失败。和读失败分开：重试的不是同一件事。 */
const writeError = ref('')
const offset = ref(0)
const hasMore = ref(false)
const selected = ref<SpaceApplication | null>(null)
const reason = ref('')
const saving = ref(false)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    // 多要一条：见文件开头第 5 条。多的那一条只决定下一页按钮亮不亮。
    const { items: rows } = (await SpacesApi.reviews(status.value, offset.value, PAGE + 1)).data
    hasMore.value = rows.length > PAGE
    items.value = hasMore.value ? rows.slice(0, PAGE) : rows
  } catch (e) {
    // 原话存下来作说明行，不用「加载失败，请重试。」这种固定话把原因吞掉。
    loadError.value = e instanceof Error && e.message ? e.message : ''
    items.value = []
    hasMore.value = false
  } finally {
    loading.value = false
  }
}

function changeStatus(next: string) {
  if (status.value === next) return
  status.value = next
  offset.value = 0
  void load()
}

function page(delta: number) {
  offset.value = Math.max(0, offset.value + delta)
  void load()
}

function reject(item: SpaceApplication) {
  writeError.value = ''
  selected.value = item
  reason.value = ''
}

async function decide(item: SpaceApplication, approved: boolean) {
  if (saving.value || (!approved && !reason.value.trim())) return
  saving.value = true
  writeError.value = ''
  try {
    await SpacesApi.review(item.id, approved, reason.value.trim())
    selected.value = null
    reason.value = ''
    await load()
  } catch {
    // 框里的错误留在框里（驳回时）：关掉框等于把刚写的理由和「为什么退回」一起丢掉。
    writeError.value = t('spaces.review.actionFailed')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <AdminSpacesPageView
    :status="status"
    :items="items"
    :loading="loading"
    :saving="saving"
    :load-error="loadError"
    :write-error="writeError"
    :offset="offset"
    :has-more="hasMore"
    :selected="selected"
    :reason="reason"
    :resolve-user="resolveUser"
    @refresh="load"
    @update:status="changeStatus"
    @previous="page(-PAGE)"
    @next="page(PAGE)"
    @approve="decide($event, true)"
    @reject="reject"
    @dismiss-write-error="writeError = ''"
    @dismiss-reject="selected = null"
    @confirm-reject="selected && decide(selected, false)"
    @update:reason="reason = $event"
    @navigate="navigate"
  />
</template>
