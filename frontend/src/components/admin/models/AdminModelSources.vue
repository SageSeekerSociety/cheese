<script setup lang="ts">
import type { SourceRow } from '@/lib/adminModels'

import { useI18n } from 'vue-i18n'

import AdminModelPriceCell from '@/components/admin/AdminModelPriceCell.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { ORDER_MAX, ORDER_MIN, PROTOCOL_KEY } from '@/lib/adminSources'

// 一个模型的线路：网关按什么先后试哪几条上游。详情抽屉里的一段。
//
// 顺序就是网关试它们的顺序（服务端排好的：优先级小的在前、相同的一起分流、没有的
// 垫底），所以这里不再排。config.yaml 声明的线路照样列出来 —— 人要看到完整的先后才
// 知道新加的那条排在哪 —— 但只读：不给按钮，标一个「配置文件」。
//
// 「提前 / 延后」把这条线路的优先级减一 / 加一，而不是和邻居交换：优先级相同的线路是
// 一起分流的，交换会把「分流」悄悄改成「先后」。数字就画在旁边，按一下变成几看得见。
//
// 「测试」是给一致性测试套件留的位置：现在灰着并说明原因，套件接上后换成真动作。
const props = defineProps<{
  /** 这个模型的线路，服务端排好的先后。 */
  sources: SourceRow[]
  /** 有写请求在路上：所有按钮都灰，免得连点出两次写。 */
  busy?: boolean
}>()

const emit = defineEmits<{
  add: []
  edit: [source: SourceRow]
  block: [source: SourceRow]
  delete: [source: SourceRow]
  move: [source: SourceRow, order: number]
}>()

const { t } = useI18n()

function protocolText(s: SourceRow): string {
  return t(PROTOCOL_KEY[s.protocol ?? 'other'])
}

function orderText(s: SourceRow): string {
  return s.order === null ? t('models.source.orderNone') : t('models.source.order', { n: s.order })
}
</script>

<template>
  <section class="ams">
    <div class="ams__head">
      <h2 class="ams__title t-eyebrow-read">{{ t('models.source.section') }}</h2>
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-plus" :disabled="props.busy" @click="emit('add')">
        {{ t('models.source.add') }}
      </BaseButton>
    </div>

    <ol class="ams__list">
      <li v-for="s in props.sources" :key="s.source_id" class="ams__item">
        <div class="ams__line">
          <span class="ams__order t-num">{{ orderText(s) }}</span>
          <span class="ams__upstream t-num">{{ s.upstream_model }}</span>
          <span class="ams__tag">{{ protocolText(s) }}</span>
          <span v-if="s.origin === 'config'" class="ams__tag">{{ t('models.table.origin.config') }}</span>
          <span v-if="s.free" class="ams__tag ams__tag--on">{{ t('models.source.free') }}</span>
          <span v-if="s.blocked" class="ams__tag ams__tag--off">{{ t('models.table.blocked') }}</span>
        </div>
        <dl class="ams__kv t-meta-read">
          <dt>{{ t('models.detail.upstreamHost') }}</dt>
          <dd class="t-num">{{ s.host || '—' }}</dd>
          <dt>{{ t('models.source.headers') }}</dt>
          <dd class="t-num">{{ s.header_names.length ? s.header_names.join(', ') : '—' }}</dd>
          <dt>{{ t('models.source.session') }}</dt>
          <dd class="t-num">{{ s.session_header || '—' }}</dd>
        </dl>
        <AdminModelPriceCell v-if="!s.free" :priced="s.priced" :prices="s.prices" :reason="null" />

        <div class="ams__actions">
          <template v-if="s.origin === 'runtime'">
            <BaseButton
              icon="mdi-arrow-up"
              size="sm"
              :aria-label="t('models.source.action.up')"
              :disabled="props.busy || s.order === null || s.order <= ORDER_MIN"
              @click="emit('move', s, (s.order ?? ORDER_MIN) - 1)"
            />
            <BaseButton
              icon="mdi-arrow-down"
              size="sm"
              :aria-label="t('models.source.action.down')"
              :disabled="props.busy || s.order === null || s.order >= ORDER_MAX"
              @click="emit('move', s, (s.order ?? ORDER_MIN) + 1)"
            />
            <BaseButton
              icon="mdi-pencil-outline"
              size="sm"
              :aria-label="t('models.source.action.edit')"
              :disabled="props.busy"
              @click="emit('edit', s)"
            />
            <BaseButton
              :icon="s.blocked ? 'mdi-play-circle-outline' : 'mdi-cancel'"
              size="sm"
              :aria-label="s.blocked ? t('models.source.action.unblock') : t('models.source.action.block')"
              :disabled="props.busy"
              @click="emit('block', s)"
            />
            <BaseButton
              icon="mdi-trash-can-outline"
              size="sm"
              :aria-label="t('models.source.action.delete')"
              :disabled="props.busy"
              @click="emit('delete', s)"
            />
          </template>
          <span v-else class="t-meta-read ams__dim">{{ t('models.source.readonly') }}</span>
          <!-- 一致性测试套件的挂点：灰着，悬停 / 点按说明为什么。 -->
          <v-tooltip :text="t('models.source.testSoon')" location="top" open-on-click :open-delay="0">
            <template #activator="{ props: tip }">
              <span v-bind="tip" class="ams__test">
                <BaseButton icon="mdi-flask-outline" size="sm" :aria-label="t('models.source.action.test')" disabled />
              </span>
            </template>
          </v-tooltip>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.ams {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ams__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.ams__title {
  margin: 0;
  color: var(--muted);
}

.ams__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ams__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.ams__line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.ams__order {
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.ams__upstream {
  overflow: hidden;
  min-width: 0;
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ams__tag {
  display: inline-block;
  padding: 2px 8px;
  background: var(--fill);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.ams__tag--on {
  background: var(--ok-wash);
  color: var(--ok-ink);
}

.ams__tag--off {
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.ams__kv {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 2px 12px;
  margin: 0;
  color: var(--muted);
}

.ams__kv dd {
  overflow: hidden;
  margin: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ams__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
}

.ams__dim {
  color: var(--muted);
}

.ams__test {
  display: inline-flex;
}
</style>
