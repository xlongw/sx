"""
筛选引擎 — AND/OR 逻辑组合筛选。
Screening engine with AND/OR logic combination.
"""

import pandas as pd

from config import MIN_DAYS_REQUIRED
from indicator import calc_indicators, check_condition_a, check_condition_b, check_condition_c
from utils import setup_logger

logger = setup_logger(__name__)


def screen_single_stock(
    df: pd.DataFrame,
    code: str,
    name: str,
    logic_mode: str = "OR",
    threshold_b: float = 1.02,
    threshold_c: float = 0.02,
    enabled_a: bool = True,
    enabled_b: bool = True,
    enabled_c: bool = False,
) -> list[dict]:
    """
    对单支股票执行筛选。

    Parameters
    ----------
    df : pd.DataFrame
        股票日线数据（需包含 close, high, low, open 列）。
    code : str
        股票代码。
    name : str
        股票名称。
    logic_mode : str
        "OR" 或 "AND"。
    threshold_b : float
        条件 B 粘合阈值。
    threshold_c : float
        条件 C 偏离阈值。
    enabled_a/b/c : bool
        是否启用对应条件。

    Returns
    -------
    list[dict]
        信号列表（一般只有 0 或 1 个元素，因为只检查最新一天）。
    """
    if df is None or len(df) < MIN_DAYS_REQUIRED:
        return []

    # calc_indicators 内部已检查 EMA 缓存：若 DataFrame 已有预计算的 EMA 列则跳过重算
    df = calc_indicators(df)
    latest = df.iloc[-1]

    cond_a = check_condition_a(df).iloc[-1] if enabled_a else False
    cond_b = check_condition_b(df, threshold_b).iloc[-1] if enabled_b else False
    cond_c = check_condition_c(df, threshold_c).iloc[-1] if enabled_c else False

    # 组合逻辑
    if logic_mode == "AND":
        active = []
        if enabled_a:
            active.append(cond_a)
        if enabled_b:
            active.append(cond_b)
        if enabled_c:
            active.append(cond_c)
        triggered = all(active) if active else False
    else:  # OR
        triggered = cond_a or cond_b or cond_c

    if not triggered:
        return []

    # 构建信号信息
    ema_mean_val = float(latest[["ema21", "ema55", "ema120"]].mean())
    deviation = float(abs(latest["close"] - ema_mean_val) / ema_mean_val * 100)

    signal_types = []
    if enabled_a and cond_a:
        signal_types.append("A-首次站上三线")
    if enabled_b and cond_b:
        signal_types.append("B-均线粘合")
    if enabled_c and cond_c:
        signal_types.append("C-低波动")

    return [
        {
            "code": code,
            "name": name,
            "date": str(latest["date"])[:10],
            "close": round(float(latest["close"]), 2),
            "ema21": round(float(latest["ema21"]), 2),
            "ema55": round(float(latest["ema55"]), 2),
            "ema120": round(float(latest["ema120"]), 2),
            "deviation_pct": round(deviation, 2),
            "signal": " + ".join(signal_types),
            "cond_a": cond_a,
            "cond_b": cond_b,
            "cond_c": cond_c,
        }
    ]
