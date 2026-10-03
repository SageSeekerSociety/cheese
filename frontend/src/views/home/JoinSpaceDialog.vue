<script setup lang="ts">
// 用邀请码加入空间。加入之后直接进那个空间：刚加入的空间里通常还没有他的东西，
// 留在原地等于什么都没发生。
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { spaceEntryRoute } from '@/lib/spaceEntry'
import { SpacesApi } from '@/network/api/spaces'

const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ joined: [] }>()

const router = useRouter()
const code = ref('')
const error = ref('')
const joining = ref(false)

watch(open, (value) => {
  if (!value) {
    code.value = ''
    error.value = ''
  }
})

async function submit() {
  const value = code.value.trim()
  if (!value || joining.value) return
  joining.value = true
  error.value = ''
  try {
    const { data } = await SpacesApi.join({ code: value })
    open.value = false
    emit('joined')
    await router.push(spaceEntryRoute(data.space))
  } catch {
    error.value = t('work.joinFailed')
  } finally {
    joining.value = false
  }
}
</script>

<template>
  <v-dialog v-model="open" max-width="440">
    <v-card rounded="lg">
      <v-card-title class="t-dialog-title">{{ t('work.joinTitle') }}</v-card-title>
      <v-card-text>
        <p class="t-body c-muted mb-3">{{ t('work.joinBody') }}</p>
        <v-text-field
          v-model="code"
          :label="t('work.joinLabel')"
          :error-messages="error"
          autocomplete="off"
          autofocus
          hide-details="auto"
          @keyup.enter="submit"
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <BaseButton @click="open = false">{{ t('work.joinCancel') }}</BaseButton>
        <BaseButton kind="primary" :loading="joining" @click="submit">{{ t('work.joinSubmit') }}</BaseButton>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
