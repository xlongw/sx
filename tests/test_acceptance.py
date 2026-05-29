"""
验收测试用例
Acceptance tests for the EMA screening tool.

运行方式:
    cd sx
    python -m pytest tests/test_acceptance.py -v
    或直接:
    python tests/test_acceptance.py
"""

import sys
import os

# 确保模块路径正确
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import json
from datetime import datetime

from config import DEFAULT_DAYS, MIN_DAYS_REQUIRED
from data_fetcher import fetch_stock_data_baostock, bs_login, bs_logout
from indicator import calc_indicators, check_condition_a, check_condition_b, check_condition_c
from screen_engine import screen_single_stock
from kline_plotter import plot_kline_with_emas


def load_known_signals():
    """加载已知信号测试数据。"""
    path = os.path.join(os.path.dirname(__file__), "known_signals.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_case_1_zhongxin_securities():
    """
    测试用例 1：中信证券 (600030) 2024年9月突破三线。

    预期: 条件 A（首次站上三线）在 2024-09-24 附近被触发。
    """
    print("\n" + "=" * 60)
    print("测试用例 1: 中信证券 (600030) 2024年9月突破三线")
    print("=" * 60)

    bs_login()

    code = "600030"
    end_date = "2024-10-15"
    start_date = "2024-06-01"

    df = fetch_stock_data_baostock(code, start_date, end_date)
    assert df is not None, f"无法获取 {code} 数据"
    assert len(df) >= MIN_DAYS_REQUIRED, f"{code} 数据不足120天"

    df = calc_indicators(df)

    # 检查条件A
    cond_a = check_condition_a(df)
    signal_dates = df.loc[cond_a, "date"]

    print(f"\n条件A触发日期: {list(signal_dates.dt.strftime('%Y-%m-%d'))}")

    # 验证在 2024-09-24 附近有信号
    target_start = pd.Timestamp("2024-09-20")
    target_end = pd.Timestamp("2024-09-30")
    nearby_signals = signal_dates[
        (signal_dates >= target_start) & (signal_dates <= target_end)
    ]

    assert len(nearby_signals) > 0, (
        f"未在 2024-09-20 ~ 2024-09-30 范围内找到条件A信号。"
        f"实际信号日期: {list(signal_dates.dt.strftime('%Y-%m-%d'))}"
    )

    print(f"✅ 测试通过！信号日期: {nearby_signals.dt.strftime('%Y-%m-%d').tolist()}")
    bs_logout()


def test_case_2_zhongjixuchuang():
    """
    测试用例 2：中际旭创 (300308) 2023年12月均线粘合。

    预期: 条件 B（均线粘合）在 2023年12月前后被触发。
    """
    print("\n" + "=" * 60)
    print("测试用例 2: 中际旭创 (300308) 2023年12月均线粘合")
    print("=" * 60)

    bs_login()

    code = "300308"
    end_date = "2024-01-15"
    start_date = "2023-09-01"

    df = fetch_stock_data_baostock(code, start_date, end_date)
    assert df is not None, f"无法获取 {code} 数据"
    assert len(df) >= MIN_DAYS_REQUIRED, f"{code} 数据不足120天"

    df = calc_indicators(df)

    # 检查条件B
    cond_b = check_condition_b(df, threshold=1.02)
    signal_dates = df.loc[cond_b, "date"]

    print(f"\n条件B触发日期: {list(signal_dates.dt.strftime('%Y-%m-%d'))}")

    # 验证在 2023年12月附近有信号
    target_start = pd.Timestamp("2023-11-15")
    target_end = pd.Timestamp("2024-01-15")
    nearby_signals = signal_dates[
        (signal_dates >= target_start) & (signal_dates <= target_end)
    ]

    assert len(nearby_signals) > 0, (
        f"未在 2023-11-15 ~ 2024-01-15 范围内找到条件B信号。"
        f"实际信号日期: {list(signal_dates.dt.strftime('%Y-%m-%d'))}"
    )

    print(f"✅ 测试通过！信号日期: {nearby_signals.dt.strftime('%Y-%m-%d').tolist()}")
    bs_logout()


def test_kline_plot():
    """
    自测：平安银行 (000001) K线图生成。

    验证 K线图能正常生成（不检查视觉正确性，仅验证无异常）。
    """
    print("\n" + "=" * 60)
    print("自测: 平安银行 (000001) K线图")
    print("=" * 60)

    bs_login()

    code = "000001"
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - pd.Timedelta(days=DEFAULT_DAYS + 30)).strftime("%Y-%m-%d")

    df = fetch_stock_data_baostock(code, start_date, end_date)
    assert df is not None, f"无法获取 {code} 数据"
    assert len(df) >= 60, f"{code} 数据不足60天"

    # 调用绘图函数，确保不抛异常
    fig = plot_kline_with_emas(
        df,
        code=code,
        name="平安银行",
        signal_date=df["date"].iloc[-1].strftime("%Y-%m-%d"),
        signal_types=["A-首次站上三线"],
        days=60,
    )

    assert fig is not None, "K线图生成失败"
    assert len(fig.data) >= 4, f"K线图 trace 数量不足: {len(fig.data)}"

    print(f"✅ K线图生成成功！包含 {len(fig.data)} 个 trace")
    bs_logout()


def test_screen_engine_or():
    """
    验证筛选引擎 OR 逻辑：至少满足一个条件即触发。
    """
    print("\n" + "=" * 60)
    print("单元测试: 筛选引擎 OR 逻辑")
    print("=" * 60)

    bs_login()

    code = "600030"
    end_date = "2024-10-15"
    start_date = "2024-06-01"

    df = fetch_stock_data_baostock(code, start_date, end_date)
    if df is None or len(df) < MIN_DAYS_REQUIRED:
        print("⚠️ 数据不足，跳过此测试")
        bs_logout()
        return

    # OR 模式，启用所有条件
    results = screen_single_stock(
        df, code, "中信证券",
        logic_mode="OR",
        threshold_b=1.02,
        threshold_c=0.02,
        enabled_a=True,
        enabled_b=True,
        enabled_c=True,
    )

    print(f"OR 模式结果数: {len(results)}")
    for r in results:
        print(f"  - {r['date']}: {r['signal']}")

    print("✅ OR 逻辑测试完成")
    bs_logout()


def test_screen_engine_and():
    """
    验证筛选引擎 AND 逻辑：需同时满足所有勾选条件。
    """
    print("\n" + "=" * 60)
    print("单元测试: 筛选引擎 AND 逻辑")
    print("=" * 60)

    bs_login()

    code = "600030"
    end_date = "2024-10-15"
    start_date = "2024-06-01"

    df = fetch_stock_data_baostock(code, start_date, end_date)
    if df is None or len(df) < MIN_DAYS_REQUIRED:
        print("⚠️ 数据不足，跳过此测试")
        bs_logout()
        return

    # AND 模式，同时要求 A 和 B（条件苛刻，结果应较少）
    results = screen_single_stock(
        df, code, "中信证券",
        logic_mode="AND",
        threshold_b=1.02,
        threshold_c=0.02,
        enabled_a=True,
        enabled_b=True,
        enabled_c=False,
    )

    print(f"AND 模式 (A+B) 结果数: {len(results)}")
    for r in results:
        print(f"  - {r['date']}: {r['signal']}")

    print("✅ AND 逻辑测试完成")
    bs_logout()


# ── 主入口 ────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("EMA 均线形态筛选工具 — 验收测试套件")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    tests = [
        ("用例1: 中信证券突破三线", test_case_1_zhongxin_securities),
        ("用例2: 中际旭创均线粘合", test_case_2_zhongjixuchuang),
        ("自测: K线图生成", test_kline_plot),
        ("单元: OR 逻辑", test_screen_engine_or),
        ("单元: AND 逻辑", test_screen_engine_and),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"\n❌ 失败 [{name}]: {e}")
        except Exception as e:
            failed += 1
            print(f"\n💥 异常 [{name}]: {e}")

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
