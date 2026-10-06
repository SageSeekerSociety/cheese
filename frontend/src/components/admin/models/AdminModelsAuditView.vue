<script setup lang="ts">
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'
import type { AuditItem } from '@/lib/adminModels'

import { useI18n } from 'vue-i18n'

import AdminAuditDiff from '@/components/admin/AdminAuditDiff.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import UserRef from '@/components/common/UserRef.vue'
import { relTime } from '@/lib/relTime'

// 最近操作那一段**画的那一半**：谁改了什么。写操作是危险动作，改完要留痕、要能回看。
//
// 读失败照原话显示、并给重试；**不**显示「暂无操作」（那是把「没读到」说成「没有」）。
// 展开哪一行由调用方记（`expanded` 是按下标记的集合），这里只负责画和往上发。
//
// 审计行里那个人名用纯展示的 `UserRef`，名字与去处由外层算好当 `resolveUser` 递进来
// （见 `composables/useUserRefResolver`）—— 所以这一件没有工作区那套依赖，预览站和单测里
// 都能单独挂（接线的容器是 `AdminModelsAudit.vue`）。
defineOptions({ name: 'AdminModelsAuditView' })

const props = defineProps<{
  /** 审计记录；空数组 = 没有（或正在加载，见 `loading`）。 */
  items: AuditItem[]
  /** 这一段在加载中。骨架只在手上一条都没有时画。 */
  loading: boolean
  /** 读失败的原话。有它时压过「暂无操作」。 */
  error: string | null
  /** 展开了「查看改动」的那几行（下标）。 */
  expanded: Set<number>
  resolveUser: (handle: string | null | undefined) => ResolvedUserRef
}>()

const emit = defineEmits<{ retry: []; toggle: [index: number]; navigate: [target: ResolvedUserRef['to']] }>()

const { t } = useI18n()

/** 审计动作 → 词条。**写成字面量表**，不在模板里拼 `models.audit.action.${action}` ——
 *  拼出来的键在源码里没有一处字面量出现，`catalog.spec.ts` 会把它们判成死词条。 */
const AUDIT_ACTION_KEY: Record<string, string> = {
  'model.add': 'models.audit.action.add',
  'model.update': 'models.audit.action.update',
  'model.delete': 'models.audit.action.delete',
  'model.blocked': 'models.audit.action.blocked',
  'project.budget': 'models.audit.action.budget',
  'subscription.start': 'models.audit.action.subscriptionStart',
  'subscription.complete': 'models.audit.action.subscriptionComplete',
  'subscription.cancel': 'models.audit.action.subscriptionCancel',
  'subscription.refresh': 'models.audit.action.subscriptionRefresh',
  'subscription.revoke': 'models.audit.action.subscriptionRevoke',
  'subscription.update_upstream': 'models.audit.action.subscriptionUpdateUpstream',
}

function auditActionLabel(action: string): string {
  return t(AUDIT_ACTION_KEY[action] ?? 'models.audit.action.other')
}

function go(handle: string | null | undefined) {
  return props.resolveUser(handle)
}
</script>

<template>
  <div class="amd__audit">
    <!-- 读失败给中性标题、原话落到说明行、并给重试；**不**显示「暂无操作」（那是把
     「没读到」说成「没有」）。它说在这一段自己的卡里，而不是页顶那条横条上。
     `!== null`：`error` 是 `null` 才算没失败，空串是「失败了但服务端没给话」——
     用真值判会把这种失败落进「暂无操作」。 -->
    <BaseLoadError
      v-if="props.error !== null"
      :title="t('models.audit.loadFailed')"
      :error="props.error || undefined"
      :retry-label="t('models.page.retry')"
      @retry="emit('retry')"
    />
    <div v-else-if="props.loading && !props.items.length" class="amd__auditSkeleton">
      <v-skeleton-loader v-for="i in 4" :key="i" type="text" />
    </div>
    <p v-else-if="!props.items.length" class="amd__auditEmpty t-meta-read">{{ t('models.audit.empty') }}</p>
    <ol v-else class="amd__auditRows">
      <li v-for="(item, i) in props.items" :key="i" class="amd__auditRow">
        <div class="amd__auditLine">
          <span class="amd__auditTime t-meta-read t-num">{{ relTime(item.created_at) }}</span>
          <span class="amd__auditWho t-body"
            ><UserRef
              :handle="item.actor_handle"
              :name="go(item.actor_handle).name"
              :to="go(item.actor_handle).to"
              @navigate="$emit('navigate', go(item.actor_handle).to)"
          /></span>
          <span class="amd__auditWhat t-body">
            {{ auditActionLabel(item.action) }}
            <span class="amd__auditTarget t-num">{{ item.target }}</span>
          </span>
          <span class="t-meta-read" :class="item.result === 'ok' ? 'amd__ok' : 'amd__fail'">
            {{ item.result === 'ok' ? t('models.audit.result.ok') : t('models.audit.result.failed') }}
          </span>
          <span v-if="item.detail" class="amd__auditDetail t-meta-read" :title="item.detail">{{ item.detail }}</span>
          <!-- 「查看改动」只在有快照可 diff 时出现：before/after 都为空的那几项
           操作没有字段变化可看，按钮摆在那儿只会点出一句「没有变化」。 -->
          <button
            v-if="item.before || item.after"
            type="button"
            class="amd__textbtn amd__auditDiffBtn"
            :aria-expanded="props.expanded.has(i)"
            @click="emit('toggle', i)"
          >
            {{ props.expanded.has(i) ? t('models.audit.diff.hide') : t('models.audit.diff.show') }}
          </button>
        </div>
        <AdminAuditDiff
          v-if="props.expanded.has(i) && (item.before || item.after)"
          :before="item.before"
          :after="item.after"
        />
      </li>
    </ol>
  </div>
</template>

<style scoped>
.amd__audit {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.amd__auditSkeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
}

.amd__auditEmpty {
  margin: 0;
  padding: 20px 16px;
  color: var(--muted);
}

.amd__auditRows {
  margin: 0;
  padding: 0;
  list-style: none;
}

.amd__auditRow {
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
}

.amd__auditRow:last-child {
  border-bottom: 0;
}

.amd__auditLine {
  display: grid;
  grid-template-columns: 88px 140px minmax(0, 1fr) 56px minmax(0, 1.2fr) auto;
  gap: 12px;
  align-items: baseline;
}

.amd__auditTime {
  color: var(--muted);
}

.amd__auditWho {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__auditWhat {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__auditTarget {
  color: var(--muted);
}

.amd__auditDetail {
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__auditDiffBtn {
  justify-self: end;
}

/* 文本按钮（审计行的「查看改动」）：看起来像一格文字，行为是一个按钮 ——
   hover 只变色不移位。 */
.amd__textbtn {
  padding: 0;
  background: transparent;
  border: 0;
  color: var(--accent-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
  cursor: pointer;
}

@media (hover: hover) and (pointer: fine) {
  .amd__textbtn:hover {
    color: var(--accent-press);
  }
}

.amd__ok {
  color: var(--ok-ink);
}

.amd__fail {
  color: var(--danger-ink);
}

/* 内容列窄于 900（容器查询挂在后台内容列上，§3.5，不是视口）：审计行收成三列，
   把「改的是什么」那一格让给正文。操作人和时间都还在
   （它们是这一行「谁改了什么」的一半），只让说明那一格换行到下面。 */
@container admin (max-width: 900px) {
  .amd__auditLine {
    grid-template-columns: 64px 92px minmax(0, 1fr) 48px auto;
    gap: 8px;
  }

  .amd__auditDetail {
    grid-column: 3 / -1;
  }
}

/* 内容列窄于 700（容器查询，§3.5）：网格换成折行的 flex。五列到了 390px 上，「改的是什么」那一格只剩
   六十来像素 —— 而它是这一行的正文。让它独占一行，时间 / 谁 / 结果挤在上面那一行，
   「谁在什么时候改了什么」还是按那个顺序读。 */
@container admin (max-width: 700px) {
  .amd__auditLine {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 8px;
    align-items: baseline;
  }

  .amd__auditWhat {
    flex: 1 1 100%;
    white-space: normal;
  }

  .amd__auditDetail {
    min-width: 0;
  }

  .amd__auditDiffBtn {
    margin-left: auto;
  }
}
</style>
