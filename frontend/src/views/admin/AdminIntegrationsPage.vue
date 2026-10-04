<script setup lang="ts">
import type { PlatformFeishuApp } from '@/api/feishu'

import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useSaveState } from '@/composables/useSaveState'

import { getPlatformFeishuApp, savePlatformFeishuApp } from '@/api/feishu'
import AdminPage from '@/components/admin/AdminPage.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'
import { relTime } from '@/lib/relTime'

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
  <AdminPage :title="t('navigation.admin.integrations')" :sub="t('integrations.admin.sub')">
    <div class="afi__body admin-form-card">
      <!-- 读失败：标题说清是哪一页没读到，服务端原话作说明行，重试就在旁边。
           **不**接着画「还没配置」和那张表单 —— 见文件开头第 4 条。
           判据是 `!== null` 而不是真值：原话取不到时 `loadError` 是空串，仍要给出错态。 -->
      <BaseLoadError
        v-if="loadError !== null"
        :title="t('integrations.admin.loadFailed')"
        :error="loadError || undefined"
        :retry-label="t('integrations.admin.retry')"
        @retry="load"
      />

      <template v-else>
        <!-- 首屏还没读回来时不画状态行：这时候还没有答案。 -->
        <p v-if="app || !loading" class="afi__status t-body" :data-configured="app?.configured ? 'yes' : 'no'">
          {{ status }}
        </p>
        <p v-if="updated" class="afi__meta t-meta">{{ updated }}</p>

        <div class="afi__form">
          <v-text-field
            v-model="form.app_id"
            autocomplete="off"
            :label="t('integrations.admin.appId')"
            :disabled="loading"
          />
          <v-text-field
            v-model="form.app_secret"
            class="afi__secret"
            autocomplete="new-password"
            type="password"
            :label="t('integrations.admin.appSecret')"
            :hint="t('integrations.admin.secretHint')"
            persistent-hint
          />
          <v-select v-model="form.domain" autocomplete="off" :items="domains" :label="t('integrations.admin.domain')" />
          <p v-if="validationError" role="alert" class="afi__error t-body">{{ validationError }}</p>
          <div class="afi__actions">
            <SaveStatus
              :saving="saving"
              :saved="saved"
              :error="saveError"
              :saved-text="t('integrations.admin.saved')"
            />
            <BaseButton kind="primary" :loading="saving" :disabled="loading" @click="save">
              {{ t('integrations.admin.save') }}
            </BaseButton>
          </div>
        </div>
      </template>
    </div>
  </AdminPage>
</template>

<style scoped>
/* 卡片的宽度（720 上限）、内边距（20/24）和内缩（16/24）都由 `.admin-form-card` 给，
   和别的表单页同一套。 */

.afi__status {
  margin: 0;
  color: var(--ink);
}

.afi__meta {
  margin: 4px 0 0;
  color: var(--faint);
}

.afi__form {
  margin-top: 16px;
}

/* Secret 那格下面挂着 hint，而下一个字段的浮动标签有一半悬在它自己框的上沿之上
   （见 `.claude/rules/frontend.md`），中间没有余量时两行字会叠在一起。给这一格
   留一段底距，标签就落在空处。 */
.afi__secret {
  margin-bottom: 12px;
}

.afi__error {
  margin: 8px 0 0;
  color: var(--danger-ink);
}

.afi__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}

/* 窄屏的两侧收窄由 `.admin-form-card` 自己那条 ≤700 规则给，这一页不再各写一份。 */
</style>
