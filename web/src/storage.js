// 只放「界面偏好」这类丢了也无所谓的东西。
//
// 任务数据不在这里：分镜图和 JSON 落在 outputs/<task_id>/，
// 任务列表由后端扫磁盘给出（GET /api/tasks）。
// 所以换浏览器、清缓存、重启服务都不影响任务本身。

const VIEW_KEY = 'storyboard.view'

// 表格 / 画廊视图偏好。切一次就记住，刷新不用再切第二次。
export function loadView() {
  try {
    const v = localStorage.getItem(VIEW_KEY)
    return v === 'gallery' || v === 'table' ? v : 'gallery'
  } catch {
    return 'gallery'
  }
}

export function saveView(view) {
  try {
    localStorage.setItem(VIEW_KEY, view)
  } catch {
    /* 隐私模式下 localStorage 可能不可写，忽略即可，不影响主流程 */
  }
}
