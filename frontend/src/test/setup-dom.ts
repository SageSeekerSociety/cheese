// happy-dom 缺、而每个浏览器都有的 DOM 接口，在这里补上。
//
// `document.elementsFromPoint`：Vuetify 的菜单「贴着鼠标位置打开」（`target` 是一个
// 坐标，右键菜单就是这样）时，跟着滚动重新定位要拿它找那个坐标底下的元素。happy-dom
// 没有它，菜单一开就在测试里抛一个没人接的异常。补一个「坐标底下什么都没有」的版本：
// 测试里没有布局，本来也没有元素在哪个坐标上。
if (typeof document.elementsFromPoint !== 'function') {
  document.elementsFromPoint = () => []
}
