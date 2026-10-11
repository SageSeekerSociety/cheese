<script setup lang="ts">
// 新手引导：一个项目都没有、也没走过引导的人登录后落在这里（router/home.ts
// `landingForMember`），帮助菜单「新手引导」带 `?again=1` 再走一遍。
//
// 五问：怎么称呼、主要用知是做什么、想先做成什么、和谁一起、芝士在哪干活。每个
// 回答都有去处，右边的「你的项目」随回答长出来；点「建好项目」才真的建
// （`POST /users/me/onboarding`），然后进它的「综合」，第一句请求已经写在输入框里，
// 「上手三步」接着带（components/room/StartSteps.vue）。
import type { OnboardingAnswers, WorkRole } from '@/api'

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { finishOnboarding, setTopicComputeChoice, uploadAttachment } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { saveComposerDraft } from '@/lib/composerDrafts'
import { desktopBridge, startConnecting } from '@/lib/desktop'
import { inDesktopApp } from '@/lib/desktopApp'
import { refreshProjects } from '@/query/projects'
import AccountService from '@/services/account'

const props = defineProps<{
  /** 从帮助菜单再走一遍：从身份开始，回答预填上次的。 */
  again?: boolean
}>()
const router = useRouter()
const again = props.again
const me = AccountService.user

// 每种身份四件事（文案在 welcome.tasks.<组>.<键>）；没对上的身份用通用的一组。
const TASKS = {
  student: ['report', 'project', 'review', 'group'],
  teacher: ['assignment', 'slides', 'feedback', 'course'],
  office: ['doc', 'table', 'tool', 'plan'],
  research: ['survey', 'data', 'section', 'reproduce'],
  general: ['tidy', 'research', 'build', 'plan'],
} as const
type TaskGroup = keyof typeof TASKS
const ROLES: WorkRole[] = ['student', 'teacher', 'office', 'research', 'maker', 'other']

// 再走一遍的人已经回答过称呼，从身份开始。
const step = ref(again ? 2 : 1)
const LAST = 5
const nickname = ref(me?.nickname ?? '')
const role = ref<WorkRole | null>((me?.work_role as WorkRole | null | undefined) ?? null)
const organization = ref(me?.organization ?? '')
const taskKey = ref<string | null>(null)
const own = ref('')
const files = ref<File[]>([])
const picker = ref<HTMLInputElement | null>(null)
const solo = ref(true)
const inviteText = ref('')
const desktop = inDesktopApp()
const onThisComputer = ref(false)
const busy = ref(false)
const failure = ref('')
// 每走一遍引导选一次：重试补完同一个项目，不会建出第二个。
const projectId = crypto.randomUUID()

const group = computed<TaskGroup>(() => (role.value && role.value in TASKS ? (role.value as TaskGroup) : 'general'))
const taskText = (key: string, part: 'label' | 'hint' | 'name' | 'text') =>
  t(`welcome.tasks.${group.value}.${key}.${part}`)
const displayName = computed(() => nickname.value.trim() || me?.username || '')
const fallbackName = computed(() => t('welcome.preview.nameFallback', { name: displayName.value }))

const projectName = computed(() => {
  const mine = own.value.trim()
  if (mine) return mine.length > 30 ? `${mine.slice(0, 30)}…` : mine
  return taskKey.value ? taskText(taskKey.value, 'name') : fallbackName.value
})
const intent = computed(() => own.value.trim() || (taskKey.value ? taskText(taskKey.value, 'label') : ''))
const request = computed(() => {
  const mine = own.value.trim()
  if (mine) return `${mine}\n${t('welcome.task.ownPlan')}`
  return taskKey.value ? taskText(taskKey.value, 'text') : ''
})
const invitees = computed(() => (solo.value ? [] : inviteText.value.split(/[\s,\uff0c]+/).filter(Boolean)))
const aboutLine = computed(() =>
  [role.value && role.value !== 'other' ? t(`welcome.role.${role.value}Hint`) : '', organization.value.trim()]
    .filter(Boolean)
    .join(t('welcome.preview.aboutSep'))
)

function pickTask(key: string) {
  taskKey.value = taskKey.value === key ? null : key
}
function pickRole(next: WorkRole) {
  if (role.value !== next) taskKey.value = null
  role.value = next
}
function addFiles(list: FileList | null) {
  if (list) files.value = [...files.value, ...Array.from(list)]
}

async function finish(skipAll = false) {
  busy.value = true
  failure.value = ''
  const answers: OnboardingAnswers = skipAll
    ? {
        project_id: projectId,
        project_name: fallbackName.value,
        intent: '',
        nickname: nickname.value,
        invitees: [],
        solo: true,
      }
    : {
        project_id: projectId,
        project_name: projectName.value,
        intent: intent.value,
        nickname: nickname.value,
        work_role: role.value,
        organization: organization.value,
        invitees: invitees.value,
        solo: solo.value,
      }
  try {
    const done = await finishOnboarding(answers)
    rememberOnAccount(skipAll)
    const room = done.root_topic_id
    if (room) {
      const atts = []
      for (const file of files.value) atts.push(await uploadAttachment(room, file))
      // 输入框里那句和起手卡写进去的一样带 @：直接回车只发给成员，芝士不会接。
      const draft = request.value ? `@${t('work.room.defaultAgentName')} ${request.value}` : ''
      if (!skipAll && (draft || atts.length)) saveComposerDraft(room, { draft, reply: null, atts })
      if (!skipAll && desktop && onThisComputer.value) await useThisComputer(room)
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
function rememberOnAccount(skipAll: boolean) {
  const user = AccountService.user
  if (!user) return
  AccountService.user = {
    ...user,
    onboarded_at: user.onboarded_at ?? new Date().toISOString(),
    nickname: nickname.value.trim() || user.nickname,
    ...(skipAll ? {} : { work_role: role.value, organization: organization.value.trim() || null }),
  }
  localStorage.setItem('user', JSON.stringify(AccountService.user))
}

// 桌面端选了「用这台电脑」：接上这台电脑，把「综合」放到它上面的隔离目录里。接不上
// 就留在云端，说一声，不拦着人进项目。
async function useThisComputer(room: string) {
  try {
    await startConnecting({ quiet: true })
    const device = await desktopBridge()?.thisDevice()
    if (!device) throw new Error('no device')
    await setTopicComputeChoice(room, { name: null, profile: 'device', device_id: device }, { visibility: 'isolated' })
  } catch {
    toast.warning(t('welcome.deviceFailed'))
  }
}

const canGoOn = computed(() => step.value !== 1 || !!nickname.value.trim())
</script>

<template>
  <div class="welcome">
    <div class="welcome__card">
      <div class="welcome__form">
        <div class="welcome__progress" :aria-label="`${step} / ${LAST}`">
          <i v-for="n in LAST" :key="n" :class="{ on: n <= step }" />
        </div>

        <template v-if="step === 1">
          <p class="t-body c-muted">{{ t('welcome.intro') }}</p>
          <h1 class="t-page-title">{{ t('welcome.name.q') }}</h1>
          <v-text-field
            v-model="nickname"
            autocomplete="nickname"
            density="comfortable"
            variant="outlined"
            hide-details
          />
          <p class="welcome__why">{{ t('welcome.name.why') }}</p>
        </template>

        <template v-else-if="step === 2">
          <h1 class="t-page-title">{{ t('welcome.role.q') }}</h1>
          <p class="welcome__why">{{ t('welcome.role.why') }}</p>
          <div class="welcome__options">
            <button
              v-for="r in ROLES"
              :key="r"
              type="button"
              class="welcome__option"
              :aria-pressed="role === r"
              @click="pickRole(r)"
            >
              <span class="t-title">{{ t(`welcome.role.${r}`) }}</span>
              <span v-if="r !== 'other'" class="t-body c-faint">{{ t(`welcome.role.${r}Hint`) }}</span>
            </button>
          </div>
          <v-text-field
            v-model="organization"
            autocomplete="off"
            :label="t('welcome.role.org')"
            density="comfortable"
            variant="outlined"
            hide-details
          />
        </template>

        <template v-else-if="step === 3">
          <h1 class="t-page-title">{{ t('welcome.task.q') }}</h1>
          <p class="welcome__why">{{ t('welcome.task.why') }}</p>
          <div class="welcome__options">
            <button
              v-for="key in TASKS[group]"
              :key="key"
              type="button"
              class="welcome__option"
              :aria-pressed="taskKey === key && !own.trim()"
              @click="pickTask(key)"
            >
              <span class="t-title">{{ taskText(key, 'label') }}</span>
              <span class="t-body c-faint">{{ taskText(key, 'hint') }}</span>
            </button>
          </div>
          <v-text-field
            v-model="own"
            autocomplete="off"
            :label="t('welcome.task.own')"
            density="comfortable"
            variant="outlined"
            hide-details
          />
          <input
            ref="picker"
            type="file"
            multiple
            hidden
            :aria-label="t('welcome.task.files')"
            @change="addFiles(($event.target as HTMLInputElement).files)"
          />
          <button
            type="button"
            class="welcome__drop"
            @click="picker?.click()"
            @dragover.prevent
            @drop.prevent="addFiles($event.dataTransfer?.files ?? null)"
          >
            <span>{{ t('welcome.task.files') }}</span>
            <span v-if="files.length" class="welcome__chips">
              <span v-for="f in files" :key="f.name" class="chip-neutral">{{ f.name }}</span>
            </span>
          </button>
          <p class="welcome__why">{{ t('welcome.task.filesNote') }}</p>
        </template>

        <template v-else-if="step === 4">
          <h1 class="t-page-title">{{ t('welcome.who.q') }}</h1>
          <p class="welcome__why">{{ t('welcome.who.why') }}</p>
          <div class="welcome__options">
            <button type="button" class="welcome__option" :aria-pressed="solo" @click="solo = true">
              <span class="t-title">{{ t('welcome.who.solo') }}</span>
              <span class="t-body c-faint">{{ t('welcome.who.soloHint') }}</span>
            </button>
            <button type="button" class="welcome__option" :aria-pressed="!solo" @click="solo = false">
              <span class="t-title">{{ t('welcome.who.team') }}</span>
              <span class="t-body c-faint">{{ t('welcome.who.teamHint') }}</span>
            </button>
          </div>
          <v-text-field
            v-if="!solo"
            v-model="inviteText"
            autocomplete="off"
            :label="t('welcome.who.invitees')"
            density="comfortable"
            variant="outlined"
            hide-details
          />
        </template>

        <template v-else>
          <h1 class="t-page-title">{{ t('welcome.where.q') }}</h1>
          <p class="welcome__why">{{ t('welcome.where.why') }}</p>
          <div class="welcome__options">
            <button
              type="button"
              class="welcome__option"
              :aria-pressed="!onThisComputer"
              @click="onThisComputer = false"
            >
              <span class="t-title">{{ t('welcome.where.cloud') }}</span>
              <span class="t-body c-faint">{{ t('welcome.where.cloudHint') }}</span>
            </button>
            <button
              v-if="desktop"
              type="button"
              class="welcome__option"
              :aria-pressed="onThisComputer"
              @click="onThisComputer = true"
            >
              <span class="t-title">{{ t('welcome.where.device') }}</span>
              <span class="t-body c-faint">{{ t('welcome.where.deviceHint') }}</span>
            </button>
          </div>
          <p v-if="!desktop" class="welcome__why">
            {{ t('welcome.where.webNote') }}
            <router-link :to="{ name: 'Download' }">{{ t('welcome.where.download') }}</router-link>
          </p>
        </template>

        <p v-if="failure" class="welcome__failure" role="alert">{{ failure }}</p>

        <div class="welcome__foot">
          <BaseButton v-if="step === 1 && !again" kind="ghost" :disabled="busy" @click="finish(true)">
            {{ t('welcome.skipAll') }}
          </BaseButton>
          <BaseButton v-else-if="step > 1 && step < LAST" kind="ghost" :disabled="busy" @click="step += 1">
            {{ t('welcome.skip') }}
          </BaseButton>
          <span v-else />
          <span class="welcome__go">
            <BaseButton v-if="step > (again ? 2 : 1)" kind="ghost" :disabled="busy" @click="step -= 1">
              {{ t('welcome.back') }}
            </BaseButton>
            <BaseButton v-if="step < LAST" kind="primary" :disabled="!canGoOn" @click="step += 1">
              {{ t('welcome.next') }}
            </BaseButton>
            <BaseButton v-else kind="primary" :loading="busy" @click="finish()">
              {{ busy ? t('welcome.finishing') : t('welcome.finish') }}
            </BaseButton>
          </span>
        </div>
      </div>

      <aside class="welcome__preview" :aria-label="t('welcome.preview.title')">
        <p class="t-eyebrow">{{ t('welcome.preview.title') }}</p>
        <div class="welcome__project">
          <p class="t-title">{{ step >= 3 ? projectName : fallbackName }}</p>
          <dl>
            <template v-if="intent">
              <dt>{{ t('welcome.preview.brief') }}</dt>
              <dd>{{ intent }}</dd>
            </template>
            <template v-if="files.length">
              <dt>{{ t('welcome.preview.files') }}</dt>
              <dd>{{ files.map((f) => f.name).join(t('welcome.preview.listSep')) }}</dd>
            </template>
            <dt>{{ t('welcome.preview.members') }}</dt>
            <dd>
              {{ displayName }} · {{ t('work.room.defaultAgentName') }}
              <template v-for="name in invitees" :key="name"> · {{ t('welcome.preview.invited', { name }) }}</template>
            </dd>
            <template v-if="aboutLine">
              <dt>{{ t('welcome.preview.about') }}</dt>
              <dd>{{ t('welcome.preview.aboutLine', { name: displayName, about: aboutLine }) }}</dd>
            </template>
            <template v-if="step >= LAST">
              <dt>{{ t('welcome.preview.where') }}</dt>
              <dd>{{ onThisComputer ? t('welcome.preview.device') : t('welcome.preview.cloud') }}</dd>
            </template>
          </dl>
        </div>
        <p class="welcome__why">{{ t('welcome.preview.hint') }}</p>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.welcome {
  min-height: 100%;
  display: grid;
  place-items: center;
  padding: 32px 16px;
  background: var(--canvas);
}
.welcome__card {
  width: min(960px, 100%);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.welcome__form {
  padding: 28px 32px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.welcome__progress {
  display: flex;
  gap: 4px;
}
.welcome__progress i {
  width: 28px;
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--line-2);
}
.welcome__progress i.on {
  background: var(--ink);
}
.welcome__why {
  color: var(--faint);
  margin: 0;
}
.welcome__options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.welcome__option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
  padding: 10px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  cursor: pointer;
}
.welcome__option:hover {
  background: var(--fill);
}
.welcome__option[aria-pressed='true'] {
  border-color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--ink);
}
.welcome__drop {
  width: 100%;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  padding: 14px;
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-md);
  color: var(--faint);
  cursor: pointer;
}
.welcome__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.welcome__failure {
  color: var(--danger);
  margin: 0;
}
.welcome__foot {
  margin-top: auto;
  padding-top: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.welcome__go {
  display: flex;
  gap: 8px;
}
.welcome__preview {
  background: var(--canvas);
  border-left: 1px solid var(--line);
  padding: 24px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.welcome__project {
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}
.welcome__project dl {
  margin: 8px 0 0;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 10px;
}
.welcome__project dt {
  color: var(--faint);
}
.welcome__project dd {
  margin: 0;
  color: var(--text);
  overflow-wrap: anywhere;
}
@media (max-width: 760px) {
  .welcome__card {
    grid-template-columns: minmax(0, 1fr);
  }
  .welcome__preview {
    border-left: 0;
    border-top: 1px solid var(--line);
  }
  .welcome__form {
    padding: 20px 16px;
  }
  .welcome__options {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
