// Browser evidence mounts the owning component, not a duplicate preview UI.
import { createApp, defineComponent, h, ref } from "vue";
import PanelPreview from "../frontend/src/components/panels/PanelPreview.vue";
import vuetify from "../frontend/src/plugins/vuetify";
import { setLocale } from "../frontend/src/i18n";
import "../frontend/src/style.css";

setLocale("zh-CN");
const active = ref(true);
const refreshTick = ref(0);
const path = ref("site/index.html");
Object.assign(window, {
  previewFixture: {
    refresh: () => {
      refreshTick.value += 1;
    },
    activate: (value: boolean) => {
      active.value = value;
    },
    path: (value: string) => {
      path.value = value;
    },
    theme: (value: string) => {
      vuetify.theme.global.name.value = value;
      document.documentElement.dataset.theme = value;
    },
  },
});
createApp(
  defineComponent({
    setup: () => () =>
      h("div", { class: "fixture-layout" }, [
        h("main", { class: "fixture-main" }, [
          h("h2", "正式预览组件验证"),
          h("p", "本地API替身与独立内容域，不是生产鉴权。"),
          h("button", { id: "focus-anchor" }, "保持焦点"),
        ]),
        h("aside", { class: "fixture-right" }, [
          h(PanelPreview, {
            topicId: "fixture-topic",
            projectId: "fixture-project",
            active: active.value,
            refreshTick: refreshTick.value,
            path: path.value,
          }),
        ]),
      ]),
  }),
)
  .use(vuetify)
  .mount("#app");
