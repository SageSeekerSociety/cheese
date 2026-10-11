/**
 * 预览站里「账号那一组整页」：从登录进门到设置密码、补邮箱、改密码的每一屏，加上
 * 404 兜底页和文档站的登录页。
 *
 * 这些页面在应用里都有各自的容器取数（`AccountSignIn.vue`、`AccountSignUp.vue` 那一批），
 * 容器碰接口层，预览站碰不得。好在它们都已经被拆成「容器 → 只吃 props 的页面」：这里展示的
 * 正是产品里真会走到的那几格 —— 还没打开浏览器 / 打开过一次、还在跑 / 出错、换备用码 /
 * 用了备用码之后、填地址 / 验证码发出去之后。各屏公用的一份数据见
 * `catalogViewAccountFixtures.ts`。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和
 * `catalogAdmin.ts`、`catalogViews.ts` 同一个理由。这里的 `CatalogEntry`、`CatalogNeed`
 * 是 type-only 引用：`catalog.ts` 反过来要分册的值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  BACK_TO_APP_ACCOUNT,
  CONSENT_DOCUMENTS,
  EMAIL_PENDING,
  OAUTH_STATE,
  SIGN_IN_WAYS,
} from './catalogViewAccountFixtures'

import Views404 from '@/views/404.vue'
import AddEmailView from '@/views/account/AddEmailView.vue'
import AppSignInFinishView from '@/views/account/AppSignInFinishView.vue'
import AppSignInStartView from '@/views/account/AppSignInStartView.vue'
import BackToAppView from '@/views/account/BackToAppView.vue'
import DesktopSignInView from '@/views/account/DesktopSignInView.vue'
import RequestView from '@/views/account/emailCode/RequestView.vue'
import EmailCodeVerifyView from '@/views/account/emailCode/VerifyView.vue'
import OAuthCompleteView from '@/views/account/OAuthCompleteView.vue'
import OAuthErrorView from '@/views/account/OAuthErrorView.vue'
import OAuthSuccessView from '@/views/account/OAuthSuccessView.vue'
import OAuthVerifyView from '@/views/account/OAuthVerifyView.vue'
import PasskeyOfferView from '@/views/account/PasskeyOfferView.vue'
import PasswordStartView from '@/views/account/recover/password/StartView.vue'
import PasswordVerifyView from '@/views/account/recover/password/VerifyView.vue'
import SignInView from '@/views/account/SignInView.vue'
import SignupStartView from '@/views/account/signup/StartView.vue'
import VerifyEmailView from '@/views/account/signup/VerifyEmailView.vue'
import Verify2FAView from '@/views/account/Verify2FAView.vue'
import DocsSignInView from '@/views/DocsSignInView.vue'

/** 画法要 Vuetify、文案要语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 上面两样，再加一条 name 指得上的路由（按钮的 `to`、链接的去处按名字解析）。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 页面里「验证码对不对」「再发一封」两趟接口由容器给，预览站上走一遍就完。 */
const ok = async () => {}
const done = async () => undefined

export const VIEW_ACCOUNT_ENTRIES: CatalogEntry[] = [
  {
    id: 'views404',
    title: 'Views404',
    about: '一个人走到不存在的地址时看到的一屏：一句「页面不存在」和一颗回首页的按钮，兜住所有没写出来的路径。',
    file: 'src/views/404.vue',
    component: Views404,
    // 空态块（BaseEmptyState）里那颗图标是 <v-icon>，文字走 t()。
    needs: UI_T,
    states: [
      {
        name: '页面不存在',
        note: '路由表最后的通配项兜底画出来的样子：标题就是 404，说明一句「页面不存在」，动作是回首页。',
        props: {},
        expect: '页面不存在',
      },
    ],
  },
  {
    id: 'docs-sign-in-view',
    title: 'DocsSignInView',
    about: '平台给文档站用的登录页：正在把读者交过去时是一条转圈的进度环，出错就写清原因并给一颗重试。',
    file: 'src/views/DocsSignInView.vue',
    component: DocsSignInView,
    needs: UI_T,
    args: { error: '', loading: false },
    states: [
      {
        name: '正在打开文档',
        note: '还没出错：不是一块空白，是转圈的进度环，一条 aria-label 说明它在干什么。',
        props: {},
        expectSelector: '[role="progressbar"]',
      },
      {
        name: '打不开，给一颗重试',
        note: '出错时换成一条红色提示，把原因照原样写出来，底下那颗按钮再试一次（转圈时它显示在转）。',
        props: { error: '文档站暂时不可用，请稍后再试', loading: false },
        expect: '文档站暂时不可用，请稍后再试',
      },
    ],
  },
  {
    id: 'add-email-view',
    title: 'AddEmailView',
    about: '给账号补一个邮箱那一屏：先填地址并按规则自检，验证码发出去之后就地换成输入验证码的一步。',
    file: 'src/views/account/AddEmailView.vue',
    component: AddEmailView,
    needs: UI_T,
    args: { email: '', error: '', sending: false, signingOut: false, verify: ok, send: done },
    states: [
      {
        name: '填地址',
        note: '第一步：地址那一栏带一条「建议使用学校或单位邮箱」的提示，底下一颗发送，角落里是退出登录。',
        props: { step: 'email' },
        expect: '添加邮箱',
      },
      {
        name: '地址被占了',
        note: '这个邮箱已经绑过别的账号时，原因写在顶上那条，表还在，改一个再发。',
        props: { step: 'email', email: 'taken@school.edu', error: '这个邮箱已被其他账号使用' },
        expect: '这个邮箱已被其他账号使用',
      },
      {
        name: '验证码发出去之后',
        note: '同一屏换成验证码那一步：说清码发去了哪个地址，六个格子，一分钟冷却到了还能重发。',
        props: { step: 'code', email: 'wang@school.edu' },
        expect: '验证码已发送至 wang@school.edu',
      },
    ],
  },
  {
    id: 'app-sign-in-finish-view',
    title: 'AppSignInFinishView',
    about:
      '桌面 app 里等待浏览器里那次登录被换成自己的一份：成功是一条正在登录的线，失败说清这次登录已失效并给回登录页的按钮。',
    file: 'src/views/account/AppSignInFinishView.vue',
    component: AppSignInFinishView,
    needs: UI_T_R,
    args: { failed: false },
    states: [
      {
        name: '正在换成 app 的登录',
        note: '链接带回了这次 app 发起的登录：标题说「正在登录」，下面一条说明它在走得动。',
        props: {},
        expect: '正在登录',
      },
      {
        name: '这次登录失效了',
        note: '链接没带上 app 发起的那次登录时：标题说「登录失败」，原因写在提示里，给一颗回登录页的主按钮。',
        props: { failed: true },
        expect: '这次登录已失效，重新登录',
      },
    ],
  },
  {
    id: 'app-sign-in-start-view',
    title: 'AppSignInStartView',
    about: '浏览器里桌面 app 发起的登录刚落地的那一下：还没决定往哪去，能画的只有一条说明它在干活的进度线。',
    file: 'src/views/account/AppSignInStartView.vue',
    component: AppSignInStartView,
    needs: ['vuetify'],
    states: [
      {
        name: '只在走',
        note: '这一屏没有一句话可看：一条占两像素的进度线就是全部，容器拿到落点就把它换掉。',
        props: {},
        expectSelector: '[role="progressbar"]',
      },
    ],
  },
  {
    id: 'back-to-app-view',
    title: 'BackToAppView',
    about:
      '浏览器里登录走完之后把这次登录交回 app 的那一屏：说清是给哪个账号登录、按下去交出去，拿到 app 链接就打开 app，交不出去就写清原因。',
    file: 'src/views/account/BackToAppView.vue',
    component: BackToAppView,
    needs: UI_T,
    args: { failed: false, link: '', asking: false, handing: false, account: BACK_TO_APP_ACCOUNT },
    states: [
      {
        name: '在等链接',
        note: '还什么都没定下来：一条进度线，没有别的东西可按。',
        props: {},
        expectSelector: '[role="progressbar"]',
      },
      {
        name: '问一声，然后交出去',
        note: '要交的账号已经定了：标题说给谁登录，下面一颗「继续」把它交出去，旁边还有换账号和取消。',
        props: { asking: true },
        expect: '登录知是 app',
      },
      {
        name: 'app 链接拿到了',
        note: '交出去之后 app 给了它自己的链接：这里给一颗「打开知是」，点了把 app 叫起来。',
        props: { link: 'cheese://signin?grant=abc123' },
        expect: '回到知是 app',
      },
      {
        name: '交不出去',
        note: '交出去的这一步失败了：标题说「登录失败」，原因写在提示里，让人回 app 里再登一次。',
        props: { failed: true },
        expect: '无法把这次登录交给知是 app，在 app 中重新登录',
      },
    ],
  },
  {
    id: 'desktop-sign-in-view',
    title: 'DesktopSignInView',
    about: '桌面 app 上登录、注册、找回密码共同的起点：一颗打开浏览器的按钮，打开过之后还能再开或把链接复制出来。',
    file: 'src/views/account/DesktopSignInView.vue',
    component: DesktopSignInView,
    needs: UI_T,
    args: { entry: 'signin', opened: false, busy: false, errorMessage: '' },
    states: [
      {
        name: '还没打开浏览器',
        note: '默认这一档：说清登录会在浏览器里完成，主按钮写着「在浏览器中登录」，底下是「还没有账号？」那条。',
        props: {},
        expect: '在浏览器中登录',
      },
      {
        name: '打开过一次了',
        note: '浏览器已经开过一次：上面多一条「已打开，完成后会回到这里」，按钮换成「再次打开浏览器」，多一颗复制链接。',
        props: { opened: true },
        expect: '已在浏览器中打开。完成后会回到这里。',
      },
      {
        name: '打不开浏览器',
        note: '叫不起来浏览器就把原因写在顶上那条，按钮留着再试一次。',
        props: { opened: true, errorMessage: '无法打开默认浏览器，请手动复制链接' },
        expect: '无法打开默认浏览器，请手动复制链接',
      },
      {
        name: '注册那一档',
        note: '同一只壳、换一个入口：标题从「登录知是」变成「注册知是」，底下那行从「还没有账号」变成「已有账号」。',
        props: { entry: 'signup' },
        expect: '注册知是',
      },
    ],
  },
  {
    id: 'o-auth-complete-view',
    title: 'OAuthCompleteView',
    about:
      '第三方账号验证回来之后那一屏：加载时一条线，加载好了就在「创建新账号」和「关联已有账号」之间选，各自一份表单；状态拿不到就写清并给回登录页的路。',
    file: 'src/views/account/OAuthCompleteView.vue',
    component: OAuthCompleteView,
    needs: UI_T_R,
    args: {
      loading: false,
      error: '',
      oauthState: OAUTH_STATE,
      providerName: '学校账号',
      step: 'form',
      requireInviteCode: false,
      registrationConfigReady: true,
      creating: false,
      binding: false,
      consentDocuments: CONSENT_DOCUMENTS,
      consentLoadError: '',
      verifyEmail: ok,
      sendCode: done,
    },
    states: [
      {
        name: '还在读第三方带回来的状态',
        note: '身份还没到手（或者还在校验）：一条进度线，别的什么都不画。',
        props: { loading: true, oauthState: null },
        expectSelector: '[role="progressbar"]',
      },
      {
        name: '创建新账号',
        note: '状态到手了：标题跟身份里的名字打招呼，默认停在「创建新账号」——用户名、昵称、邮箱各一栏，底下是同意协议的勾。',
        props: {},
        expect: '欢迎，王小明',
      },
      {
        name: '这个站要邀请码',
        note: '注册要邀请码时多一栏：和注册页一样，没填就提交会被自检拦下。',
        props: { requireInviteCode: true },
        expect: '邀请码',
      },
      {
        name: '状态拿不到',
        note: '第三方的状态没回来（过期或出错）：标题说「无法继续登录」，原因写在导语里，一颗回登录页的主按钮。',
        props: { oauthState: null, error: '这次登录已过期，请重新发起' },
        expect: '无法继续登录',
      },
    ],
  },
  {
    id: 'o-auth-error-view',
    title: 'OAuthErrorView',
    about: '第三方登录失败之后那一屏：标题写着结果，错误码带出原因时另起一条说清，回登录页或在提供方那里再试一次。',
    file: 'src/views/account/OAuthErrorView.vue',
    component: OAuthErrorView,
    needs: UI_T_R,
    args: { errorDescription: '', providerId: '', providerName: '学校账号' },
    states: [
      {
        name: '只有结果',
        note: '错误码没带出别的话：标题说「登录失败」就够了，底下只有一颗回登录页的主按钮。',
        props: {},
        expect: '登录失败',
      },
      {
        name: '带出了原因',
        note: '错误码能翻成一句话时另起一条：原因写在提示里，不再和标题重复。',
        props: { errorDescription: '你在提供方那里取消了授权' },
        expect: '你在提供方那里取消了授权',
      },
      {
        name: '能在提供方重试',
        note: '地址里带着提供方时多一颗次要按钮：它按提供方的名字写「重新使用 学校账号 登录」，点了直接跳回那边。',
        props: { providerId: 'school-sso' },
        expect: '重新使用 学校账号 登录',
      },
    ],
  },
  {
    id: 'o-auth-success-view',
    title: 'OAuthSuccessView',
    about:
      '第三方登录回来之后收尾的那一屏：还在读会话时说正在完成哪一家的登录，读好了说登录成功正在跳转，出错就把原因写清并给回登录页的路。',
    file: 'src/views/account/OAuthSuccessView.vue',
    component: OAuthSuccessView,
    needs: UI_T_R,
    args: { processing: true, error: '', providerName: '学校账号' },
    states: [
      {
        name: '正在完成第三方登录',
        note: '还在拿重定向里那块 cookie 换会话：标题说「正在登录」，导语点名是哪一家，下面一条进度线。',
        props: {},
        expect: '正在完成 学校账号 登录',
      },
      {
        name: '登录成功，正在跳转',
        note: '会话换好了：标题从「正在登录」变成「登录成功」，导语说「正在跳转」，等着被带到该去的地方。',
        props: { processing: false },
        expect: '正在跳转',
      },
      {
        name: '没换成',
        note: '拿着 cookie 也没换到会话：标题说「登录失败」，原因写在提示里，给一颗回登录页的主按钮。',
        props: { error: '登录信息已失效，请重新登录' },
        expect: '登录信息已失效，请重新登录',
      },
    ],
  },
  {
    id: 'o-auth-verify-view',
    title: 'OAuthVerifyView',
    about: '把第三方账号关联到已有账号时问一次密码：说清在关联哪个邮箱，密码不对可以写在框上，也可以另起一条写在上面。',
    file: 'src/views/account/OAuthVerifyView.vue',
    component: OAuthVerifyView,
    needs: UI_T_R,
    args: { email: 'wang@school.edu', error: '', errorMessage: '', loading: false },
    states: [
      {
        name: '问一次密码',
        note: '默认这一格：标题说「关联账号」，导语点名是哪个邮箱，一栏密码，一颗「验证并关联」，底下回登录页。',
        props: {},
        expect: '关联账号',
      },
      {
        name: '密码不对，写在框上',
        note: '只是密码错时不给整页刷红：那句话落在密码框自己底下，人就在原地改。',
        props: { errorMessage: '密码不正确' },
        expect: '密码不正确',
      },
      {
        name: '一般性失败',
        note: '不是密码的问题（比如地址已被占用）时另起一条：原因写在表单上面，框还是干净的。',
        props: { error: '这个邮箱已经绑定了另一个账号' },
        expect: '这个邮箱已经绑定了另一个账号',
      },
    ],
  },
  {
    id: 'passkey-offer-view',
    title: 'PasskeyOfferView',
    about: '登录之后劝人加一枚通行密钥：接受和暂不添加一样大，被拒过的账号还能安静地说一声「不再提醒」。',
    file: 'src/views/account/PasskeyOfferView.vue',
    component: PasskeyOfferView,
    needs: UI_T,
    args: { offer: { canStopAsking: false }, adding: false, declining: null, errorMessage: '' },
    states: [
      {
        name: '劝加一枚',
        note: '默认这一格：说清在这台设备上用指纹/面容登录，两颗等大的按钮 —— 一颗加，一颗暂不添加。',
        props: {},
        expect: '添加通行密钥',
      },
      {
        name: '被拒过，可以不再提醒',
        note: '这个人之前拒过：多一条安静的字「不再提醒」，点了以后就不问，明显不是被推着走。',
        props: { offer: { canStopAsking: true } },
        expect: '不再提醒',
      },
      {
        name: '加的时候出了岔子',
        note: '这台设备建不出通行密钥时把原因写在顶上那条，两颗按钮都还留着。',
        props: { errorMessage: '这台设备不支持通行密钥' },
        expect: '这台设备不支持通行密钥',
      },
    ],
  },
  {
    id: 'sign-in-view',
    title: 'SignInView',
    about: '登录页：通知或错误，进入的几种方式（上次用过的排在前面并带一枚小标），和用户名密码表单。',
    file: 'src/views/account/SignInView.vue',
    component: SignInView,
    needs: UI_T_R,
    args: {
      errorMessage: '',
      notice: null,
      parts: ['alt', 'or', 'form'],
      alternatives: SIGN_IN_WAYS,
      last: null,
      busy: null,
      submitting: false,
      waiting: false,
      initialUsername: '',
    },
    states: [
      {
        name: '默认顺序：一click的方式在前',
        note: '这台浏览器没有登录历史：一click的方式排前面，然后是「或」，最后是密码表单；标题那句带一颗去注册的链接。',
        props: {},
        expect: '还没有账号？',
      },
      {
        name: '上次用的是通行密钥',
        note: '有登录历史时那一样先画、并挂一枚「上次使用」的小标，容器也把它排到最前面。',
        props: { last: 'passkey' },
        expect: '上次使用',
      },
      {
        name: '一次尝试被拒',
        note: '用户名密码不对时把原因写在顶上那条；等待期里表单那颗按钮被按住，要等够秒数。',
        props: { errorMessage: '用户名或密码不正确，请 60 秒后再试' },
        expect: '用户名或密码不正确，请 60 秒后再试',
      },
      {
        name: '带着一句通知回来',
        note: '通知走的是另一条绿色提示：比如刚重置完密码、或者刚退出登录，说一句再让人填表。',
        props: { notice: '密码已重置，请用新密码登录' },
        expect: '密码已重置，请用新密码登录',
      },
    ],
  },
  {
    id: 'verify2-fa-view',
    title: 'Verify2FAView',
    about:
      '两步验证那一步：信任设备的勾、当前验证码类型的输入框、回登录页的链接，和用过备用码之后劝人换一组新码的提醒弹窗。',
    file: 'src/views/account/Verify2FAView.vue',
    component: Verify2FAView,
    needs: UI_T_R,
    // 用过备用码之后那颗提醒是 v-dialog，画在 body 上。
    teleport: true,
    args: {
      codeType: 'totp',
      errorMessage: '',
      loading: false,
      submitDisabled: true,
      trustDevice: false,
      totpCode: '',
      backupCode: '',
      backTo: '/account/signin',
      showBackupCodeDialog: false,
    },
    states: [
      {
        name: '输入验证器验证码',
        note: '默认这一格：导语说清去验证器应用里取 6 位码，勾选框守着「这台设备 30 天内不再问」，底下还有连续输错会锁多久。',
        props: {},
        expect: '输入身份验证器应用中的 6 位验证码',
      },
      {
        name: '改用备用码',
        note: '换成备用码时只换要填的东西和那颗按钮：导语变成「输入一个 8 位备用码」，按钮写着「验证备用码」。',
        props: { codeType: 'backup' },
        expect: '输入一个 8 位备用码',
      },
      {
        name: '输错了',
        note: '码不对时把原因写在顶上那条，框还在原处让人重填。',
        props: { errorMessage: '验证码不正确' },
        expect: '验证码不正确',
      },
      {
        name: '用过备用码之后的提醒',
        note: '拿备用码过了这一关，就地弹一颗确认框：说清每个备用码只能用一次，劝人现在生成一组新的（这里没点开，只画出来）。',
        props: { showBackupCodeDialog: true },
        expect: '每个备用码只能使用一次。建议现在生成一组新的备用码。',
      },
    ],
  },
  {
    id: 'request-view',
    title: 'RequestView',
    about: '用邮箱验证码登录的第一步：填地址、按规则自检、发送，以及回登录页的那条路。',
    file: 'src/views/account/emailCode/RequestView.vue',
    component: RequestView,
    needs: UI_T_R,
    args: { initialEmail: '', error: '', waiting: false, submitting: false, backToSignIn: '/account/signin' },
    states: [
      {
        name: '填地址',
        note: '默认这一格：标题说「邮箱验证码登录」，一栏邮箱，一颗「发送验证码」，底下回登录页。',
        props: {},
        expect: '邮箱验证码登录',
      },
      {
        name: '发送太频繁',
        note: '一分钟内连发会被服务器拦下：原因写在顶上那条，等够了再点那颗按钮。',
        props: { error: '发送太频繁，请稍后再试' },
        expect: '发送太频繁，请稍后再试',
      },
    ],
  },
  {
    id: 'email-code-verify-view',
    title: 'EmailCodeVerifyView',
    about: '用邮箱验证码登录的第二步：说清验证码发到了哪个地址、六个格子、一分钟重发倒计时和回登录页的路。',
    file: 'src/views/account/emailCode/VerifyView.vue',
    component: EmailCodeVerifyView,
    needs: UI_T_R,
    args: {
      pending: EMAIL_PENDING,
      code: '',
      error: '',
      submitting: false,
      waiting: false,
      resending: false,
      resendWait: 0,
      backToSignIn: '/account/signin',
    },
    states: [
      {
        name: '输入验证码',
        note: '默认这一格：标题说「输入验证码」，导语点名码发去了哪个地址，六个格子，填满才让提交。',
        props: {},
        expect: '验证码已发送至 wang@school.edu',
      },
      {
        name: '重发倒计时',
        note: '刚发过一封时那颗「重新发送」先收起来，换成一枚还有多少秒的倒计时，秒数一到它才回来。',
        props: { resendWait: 42 },
        expect: '42 秒后可重新发送',
      },
      {
        name: '验证码不对',
        note: '码不对或过期时把原因写在顶上那条，格子清空让人重新输。',
        props: { error: '验证码不正确，请重新输入' },
        expect: '验证码不正确，请重新输入',
      },
    ],
  },
  {
    id: 'password-start-view',
    title: 'PasswordStartView',
    about: '找回密码的第一步：填注册邮箱寄出一封重置链接，寄出去之后那一屏就地换成一句「查收邮件」。',
    file: 'src/views/account/recover/password/StartView.vue',
    component: PasswordStartView,
    needs: UI_T_R,
    args: { error: '', sent: false, submitting: false },
    states: [
      {
        name: '填注册邮箱',
        note: '默认这一格：说清会往这个邮箱寄一条重置链接，一颗「发送重置邮件」，底下是「想起密码了？」和回登录页。',
        props: {},
        expect: '发送重置邮件',
      },
      {
        name: '邮件已发出',
        note: '寄出去之后表单让位给结果：标题变成「查收邮件」，一句「重置密码邮件已发送」，只留一颗回登录页的按钮。',
        props: { sent: true },
        expect: '查收邮件',
      },
      {
        name: '寄不出去',
        note: '发送失败时原因写在顶上那条，表还在，改个地址或稍后再试。',
        props: { error: '发送失败，请稍后再试' },
        expect: '发送失败，请稍后再试',
      },
    ],
  },
  {
    id: 'password-verify-view',
    title: 'PasswordVerifyView',
    about: '重置链接打开的第二步：把新密码输两遍，账号名藏在表单里给密码管理器认，两遍不一致就地写在框上。',
    file: 'src/views/account/recover/password/VerifyView.vue',
    component: PasswordVerifyView,
    needs: UI_T_R,
    args: { username: 'wangxiaoming', error: '', submitting: false },
    states: [
      {
        name: '设置新密码',
        note: '默认这一格：标题说「设置新密码」，新密码和确认各一栏，带一条至少 8 位、要有字母数字特殊字符的提示。',
        props: {},
        expect: '设置新密码',
      },
      {
        name: '链接已失效',
        note: '重置令牌过期时原因写在顶上那条：让人回登录页重新走一遍找回密码。',
        props: { error: '这条重置链接已失效，请重新申请' },
        expect: '这条重置链接已失效，请重新申请',
      },
    ],
  },
  {
    id: 'signup-start-view',
    title: 'SignupStartView',
    about: '创建账号那一屏：用户名、昵称、邮箱、密码两组字段各自自检，要邀请码时多一栏，底下是同意协议的勾。',
    file: 'src/views/account/signup/StartView.vue',
    component: SignupStartView,
    needs: UI_T_R,
    args: {
      error: '',
      requireInviteCode: false,
      registrationConfigReady: true,
      submitting: false,
      consentDocuments: CONSENT_DOCUMENTS,
      consentLoadError: '',
    },
    states: [
      {
        name: '填表',
        note: '默认这一格：标题「创建账号」，五栏字段各带自己的规则，底下是协议那一勾，主按钮要配置到齐了才亮。',
        props: {},
        expect: '创建账号',
      },
      {
        name: '这个站要邀请码',
        note: '注册要邀请码时多一栏：和别的一样自带自检，空着提交会被拦下。',
        props: { requireInviteCode: true },
        expect: '邀请码',
      },
      {
        name: '注册失败',
        note: '后端拒了（用户名被占、邮箱重复）就把原因写在顶上那条，表里的内容不动，改一处再交。',
        props: { error: '这个用户名已被占用' },
        expect: '这个用户名已被占用',
      },
      {
        name: '协议没取到',
        note: '协议版本没拿回来时，同意那一栏底下写一句取不到的原因，主按钮也按配置没齐按住。',
        props: { registrationConfigReady: false, consentLoadError: '暂时无法获取协议，刷新页面重试' },
        expect: '暂时无法获取协议，刷新页面重试',
      },
    ],
  },
  {
    id: 'verify-email-view',
    title: 'VerifyEmailView',
    about: '注册后验证邮箱那一屏：六个格子，刷新掉了密码就再要一次密码，同意没留住就再问一次，底下是重发倒计时。',
    file: 'src/views/account/signup/VerifyEmailView.vue',
    component: VerifyEmailView,
    needs: UI_T_R,
    args: {
      email: 'wang@school.edu',
      error: '',
      needsPassword: false,
      needsConsent: false,
      waiting: false,
      submitting: false,
      resendWait: 0,
      resending: false,
      consentDocuments: CONSENT_DOCUMENTS,
      consentLoadError: '',
    },
    states: [
      {
        name: '输入验证码',
        note: '默认这一格：标题「验证邮箱」，导语点名码发去了哪个地址，六个格子，底下是重发和回登录页。',
        props: {},
        expect: '验证码已发送至 wang@school.edu',
      },
      {
        name: '刷新掉了密码，再要一次',
        note: '刷新把注册时那半截状态丢了：多一栏密码，说清「再次输入注册时设置的密码」，免得验证过了却没密码可用。',
        props: { needsPassword: true },
        expect: '再次输入注册时设置的密码',
      },
      {
        name: '重发倒计时',
        note: '刚发过一封时「重新发送」先收起来，换成一枚还有多少秒的倒计时。',
        props: { resendWait: 30 },
        expect: '30 秒后可重新发送',
      },
      {
        name: '验证失败',
        note: '码不对或过期时把原因写在顶上那条，格子清空让人重来。',
        props: { error: '验证码不正确，请重新输入' },
        expect: '验证码不正确，请重新输入',
      },
    ],
  },
]
