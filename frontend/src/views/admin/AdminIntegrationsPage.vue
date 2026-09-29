<script setup lang="ts">
import type { PlatformFeishuApp } from '@/api'

import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getPlatformFeishuApp, savePlatformFeishuApp } from '@/api'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
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
defineOptions({ name: 'AdminIntegrationsPage' })

const { t } = useI18n()

const loading = ref(false)
/** 读这一页失败。它说的是「这一页没读到」，和保存失败是两件事。 */
const loadError = ref('')
/** 保存失败。留在页顶那条错误里，表单里的东西一个字不动。 */
const saveError = ref('')
const saved = ref(false)
const saving = ref(false)
const app = ref<PlatformFeishuApp | null>(null)

const form = reactive({ app_id: '', app_secret: '', domain: 'feishu' })

const domains = computed(() => [
  { value: 'feishu', title: '飞书（feishu.cn）' },
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
  loadError.value = ''
  try {
    const current = await getPlatformFeishuApp()
    app.value = current
    form.app_id = current.app_id
    form.domain = current.domain || 'feishu'
    // Secret 不回显，所以这一格永远是空的 —— 它的空表示「不改」，见文件开头第 1 条。
    form.app_secret = ''
  } catch {
    loadError.value = t('integrations.admin.loadFailed')
    app.value = null
  } finally {
    loading.value = false
  }
}

async function save() {
  saveError.value = ''
  saved.value = false
  if (!form.app_id.trim()) {
    saveError.value = t('integrations.admin.appIdRequired')
    return
  }
  saving.value = true
  try {
    app.value = await savePlatformFeishuApp({
      app_id: form.app_id.trim(),
      app_secret: form.app_secret,
      domain: form.domain,
    })
    form.app_secret = ''
    saved.value = true
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : t('integrations.admin.saveFailed')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="afi">
    <AdminPageHeader :title="t('integrations.admin.title')" :sub="t('integrations.admin.sub')" />

    <div class="afi__body">
      <p v-if="loadError" role="alert" class="afi__error t-body">{{ loadError }}</p>

      <p class="afi__status t-body" :data-configured="app?.configured ? 'yes' : 'no'">{{ status }}</p>
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
          autocomplete="new-password"
          type="password"
          :label="t('integrations.admin.appSecret')"
          :hint="t('integrations.admin.secretHint')"
          persistent-hint
        />
        <v-select v-model="form.domain" autocomplete="off" :items="domains" :label="t('integrations.admin.domain')" />
        <p v-if="saveError" role="alert" class="afi__error t-body">{{ saveError }}</p>
        <div class="afi__actions">
          <span v-if="saved" role="status" class="t-meta c-faint">{{ t('integrations.admin.saved') }}</span>
          <v-btn color="primary" variant="flat" :loading="saving" :disabled="loading" @click="save">
            {{ t('integrations.admin.save') }}
          </v-btn>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.afi {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.afi__body {
  max-width: 640px;
  padding: 16px 24px;
}

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

@media (width <= 700px) {
  .afi__body {
    padding: 12px 16px;
  }
}
</style>
