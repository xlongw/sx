
import baostock as bs
import sys
c = sys.argv[1]
bs.login()
bc = ('sh.' if c.startswith('6') else 'sz.') + c
rs = bs.query_history_k_data_plus(bc, 'date,close', start_date='2026-06-12', end_date='2026-06-13', frequency='d', adjustflag='2')
n = 0
if rs and rs.error_code == '0':
    while rs.next(): n += 1
print(c + ':' + str(n) + 'rows')
bs.logout()
