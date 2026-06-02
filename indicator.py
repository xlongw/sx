"""
技术指标计算模块
EMA、ATR、三条均线粘合条件等 — 全部向量化计算。
"""

import numpy as np
import pandas as pd


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    向量化计算 EMA21, EMA55, EMA120, ATR5 及相关指标。

    Parameters
    ----------
    df : pd.DataFrame
        必须包含 close, high, low, open 列。

    Returns
    -------
    pd.DataFrame
        在原 DataFrame 基础上追加以下列：
        ema21, ema55, ema120, tr, atr5, high5, low5
    """
    df = df.copy()

    # 若 DataFrame 已包含有效的 EMA 列（如从数据库缓存加载），跳过重算
    _ema_cols = ["ema21", "ema55", "ema120"]
    if all(c in df.columns and not df[c].isna().all() for c in _ema_cols):
        # 已有预计算的 EMA，只需补充 ATR 等其余指标
        pass
    else:
        # EMA（指数移动平均）
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
        df["ema55"] = df["close"].ewm(span=55, adjust=False).mean()
        df["ema120"] = df["close"].ewm(span=120, adjust=False).mean()

    # ATR(5)
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = np.maximum(tr1, np.maximum(tr2, tr3))
    df["atr5"] = df["tr"].rolling(5).mean()

    # 5日最高/最低
    df["high5"] = df["high"].rolling(5).max()
    df["low5"] = df["low"].rolling(5).min()

    return df


def check_condition_a(df: pd.DataFrame, max_deviation: float = 0.15) -> pd.Series:
    """
    条件 A：首次站上三条 EMA 均线。

    今日收盘价同时大于 EMA21、EMA55、EMA120，
    且昨日不满足此条件（即今天是突破的第一个交易日），
    且收盘价偏离三条 EMA 均值不超过 max_deviation（默认 15%）。

    Parameters
    ----------
    max_deviation : float
        收盘价偏离 EMA 均值的最大比例，默认 0.15（15%）。
        设为 1.0 可完全禁用偏离度约束。

    Returns
    -------
    pd.Series (bool)
    """
    above_all = (
        (df["close"] > df["ema21"])
        & (df["close"] > df["ema55"])
        & (df["close"] > df["ema120"])
    )
    first_break = above_all & ~above_all.shift(1).fillna(False)

    # 偏离度约束：防止选出已大幅远离均线的股票
    ema_mean = df[["ema21", "ema55", "ema120"]].mean(axis=1)
    deviation = (df["close"] - ema_mean).abs() / ema_mean
    within_range = deviation <= max_deviation

    return first_break & within_range


def check_condition_b(df: pd.DataFrame, threshold: float = 1.02) -> pd.Series:
    """
    条件 B：三条均线粘合。

    max(EMA21, EMA55, EMA120) / min(EMA21, EMA55, EMA120) <= threshold
    且收盘价偏离三条均线平均值 ≤ 3%。

    Parameters
    ----------
    threshold : float
        粘合阈值，默认 1.02（即相差不超过 2%）。

    Returns
    -------
    pd.Series (bool)
    """
    ema_cols = df[["ema21", "ema55", "ema120"]]
    ema_max = ema_cols.max(axis=1)
    ema_min = ema_cols.min(axis=1)
    ema_ratio = ema_max / ema_min
    ema_mean = ema_cols.mean(axis=1)
    close_dev = (df["close"] - ema_mean).abs() / ema_mean

    return (ema_ratio <= threshold) & (close_dev <= 0.03)


def check_condition_c(df: pd.DataFrame, threshold: float = 0.02) -> pd.Series:
    """
    条件 C：均线附近低波动。

    收盘价偏离每条 EMA < threshold，且满足以下之一：
    - ATR(5) / 收盘价 < 3%
    - 5日最高价 / 5日最低价 < 1.05

    Parameters
    ----------
    threshold : float
        偏离阈值，默认 0.02（即 2%）。

    Returns
    -------
    pd.Series (bool)
    """
    dev_21 = (df["close"] - df["ema21"]).abs() / df["ema21"]
    dev_55 = (df["close"] - df["ema55"]).abs() / df["ema55"]
    dev_120 = (df["close"] - df["ema120"]).abs() / df["ema120"]
    near_emas = (dev_21 < threshold) & (dev_55 < threshold) & (dev_120 < threshold)

    low_vol_atr = df["atr5"] / df["close"] < 0.03
    low_vol_range = df["high5"] / df["low5"] < 1.05
    low_vol = low_vol_atr | low_vol_range

    return near_emas & low_vol


def check_condition_d(df: pd.DataFrame, vol_multiplier: float = 1.5) -> pd.Series:
    """
    条件 D：成交量确认 — 今日成交量明显放大。

    今日成交量 > 20日均量 × vol_multiplier，表明有资金介入。

    Parameters
    ----------
    vol_multiplier : float
        成交量倍数阈值，默认 1.5（即今日量 > 20日均量的 1.5 倍）。

    Returns
    -------
    pd.Series (bool)
    """
    if "volume" not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    vol_ma20 = df["volume"].rolling(20).mean()
    return df["volume"] > vol_ma20 * vol_multiplier
