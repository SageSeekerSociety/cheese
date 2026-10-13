<script setup lang="ts">
import { computed, ref } from 'vue'

import LandingUseCaseScene, { type UseCaseRole } from './LandingUseCaseScene.vue'

import { t } from '@/i18n'
import { docsUrl } from '@/lib/docsSite'

// What each kind of visitor does in the product, one tab per role: four concrete
// things they do, the page of the manual that walks them through it, and a sample
// of the screen it happens on. Every item names something the product does today.
// Keys are listed in full so the catalog check can see every one of them.

const roles = computed(() => [
  {
    id: 'teachers' as UseCaseRole,
    label: t('publicSite.useCases.teachers.label'),
    title: t('publicSite.useCases.teachers.title'),
    items: [
      { title: t('publicSite.useCases.teachers.item1Title'), body: t('publicSite.useCases.teachers.item1Body') },
      { title: t('publicSite.useCases.teachers.item2Title'), body: t('publicSite.useCases.teachers.item2Body') },
      { title: t('publicSite.useCases.teachers.item3Title'), body: t('publicSite.useCases.teachers.item3Body') },
      { title: t('publicSite.useCases.teachers.item4Title'), body: t('publicSite.useCases.teachers.item4Body') },
    ],
    docs: { label: t('publicSite.useCases.teachers.docs'), href: docsUrl('/spaces') },
  },
  {
    id: 'students' as UseCaseRole,
    label: t('publicSite.useCases.students.label'),
    title: t('publicSite.useCases.students.title'),
    items: [
      { title: t('publicSite.useCases.students.item1Title'), body: t('publicSite.useCases.students.item1Body') },
      { title: t('publicSite.useCases.students.item2Title'), body: t('publicSite.useCases.students.item2Body') },
      { title: t('publicSite.useCases.students.item3Title'), body: t('publicSite.useCases.students.item3Body') },
      { title: t('publicSite.useCases.students.item4Title'), body: t('publicSite.useCases.students.item4Body') },
    ],
    docs: { label: t('publicSite.useCases.students.docs'), href: docsUrl('/solve-a-challenge') },
  },
  {
    id: 'office' as UseCaseRole,
    label: t('publicSite.useCases.office.label'),
    title: t('publicSite.useCases.office.title'),
    items: [
      { title: t('publicSite.useCases.office.item1Title'), body: t('publicSite.useCases.office.item1Body') },
      { title: t('publicSite.useCases.office.item2Title'), body: t('publicSite.useCases.office.item2Body') },
      { title: t('publicSite.useCases.office.item3Title'), body: t('publicSite.useCases.office.item3Body') },
      { title: t('publicSite.useCases.office.item4Title'), body: t('publicSite.useCases.office.item4Body') },
    ],
    docs: { label: t('publicSite.useCases.office.docs'), href: docsUrl('/working-with-cheese') },
  },
  {
    id: 'developers' as UseCaseRole,
    label: t('publicSite.useCases.developers.label'),
    title: t('publicSite.useCases.developers.title'),
    items: [
      { title: t('publicSite.useCases.developers.item1Title'), body: t('publicSite.useCases.developers.item1Body') },
      { title: t('publicSite.useCases.developers.item2Title'), body: t('publicSite.useCases.developers.item2Body') },
      { title: t('publicSite.useCases.developers.item3Title'), body: t('publicSite.useCases.developers.item3Body') },
      { title: t('publicSite.useCases.developers.item4Title'), body: t('publicSite.useCases.developers.item4Body') },
    ],
    docs: { label: t('publicSite.useCases.developers.docs'), href: docsUrl('/accept') },
  },
])

const roleId = ref<UseCaseRole>('teachers')
const role = computed(() => roles.value.find((item) => item.id === roleId.value)!)

function moveTab(event: KeyboardEvent) {
  const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End']
  if (!keys.includes(event.key)) return
  const tabs = Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="tab"]'))
  const index = tabs.indexOf(event.target as HTMLButtonElement)
  if (index === -1) return
  event.preventDefault()
  const last = tabs.length - 1
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? last
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
  roleId.value = roles.value[next].id
  tabs[next].focus()
}
</script>

<template>
  <section id="use-cases" class="uses" aria-labelledby="use-cases-title">
    <h2 id="use-cases-title" class="uses-title">{{ t('publicSite.useCases.title') }}</h2>
    <div class="uses-tabs" role="tablist" :aria-label="t('publicSite.useCases.tabsLabel')" @keydown="moveTab">
      <button
        v-for="item in roles"
        :id="`use-case-${item.id}`"
        :key="item.id"
        type="button"
        role="tab"
        class="uses-tab"
        :aria-selected="roleId === item.id"
        :tabindex="roleId === item.id ? 0 : -1"
        aria-controls="use-case-panel"
        @click="roleId = item.id"
      >
        {{ item.label }}
      </button>
    </div>
    <Transition name="use-swap" mode="out-in">
      <div
        id="use-case-panel"
        :key="role.id"
        class="use"
        role="tabpanel"
        :aria-labelledby="`use-case-${role.id}`"
        tabindex="0"
      >
        <div class="use-main">
          <h3 class="use-title">{{ role.title }}</h3>
          <ul class="use-items">
            <li v-for="item in role.items" :key="item.title">
              <span class="use-item-title">{{ item.title }}</span>
              <span class="use-item-body">{{ item.body }}</span>
            </li>
          </ul>
          <a class="use-docs" :href="role.docs.href">
            {{ role.docs.label }}
            <v-icon icon="mdi-arrow-right" size="16" />
          </a>
        </div>
        <LandingUseCaseScene class="use-scene" :role="role.id" />
      </div>
    </Transition>
  </section>
</template>

<style scoped>
.uses {
  display: flex;
  padding: 64px var(--gutter);
  flex-direction: column;
  gap: 32px;
  scroll-margin-top: var(--bar-h);
}

.uses-title {
  padding-top: 64px;
  font-family: var(--font-display);
  font-size: clamp(40px, 5vw, 72px);
  font-weight: 700;
  line-height: 1.1;
  color: var(--ink);
}

.uses-tabs {
  display: flex;
  border-bottom: 1px solid var(--line-2);
  flex-wrap: wrap;
  column-gap: 32px;
}

.uses-tab {
  padding: 16px 0;
  margin-bottom: -1px;
  font-size: 18px;
  line-height: var(--lh-18);
  color: var(--muted);
  white-space: nowrap;
  cursor: pointer;
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
  transition:
    color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}

.uses-tab:hover {
  color: var(--ink);
}

.uses-tab[aria-selected='true'] {
  font-weight: 600;
  color: var(--ink);
  border-bottom-color: var(--accent);
}

.use {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
  column-gap: 64px;
  align-items: start;
  outline-offset: 8px;
}

.use-main {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.use-title {
  font-family: var(--font-display);
  font-size: clamp(24px, 2.4vw, 32px);
  font-weight: 700;
  line-height: 1.2;
  color: var(--ink);
}

.use-items {
  display: flex;
  padding: 0;
  margin: 0;
  list-style: none;
  flex-direction: column;
  gap: 16px;
}

.use-items li {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.use-item-title {
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
  color: var(--ink);
}

.use-item-body {
  font-size: 15px;
  line-height: var(--lh-15-reading);
  color: var(--text);
}

.use-docs {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  text-decoration: none;
  align-self: flex-start;
}

.use-docs .v-icon {
  margin-left: 4px;
  vertical-align: -2px;
}

.use-docs:hover {
  text-decoration: underline;
}

.use-swap-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.use-swap-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}

.use-swap-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.use-swap-leave-to {
  opacity: 0;
}

@media (width <= 900px) {
  .uses {
    padding-top: 32px;
    padding-bottom: 32px;
  }

  .uses-title {
    padding-top: 32px;
  }

  .uses-tabs {
    column-gap: 24px;
  }

  .uses-tab {
    font-size: 15px;
    line-height: var(--lh-15);
  }

  .use {
    grid-template-columns: minmax(0, 1fr);
    row-gap: 32px;
  }
}
</style>
