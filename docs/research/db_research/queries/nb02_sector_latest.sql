select distinct on (symbol) symbol, sector, date as sector_asof from daily_fundamentals where sector is not null order by symbol, date desc
