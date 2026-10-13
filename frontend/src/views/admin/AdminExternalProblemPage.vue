<script setup lang="ts">
import type { ExternalProblemAccount } from '@/api/externalProblem'

import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useSaveState } from '@/composables/useSaveState'

import AdminExternalProblemPageView from './AdminExternalProblemPageView.vue'

import { getExternalProblemAccount, saveExternalProblemAccount } from '@/api/externalProblem'
import { relTime } from '@/lib/relTime'

// 管理后台的「外站题账号」（`/admin/integrations/external-problem`）：平台去外站题
// （今天只有 YOJ）读成绩用的一个账号。取数与写在这一半，画法在
// `AdminExternalProblemPageView.vue`。
//
// 外站题的成绩只有那边的某个账号看得到，所以平台替整个组织拿一个账号去读；这一页就是
// 填那一个账号的地方 —— 和飞书应用一样，一次性、平台级，不属于任何一个项目。
//
// 三件事决定了这一页的形状：
//
// 1. **密码只写不回显。** 服务端读回来的结构里根本没有它（`ExternalProblemAccount`），
//    所以输入框永远是空的，空着提交表示「不改已经存下的那一个」—— 只改账号不必先把密码
//    找回来。密码框给 `autocomplete="new-password"`：让浏览器别把这一格当成登录表单的
//    一部分去自动填充。
// 2. **有没有配过是页面上第一个问题。** 没配过时外站题那边读不了数，所以这里的状态行说的
//    正是那句话的答案。
// 3. **读不到就只说读不到。** 那个答案是读回来才有的；读失败时我们并没有它，所以这时不画
//    状态行、也不画表单，只画「读不到」加重试 —— 画上「还没配置」等于拿一个不知道的答案
//    当知道的用。
defineOptions({ name: 'AdminExternalProblemPage' })

const { t } = useI18n()

const loading = ref(false)
/** 读这一页失败时是**服务端原话**（原话取不到就空串）；`null` 表示没失败。
 *  它说的是「这一页没读到」，和保存失败是两件事。失败与否和原话也是两件事，得分开存：
 *  原话为空时仍要给出错态，不能因为取不到原因就当成没读到、落进正常表单。 */
const loadError = ref<string | null>(null)
/** 表单没填全（账号为空）—— 这是填写本身的问题，还没发请求。 */
const validationError = ref('')
/** 保存失败。就地回执，表单里的东西一个字不动。 */
const {
  saving,
  saved,
  error: saveError,
  run,
} = useSaveState({
  feedback: 'inline',
  messages: { failed: t('integrations.admin.externalProblem.saveFailed') },
})
const account = ref<ExternalProblemAccount | null>(null)

const form = reactive({ username: '', password: '' })

const status = computed(() =>
  account.value?.configured
    ? `${t('integrations.admin.externalProblem.configured')} · ${account.value.username} · ${account.value.source}`
    : t('integrations.admin.externalProblem.notConfigured')
)
const updated = computed(() => {
  if (!account.value?.updated_at || !account.value.updated_by) return ''
  return `${t('integrations.admin.externalProblem.updatedBy', { handle: account.value.updated_by })} · ${relTime(account.value.updated_at)}`
})
/** 首屏还没读回来时不画状态行：这时候还没有答案。 */
const showStatus = computed(() => account.value !== null || !loading.value)
/** 有没有配过 —— 状态行那个 `data-configured` 说的就是它。 */
const configured = computed(() => account.value?.configured ?? false)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const current = await getExternalProblemAccount()
    account.value = current
    form.username = current.username
    // 密码不回显，所以这一格永远是空的 —— 它的空表示「不改」，见文件开头第 1 条。
    form.password = ''
  } catch (e) {
    // 原话存下来作说明行，不用「无法读取」这种固定话把原因吞掉。
    loadError.value = e instanceof Error && e.message ? e.message : ''
    account.value = null
  } finally {
    loading.value = false
  }
}

async function save() {
  validationError.value = ''
  if (!form.username.trim()) {
    validationError.value = t('integrations.admin.externalProblem.usernameRequired')
    return
  }
  await run(async () => {
    account.value = await saveExternalProblemAccount({
      username: form.username.trim(),
      password: form.password,
    })
    form.password = ''
  })
}

onMounted(load)
</script>

<template>
  <AdminExternalProblemPageView
    v-model:username="form.username"
    v-model:password="form.password"
    :loading="loading"
    :load-error="loadError"
    :status="status"
    :show-status="showStatus"
    :configured="configured"
    :updated="updated"
    :saving="saving"
    :saved="saved"
    :save-error="saveError"
    :validation-error="validationError"
    @retry="load"
    @save="save"
  />
</template>
