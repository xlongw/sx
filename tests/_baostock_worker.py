"""Single-process Baostock worker for concurrency testing."""
import baostock as bs
import time
import sys

code = sys.argv[1]

t0 = time.time()
lg = bs.login()
if lg.error_code != "0":
    print(f"{code}: LOGIN_FAILED {lg.error_msg}")
    sys.exit(1)

if code.startswith("6"):
    bs_code = "sh." + code
else:
    bs_code = "sz." + code

rs = bs.query_history_k_data_plus(
    bs_code,
    "date,close",
    start_date="2026-06-10",
    end_date="2026-06-16",
    frequency="d",
    adjustflag="2",
)

if rs is None or rs.error_code != "0":
    err = rs.error_msg if rs else "None"
    print(f"{code}: QUERY_FAILED {err}")
else:
    rows = 0
    while rs.next():
        rows += 1
    elapsed = time.time() - t0
    print(f"{code}: OK {rows}rows {elapsed:.1f}s")

bs.logout()
