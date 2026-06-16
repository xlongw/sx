import baostock as bs
import sys
import time

code = sys.argv[1]
t0 = time.time()
lg = bs.login()
if lg.error_code != "0":
    print(f"{code}: LOGIN_FAILED {lg.error_msg}")
    sys.exit(1)
bs_code = ("sh." if code.startswith("6") else "sz.") + code
rs = bs.query_history_k_data_plus(
    bs_code, "date,close",
    start_date="2026-06-10", end_date="2026-06-16",
    frequency="d", adjustflag="2")
if rs and rs.error_code == "0":
    rows = 0
    while rs.next():
        rows += 1
    print(f"{code}: OK {rows}rows {time.time()-t0:.1f}s")
else:
    err = rs.error_msg if rs else "None"
    print(f"{code}: FAIL {err}")
bs.logout()
