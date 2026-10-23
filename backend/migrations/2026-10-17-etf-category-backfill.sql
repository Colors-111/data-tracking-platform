-- v1.3.x 大 ETF 补齐：从全市场快照表回填 instruments 内全部 share 品种缺失的历史观测
-- （新补 11 只大 ETF 无任何观测，取全快照史；已跟踪品种存量观测经
--   ON CONFLICT DO NOTHING 保留——幂等，可重复执行；同时惠及 watchlist 注册品种的缺口日）。
-- 单位：etf_snapshots.shares 采集时已 ÷1e8 存为亿份，与 observations.share 同单位，直取。
-- 前置：新 instruments 已由 seed.sql 补种（init_db）。
INSERT INTO observations (instrument_id, metric, trade_date, value, raw_json, fetched_at)
SELECT i.id, i.metric, s.trade_date, s.shares, NULL, datetime('now')
  FROM etf_snapshots s
  JOIN instruments i ON i.code = s.code
 WHERE i.metric = 'share' AND s.shares IS NOT NULL
ON CONFLICT(instrument_id, metric, trade_date) DO NOTHING;
