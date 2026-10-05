<script setup lang="ts">
import type { ChangelogDay, Download } from '@/lib/desktop'

import { computed, ref } from 'vue'
import { renderSVG } from 'uqr'

import LandingShell from './LandingShell.vue'

import appIcon from '@/assets/app-icon.png'
import BaseButton from '@/components/base/BaseButton.vue'
import i18n, { t } from '@/i18n'
import { canPromptInstall, detectIos, isInstalled, promptInstall } from '@/lib/pwaInstall'

// 下载页**画的那一半**：电脑上是一颗下载按钮（其他版本在它右边的下拉里）、一张真实
// 界面的截图、更新日志和一个扫码去手机的二维码；手机上是添加到主屏幕。
//
// 版本清单、这台电脑该下哪个、更新日志都要问一声，那些在容器 `Download.vue` 里；
// 这里只吃 props。截图由 e2e/download-shots.mjs 从真实前端截出来。

const props = defineProps<{
  loggedIn: boolean
  downloads: Download[]
  primary: Download
  version: string | null
  days: ChangelogDay[]
}>()

const ios = detectIos()
const phone = ios || /Android/i.test(navigator.userAgent)

const showAll = ref(false)
const downloaded = ref(false)
const copied = ref(false)
const menuOpen = ref(false)

const OS_ICON = { mac: 'mdi-apple', windows: 'mdi-microsoft-windows' } as const

// What the file is, read off its name, so the row says what will land in Downloads.
function fileKind(href: string): string {
  return href.endsWith('.exe') ? t('publicSite.downloadPage.exeFile') : t('publicSite.downloadPage.dmgFile')
}

const primaryLabel = computed(() =>
  props.primary.os === 'windows' ? t('publicSite.downloadPage.forWindows') : t('publicSite.downloadPage.forMac')
)
const firstOpenTip = computed(() =>
  props.primary.os === 'windows'
    ? t('publicSite.downloadPage.firstOpenWindows')
    : t('publicSite.downloadPage.firstOpenMac')
)
const shownDays = computed(() => (showAll.value ? props.days : props.days.slice(0, 3)))

const pageUrl = `${window.location.origin}/download`
const qr = renderSVG(pageUrl, { border: 0, blackColor: 'currentColor', whiteColor: 'transparent' })

function dayLabel(date: string): string {
  const [y, m, d] = date.split('-').map(Number)
  return new Intl.DateTimeFormat(i18n.global.locale.value, { year: 'numeric', month: 'long', day: 'numeric' }).format(
    new Date(y, m - 1, d)
  )
}

async function install() {
  await promptInstall()
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText(pageUrl)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    // 剪贴板被拒绝：地址就写在旁边，照着输入即可
  }
}
</script>

<template>
  <LandingShell page="download" :logged-in="loggedIn">
    <div class="dl">
      <section class="dl-hero">
        <img :src="appIcon" alt="" width="96" height="96" class="dl-icon" />
        <h1 class="dl-title">{{ t('publicSite.downloadPage.title') }}</h1>

        <!-- 手机：添加到主屏幕。安卓上 Chrome 给得出安装按钮；iPhone 只能从 Safari 的分享菜单加。 -->
        <div v-if="phone" class="dl-phone">
          <BaseButton v-if="isInstalled" kind="primary" size="lg" block href="/">{{
            t('publicSite.downloadPage.openCheese')
          }}</BaseButton>
          <template v-else-if="ios">
            <ol class="dl-steps">
              <li>
                <span class="dl-steps__n">1</span>{{ t('publicSite.downloadPage.iosShare') }}
                <v-icon icon="mdi-export-variant" size="20" class="dl-steps__icon" />
              </li>
              <li><span class="dl-steps__n">2</span>{{ t('publicSite.downloadPage.iosAdd') }}</li>
            </ol>
            <p class="dl-note">{{ t('publicSite.downloadPage.iosInApp') }}</p>
          </template>
          <template v-else>
            <BaseButton v-if="canPromptInstall" kind="primary" size="lg" block @click="install">{{
              t('publicSite.downloadPage.addToHomeScreen')
            }}</BaseButton>
            <p v-else class="dl-note dl-note--strong">{{ t('publicSite.downloadPage.androidMenu') }}</p>
            <p class="dl-note">{{ t('publicSite.downloadPage.chromeOnly') }}</p>
          </template>
        </div>

        <!-- 电脑：这台电脑的版本一颗按钮，其他版本和手机在右边的下拉里。 -->
        <template v-else>
          <div class="dl-split">
            <!-- eslint-disable-next-line vue/no-restricted-syntax -- nav bar button whose look this component styles exactly (design-system §3.6 exception) -->
            <v-btn
              color="primary"
              size="x-large"
              rounded="s-lg"
              class="dl-split__main"
              prepend-icon="mdi-download"
              :href="primary.href"
              @click="downloaded = true"
              >{{ primaryLabel }}</v-btn
            >
            <v-menu v-model="menuOpen" location="bottom end" :offset="8">
              <template #activator="{ props: menu }">
                <!-- eslint-disable-next-line vue/no-restricted-syntax -- nav bar button whose look this component styles exactly (design-system §3.6 exception) -->
                <v-btn
                  v-bind="menu"
                  color="primary"
                  size="x-large"
                  rounded="e-lg"
                  class="dl-split__more"
                  :aria-label="t('publicSite.downloadPage.otherVersions')"
                  ><v-icon icon="mdi-chevron-down" class="dl-split__chevron" :class="{ 'is-open': menuOpen }"
                /></v-btn>
              </template>
              <v-list class="menu-list dl-menu" nav density="compact" min-width="260">
                <v-list-item v-for="d in downloads" :key="d.href" :href="d.href" @click="downloaded = true">
                  <template #prepend>
                    <v-icon :icon="OS_ICON[d.os]" size="18" class="dl-menu__icon" aria-hidden="true" />
                  </template>
                  <v-list-item-title>{{ t(d.labelKey) }}</v-list-item-title>
                  <v-list-item-subtitle class="dl-menu__sub">{{ fileKind(d.href) }}</v-list-item-subtitle>
                </v-list-item>
                <v-divider class="my-1" />
                <!-- Not a file: the phone uses the web app, so this row goes to the QR code below. -->
                <v-list-item href="#phone">
                  <template #prepend>
                    <v-icon icon="mdi-cellphone" size="18" class="dl-menu__icon" aria-hidden="true" />
                  </template>
                  <v-list-item-title>{{ t('publicSite.downloadPage.phone') }}</v-list-item-title>
                  <v-list-item-subtitle class="dl-menu__sub">{{
                    t('publicSite.downloadPage.phoneHint')
                  }}</v-list-item-subtitle>
                </v-list-item>
              </v-list>
            </v-menu>
          </div>
          <p v-if="downloaded" class="dl-note">{{ firstOpenTip }}</p>
          <p v-else-if="version" class="dl-note">
            {{ t('publicSite.downloadPage.version', { version }) }} ·
            <a href="#changelog" class="dl-link">{{ t('publicSite.downloadPage.changelog') }}</a>
          </p>
        </template>
      </section>

      <section v-if="!phone" class="dl-shot">
        <img
          src="/images/download/app-light.webp"
          :alt="t('publicSite.downloadPage.screenshotAlt')"
          class="dl-shot__img dl-shot__img--light"
          width="1440"
          height="900"
        />
        <img
          src="/images/download/app-dark.webp"
          :alt="t('publicSite.downloadPage.screenshotAlt')"
          class="dl-shot__img dl-shot__img--dark"
          width="1440"
          height="900"
        />
      </section>

      <section v-if="days.length" id="changelog" class="dl-log">
        <h2 class="dl-h2">{{ t('publicSite.downloadPage.changelog') }}</h2>
        <div v-for="day in shownDays" :key="day.date" class="dl-log__day">
          <time :datetime="day.date" class="dl-log__date">{{ dayLabel(day.date) }}</time>
          <ul class="dl-log__changes">
            <li v-for="change in day.changes" :key="change">{{ change }}</li>
          </ul>
        </div>
        <button v-if="days.length > 3 && !showAll" type="button" class="dl-log__more" @click="showAll = true">
          {{ t('publicSite.downloadPage.showAll') }}
        </button>
      </section>

      <section v-if="!phone" id="phone" class="dl-qr">
        <!-- eslint-disable-next-line vue/no-v-html -- an SVG this page draws from its own address -->
        <div class="dl-qr__code" role="img" :aria-label="pageUrl" v-html="qr" />
        <div class="dl-qr__text">
          <h2 class="dl-qr__title">{{ t('publicSite.downloadPage.phoneTitle') }}</h2>
          <p class="dl-qr__hint">{{ t('publicSite.downloadPage.phoneScan') }}</p>
        </div>
      </section>

      <section v-else class="dl-desktop">
        <div class="dl-desktop__text">
          <span class="dl-desktop__title">{{ t('publicSite.downloadPage.desktopTitle') }}</span>
          <span class="dl-desktop__hint">{{ t('publicSite.downloadPage.desktopHint') }}</span>
        </div>
        <BaseButton kind="secondary" @click="copyLink">{{
          copied ? t('publicSite.downloadPage.copied') : t('publicSite.downloadPage.copyLink')
        }}</BaseButton>
      </section>
    </div>
  </LandingShell>
</template>

<style scoped>
/* A door page (design-system §3.2): the title is one-off display type. */
.dl {
  display: flex;
  padding: 0 var(--gutter, 48px) 96px;
  flex-direction: column;
  align-items: center;
}

.dl-hero {
  display: flex;
  width: 100%;
  padding-top: 88px;
  background: radial-gradient(
    640px 320px at 50% 30%,
    color-mix(in srgb, var(--logo-primary) 12%, transparent),
    transparent 70%
  );
  text-align: center;
  flex-direction: column;
  align-items: center;
}

/* The app's own icon, its rounded tile drawn into the picture. */
.dl-icon {
  width: 96px;
  height: 96px;
}

.dl-title {
  margin-top: 28px;
  font-family: var(--font-display);
  font-size: 52px;
  font-weight: 700;
  line-height: 1.15;
  letter-spacing: -0.01em;
  color: var(--ink);
}

/* One button in two parts: the build for this computer, and the others. The
   halves touch, split by a hairline in the pressed amber, so they read as one
   control; the chevron turns over while the menu is open. Each half is rounded
   on its outer side only (`rounded="s-lg"` / `"e-lg"`): the theme's default
   `rounded-lg` is an !important utility that rounded the inner corners too and
   left a notch between the halves. */
.dl-split {
  display: flex;
  margin-top: 36px;
}

.dl-split__main {
  border-start-end-radius: 0;
  border-end-end-radius: 0;
}

.dl-split__more {
  min-width: 0;
  padding: 0 12px;
  border-inline-start: 1px solid var(--accent-press);
  border-start-start-radius: 0;
  border-end-start-radius: 0;
}

/* The open menu is shown by the chevron, not by Vuetify's open-menu tint, which
   made this half a lighter amber than the other. */
.dl-split__more.dl-split__more[aria-expanded='true'] > :deep(.v-btn__overlay) {
  opacity: 0;
}

.dl-split__chevron {
  transition: transform var(--dur-base) var(--ease-standard);
}

.dl-split__chevron.is-open {
  transform: rotate(180deg);
}

/* Vuetify leaves 32px after a prepended icon; the rows here sit 12px from it. */
.dl-menu :deep(.v-list-item__spacer) {
  width: 12px;
}

.dl-menu__icon {
  color: var(--muted);
}

.dl-menu__sub.dl-menu__sub {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  opacity: 1;
}

/* The page's anchors (#changelog, #phone) land below the sticky bar. */
.dl [id] {
  scroll-margin-top: var(--bar-h);
}

.dl-note {
  max-width: 480px;
  margin-top: 16px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.dl-note--strong {
  font-size: 15px;
  line-height: var(--lh-15);
  color: var(--text);
}

.dl-link {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.dl-shot {
  width: min(100%, 1040px);
  margin-top: 64px;
}

.dl-shot__img {
  display: block;
  width: 100%;
  height: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

/* Two pictures, not two colours: the screenshot matching the page's theme shows. */
:root[data-theme='dark'] .dl-shot__img--light,
:root:not([data-theme='dark']) .dl-shot__img--dark {
  display: none;
}

.dl-h2 {
  margin-bottom: 8px;
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 700;
  line-height: 1.3;
  color: var(--ink);
}

.dl-log {
  display: flex;
  width: min(100%, 760px);
  margin-top: 96px;
  flex-direction: column;
}

.dl-log__day {
  display: grid;
  padding: 24px 0;
  border-top: 1px solid var(--line);
  grid-template-columns: 160px minmax(0, 1fr);
  gap: 24px;
}

.dl-log__date {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
}

.dl-log__changes {
  display: flex;
  padding-left: 18px;
  font-size: 14px;
  line-height: var(--lh-14-loose);
  color: var(--text);
  flex-direction: column;
  gap: 6px;
}

.dl-log__more {
  padding-top: 16px;
  font: inherit;
  font-size: 14px;
  color: var(--muted);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-top: 1px solid var(--line);
}

.dl-log__more:hover {
  color: var(--ink);
}

.dl-qr {
  display: flex;
  margin-top: 88px;
  align-items: center;
  gap: 28px;
}

.dl-qr__code {
  width: 104px;
  height: 104px;
  padding: 10px;
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.dl-qr__code :deep(svg) {
  display: block;
  width: 100%;
  height: 100%;
}

.dl-qr__text {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dl-qr__title {
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
  color: var(--ink);
}

.dl-qr__hint {
  font-size: 14px;
  line-height: var(--lh-14-loose);
  color: var(--muted);
}

.dl-phone {
  display: flex;
  width: 100%;
  max-width: 360px;
  margin-top: 40px;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.dl-steps {
  display: flex;
  width: 100%;
  padding: 20px;
  list-style: none;
  text-align: left;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  flex-direction: column;
  gap: 16px;
}

.dl-steps li {
  display: flex;
  font-size: 15px;
  line-height: var(--lh-15);
  color: var(--text);
  align-items: center;
  gap: 12px;
}

.dl-steps__n {
  display: inline-flex;
  width: 24px;
  height: 24px;
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-pill);
  flex: none;
  align-items: center;
  justify-content: center;
}

.dl-steps__icon {
  color: var(--muted);
}

.dl-desktop {
  display: flex;
  width: min(100%, 360px);
  margin-top: 64px;
  padding: 14px 16px;
  background: var(--fill);
  border-radius: var(--radius-lg);
  align-items: center;
  gap: 12px;
}

.dl-desktop__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  text-align: left;
}

.dl-desktop__title {
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
}

.dl-desktop__hint {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

@media (width < 600px) {
  .dl {
    padding: 0 16px 48px;
  }

  .dl-hero {
    padding-top: 48px;
  }

  .dl-title {
    font-size: 34px;
  }

  .dl-log__day {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
}
</style>
