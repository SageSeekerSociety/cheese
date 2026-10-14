<script setup lang="ts">
// 新手引导：一个项目都没有、也没走过引导的人登录后落在这里（router/home.ts
// `landingForMember`），帮助菜单「新手引导」带 `?again=1` 再走一遍。
//
// 画面在 WelcomeView.vue。这一页接住它交出来的回答：建项目
// （`POST /users/me/onboarding`），附上文件、把第一句请求写进「综合」的输入框，然后
// 进「综合」，新手引导的浮窗接着带（composables/useProjectTour.ts）。
import type { WelcomeFinish } from './WelcomeView.vue'

import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import WelcomeView from './WelcomeView.vue'

import { finishOnboarding, uploadAttachment } from '@/api'
import { t } from '@/i18n'
import { storageSetJson } from '@/lib/browserStorage'
import { saveComposerDraft } from '@/lib/composerDrafts'
import { desktopBridge, startConnecting } from '@/lib/desktop'
import { inDesktopApp } from '@/lib/desktopApp'
import { refreshProjects } from '@/query/projects'
import AccountService from '@/services/account'

defineProps<{ again?: boolean }>()
const router = useRouter()
const me = AccountService.user
const desktop = inDesktopApp()
const busy = ref(false)
const failure = ref('')

async function finish({ answers, skipAll, draft, files, onThisComputer }: WelcomeFinish) {
  busy.value = true
  failure.value = ''
  try {
    const done = await finishOnboarding({ ...answers, device_id: onThisComputer ? await thisComputer() : null })
    if (onThisComputer && !done.on_device) toast.warning(t('welcome.deviceFailed'))
    rememberOnAccount(answers, skipAll)
    const room = done.root_topic_id
    if (room) {
      const atts = []
      for (const file of files) atts.push(await uploadAttachment(room, file))
      if (draft || atts.length) saveComposerDraft(room, { draft, reply: null, atts })
    }
    if (done.not_invited.length)
      toast.warning(t('welcome.notInvited', { names: done.not_invited.join(t('welcome.preview.listSep')) }))
    await refreshProjects()
    await router.replace(
      room
        ? { name: 'workspace-topic', params: { projectId: done.project_id, topicId: room } }
        : { name: 'workspace-project', params: { projectId: done.project_id } }
    )
  } catch (e) {
    failure.value = t('welcome.failed', { reason: e instanceof Error ? e.message : String(e) })
  } finally {
    busy.value = false
  }
}

// 账号上记下这次的回答：登录态里那份用户记录也要跟着变，否则同一个标签页回到首页
// 时还以为没走过引导。
function rememberOnAccount(answers: WelcomeFinish['answers'], skipAll: boolean) {
  const user = AccountService.user
  if (!user) return
  AccountService.user = {
    ...user,
    onboarded_at: user.onboarded_at ?? new Date().toISOString(),
    nickname: answers.nickname.trim() || user.nickname,
    ...(skipAll ? {} : { work_role: answers.work_role ?? null, organization: answers.organization?.trim() || null }),
  }
  storageSetJson('user', AccountService.user)
}

// 桌面端选了「用这台电脑」：先接上这台电脑，建项目时把它带上，服务端把它加进项目、
// 让「综合」里的芝士在它上面的隔离环境里干活。接不上就留在云端，说一声，不拦着人进项目。
async function thisComputer(): Promise<string | null> {
  try {
    await startConnecting({ quiet: true })
    return (await desktopBridge()?.thisDevice()) ?? null
  } catch {
    return null
  }
}
</script>

<template>
  <WelcomeView :again="again" :me="me" :desktop="desktop" :busy="busy" :failure="failure" @finish="finish" />
</template>
