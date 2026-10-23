// 时间范围档位与自定义起止窗口的纯函数模块：供 SourceDetailView 与 Vitest 复用。
// RANGES 顺序即时间条渲染顺序；'custom' 不是后端档位——它只切换前端输入态，
// 请求时以 start/end 两个 query 参数代替 range（后端语义：给了 start/end 即覆盖 range 窗口）。
export const RANGES = [
  ["1m", "1月"], ["3m", "3月"], ["6m", "6月"], ["1y", "1年"],
  ["3y", "3年"], ["5y", "5年"], ["all", "全部"], ["custom", "自定义"],
];

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// 自定义起止校验：格式 YYYY-MM-DD + 顺序（start ≤ end）。
// 返回错误信息字符串供调用方直接展示，合法返回 null。
export function validateRange(start, end) {
  if (!DATE_RE.test(start ?? "")) return "开始日期格式应为 YYYY-MM-DD";
  if (!DATE_RE.test(end ?? "")) return "结束日期格式应为 YYYY-MM-DD";
  if (start > end) return "开始日期不能晚于结束日期";
  return null;
}

// 请求参数：custom 模式返回 {start, end}（range 不传）；普通档位返回 {range}。
// start/end 由 <input type="date"> 产生、天然 YYYY-MM-DD；后端对其绑参，注入面为零。
export function rangeParams(mode, start, end) {
  return mode === "custom" ? { start, end } : { range: mode };
}
