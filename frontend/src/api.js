const j = (r) => { if (!r.ok) throw new Error(`${r.status} ${r.url}`); return r.json(); };

export const api = {
  sources: () => fetch("/api/sources").then(j),
  sourceDetail: (id, range = "3m") => fetch(`/api/sources/${id}?range=${range}`).then(j),
  movements: () => fetch("/api/movements").then(j),
  jobs: () => fetch("/api/jobs/latest").then(j),
  series: (instruments, { normalize = 0, range = "6m" } = {}) =>
    fetch(`/api/series?instruments=${instruments.join(",")}&normalize=${normalize}&range=${range}`).then(j),
  // v1.3 全市场资金流：date=null → 后端取最新快照日；limit 控制双榜行数
  flows: (date = null, limit = 10) =>
    fetch(`/api/flows?limit=${limit}${date ? `&date=${date}` : ""}`).then(j),
  // 加入跟踪：注册进自选源 etf-watchlist 并从快照表回填全部历史（零外部调用）；
  // 404 → 快照中无此代码（isNotFound 判别）
  watchlistAdd: (code) =>
    fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    }).then(j),
};
