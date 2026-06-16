"""Quick Baostock concurrent test - sequential vs parallel."""
import subprocess, sys, time

WORKER = '''
import baostock as bs, time
code = "STOCKCODE"
t0 = time.time()
lg = bs.login()
if lg.error_code != "0":
    print(code + ": LOGIN_FAILED")
    import os as _os; _os._exit(1)
bs_code = ("sh." if code.startswith("6") else "sz.") + code
rs = bs.query_history_k_data_plus(bs_code, "date,close",
    start_date="2026-06-10", end_date="2026-06-16",
    frequency="d", adjustflag="2")
if rs and rs.error_code == "0":
    rows = 0
    while rs.next():
        rows += 1
    print(code + ": OK " + str(rows) + "rows " + str(round(time.time()-t0,1)) + "s")
else:
    print(code + ": FAIL")
bs.logout()
'''

# Sequential
print("--- Sequential ---")
t0 = time.time()
for code in ["600036", "000001"]:
    p = subprocess.run(
        [sys.executable, "-c", WORKER.replace("STOCKCODE", code)],
        capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )
    print(f"  {p.stdout.strip()}")
    if p.stderr:
        print(f"  stderr: {p.stderr.strip()[:100]}")
seq_time = time.time() - t0
print(f"Sequential: {seq_time:.1f}s")

# Concurrent
print("\n--- Concurrent (2 proc) ---")
t0 = time.time()
p1 = subprocess.Popen(
    [sys.executable, "-c", WORKER.replace("STOCKCODE", "600036")],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    stdin=subprocess.DEVNULL,
)
p2 = subprocess.Popen(
    [sys.executable, "-c", WORKER.replace("STOCKCODE", "000001")],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    stdin=subprocess.DEVNULL,
)
o1, e1 = p1.communicate(timeout=60)
o2, e2 = p2.communicate(timeout=60)
par_time = time.time() - t0
print(f"  Worker 1: {o1.strip()}")
print(f"  Worker 2: {o2.strip()}")
if e1:
    print(f"  e1: {e1.strip()[:100]}")
if e2:
    print(f"  e2: {e2.strip()[:100]}")
print(f"Concurrent: {par_time:.1f}s")

# Result
speedup = seq_time / par_time if par_time > 0 else 0
print(f"\nSpeedup: {speedup:.1f}x")
if speedup >= 1.5:
    print("MULTIPROCESSING WORKS for Baostock!")
else:
    print("MULTIPROCESSING does NOT work for Baostock")
