INSERT OR IGNORE INTO sources(id,name,group_name,freq,fetcher,unit,movement_th,lag_days,enabled,sort_order) VALUES
 ('hs300-etf','沪深300 ETF','指数 ETF','daily','akshare.sse_etf_scale','亿份',1.0,1,1,1),
 ('cu-inventory','电解铜库存','大宗商品','daily','akshare.futures_inventory_em','吨',2.0,1,1,2),
 ('coal-price','煤炭价格','大宗商品','daily','scrapers.coal_price','元/吨',1.0,1,1,3);

INSERT OR IGNORE INTO instruments(id,source_id,code,metric,name,sort_order) VALUES
 ('510300','hs300-etf','510300','share','沪深300ETF华泰柏瑞',1),
 ('510310','hs300-etf','510310','share','沪深300ETF易方达',2),
 ('510320','hs300-etf','510320','share','沪深300ETF中金',3),
 ('510330','hs300-etf','510330','share','沪深300ETF华夏',4),
 ('510350','hs300-etf','510350','share','沪深300ETF工银',5),
 ('cu-inventory','cu-inventory','cu','inventory','电解铜库存',1),
 ('coal-price','coal-price',NULL,'price','动力煤现货价',1);
