<script setup lang="ts">
import type { PlatformFeishuApp } from '@/api/feishu'

import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useSaveState } from '@/composables/useSaveState'

import { getPlatformFeishuApp, savePlatformFeishuApp } from '@/api/feishu'
import { relTime } from '@/lib/relTime'
import AdminIntegrationsPageView from '@/views/admin/AdminIntegrationsPageView.vue'

// 管理后台的「飞书应用」（`/admin/integrations`）：平台**唯一**一处飞书应用凭据。
//
// 成员以前各自去飞书建一个企业自建应用，再把 App ID / Secret 填进「我的连接」——
// 每个成员一次，做的是同一件事。应用属于组织：管理员在飞书建一个自建应用、开好文档
// 权限、发布并通过审核，然后在这一页把凭据填一次；成员那边点一下「连接飞书」即可。
//
// 三件事决定了这一页的形状：
//
// 1. **Secret 只写不回显。** 服务端读回来的结构里根本没有它（`feishu_app_view`），
//    所以输入框永远是空的，空着提交表示「不改已经存下的那一个」—— 改域名或 App ID
//    不必先把口令找回来。密码框给 `autocomplete="new-password"`：让浏览器别把这一格
//    当成登录表单的一部分去自动填充。
// 2. **有没有配过是页面上第一个问题。** 没配过时成员那边的按钮是灰的（一句「管理员
//    还没配置飞书应用」），所以这里的状态行说的正是那句话的答案。
// 3. **保存失败说服务端原话，表单不关。** 一句「操作失败」会把这页最需要的东西 ——
//    为什么失败 —— 丢掉。
// 4. **读不到就只说读不到。** 第 2 条那个答案是给成员看的；读失败时我们并没有它，
//    所以这时不画状态行、也不画表单，只画「读不到」加重试 —— 画上「还没配置」等于
//    拿一个不知道的答案当知道的用。
//
// 这一只只做**取数、写、校验和保存状态机**；画面在同目录的 `AdminIntegrationsPageView.vue`
// 里，只吃 props、只往上发事件（`pnpm run lint:scenes` 把它当容器看，冻结的是那个视图）。
defineOptions({ name: 'AdminIntegrationsPage' })

const { t } = useI18n()

const loading = ref(false)
/** 读这一页失败时是**服务端原话**（原话取不到就空串）；`null` 表示没失败。
 *  它说的是「这一页没读到」，和保存失败是两件事。失败与否和原话是两件事，也得分开存：
 *  原话为空时仍要给出错态，不能因为取不到原因就当成没读到、落进正常表单。 */
const loadError = ref<string | null>(null)
/** 表单没填全（App ID 为空）—— 这是填写本身的问题，还没发请求。 */
const validationError = ref('')
/** 保存失败。就地回执，表单里的东西一个字不动。 */
const {
  saving,
  saved,
  error: saveError,
  run,
} = useSaveState({
  feedback: 'inline',
  messages: { failed: t('integrations.admin.saveFailed') },
})
const app = ref<PlatformFeishuApp | null>(null)

const form = reactive({ app_id: '', app_secret: '', domain: 'feishu' })

const domains = computed(() => [
  { value: 'feishu', title: t('admin.integrations.domainFeishu') },
  { value: 'lark', title: 'Lark（larksuite.com）' },
])

const status = computed(() =>
  app.value?.configured
    ? `${t('integrations.admin.configured')} · ${app.value.app_id} · ${app.value.domain}`
    : t('integrations.admin.notConfigured')
)
const updated = computed(() => {
  if (!app.value?.updated_at || !app.value.updated_by) return ''
  return `${t('integrations.admin.updatedBy', { handle: app.value.updated_by })} · ${relTime(app.value.updated_at)}`
})
/** 首屏还没读回来时不画状态行：这时候还没有答案。 */
const showStatus = computed(() => Boolean(app.value) || !loading.value)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const current = await getPlatformFeishuApp()
    app.value = current
    form.app_id = current.app_id
    form.domain = current.domain || 'feishu'
    // Secret 不回显，所以这一格永远是空的 —— 它的空表示「不改」，见文件开头第 1 条。
    form.app_secret = ''
  } catch (e) {
    // 原话存下来作说明行，不用「无法读取」这种固定话把原因吞掉。
    loadError.value = e instanceof Error && e.message ? e.message : ''
    app.value = null
  } finally {
    loading.value = false
  }
}

async function save() {
  validationError.value = ''
  if (!form.app_id.trim()) {
    validationError.value = t('integrations.admin.appIdRequired')
    return
  }
  await run(async () => {
    app.value = await savePlatformFeishuApp({
      app_id: form.app_id.trim(),
      app_secret: form.app_secret,
      domain: form.domain,
    })
    form.app_secret = ''
  })
}

onMounted(load)
</script>

<template>
  <AdminIntegrationsPageView
    :loading="loading"
    :load-error="loadError"
    :validation-error="validationError"
    :saving="saving"
    :saved="saved"
    :save-error="saveError"
    :app-id="form.app_id"
    :app-secret="form.app_secret"
    :domain="form.domain"
    :domains="domains"
    :status="status"
    :updated="updated"
    :show-status="showStatus"
    :configured="Boolean(app?.configured)"
    @retry="load"
    @save="save"
    @update:app-id="form.app_id = $event"
    @update:app-secret="form.app_secret = $event"
    @update:domain="form.domain = $event"
  />
</template>
