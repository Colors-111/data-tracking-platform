INSERT OR IGNORE INTO sources(id,name,group_name,freq,fetcher,unit,movement_th,lag_days,enabled,sort_order) VALUES
 ('hs300-etf','沪深300 ETF','指数 ETF','daily','akshare.sse_etf_scale','亿份',1.0,1,1,1),
 ('cu-inventory','电解铜库存','大宗商品','daily','akshare.futures_inventory_em','吨',2.0,1,1,2),
 ('coal-price','煤炭价格','大宗商品','daily','scrapers.coal_price','元/吨',1.0,1,1,3),
 ('csi500-etf','中证500 ETF','指数 ETF','daily','akshare.etf_scale','亿份',1.0,1,1,10),
 ('star50-etf','科创50 ETF','指数 ETF','daily','akshare.etf_scale','亿份',1.0,1,1,11),
 ('chinext-etf','创业板 ETF','指数 ETF','daily','akshare.etf_scale','亿份',1.0,1,1,12),
 ('dividend-etf','红利 ETF','指数 ETF','daily','akshare.etf_scale','亿份',1.0,1,1,13),
 ('a500-etf','中证A500 ETF','指数 ETF','daily','akshare.etf_scale','亿份',1.0,1,1,14),
 ('sse50-etf','上证50 ETF','指数 ETF','daily','akshare.sse_etf_scale','亿份',1.0,1,1,15),
 ('zz1000-etf','中证1000 ETF','指数 ETF','daily','akshare.etf_scale','亿份',1.0,1,1,16);

INSERT OR IGNORE INTO instruments(id,source_id,code,metric,name,sort_order) VALUES
 ('510300','hs300-etf','510300','share','沪深300ETF华泰柏瑞',1),
 ('510310','hs300-etf','510310','share','沪深300ETF易方达',2),
 ('510320','hs300-etf','510320','share','沪深300ETF中金',3),
 ('510330','hs300-etf','510330','share','沪深300ETF华夏',4),
 ('510350','hs300-etf','510350','share','沪深300ETF工银',5),
 ('cu-inventory','cu-inventory','cu','inventory','电解铜库存',1),
 ('coal-price','coal-price',NULL,'price','动力煤现货价',1),
 ('159922','csi500-etf','159922','share','中证500ETF嘉实',1),
 ('512500','csi500-etf','512500','share','中证500ETF南方',2),
 ('159820','csi500-etf','159820','share','中证500ETF天弘',3),
 ('588000','star50-etf','588000','share','科创50ETF华夏',1),
 ('588080','star50-etf','588080','share','科创板50ETF易方达',2),
 ('588020','star50-etf','588020','share','科创50ETF',3),
 ('159915','chinext-etf','159915','share','创业板ETF易方达',1),
 ('159949','chinext-etf','159949','share','创业板50ETF华安',2),
 ('159952','chinext-etf','159952','share','创业板ETF广发',3),
 ('159967','chinext-etf','159967','share','创业板成长ETF华夏',4),
 ('159977','chinext-etf','159977','share','创业板ETF天弘',5),
 ('512890','dividend-etf','512890','share','红利低波ETF',1),
 ('515450','dividend-etf','515450','share','红利50ETF',2),
 ('515180','dividend-etf','515180','share','中证红利ETF',3),
 ('563020','dividend-etf','563020','share','低波红利ETF',4),
 ('515080','dividend-etf','515080','share','中证红利ETF',5),
 ('159352','a500-etf','159352','share','A500ETF南方',1),
 ('159361','a500-etf','159361','share','A500ETF易方达',2),
 ('159338','a500-etf','159338','share','中证A500ETF国泰',3),
 ('510050','sse50-etf','510050','share','上证50ETF华夏',1),
 ('512100','zz1000-etf','512100','share','中证1000ETF南方',1),
 ('560010','zz1000-etf','560010','share','1000基金华泰柏瑞',2),
 ('159845','zz1000-etf','159845','share','中证1000ETF广发',3);

-- ============ v1.2 商品组源（铜/铝/氧化铝/黄金） ============
-- 每商品一个 source，内含多个不同 metric 的 instruments（价格/库存/仓单/资金），
-- 混合单位 → instruments.unit 品种级单位；sources.unit='混合'（前端按 instrument.unit 显示）
INSERT OR IGNORE INTO sources(id,name,group_name,freq,fetcher,unit,movement_th,lag_days,enabled,sort_order) VALUES
 ('copper','铜','大宗商品','daily','akshare.composite','混合',1.0,1,1,20),
 ('aluminum','铝','大宗商品','daily','akshare.composite','混合',1.0,1,1,21),
 ('alumina','氧化铝','大宗商品','daily','akshare.composite','混合',1.0,1,1,22),
 ('gold','黄金','大宗商品','daily','akshare.composite','混合',1.0,1,1,23);
-- cu-inventory 并入铜组（instrument id 不变，观测无缝保留）
INSERT INTO instruments(id,source_id,code,metric,unit,name,sort_order) VALUES
 ('cu-inventory','copper','cu','inventory','吨','SHFE电解铜库存',3)
 ON CONFLICT(id) DO UPDATE SET source_id='copper', unit='吨', name='SHFE电解铜库存', sort_order=3;
DELETE FROM sources WHERE id='cu-inventory';   -- job_runs.source_id 无 FK，历史留痕不受影响
-- 铜组其余品种（composite fetcher 按 code 家族编排：SHFE日K/现货/仓单/LME/CFTC）
INSERT OR IGNORE INTO instruments(id,source_id,code,metric,unit,name,sort_order) VALUES
 ('cu-shfe-price','copper','CU0','price','元/吨','SHFE沪铜主力',1),
 ('cu-spot-price','copper','CU','price','元/吨','现货铜价',2),
 ('cu-warrant','copper','CU-WARRANT','warehouse','吨','SHFE铜仓单',4),
 ('cu-lme-stock','copper','LME-CU-STOCK','inventory','吨','LME铜库存',5),
 ('cu-lme-warrant','copper','LME-CU-WARRANT','warehouse','吨','LME铜注册仓单',6),
 ('cu-lme-holding','copper','LME-CU-POS','positioning','张','LME铜净持仓',7);
-- 铝组
INSERT OR IGNORE INTO instruments(id,source_id,code,metric,unit,name,sort_order) VALUES
 ('al-shfe-price','aluminum','AL0','price','元/吨','SHFE沪铝主力',1),
 ('al-spot-price','aluminum','AL','price','元/吨','现货铝价',2),
 ('al-inventory','aluminum','al','inventory','吨','东财铝库存',3),
 ('al-warrant','aluminum','AL-WARRANT','warehouse','吨','SHFE铝仓单',4),
 ('al-lme-stock','aluminum','LME-AL-STOCK','inventory','吨','LME铝库存',5),
 ('al-lme-warrant','aluminum','LME-AL-WARRANT','warehouse','吨','LME铝注册仓单',6),
 ('al-lme-holding','aluminum','LME-AL-POS','positioning','张','LME铝净持仓',7);
-- 氧化铝组
INSERT OR IGNORE INTO instruments(id,source_id,code,metric,unit,name,sort_order) VALUES
 ('ao-shfe-price','alumina','AO0','price','元/吨','SHFE氧化铝主力',1),
 ('ao-spot-price','alumina','AO','price','元/吨','现货氧化铝价',2),
 ('ao-inventory','alumina','ao','inventory','吨','东财氧化铝库存',3),
 ('ao-warrant','alumina','AO-WARRANT','warehouse','吨','SHFE氧化铝仓单',4);
-- 黄金组
INSERT OR IGNORE INTO instruments(id,source_id,code,metric,unit,name,sort_order) VALUES
 ('au-shfe-price','gold','AU0','price','元/克','SHFE沪金主力',1),
 ('au-sge-price','gold','AU9999','price','元/克','SGE黄金现货',2),
 ('au-warrant','gold','AU-WARRANT','warehouse','千克','SHFE黄金仓单',3),
 ('au-reserve','gold','GOLD-RESERVE','reserve','万盎司','央行黄金储备',4),
 ('518880','gold','518880','holding','亿份','黄金ETF华安',5),
 ('518800','gold','518800','holding','亿份','黄金基金国泰',6),
 ('518660','gold','518660','holding','亿份','工银黄金',7),
 ('au-cftc','gold','CFTC-AU','positioning','手','CFTC黄金非商业净多',8),
 ('au-london-price','gold','XAU','price','美元/盎司','伦敦金现',9);
