"""
测试 Baostock 多进程并行拉取的可行性。
Multi-process Baostock fetch feasibility test.

验证：
1. 多个进程能否同时登录 Baostock
2. 多个进程能否同时查询不同股票
3. 服务器是否会限流/拒绝并发连接
4. 实际加速比
"""

import time
import sys
import os

# 确保能找到项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta

TEST_CODES = [
    "600036",  # 招商银行
    "000001",  # 平安银行
    "600519",  # 贵州茅台
    "000858",  # 五粮液
    "600030",  # 中信证券
    "000333",  # 美的集团
    "600276",  # 恒瑞医药
    "000725",  # 京东方A
]

END_DATE = datetime.now().strftime("%Y-%m-%d")
START_DATE = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")


def fetch_single_process(code: str) -> dict:
    """单进程内：登录 Baostock 并拉取一只股票的数据。"""
    import baostock as bs
    import pandas as pd

    t0 = time.time()

    # 登录
    lg = bs.login()
    if lg.error_code != "0":
        return {"code": code, "error": f"登录失败: {lg.error_msg}", "time": time.time() - t0}

    # 确定代码前缀
    if code.startswith("6"):
        bs_code = f"sh.{code}"
    else:
        bs_code = f"sz.{code}"

    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume",
            start_date=START_DATE,
            end_date=END_DATE,
            frequency="d",
            adjustflag="2",
        )

        if rs is None or rs.error_code != "0":
            err = rs.error_msg if rs else "rs is None"
            return {"code": code, "error": f"查询失败: {err}", "time": time.time() - t0}

        rows = 0
        while rs.next():
            rows += 1

        elapsed = time.time() - t0
        return {"code": code, "rows": rows, "time": round(elapsed, 2), "error": None}

    except Exception as e:
        return {"code": code, "error": str(e), "time": time.time() - t0}
    finally:
        bs.logout()


def test_sequential(codes: list[str]) -> dict:
    """顺序拉取（基线）。"""
    print(f"\n{'='*50}")
    print("[1] Sequential (serial baseline)")
    print(f"{'='*50}")

    t0 = time.time()
    results = []
    for code in codes:
        r = fetch_single_process(code)
        results.append(r)
        status = f"OK: {r['rows']}行" if not r['error'] else f"FAIL: {r['error'][:50]}"
        print(f"  {r['code']}: {status} | {r['time']}s")

    total = time.time() - t0
    avg = sum(r["time"] for r in results) / len(results) if results else 0
    print(f"  总耗时: {total:.1f}s | 平均每只: {avg:.1f}s | 有效并行度: 1x")
    return {"mode": "sequential", "total_time": total, "results": results}


def test_multiprocess(codes: list[str], workers: int) -> dict:
    """多进程并行拉取。"""
    print(f"\n{'='*50}")
    print(f"[2]  多进程并行 ({workers} workers)")
    print(f"{'='*50}")

    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(fetch_single_process, c): c for c in codes}
        for future in as_completed(future_map):
            code = future_map[future]
            try:
                r = future.result(timeout=60)
                results.append(r)
                status = f"OK: {r['rows']}行" if not r['error'] else f"FAIL: {r['error'][:50]}"
                print(f"  {r['code']}: {status} | {r['time']}s")
            except Exception as e:
                results.append({"code": code, "error": str(e), "time": 0})
                print(f"  {code}: FAIL: Future异常: {e}")

    total = time.time() - t0
    successful = [r for r in results if not r.get("error")]
    errors = [r for r in results if r.get("error")]
    avg = sum(r["time"] for r in results) / len(results) if results else 0

    # 计算加速比
    # 顺序基准用单个请求平均时间 * 数量来估算
    per_request_avg = avg
    if successful:
        ideal_sequential = per_request_avg * len(codes)
        speedup = ideal_sequential / total if total > 0 else 0
    else:
        speedup = 0

    print(f"  总耗时: {total:.1f}s | 成功: {len(successful)} | 失败: {len(errors)}")
    print(f"  平均每只: {avg:.1f}s | 加速比: ~{speedup:.1f}x")

    if errors:
        print(f"  WARN: 错误详情:")
        for e in errors:
            print(f"    {e['code']}: {e['error'][:80]}")

    return {
        "mode": f"multi_{workers}",
        "total_time": total,
        "successful": len(successful),
        "errors": len(errors),
        "speedup": speedup,
        "results": results,
    }


if __name__ == "__main__":
    import multiprocessing

    # Windows 需要 freeze_support
    multiprocessing.freeze_support()

    # 测试数量
    codes = TEST_CODES[:6]  # 先测6只
    print(f"测试股票: {codes}")
    print(f"日期范围: {START_DATE} ~ {END_DATE}")

    # 1. 顺序基线
    seq = test_sequential(codes)

    # 2. 多进程 2 workers
    mp2 = test_multiprocess(codes, workers=2)

    # 3. 多进程 4 workers
    mp4 = test_multiprocess(codes, workers=4)

    # ── 结论 ──
    print(f"\n{'='*50}")
    print("SUMMARY: 结论")
    print(f"{'='*50}")
    seq_time = seq["total_time"]
    mp2_time = mp2["total_time"]
    mp4_time = mp4["total_time"]

    print(f"  顺序:        {seq_time:.1f}s  (基线)")
    print(f"  多进程 x2:   {mp2_time:.1f}s  (加速 {seq_time/mp2_time:.1f}x)" if mp2_time > 0 else "  多进程 x2:   失败")
    print(f"  多进程 x4:   {mp4_time:.1f}s  (加速 {seq_time/mp4_time:.1f}x)" if mp4_time > 0 else "  多进程 x4:   失败")

    mp2_ok = mp2["errors"] == 0
    mp4_ok = mp4["errors"] == 0

    if mp2_ok or mp4_ok:
        print(f"\n  OK: 多进程并行可行！")
        if mp4_ok and mp4["speedup"] >= 2.5:
            print(f"     推荐: 4 workers, 加速 ~{mp4['speedup']:.1f}x")
        elif mp2_ok:
            print(f"     推荐: 2 workers, 加速 ~{mp2['speedup']:.1f}x")
    else:
        print(f"\n  FAIL: 多进程并行不可行，服务器可能限制了并发连接数")
        print(f"     建议: 保持单线程，使用快速模式减少请求数量")
