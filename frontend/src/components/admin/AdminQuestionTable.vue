<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import { fmtNum } from '@/lib/usageFormat'

// 「答不上来的问题」那张表 —— 它是一份**给写文档的人**的清单，不是一份用户报表。
//
// **没有、也不会有「谁问的」这一列。** 这一条是这张表的定义的一部分，不是还没加：
// 表里放的是原文问题（用户可能在里面写了任何东西），把提问者的身份和它并排放，等于
// 把「这个人在问什么」交到平台管理员手上 —— 而他需要的只是「哪一页缺什么」。所以
// 连接口都不发这个字段（后端 `features/docs_assistant._unanswered` 只选问题、页面、
// 次数三列），这里也就不可能画出来。要加一列「谁问的」时，请先问这是不是那张该做的
// 事，而不是在这里补一个字段。
//
// `page` 是**问的那一页的 slug**（`quickstart` 这种），不是 URL：它说的是「这个疑问
// 是在哪一页上产生的」，而同一页的 URL 会变。空表示那一刻没记下页面（老数据、或者
// 从搜索直接进来的），画长破折号不画空串。
defineOptions({ name: 'AdminQuestionTable' })

withDefaults(
  defineProps<{
    title: string
    /** 口径注。给了才在标题旁画 info tip。 */
    note?: string
    rows: { question: string; page: string | null; count: number }[]
    /** 一行都没有时画的那句话。 */
    empty: string
    loading?: boolean
  }>(),
  { note: undefined, loading: false }
)

const { t } = useI18n()

const shown = (page: string | null) => page || '—'
</script>

<template>
  <section class="aqt">
    <header class="aqt__head">
      <h2 class="aqt__title">{{ title }}</h2>
      <AdminNoteTip v-if="note" :text="note" />
    </header>

    <div v-if="loading" class="aqt__bones">
      <span v-for="n in 4" :key="n" class="aqt__bone" />
    </div>

    <AdminEmptyState v-else-if="rows.length === 0" :title="empty" compact />

    <table v-else class="aqt__table">
      <thead>
        <tr>
          <th scope="col" class="aqt__th aqt__th--q">{{ t('featureStats.unanswered.column.question') }}</th>
          <th scope="col" class="aqt__th aqt__th--page">{{ t('featureStats.unanswered.column.page') }}</th>
          <th scope="col" class="aqt__th aqt__th--n">{{ t('featureStats.unanswered.column.count') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in rows" :key="`${row.question}\u0000${index}`" class="aqt__row">
          <td class="aqt__cell aqt__cell--q">{{ row.question }}</td>
          <td class="aqt__cell aqt__cell--page t-num">{{ shown(row.page) }}</td>
          <td class="aqt__cell aqt__cell--n t-num">{{ fmtNum(row.count) }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.aqt {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.aqt__head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}

.aqt__title {
  margin: 0;
  color: var(--ink);
  font-size: 13.5px;
  font-weight: 600;
  line-height: var(--lh-13);
}

.aqt__table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.aqt__th {
  padding: 0 8px 6px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  text-align: left;
}

/* 问题那一列吃掉剩下的宽度（`table-layout: fixed` 下由这里定分配），右边两列定宽：
   页面 slug 最长也就十几个字符，次数最多五位数。 */
.aqt__th--q,
.aqt__cell--q {
  width: auto;
}

.aqt__th--page,
.aqt__cell--page {
  width: 140px;
}

.aqt__th--n,
.aqt__cell--n {
  width: 64px;
  text-align: right;
}

.aqt__row {
  border-top: 1px solid var(--line-2);
}

/* 问题原文**不截断**：它是这一行的全部内容，截成省略号之后这张表就只剩「有 50 条」
   这一个信息了。长问题换行，行高跟着长。 */
.aqt__cell {
  padding: 8px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  overflow-wrap: anywhere;
  vertical-align: top;
}

.aqt__cell--page {
  color: var(--muted);
  font-size: 12.5px;
}

.aqt__bones {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.aqt__bone {
  height: 12px;
  background: var(--line-2);
  border-radius: var(--radius-sm);
}
</style>
