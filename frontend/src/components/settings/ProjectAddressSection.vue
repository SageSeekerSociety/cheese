<script setup lang="ts">
// 项目地址：链接里的那个短名（`/projects/<短名>/tasks/318`）。只有项目的管理者看得到
// 这一块，后端也只认他们。改名之后旧链接照样打开这个项目，旧短名不会给别的项目用，
// 所以这里不用再问一句「确定吗」。
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { setProjectSlug } from '@/api/addresses'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { rememberProject } from '@/lib/addresses'

defineOptions({ name: 'ProjectAddressSection' })

const props = defineProps<{ projectId: string; slug: string }>()
const emit = defineEmits<{ renamed: [slug: string] }>()

const router = useRouter()
const draft = ref(props.slug)
const saving = ref(false)
const error = ref('')
watch(
  () => props.slug,
  (slug) => (draft.value = slug)
)

const link = computed(() => `${window.location.host}/projects/${draft.value.trim().toLowerCase() || props.slug}`)
const unchanged = computed(() => draft.value.trim().toLowerCase() === props.slug)

async function save() {
  if (unchanged.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const { slug } = await setProjectSlug(props.projectId, draft.value.trim())
    rememberProject(props.projectId, slug)
    emit('renamed', slug)
    // 地址栏里还是旧短名：换成新的，刷新、复制都是新地址。
    const here = router.currentRoute.value
    void router.replace({
      name: here.name ?? undefined,
      params: { ...here.params, projectId: slug },
      query: here.query,
    })
    toast(t('work.projectSettings.address.saved'))
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.projectSettings.address.failed')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-link-variant</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.address.title') }}</span>
    </div>
    <div class="page-section-body">
      <div class="d-flex align-center" style="gap: 8px">
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
        <BaseButton kind="primary" size="sm" :loading="saving" :disabled="unchanged" @click="save">
          {{ t('work.projectSettings.address.save') }}
        </BaseButton>
      </div>
      <p class="t-body c-muted mt-2 address-link">{{ link }}</p>
      <p class="t-body c-faint mt-1 settings-hint">{{ t('work.projectSettings.address.hint') }}</p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
<style scoped>
.address-link {
  overflow-wrap: anywhere;
}
</style>
