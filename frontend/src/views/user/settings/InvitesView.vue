<!--
  What the invite-friends page shows (Invites.vue): the person's own invite code
  with the seats it has left, what inviting has earned them, and the friends who
  signed up with it. Reading the code and copying it happen in the page; this
  half only draws.
-->
<template>
  <div class="settings-page invites">
    <header>
      <h1 class="t-page-title">{{ t('account.invites.title') }}</h1>
      <p class="settings-page__lede">{{ t('account.invites.lede') }}</p>
    </header>

    <BaseLoadError
      v-if="failed"
      :title="t('account.invites.loadFailed')"
      :error="failingReason"
      :retry-label="t('account.invites.retry')"
      @retry="emit('retry')"
    />

    <!-- Held at the cards' height until the data arrives, so the page does not jump. -->
    <section v-else-if="!invites" class="settings-card invites__pending" aria-busy="true" />

    <template v-else>
      <section class="settings-card">
        <h2 class="settings-card__title">{{ t('account.invites.code.title') }}</h2>
        <div class="invites__code">
          <div class="invites__code-main">
            <span class="invites__code-value t-num" :aria-label="t('account.invites.code.label')">{{
              invites.code
            }}</span>
            <span class="invites__seats t-num" data-testid="invite-seats">{{
              t('account.invites.code.remaining', { left: seatsLeft, total: invites.max_uses })
            }}</span>
          </div>
          <p v-if="!invites.unlocked" class="invites__hint" data-testid="invite-unlock-hint">
            {{ t('account.invites.code.unlockHint', { n: invites.unlock_uses }) }}
          </p>
          <div class="invites__actions">
            <BaseButton kind="secondary" prepend-icon="mdi-content-copy" @click="emit('copyCode')">
              {{ t('account.invites.code.copyCode') }}
            </BaseButton>
            <!-- Sharing the link is what this page is for: its one amber action. -->
            <BaseButton kind="primary" prepend-icon="mdi-link-variant" @click="emit('copyLink')">
              {{ t('account.invites.code.copyLink') }}
            </BaseButton>
          </div>
        </div>
      </section>

      <section class="settings-card">
        <h2 class="settings-card__title">{{ t('account.invites.earned.title') }}</h2>
        <p class="settings-card__desc">{{ t('account.invites.earned.note') }}</p>
        <div class="srow">
          <span class="srow__k">{{ t('account.invites.earned.credits') }}</span>
          <span class="srow__v"
            ><span class="srow__strong t-num">{{
              t('account.invites.earned.creditsValue', { n: invites.invite_credits })
            }}</span></span
          >
          <NavLink v-if="creditsTo" class="invites__link" :to="creditsTo">
            {{ t('account.invites.earned.viewCredits') }}<v-icon icon="mdi-chevron-right" size="16" />
          </NavLink>
        </div>
      </section>

      <section class="settings-card">
        <h2 class="settings-card__title">{{ t('account.invites.people.title') }}</h2>
        <BaseEmptyState
          v-if="!people.length"
          class="settings-empty"
          size="inline"
          :title="t('account.invites.people.empty')"
        />
        <ul v-else class="invites__people">
          <li v-for="person in people" :key="person.handle" class="invitee">
            <UserAvatar :avatar="person.avatarUrl" :name="person.nickname || person.handle" size="32" />
            <span class="invitee__who">
              <span class="invitee__name" data-user-content>{{ person.nickname || person.handle }}</span>
              <span class="invitee__meta">
                <span>@{{ person.handle }}</span>
                <span class="t-num">{{
                  t('account.invites.people.registeredOn', { date: dateOf(person.registeredAt) })
                }}</span>
              </span>
            </span>
            <span class="invitee__stage">
              <span class="status-dot" :class="person.stage === 'activated' ? 'status-dot--ok' : 'status-dot--muted'" />
              {{ stageText(person) }}
            </span>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { NavTarget } from '@/lib/navTarget'
import type { InviteStage, MyInvites } from '@/types/invites'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import NavLink from '@/components/common/NavLink.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { absDate } from '@/lib/dateText'

/** One friend as drawn: the page has already turned their avatar id into a URL. */
export interface InviteeRow {
  handle: string
  nickname: string | null
  /** Empty when they have not chosen an avatar: the initial is drawn instead. */
  avatarUrl: string
  registeredAt: string
  stage: InviteStage
  /** Credits the person received for this friend once activated; 0 past the monthly cap. */
  credits: number
}

const props = defineProps<{
  /** Null until the code arrives (or when reading it failed). */
  invites: MyInvites | null
  people: InviteeRow[]
  failed: boolean
  failingReason?: string | null
  /** Where the person's own credits are shown; null when there is no signed-in handle to go by. */
  creditsTo: NavTarget | null
}>()

const emit = defineEmits<{ copyCode: []; copyLink: []; retry: [] }>()

const seatsLeft = computed(() => (props.invites ? Math.max(0, props.invites.max_uses - props.invites.use_count) : 0))

const dateOf = (at: string) => absDate(Date.parse(at))

/** Activated says what it actually earned: past the monthly cap it earned no credits. */
function stageText(person: InviteeRow): string {
  if (person.stage !== 'activated') return t(`account.invites.people.stage.${person.stage}`)
  return person.credits > 0
    ? t('account.invites.people.stage.activated', { n: person.credits })
    : t('account.invites.people.stage.activatedNoCredits')
}
</script>

<style scoped>
.invites__pending {
  min-height: 160px;
}

.invites__code {
  display: grid;
  gap: 12px;
  padding: 4px 24px 20px;
}

.invites__code-main {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: baseline;
}

.invites__code-value {
  font-family: var(--font-mono);
  font-size: 23px;
  font-weight: 600;
  line-height: var(--lh-23);
  letter-spacing: 0.04em;
  color: var(--ink);
}

.invites__seats {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.invites__hint {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.invites__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.invites__link {
  display: inline-flex;
  align-items: center;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}

.invites__people {
  padding: 0;
  margin: 0;
  list-style: none;
}

.invitee {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 12px 24px;
  border-top: 1px solid var(--line);
}

.invitee__who {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.invitee__name {
  overflow: hidden;
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.invitee__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0 12px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.invitee__stage {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  white-space: nowrap;
}

@media (max-width: 767.98px) {
  .invites__code {
    padding: 4px 16px 16px;
  }

  /* The stage drops under the name instead of squeezing it to a few characters. */
  .invitee {
    grid-template-columns: auto minmax(0, 1fr);
    padding: 12px 16px;
  }

  .invitee__stage {
    grid-column: 2;
  }
}
</style>
