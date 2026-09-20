<script setup lang="ts">
import type { AdminTab } from '@/stores/feedback'

import { computed, onMounted, ref } from 'vue'

import AdminFeedbackDetailDrawer from '@/components/feedback/AdminFeedbackDetailDrawer.vue'
import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import FeedbackStatusChip from '@/components/feedback/FeedbackStatusChip.vue'
import { KIND_LABEL, SOURCE_LABEL } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 管理员后台的「反馈管理」（/admin/feedback）。
//
// 需求说它属于**独立的**后台（类似 admin.okcheese.com），不放在用户侧反馈中心里。
// 所以这一页有自己的外壳和口径，和 /feedback 长得不一样是**故意的**：两边面对的
// 问题不同 —— 用户侧问「有没有人也遇到这个」，管理侧问「这一条现在该谁动」。同一个
// 界面同时回答这两个问题，结果是两边都不好用。
//
// 「我是不是管理员」由**服务端**回答（`GET /feedback/meta` 的 `is_admin`，按
// `settings.feedback_admin_handles` 判定）。原型上有一个可以自己拨的身份开关，
// 那个东西真接上服务端之后就没有意义了：能不能看这一页不是客户端说了算的，而且
// 每个管理端接口自己还会再判一次 —— 前端这道判断只是为了不画一个必然全 403 的
// 空表，不是权限边界。
defineOptions({ name: 'AdminFeedbackPage' })

const store = useFeedbackStore()

const TABS: { value: AdminTab; label: string }[] = [
  { value: 'public', label: '公开反馈' },
  { value: 'private', label: '私密反馈' },
  { value: 'agent', label: 'Agent 发现' },
  { value: 'security', label: '安全问题' },
]
// 管理端四栏**不显示计数**：服务端的 counts 是给用户侧那四栏（全部/热门/处理中/
// 已完成）算的，口径不同 —— 拿它填这几栏是编数字。
const tabs = computed(() => TABS)

const selectedId = ref<string | null>(null)
const drawerOpen = ref(false)

function open(id: string) {
  selectedId.value = id
  drawerOpen.value = true
}

// 关掉抽屉时把选中项一起清掉：留着它，下次点另一条之前会先闪一下上一条的内容。
function close(open: boolean) {
  drawerOpen.value = open
  if (!open) selectedId.value = null
}

onMounted(async () => {
  // meta 决定这一页画不画。它可能已经被反馈中心拉过了，这里再调一次是幂等的，
  // 而直接输地址进来的时候没有它就没法判断。
  //
  // **要等**：`loadMeta` 是网络调用，不 await 的话这一行读到的永远是「还不是管理员」，
  // 于是直接打开/刷新 `/admin/feedback` 看到的是一张空表 —— 表格画出来了，一行没有，
  // 而人以为「后台里没东西」。这个 bug 是预览里发现的：冷启动进来就是这样，从反馈
  // 中心点进来反而正常（那边已经把 meta 拉过了），所以只盯着点进来的路径看不出来。
  await store.loadMeta()
  if (store.isAdmin) void store.loadAdmin()
})
</script>

<template>
  <!-- 滚动归这一页自己领，理由见 FeedbackCenterPage 顶部那段注释。 -->
  <div class="fb-admin fill-height overflow-y-auto">
    <!-- 这里要**三段**，不是两段。「我是不是管理员」在服务端，meta 没到之前
         `isAdmin` 是 false —— 两段的话，直接打开/刷新这一页时先画出来的就是「你的
         账号不在管理员名单里」，一个真管理员看到的第一句话是假的，然后它才变成
         表格。await 只解决了「拉不拉列表」，解决不了这一帧画什么。 -->
    <div v-if="!store.metaChecked" class="fb-admin__gate page-container">
      <v-icon size="28" class="mb-2">mdi-shield-account-outline</v-icon>
      <div class="t-body mb-1">正在确认权限…</div>
    </div>

    <div v-else-if="!store.isAdmin" class="fb-admin__gate page-container">
      <v-icon size="28" class="mb-2">mdi-shield-account-outline</v-icon>
      <div class="t-body mb-1">这一页是管理员后台</div>
      <div class="t-meta mb-3">你的账号不在管理员名单里，看不到这里的反馈 —— 私密反馈和安全问题对非管理员不存在</div>
      <v-btn variant="text" color="secondary" size="small" to="/feedback">回到反馈中心</v-btn>
    </div>

    <div v-else class="fb-admin__inner page-container--wide">
      <header class="fb-admin__head">
        <div>
          <div class="t-eyebrow">管理后台</div>
          <h1 class="t-page-title">反馈管理</h1>
        </div>
        <v-spacer />
        <v-btn variant="outlined" color="secondary" size="small" to="/feedback">用户侧反馈中心</v-btn>
      </header>

      <!-- 栏位是服务端的筛选（`?tab=`），不认的值服务端回 400 —— 所以这里切栏位
           就是重新拉一页，不做本地过滤（本地过滤是第二份实现，两边迟早不一样）。 -->
      <v-tabs
        :model-value="store.adminTab"
        density="comfortable"
        color="primary"
        class="fb-admin__tabs"
        @update:model-value="store.setAdminTab($event as AdminTab)"
      >
        <v-tab v-for="tab in tabs" :key="tab.value" :value="tab.value">{{ tab.label }}</v-tab>
      </v-tabs>

      <v-table v-if="store.adminLoading" class="fb-admin__table">
        <tbody>
          <tr>
            <td class="fb-admin__loading">加载中…</td>
          </tr>
        </tbody>
      </v-table>

      <v-alert v-else-if="store.error && !drawerOpen" type="error" density="compact" variant="tonal" class="mt-4">
        {{ store.error }}
        <template #append>
          <v-btn variant="text" size="small" @click="store.loadAdmin()">重试</v-btn>
        </template>
      </v-alert>

      <v-table v-else hover class="fb-admin__table">
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
          <tr v-for="item in store.adminItems" :key="item.id" class="fb-row" @click="open(item.id)">
            <td class="fb-td fb-td--title">
              <div class="fb-td__title">{{ item.title }}</div>
              <div class="t-meta">
                {{ item.display_id }}<template v-if="item.assignee_handle"> · @{{ item.assignee_handle }}</template>
              </div>
            </td>
            <td class="fb-td">
              <div class="d-flex align-center ga-2">
                <FeedbackAuthorAvatar :handle="item.author_handle" :is-agent="item.author_is_agent" :size="22" />
                <span>{{ item.author_handle }}</span>
                <!-- 私密反馈在管理端必须看得出来：它和公开的挤在同一栏里长得一样，
                     管理员就得靠读正文才发现「这条别人看不到」。 -->
                <span v-if="item.visibility === 'private'" class="chip-neutral fb-td__flag">
                  <v-icon size="12">mdi-lock-outline</v-icon>私密
                </span>
              </div>
            </td>
            <td class="fb-td">
              <!-- 来源这一格和反馈卡、详情页用的是同一个形态（中性 chip + 机器人图标）：
                   三处说的是同一件事，长得一样才不用重新认。琥珀留给这一页唯一的主操作。 -->
              <span v-if="item.author_is_agent" class="chip-neutral">
                <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
              </span>
              <span v-else class="c-muted">{{ SOURCE_LABEL.user }}</span>
            </td>
            <td class="fb-td">{{ KIND_LABEL[item.kind] }}</td>
            <td class="fb-td"><FeedbackStatusChip :priority="item.priority" /></td>
            <td class="fb-td"><FeedbackStatusChip :status="item.status" /></td>
            <td class="fb-td t-meta">{{ relTime(item.created_at) }}</td>
          </tr>
          <tr v-if="!store.adminItems.length">
            <td colspan="7" class="fb-td fb-td--empty">这一栏没有反馈</td>
          </tr>
        </tbody>
      </v-table>
    </div>

    <!-- 传 id 而不是整行：抽屉里的写操作（改状态、指派、标安全问题）服务端回的
         **是刷新后的整条详情**，本地那份行数据当场就旧了。让抽屉自己去拉详情，
         列表和抽屉就不会各持一份可能不一样的说法。 -->
    <AdminFeedbackDetailDrawer :feedback-id="selectedId" :open="drawerOpen" @update:open="close" />
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
.fb-admin__loading {
  padding: 40px 0;
  text-align: center;
  color: var(--faint);
}
.fb-td__flag {
  margin-left: 6px;
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
