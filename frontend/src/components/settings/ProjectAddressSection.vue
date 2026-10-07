<script setup lang="ts">
// 项目地址：链接里的那个短名（`/projects/<短名>/tasks/318`）。只有项目的管理者看得到
// 这一块，后端也只认他们。改名之后旧链接照样打开这个项目，旧短名不会给别的项目用，
// 所以这里不用再问一句「确定吗」。保存由页面去做（`views/ProjectSettingsView.vue`）。
import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineOptions({ name: 'ProjectAddressSection' })

const props = defineProps<{
  /** 现在的短名。 */
  slug: string
  /** 链接里短名前面那一段，如 `okcheese.com/projects/`。 */
  prefix: string
  saving: boolean
  /** 上一次保存为什么没成。 */
  error: string
}>()
const emit = defineEmits<{ save: [slug: string] }>()

const draft = ref(props.slug)
watch(
  () => props.slug,
  (slug) => (draft.value = slug)
)

const typed = computed(() => draft.value.trim().toLowerCase())
const unchanged = computed(() => typed.value === props.slug)

function save() {
  if (!unchanged.value && !props.saving) emit('save', typed.value)
}
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-link-variant</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.address.title') }}</span>
    </div>
    <div class="page-section-body">
      <!-- 顶对齐：输入框下面冒出错误提示时，按钮留在输入框旁边。 -->
      <div class="d-flex align-start" style="gap: 8px">
        <v-text-field
          v-model="draft"
          :label="t('work.projectSettings.address.label')"
          autocomplete="off"
          spellcheck="false"
          density="compact"
          variant="outlined"
          :error-messages="error ? [error] : []"
          hide-details="auto"
          maxlength="32"
          style="flex: 1"
          @keydown.enter="save"
        />
        <BaseButton class="address-save" kind="primary" size="sm" :loading="saving" :disabled="unchanged" @click="save">
          {{ t('work.projectSettings.address.save') }}
        </BaseButton>
      </div>
      <p class="t-body c-muted mt-2 address-link">{{ prefix }}{{ typed || slug }}</p>
      <p class="t-body c-faint mt-1 settings-hint">{{ t('work.projectSettings.address.hint') }}</p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
<style scoped>
/* 小号按钮比紧凑输入框矮 8px：上下各让一半，和输入框居中对齐。 */
.address-save {
  margin-top: 4px;
}
.address-link {
  overflow-wrap: anywhere;
}
</style>
