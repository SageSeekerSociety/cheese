<script setup lang="ts">
// 「更多设置」：可领取范围、实名、AI 指导（调用方塞进来的那一格）。
//
// 多数题目一样都不用改，所以默认收起；收起时那一行写明现在设成了什么，不用点开就看得
// 见。它只画：值都是 props 进来的，改一次往上报一次。
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseField from '@/components/base/BaseField.vue'

const props = defineProps<{
  /** 邮箱域名组的选项（外面从 `domainGroups` 映射好，带一句域名清单当副标题）。 */
  domainGroupItems: { title: string; value: number; subtitle: string }[]
  /** 这道题有没有单独写 AI 指导；收起时那一行要说。 */
  teachingCustom: boolean
}>()

defineSlots<{
  /** AI 指导那一格。 */
  teaching?: () => unknown
}>()

const accessControlEnabled = defineModel<boolean | undefined>('accessControlEnabled', { required: true })
const accessDomainGroupIds = defineModel<number[] | undefined>('accessDomainGroupIds', { required: true })
const requireRealName = defineModel<boolean | undefined>('requireRealName', { required: true })

const { t } = useI18n()

const open = ref(false)

const access = computed({
  get: () => (accessControlEnabled.value ? 'domains' : 'all'),
  set: (value: string) => {
    accessControlEnabled.value = value === 'domains'
  },
})

const summary = computed(() => {
  const names = props.domainGroupItems
    .filter((item) => accessDomainGroupIds.value?.includes(item.value))
    .map((item) => item.title)
  return [
    accessControlEnabled.value && names.length
      ? t('tasks.form.more.summaryDomains', { names: names.join('、') })
      : t('tasks.form.more.summaryAll'),
    requireRealName.value ? t('tasks.form.more.summaryRealName') : t('tasks.form.more.summaryNoRealName'),
    props.teachingCustom ? t('tasks.form.more.summaryTeachingOwn') : t('tasks.form.more.summaryTeachingDefault'),
  ].join(' · ')
})
</script>

<template>
  <section class="tfm">
    <button type="button" class="tfm__toggle" :aria-expanded="open" data-testid="task-form-more" @click="open = !open">
      <v-icon size="18" class="tfm__chevron" :class="{ 'tfm__chevron--open': open }">mdi-chevron-right</v-icon>
      <span class="tfm__title t-title">{{ t('tasks.form.more.title') }}</span>
      <span v-if="!open" class="tfm__summary t-meta-read">{{ summary }}</span>
    </button>

    <div v-if="open" class="tfm__body">
      <div class="tfm__row">
        <BaseField :label="t('tasks.form.more.access')">
          <v-radio-group v-model="access" hide-details density="compact" class="tf-choices">
            <v-radio value="all" :label="t('tasks.form.more.accessAll')" />
            <v-radio value="domains" :label="t('tasks.form.more.accessDomains')" />
          </v-radio-group>
          <template v-if="accessControlEnabled">
            <v-select
              v-if="domainGroupItems.length > 0"
              v-model="accessDomainGroupIds"
              autocomplete="off"
              :items="domainGroupItems"
              :aria-label="t('tasks.form.more.accessDomains')"
              item-title="title"
              item-value="value"
              variant="outlined"
              density="comfortable"
              multiple
              chips
              closable-chips
              hide-details
              class="tfm__groups"
            >
              <template #item="{ item, props: itemProps }">
                <v-list-item v-bind="itemProps" :subtitle="item.raw?.subtitle" />
              </template>
            </v-select>
            <BaseEmptyState v-else size="inline" class="tfm__empty" :title="t('tasks.form.more.noDomainGroups')" />
          </template>
        </BaseField>
      </div>

      <div class="tfm__row">
        <v-switch
          v-model="requireRealName"
          color="primary"
          density="compact"
          hide-details
          inset
          data-testid="task-form-real-name"
        >
          <template #label>
            <span>
              <span class="tfm__label">{{ t('tasks.form.more.realName') }}</span>
              <span class="tf-choice-hint">{{ t('tasks.form.more.realNameHint') }}</span>
            </span>
          </template>
        </v-switch>
      </div>

      <div v-if="$slots.teaching" class="tfm__row">
        <slot name="teaching" />
      </div>
    </div>
  </section>
</template>

<style scoped src="./task-form.css"></style>
<style scoped>
.tfm {
  border-bottom: 1px solid var(--line);
}

.tfm__toggle {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  padding: 20px 0;
  border: 0;
  background: none;
  color: var(--ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.tfm__chevron {
  color: var(--muted);
  transition: transform var(--dur-quick) var(--ease-standard);
}

.tfm__chevron--open {
  transform: rotate(90deg);
}

.tfm__summary {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tfm__body {
  padding-bottom: 8px;
}

.tfm__row {
  padding: 16px 0;
  border-top: 1px solid var(--line);
}

.tfm__groups {
  margin-top: 8px;
}

.tfm__empty {
  margin: 8px 0 0;
}

.tfm__label {
  display: block;
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
}
</style>
