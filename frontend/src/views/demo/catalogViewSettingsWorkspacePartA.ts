/**
 * 预览站里「账号设置与项目工作台那一组整页」的一册分卷——账号设置那一组的前一半（通用、通知、个人资料、实名信息）。
 *
 * 它和同级的另外两份分卷一起，由 `catalogViewSettingsWorkspace.ts` 合并成
 * `VIEW_SETTINGS_WORKSPACE_ENTRIES` 交给 `catalogViews.ts`；各屏公用数据见
 * `catalogViewSettingsWorkspaceFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { NOTIF_PREFS, REAL_NAME_LOGS, REAL_NAME_RECORD, SETTINGS_USER } from './catalogViewSettingsWorkspaceFixtures'

import { t } from '@/i18n'
import General from '@/views/user/settings/General.vue'
import NotificationsView from '@/views/user/settings/NotificationsView.vue'
import ProfileView from '@/views/user/settings/ProfileView.vue'
import RealNameView from '@/views/user/settings/RealNameView.vue'

/** 画法要 Vuetify、文案要语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']
/** 上面那两样，再加一条 name 指得上的路由（页面里的链接、按钮按名字指路时用）。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const VIEW_SETTINGS_WORKSPACE_ENTRIES_A: CatalogEntry[] = [
  {
    id: 'settings-general-view',
    title: 'General',
    get about() {
      return t('catalog.settingsGeneralView.about')
    },
    file: 'src/views/user/settings/General.vue',
    component: General,
    needs: UI_T,
    states: [
      {
        get name() {
          return t('catalog.settingsGeneralView.s1.name')
        },
        get note() {
          return t('catalog.settingsGeneralView.s1.note')
        },
        props: {},
        // 这一行只有这一件控件：那颗开关的输入框是它自己的锚。
        expectSelector: '#general-open-at-login',
        expect: '开机时启动',
      },
    ],
  },
  {
    id: 'notifications-view',
    title: 'NotificationsView',
    get about() {
      return t('catalog.notificationsView.about')
    },
    file: 'src/views/user/settings/NotificationsView.vue',
    component: NotificationsView,
    needs: UI_T,
    args: {
      prefs: NOTIF_PREFS,
      loading: false,
      failed: false,
      failingReason: null,
      saving: false,
      saved: false,
      error: '',
      pending: null,
    },
    states: [
      {
        get name() {
          return t('catalog.notificationsView.s1.name')
        },
        get note() {
          return t('catalog.notificationsView.s1.note')
        },
        props: {},
        // 矩阵那张表只有读到偏好才画。
        expectSelector: '.notif-matrix',
        expect: '按类型',
      },
      {
        get name() {
          return t('catalog.notificationsView.s2.name')
        },
        get note() {
          return t('catalog.notificationsView.s2.note')
        },
        props: { loading: true, prefs: null },
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.notificationsView.s3.name')
        },
        get note() {
          return t('catalog.notificationsView.s3.note')
        },
        props: { failed: true, prefs: null, failingReason: '通知设置暂时读不出来' },
        expect: '通知设置暂时读不出来',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.notificationsView.s4.name')
        },
        get note() {
          return t('catalog.notificationsView.s4.note')
        },
        props: { pending: 'mention.inApp' },
        // 只有正在存的那一格按钮被按住，别的圆点都还亮着。
        expectSelector: 'button.notif-dot[disabled]',
      },
      {
        get name() {
          return t('catalog.notificationsView.s5.name')
        },
        get note() {
          return t('catalog.notificationsView.s5.note')
        },
        props: { pending: 'inAppEnabled' },
        // 按住的是「站内」那一颗：它外面那层 v-input 带上了 disabled。
        expectSelector: '.v-input--disabled #notif-in-app',
      },
    ],
  },
  {
    id: 'profile-view',
    title: 'ProfileView',
    get about() {
      return t('catalog.profileView.about')
    },
    file: 'src/views/user/settings/ProfileView.vue',
    component: ProfileView,
    needs: UI_T_R,
    args: {
      user: SETTINGS_USER,
      shownAvatar: '',
      avatarSeed: '王小明',
      canRemoveAvatar: true,
      changingAvatar: false,
      removingAvatar: false,
      avatarAccept: 'image/png,image/jpeg,image/webp,image/gif',
      savedNickname: '王小明',
      savedIntro: '教操作系统，也带嵌入式。',
      saving: false,
      saved: false,
      error: '',
    },
    states: [
      {
        get name() {
          return t('catalog.profileView.s1.name')
        },
        get note() {
          return t('catalog.profileView.s1.note')
        },
        props: {},
        // 昵称那一栏是这一页自己的草稿框。
        expectSelector: '#profile-nickname',
        expect: '昵称',
      },
      {
        get name() {
          return t('catalog.profileView.s2.name')
        },
        get note() {
          return t('catalog.profileView.s2.note')
        },
        props: { user: null },
        // 页头此时是这一页唯一的子元素（表单那一片整块没画）。
        expectSelector: '.settings-page > header.profile__head:first-child:last-child',
      },
      {
        get name() {
          return t('catalog.profileView.s3.name')
        },
        get note() {
          return t('catalog.profileView.s3.note')
        },
        props: { saving: true },
        expectSelector: '.save-status--saving',
      },
      {
        get name() {
          return t('catalog.profileView.s4.name')
        },
        get note() {
          return t('catalog.profileView.s4.note')
        },
        props: { saved: true },
        expect: '已保存',
        expectSelector: '.save-status--saved',
      },
      {
        get name() {
          return t('catalog.profileView.s5.name')
        },
        get note() {
          return t('catalog.profileView.s5.note')
        },
        props: { error: '昵称已被占用' },
        expect: '昵称已被占用',
        expectSelector: '.save-status--error',
      },
    ],
  },
  {
    id: 'real-name-view',
    title: 'RealNameView',
    get about() {
      return t('catalog.realNameView.about')
    },
    file: 'src/views/user/settings/RealNameView.vue',
    component: RealNameView,
    needs: UI_T_R,
    args: {
      loaded: false,
      loadFailed: false,
      editing: false,
      record: null,
      full: null,
      revealing: false,
      opening: false,
      saving: false,
      deleting: false,
      rows: [],
      logTotal: 0,
      logsFailed: false,
      logsHaveMore: false,
      loadingLogs: false,
    },
    states: [
      {
        get name() {
          return t('catalog.realNameView.s1.name')
        },
        get note() {
          return t('catalog.realNameView.s1.note')
        },
        props: {},
        expectSelector: 'section.realname__pending[aria-busy="true"]',
      },
      {
        get name() {
          return t('catalog.realNameView.s2.name')
        },
        get note() {
          return t('catalog.realNameView.s2.note')
        },
        props: { loadFailed: true },
        expect: '未能加载实名信息',
        expectSelector: 'section.realname__pending[aria-busy="false"]',
      },
      {
        get name() {
          return t('catalog.realNameView.s3.name')
        },
        get note() {
          return t('catalog.realNameView.s3.note')
        },
        props: { loaded: true },
        expect: '还没有填写实名信息',
        expectSelector: 'section.realname__empty',
      },
      {
        get name() {
          return t('catalog.realNameView.s4.name')
        },
        get note() {
          return t('catalog.realNameView.s4.note')
        },
        props: { loaded: true, editing: true },
        expect: '填写实名信息',
        // 姓名那一栏：表单里第一格，必填。
        expectSelector: '#realname-realName',
      },
      {
        get name() {
          return t('catalog.realNameView.s5.name')
        },
        get note() {
          return t('catalog.realNameView.s5.note')
        },
        props: { loaded: true, record: REAL_NAME_RECORD },
        expect: '你的信息',
        expectSelector: '.realname__actions',
      },
      {
        get name() {
          return t('catalog.realNameView.s6.name')
        },
        get note() {
          return t('catalog.realNameView.s6.note')
        },
        props: { loaded: true, record: REAL_NAME_RECORD, rows: REAL_NAME_LOGS, logTotal: 2 },
        expect: '你查看了完整信息',
        expectSelector: '.realname__log',
      },
    ],
  },
]
