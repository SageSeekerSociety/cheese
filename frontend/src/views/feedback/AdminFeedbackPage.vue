<script setup lang="ts">
import type { FeedbackItem } from '@/lib/feedbackMock'
import type { AdminTab } from '@/stores/feedback'

import { computed, ref, watch } from 'vue'

import AdminFeedbackDetailDrawer from '@/components/feedback/AdminFeedbackDetailDrawer.vue'
import FeedbackStatusChip from '@/components/feedback/FeedbackStatusChip.vue'
import { KIND_LABEL, SOURCE_LABEL } from '@/lib/feedbackMock'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 管理员后台的「反馈管理」(原型地址 /admin/feedback)。
//
// 需求说它属于**独立的**后台（类似 admin.okcheese.com），不放在用户侧反馈中心里。
// 所以这一页有自己的外壳和口径，和 /feedback 长得不一样是**故意的**：两边面对的
// 问题不同——用户侧问「有没有人也遇到这个」，管理侧问「这一条现在该谁动」。同一个
// 界面同时回答这两个问题，结果是两边都不好用。
//
// 这一页同时承担原型的角色开关：需求要求「通过 mock role 展示 user/admin 两套
// 界面」，而最能看出差别的就是这一页 —— 普通身份打开它只会看到一句挡板。
defineOptions({ name: 'AdminFeedbackPage' })

const store = useFeedbackStore()

const TABS: { value: AdminTab; label: string }[] = [
  { value: 'public', label: '公开反馈' },
  { value: 'private', label: '私密反馈' },
  { value: 'agent', label: 'Agent 发现' },
  { value: 'security', label: '安全问题' },
]
const tabs = computed(() => TABS.map((tab) => ({ ...tab, count: store.adminTabCounts[tab.value] })))

const selected = ref<FeedbackItem | null>(null)
const drawerOpen = ref(false)

function open(item: FeedbackItem) {
  selected.value = item
  drawerOpen.value = true
}

// 关掉抽屉时把选中项一起清掉：留着它，下次点另一条之前会先闪一下上一条的内容。
watch(drawerOpen, (open) => {
  if (!open) selected.value = null
})
</script>

<template>
  <div class="fb-admin">
    <div v-if="store.role !== 'admin'" class="fb-admin__gate page-container">
      <v-icon size="28" class="mb-2">mdi-shield-account-outline</v-icon>
      <div class="t-body mb-1">这一页是管理员后台</div>
      <div class="t-meta mb-3">你现在的原型身份是「用户」，看不到管理界面</div>
      <v-btn color="primary" size="small" @click="store.setRole('admin')">以管理员身份查看</v-btn>
      <v-btn variant="text" color="secondary" size="small" to="/feedback">回到反馈中心</v-btn>
    </div>

    <div v-else class="fb-admin__inner page-container--wide">
      <header class="fb-admin__head">
        <div>
          <div class="t-eyebrow">管理后台</div>
          <h1 class="t-page-title">反馈管理</h1>
        </div>
        <v-spacer />
        <span class="chip-neutral">原型身份：管理员</span>
        <v-btn variant="outlined" color="secondary" size="small" to="/feedback">用户侧反馈中心</v-btn>
        <v-btn variant="text" color="secondary" size="small" @click="store.setRole('user')">切回用户身份</v-btn>
      </header>

      <v-tabs v-model="store.adminTab" density="comfortable" color="primary" class="fb-admin__tabs">
        <v-tab v-for="tab in tabs" :key="tab.value" :value="tab.value">
          {{ tab.label }}
          <span class="fb-admin__count">{{ tab.count }}</span>
        </v-tab>
      </v-tabs>

      <v-table hover class="fb-admin__table">
        <thead>
          <tr>
            <th class="fb-th fb-th--title">标题</th>
            <th class="fb-th">用户</th>
            <th class="fb-th">来源</th>
            <th class="fb-th">类型</th>
            <th class="fb-th">优先级</th>
            <th class="fb-th">状态</th>
            <th class="fb-th">创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in store.adminItems" :key="item.id" class="fb-row" @click="open(item)">
            <td class="fb-td fb-td--title">
              <div class="fb-td__title">{{ item.title }}</div>
              <div class="t-meta">
                {{ item.id }}<template v-if="item.assignee"> · @{{ item.assignee }}</template>
              </div>
            </td>
            <td class="fb-td">{{ item.author }}</td>
            <td class="fb-td">
              <!-- 来源这一格和反馈卡、详情页用的是同一个形态（中性 chip + 机器人图标）：
                  三处说的是同一件事，长得一样才不用重新认。琥珀留给这一页唯一的主操作。 -->
              <span v-if="item.source === 'agent'" class="chip-neutral">
                <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
              </span>
              <span v-else class="c-muted">{{ SOURCE_LABEL[item.source] }}</span>
            </td>
            <td class="fb-td">{{ KIND_LABEL[item.kind] }}</td>
            <td class="fb-td"><FeedbackStatusChip :priority="item.priority" /></td>
            <td class="fb-td"><FeedbackStatusChip :status="item.status" /></td>
            <td class="fb-td t-meta">{{ relTime(item.createdAt) }}</td>
          </tr>
          <tr v-if="!store.adminItems.length">
            <td colspan="7" class="fb-td fb-td--empty">暂无反馈</td>
          </tr>
        </tbody>
      </v-table>
    </div>

    <AdminFeedbackDetailDrawer :item="selected" @update:open="drawerOpen = $event" />
  </div>
</template>

<style scoped>
.fb-admin {
  padding: 24px 16px 48px;
}
.fb-admin__inner,
.fb-admin__gate {
  margin: 0 auto;
}
.fb-admin__gate {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 64px 0;
  color: var(--faint);
}
.fb-admin__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.fb-admin__tabs {
  border-bottom: 1px solid var(--line);
}
.fb-admin__count {
  margin-left: 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}
.fb-admin__table {
  background: transparent;
}
/* v-table 自己给 th/td 写的是
   `.v-table > .v-table__wrapper > table > thead > tr > th`（两个 class + 四个元素），
   单类选择器赢不过它 —— 之前 `.fb-th` 上是两条 !important 就是因为这个。这里把
   Vuetify 自己的那几层写进选择器（:deep 让 scoped 样式能选到组件渲染出来的节点），
   权重 (0,3,3) 对 (0,2,4)，正常赢，不再需要 !important。 */
.fb-admin__table :deep(.v-table__wrapper table) :is(tbody td) {
  font-size: 13px;
  color: var(--text);
  vertical-align: top;
}
/* 表头比正文小一档、浅一档：它是标签，不是数据（.t-eyebrow 也是 12px）。 */
.fb-admin__table :deep(.v-table__wrapper table thead th) {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  white-space: nowrap;
}
.fb-th--title {
  width: 40%;
}
.fb-row {
  cursor: pointer;
}
.fb-td--title {
  min-width: 240px;
}
/* 空行那一格也要 :deep：Vuetify 给 td 的 `padding: 0 16px` 比单类选择器权重高，
   直接写 `.fb-td--empty { padding: 40px 0 }` 是压不过它的（写了也没有上下留白）。 */
.fb-admin__table :deep(.v-table__wrapper table tbody td.fb-td--empty) {
  padding: 40px 0;
  text-align: center;
  color: var(--faint);
}
.fb-td__title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.45;
  color: var(--ink);
}
</style>
