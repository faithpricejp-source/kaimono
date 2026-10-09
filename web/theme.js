// 在 <head> 里同步执行，先定主题再渲染，避免闪一下。
// desk = 发光屏（Mac App / 桌面浏览器）：暖纸色、封面墙、阴影；eink = 墨水屏（安卓设备）：纯黑白、无动画。
// 手动覆盖：localStorage own-reader-theme = desk | eink（Mac App 菜单「显示 → 墨水屏模式」切换）。
(() => {
  let t = null
  try { t = localStorage.getItem('own-reader-theme') } catch {}
  if (t !== 'desk' && t !== 'eink') t = /Android/.test(navigator.userAgent) ? 'eink' : 'desk'
  document.documentElement.dataset.theme = t
  window.OWN_READER_THEME = t
})()
