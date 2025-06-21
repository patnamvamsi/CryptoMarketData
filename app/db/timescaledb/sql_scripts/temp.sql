
select count(*) from nse_bhav_copy

--truncate table nse_bhav_copy

select count(distinct (date1))   from nse_bhav_copy


select  min(date1)   from nse_bhav_copy;
select  max(date1)   from nse_bhav_copy



select count (date1)  from nse_bhav_copy where symbol  = 'ABCAPITAL' --order by 3

select date1, count(date1) from nse_bhav_copy where symbol  = 'ABCAPITAL' group by (date1) having count(date1) > 1


select * from nse_bhav_copy where date1 = '2020-02-20' and symbol  = 'ABCAPITAL'
select * from nse_bhav_copy where date1 = '2020-02-20' order by  symbol



select * from binance_symbols where active = 'true'

select * from binance_symbols where symbol = 'XRPAUD'
--update binance_symbols set priority = 1 , active  = 'true' where symbol = 'XRPAUD'

select max(open_time) from binance_xrpaud_kline_1m

select * from vivek_v51_stocks


select * from nse_bhav_copy where date1  = '05-Nov-2020' and symbol in (select * from vivek_v51_stocks)


