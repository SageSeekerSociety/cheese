<script setup lang="ts">
// 输入框旁边那颗「带选项提问」打开的框：写一个问题和两到四个选项。发出去的路由房间
// 交进来（`post`），发成了才关；没发成框还开着，写下的字都在，原因写在框里。
import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '../common/AdaptiveDialog.vue'

import { t } from '@/i18n'

const MIN_OPTIONS = 2
const MAX_OPTIONS = 4

const open = defineModel<boolean>({ default: false })

const props = defineProps<{
  /** 发出去；没发成就抛错。 */
  post: (question: string, options: string[]) => Promise<void>
}>()

const question = ref('')
const options = ref<string[]>([])
const sending = ref(false)
const error = ref<string | null>(null)

watch(
  open,
  (shown) => {
    if (!shown) return
    question.value = ''
    options.value = Array.from({ length: MIN_OPTIONS }, () => '')
    error.value = null
  },
  { immediate: true }
)

const filled = computed(() => options.value.map((o) => o.trim()))

/** 和前面某一个选项写得一样：点到哪一个都分不出来，所以这一格要改。 */
function repeats(index: number): boolean {
  const text = filled.value[index]
  return !!text && filled.value.indexOf(text) < index
}

const ready = computed(
  () => !!question.value.trim() && filled.value.every((o) => !!o) && filled.value.every((_, i) => !repeats(i))
)

function addOption() {
  if (options.value.length < MAX_OPTIONS) options.value.push('')
}

function removeOption(index: number) {
  if (options.value.length > MIN_OPTIONS) options.value.splice(index, 1)
}

async function send() {
  if (!ready.value || sending.value) return
  sending.value = true
  error.value = null
  try {
    await props.post(question.value.trim(), filled.value)
    open.value = false
  } catch (e) {
    error.value = e instanceof Error && e.message ? e.message : t('work.room.ask.failed')
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.room.ask.title')"
    :primary-label="t('work.room.ask.send')"
    primary-icon="mdi-send"
    :primary-loading="sending"
    :primary-disabled="!ready"
    :close-disabled="sending"
    @primary="send"
  >
    <v-text-field
      v-model="question"
      class="pt-2"
      :label="t('work.room.ask.question')"
      variant="outlined"
      density="comfortable"
      autofocus
      autocomplete="off"
    />
    <v-text-field
      v-for="(_, i) in options"
      :key="i"
      v-model="options[i]"
      :label="t('work.room.ask.option', { n: i + 1 })"
      :error-messages="repeats(i) ? t('work.room.ask.repeated') : undefined"
      variant="outlined"
      density="comfortable"
      autocomplete="off"
    >
      <template v-if="options.length > MIN_OPTIONS" #append-inner>
        <v-btn
          icon="mdi-close"
          variant="text"
          size="x-small"
          color="medium-emphasis"
          :title="t('work.room.ask.removeOption')"
          :aria-label="t('work.room.ask.removeOption')"
          @click="removeOption(i)"
        />
      </template>
    </v-text-field>
    <v-btn v-if="options.length < MAX_OPTIONS" variant="text" size="small" prepend-icon="mdi-plus" @click="addOption">
      {{ t('work.room.ask.addOption') }}
    </v-btn>
    <v-alert v-if="error" type="error" density="comfortable" class="mt-4">
      {{ error }}
    </v-alert>
  </AdaptiveDialog>
</template>
