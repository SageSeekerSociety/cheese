# 原始证据

## source.json

```
{
  "commit": "9d2c8ecf0ac399b4819735af179af9de9a47225f",
  "directory": "/var/tmp/ask-browser-9d2-ike73kbw",
  "files": {
    "frontend/src/components/ask/AskGroupFlow.vue": "2b9197a5d07c607a07de5edfde45a43504edbfe99a5eb2b41f877e09cb35ce9e",
    "frontend/src/components/ask/AskQuestionForm.vue": "df6864f3fb37a54590625bb4cc821074b8b7972288a0a5d206d5f17a0b0fd950",
    "frontend/src/composables/useAskGroups.ts": "481a5e6b8e2b3a6387d2f482291d77e87f2dd4f1214aa3b3a81e8334b163ecc7",
    "frontend/src/preview/ask.ts": "49001a89aa51ba10c3f426e17dfc17490a9284b89c98cd4121499d2aecd5a049"
  },
  "mode": "real components, local fixture; no backend connection"
}

```

## browser-result.json

```
{
  "renderer": "Chromium real Vue components",
  "source": "9d2c8ecf0ac399b4819735af179af9de9a47225f",
  "backend": "not connected; deterministic local fixture",
  "tests": [
    "real mouse selects without sending",
    "draft survives page reload",
    "go-back closes confirm without sending",
    "question tab changes actual component",
    "390px no horizontal overflow"
  ],
  "passed": true
}
```

## layout.json

```
{"width":390,"scrollWidth":380,"theme":"dark","bodyBackground":"rgb(20, 21, 23)","text":"rgb(211, 214, 219)","font":true,"textareas":[{"width":320,"right":363}],"bodyText":"提问体验：真实组件演示\n\n固定本地数据，未连接后端。与真实房间共用 AskGroupFlow 和 AskQuestionForm；回执是标注的示例。\n\n点选仅存草稿；整组回去补／照样交；刷新可恢复此演示草稿。\n\n已答 0 / 2 题\n刷新状态\n\n示例网络超时：提交结果未确认，请保留原操作后刷新\n\n第 1 题\n第 2 题\n本轮先接入哪个入口？\n选择后提交\n1\n接入真实房间\n直接复用房间里的表单组件与组提交契约\n2\n先整理证据\n保留已通过记录，先补缺项\n自己填写\n以上都不是\n补充说明（可选）\n\n草稿已存于此浏览器，尚未提交\n\n稍后回答\n\n待提交 1 题 · 稍后 0 题 · 未答 1 题\n\n提交整组"}
```

## pdf-text.txt

```
提问体验：前端实现与证据
2026-10-01 · PR #2213 保持草稿 · 未上线
此 PDF 使用实际 AskGroupFlow / AskQuestionForm 组件截图。数据与回执是固定本地演示，未连接后端。不能证明原执行者
收到了答案或已完成接续。
固定源码
截图基线：9d2c8ecf0ac399b4819735af179af9de9a47225f。包含组四项修复、旧操作重放当前行修复及前端 CI 拆分；从该提
交独立导出，未修改产品源码。
已验证
旧状态 8/8、旧表单 4/4 已收，未复跑。本轮新增组控制器 9/9、单题生命周期 4/4、组表单 3/3、冲突条件 1/1；随后新增四项
组回归与一项旧操作重放回归均通过；各轮仅跑新增用例。修复组合源类型检查退出 0，不代表本截图基线完整 CI 已绿。
八目标覆盖边界
 目标             前端实现                                     本份证据未覆盖
 多题进度           固定成员、切题、已答数、未答确认                         真实组接口
 解释与自由输入        解释、note、reject、补充说明                      真实提交联调
 草稿             账号/版本隔离，新输入不被恢复覆盖                        多标签并发
 稍后找回           settlement恢复、精准block定位                   后端blockId
 重试             原op与payload、发送前持久化                       真实旧op重放
 回执与更正          原答者、历史、saved/received/completed区分        单题真实回执
 刷新恢复           版本单调、账号代次、迟到响应隔离                         完整真实HTTP
 原执行者接续         只呈现服务端关联回执                               原生身份/接续，由S验
选择与草稿
真实鼠标选择只存演示草稿，不发送后端请求。
刷新恢复
本地固定数据演示刷新后选项恢复；不等于真实服务端联调。
深色与回执
不确定回执为标注示例；当前答案、更正历史与接续状态分开。
390px 窄屏与失败提示
保留输入，显示错误；真实 Vue 组件渲染，不是静态重画。

```

## 文件 SHA256

- `dark-receipt.png`: `63f848de5a60b3876cbd3b283f967d189214204fd8e0db2b23ebaa7c0b37985c`
- `light-selected.png`: `dba4a3a5f7ece58db7af06bb92b1afa38ce40809f19bd88a2c842a88f2f45744`
- `narrow-error.png`: `7b3a6f486a339335fcfe6ad1b6c823c1879531e6695294a68372aa8003fd6f1e`
- `pdf-cover.png`: `68da8c59e8e1402f3284fff484d1a6ab2084c0a12b78bedc84c551a8ba08af55`
- `pdf-page-1.png`: `3703fddc9a36a6648b7181a08abf181762ed3a2344d625d496b8f665e1d0f3b5`
- `pdf-page-2.png`: `12a6cdd550cd141f6ab8f6d6b13aab9d17c4d1d97821ecc7c89445ccd6385962`
- `pdf-page-3.png`: `5727516db0d4dad921742b09799810957437a17f5c5b5599317ae2a80cd6c568`
- `pdf-page-4.png`: `2da38a0561ec3c9db04c8681c44da1333640d7ea3773876a6095fa20ae21d1cb`
- `pdf-page-5.png`: `646f34fde8ab2eec1c6953189bf2b91b21fa72f72b61e8bfd3d726250b87f371`
- `refresh-restored.png`: `e89d97f4a8e6be2539be72a13df752fd6f53ee06e05b458c6a483093171ceab3`
- `提问体验前端证据.pdf`: `8ff359b73628555d5916bb054d9dfb1d83ad5f4896f00f4a8fc9d91012abe395`
