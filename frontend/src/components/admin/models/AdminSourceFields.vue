<script setup lang="ts">
import type { HeaderDraft, SourceDraft } from '@/lib/adminSources'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { draftProblems, headerNameProblem, headerValueOk, PROTOCOL_CHOICES, PROTOCOL_KEY } from '@/lib/adminSources'

// 一条线路的那组字段：协议、上游模型、地址、密钥、优先级、免费、单价、请求头、会话头。
// 新建模型（第一条线路）和添加 / 编辑线路两个框都用它，所以它只收一份草稿、只往上发
// 改过的草稿 —— 什么时候提交、提交给谁，是外面那个框的事。
//
// 编辑时三样东西服务端不下发：上游密钥、上游地址的全文、请求头的值。前两样留空即
// 「不动」；请求头要显式点「重新设置」才进入编辑，进入之后整份替换（一行不留就是清空）。
const draft = defineModel<SourceDraft>({ required: true })

defineProps<{
  /** 编辑一条已有线路：密钥与地址的提示换成「留空沿用」。 */
  editing?: boolean
}>()

const { t } = useI18n()

const problems = computed(() => draftProblems(draft.value))

const protocolItems = computed(() => PROTOCOL_CHOICES.map((value) => ({ value, title: t(PROTOCOL_KEY[value]) })))

function patch(next: Partial<SourceDraft>) {
  draft.value = { ...draft.value, ...next }
}

function patchPrice(key: keyof SourceDraft['prices'], value: string) {
  patch({ prices: { ...draft.value.prices, [key]: value } })
}

function setHeader(index: number, next: Partial<HeaderDraft>) {
  patch({ headers: draft.value.headers.map((h, i) => (i === index ? { ...h, ...next } : h)) })
}

function addHeader() {
  patch({ headers: [...draft.value.headers, { name: '', value: '' }] })
}

function removeHeader(index: number) {
  patch({ headers: draft.value.headers.filter((_, i) => i !== index) })
}

function headerRowBad(h: HeaderDraft): boolean {
  return h.name.trim() !== '' && (headerNameProblem(h.name) !== null || !headerValueOk(h.value))
}
</script>

<template>
  <div class="asf">
    <div class="asf__row">
      <v-select
        autocomplete="off"
        :model-value="draft.protocol"
        :items="protocolItems"
        item-title="title"
        item-value="value"
        variant="outlined"
        density="comfortable"
        :label="t('models.source.field.protocol')"
        hide-details
        @update:model-value="patch({ protocol: $event })"
      />
      <v-text-field
        :model-value="draft.order"
        autocomplete="off"
        type="number"
        variant="outlined"
        density="comfortable"
        :label="t('models.source.field.order')"
        :error="problems.order"
        hide-details
        @update:model-value="patch({ order: $event })"
      />
    </div>
    <p class="asf__hint t-meta-read">{{ t('models.source.field.orderHint') }}</p>

    <v-text-field
      :model-value="draft.upstream"
      autocomplete="off"
      variant="outlined"
      density="comfortable"
      :label="t('models.dialog.field.upstreamModel')"
      :hint="
        draft.protocol === 'other' ? t('models.dialog.field.upstreamModelHint') : t('models.source.field.upstreamHint')
      "
      persistent-hint
      hide-details="auto"
      @update:model-value="patch({ upstream: $event })"
    />
    <v-text-field
      :model-value="draft.apiBase"
      autocomplete="off"
      variant="outlined"
      density="comfortable"
      :label="t('models.dialog.field.apiBase')"
      :hint="editing ? t('models.source.field.apiBaseKeep') : t('models.dialog.field.apiBaseHint')"
      :error="problems.apiBase"
      hide-details="auto"
      @update:model-value="patch({ apiBase: $event })"
    />
    <v-text-field
      :model-value="draft.apiKey"
      autocomplete="off"
      type="password"
      variant="outlined"
      density="comfortable"
      :label="t('models.dialog.field.apiKey')"
      :hint="editing ? t('models.dialog.field.apiKeyKeep') : t('models.dialog.field.apiKeyHint')"
      hide-details="auto"
      @update:model-value="patch({ apiKey: $event })"
    />

    <div class="asf__switch">
      <v-switch
        :model-value="draft.free"
        color="primary"
        density="compact"
        :label="t('models.source.field.free')"
        hide-details
        @update:model-value="patch({ free: !!$event })"
      />
      <p class="asf__hint t-meta-read">{{ t('models.source.field.freeHint') }}</p>
    </div>

    <p class="asf__group t-eyebrow-read">{{ t('models.dialog.group.prices') }}</p>
    <p class="asf__hint t-meta-read">{{ t('models.dialog.priceUnit') }}</p>
    <div class="asf__prices">
      <v-text-field
        :model-value="draft.free ? '0' : draft.prices.input"
        autocomplete="off"
        type="number"
        variant="outlined"
        density="comfortable"
        :label="t('models.price.input')"
        :disabled="draft.free"
        hide-details
        @update:model-value="patchPrice('input', $event)"
      />
      <v-text-field
        :model-value="draft.free ? '0' : draft.prices.output"
        autocomplete="off"
        type="number"
        variant="outlined"
        density="comfortable"
        :label="t('models.price.output')"
        :disabled="draft.free"
        hide-details
        @update:model-value="patchPrice('output', $event)"
      />
      <v-text-field
        :model-value="draft.prices.cacheRead"
        autocomplete="off"
        type="number"
        variant="outlined"
        density="comfortable"
        :label="t('models.price.cacheRead')"
        hide-details
        @update:model-value="patchPrice('cacheRead', $event)"
      />
      <v-text-field
        :model-value="draft.prices.cacheCreation"
        autocomplete="off"
        type="number"
        variant="outlined"
        density="comfortable"
        :label="t('models.price.cacheCreation')"
        hide-details
        @update:model-value="patchPrice('cacheCreation', $event)"
      />
    </div>
    <p v-if="problems.free" class="asf__hint asf__hint--bad t-meta-read">
      {{ t('models.source.field.zeroNeedsFree') }}
    </p>

    <p class="asf__group t-eyebrow-read">{{ t('models.source.field.headers') }}</p>
    <template v-if="!draft.headersTouched">
      <p class="asf__hint t-meta-read">
        {{
          draft.existingHeaderNames.length
            ? t('models.source.field.existingHeaders', { names: draft.existingHeaderNames.join(', ') })
            : t('models.source.field.noHeaders')
        }}
      </p>
      <div>
        <BaseButton kind="secondary" size="sm" @click="patch({ headersTouched: true })">
          {{ t('models.source.field.editHeaders') }}
        </BaseButton>
      </div>
    </template>
    <template v-else>
      <div v-for="(h, i) in draft.headers" :key="i" class="asf__header">
        <v-text-field
          :model-value="h.name"
          autocomplete="off"
          variant="outlined"
          density="comfortable"
          :label="t('models.source.field.headerName')"
          :error="headerRowBad(h)"
          hide-details
          @update:model-value="setHeader(i, { name: $event })"
        />
        <v-text-field
          :model-value="h.value"
          autocomplete="off"
          variant="outlined"
          density="comfortable"
          :label="t('models.source.field.headerValue')"
          :error="!headerValueOk(h.value)"
          hide-details
          @update:model-value="setHeader(i, { value: $event })"
        />
        <BaseButton
          icon="mdi-close"
          size="sm"
          :aria-label="t('models.source.field.removeHeader')"
          @click="removeHeader(i)"
        />
      </div>
      <p v-if="problems.headers" class="asf__hint asf__hint--bad t-meta-read">
        {{ t('models.source.field.headerInvalid') }}
      </p>
      <div>
        <BaseButton kind="secondary" size="sm" prepend-icon="mdi-plus" @click="addHeader">
          {{ t('models.source.field.addHeader') }}
        </BaseButton>
      </div>
    </template>

    <v-text-field
      :model-value="draft.sessionHeader"
      autocomplete="off"
      variant="outlined"
      density="comfortable"
      :label="t('models.source.field.session')"
      :hint="t('models.source.field.sessionHint')"
      :error="problems.sessionHeader"
      persistent-hint
      hide-details="auto"
      @update:model-value="patch({ sessionHeader: $event })"
    />
  </div>
</template>

<style scoped>
/* 字段之间的缝由这里给（同 `AdminModelFormDialog`）：outlined 字段的浮动标签有一半悬在
   框的上沿之上，不给 gap 下一条的标签就压在上一条的下边框上。 */
.asf {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.asf__row {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 8px;
}

.asf__switch {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.asf__group {
  margin-top: 8px;
  color: var(--muted);
}

.asf__hint {
  margin: 0;
  color: var(--muted);
}

.asf__hint--bad {
  color: var(--danger-ink);
}

.asf__prices {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.asf__header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}
</style>
