"""
多因子选股策略（v1.0）
综合趋势、动量、成交量、波动率、均线位置五大因子评分。

使用方法:
    python strategy_multi_factor.py

输出: 按总分排序的股票列表，展示各因子得分明细。
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

DB_PATH = "stock_data.db"

# 因子权重配置
WEIGHTS = {
    "trend": 0.30,       # 趋势强度 — 均线多头排列 + 价格位置
    "momentum": 0.20,    # 动量 — 短期涨幅合理且健康
    "volume": 0.15,      # 成交量 — 量价配合
    "volatility": 0.15,  # 波动率 — 低波动稳定上涨
    "support": 0.20,     # 支撑位置 — 回调到均线支撑位（安全边际）
}

MIN_TRADING_DAYS = 200
TOP_N = 10


def load_all_stocks(conn):
    """加载所有主板的近期数据"""
    stocks = pd.read_sql(f"""
        SELECT code, name, COUNT(*) as cnt, MAX(date) as last_date
        FROM daily_quotes
        WHERE code NOT LIKE '30%' AND code NOT LIKE '68%'
        GROUP BY code
        HAVING cnt >= {MIN_TRADING_DAYS}
        ORDER BY code
    """, conn)
    return stocks


def load_stock_data(conn, code, days=300):
    """加载单支股票的日线数据"""
    df = pd.read_sql(f"""
        SELECT date, open, high, low, close, volume,
               ema21, ema55, ema120
        FROM daily_quotes
        WHERE code = '{code}'
        ORDER BY date DESC
        LIMIT {days}
    """, conn)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def calc_factors(df: pd.DataFrame) -> dict:
    """
    计算五大因子得分，返回 0-100 的分数。
    分数越高越好。
    """
    scores = {}

    # 准备数据
    close = df["close"].values
    volume = df["volume"].values
    ema21 = df["ema21"].values
    ema55 = df["ema55"].values
    ema120 = df["ema120"].values
    high = df["high"].values
    low = df["low"].values
    n = len(close)
    latest_close = close[-1]

    # =========================================================
    # 因子1: 趋势强度 (Trend) — 均线排列 + 价格位置
    # =========================================================
    check_days = min(20, n)
    bull_count = 0
    for i in range(n - check_days, n):
        if close[i] > ema21[i] and ema21[i] > ema55[i] and ema55[i] > ema120[i]:
            bull_count += 1

    align_ratio = bull_count / check_days if check_days > 0 else 0
    align_score = align_ratio * 40

    # 当前价格位置 (0-30)
    if latest_close > ema21[-1]:
        pos_score = 30
    elif latest_close > ema55[-1]:
        pos_score = 20
    elif latest_close > ema120[-1]:
        pos_score = 10
    else:
        pos_score = 0

    # 趋势稳定性 (0-30)
    deviations = (close[-check_days:] - ema21[-check_days:]) / ema21[-check_days:]
    dev_std = float(np.std(deviations)) if len(deviations) > 1 else 1

    if dev_std < 0.05:
        stability_score = 30
    elif dev_std < 0.08:
        stability_score = 20
    elif dev_std < 0.12:
        stability_score = 10
    else:
        stability_score = 0

    trend_score = align_score + pos_score + stability_score
    scores["trend"] = round(trend_score, 1)
    scores["trend_detail"] = {
        "bull_ratio": round(align_ratio, 2),
        "pos_score": pos_score,
        "stability_score": stability_score,
        "dev_std": round(dev_std, 4),
    }

    # =========================================================
    # 因子2: 动量 (Momentum)
    # =========================================================
    ret_20d = (close[-1] / close[-20] - 1) if n >= 20 else 0
    ret_60d = (close[-1] / close[-60] - 1) if n >= 60 else 0

    # 短期动量 (0-35)
    if 0.03 <= ret_20d <= 0.20:
        mom_20_score = 35
    elif 0.01 <= ret_20d < 0.03:
        mom_20_score = 20
    elif -0.05 <= ret_20d < 0.01:
        mom_20_score = 5
    elif ret_20d > 0.20:
        mom_20_score = 10
    else:
        mom_20_score = 0

    # 中期动量 (0-35)
    if 0.05 <= ret_60d <= 0.30:
        mom_60_score = 35
    elif 0.02 <= ret_60d < 0.05:
        mom_60_score = 20
    elif -0.10 <= ret_60d < 0.02:
        mom_60_score = 5
    elif ret_60d > 0.30:
        mom_60_score = 5
    else:
        mom_60_score = 0

    # 动量趋势 (0-30)
    if n >= 21:
        ret_recent_10 = (close[-1] / close[-11] - 1)
        ret_prior_10 = (close[-11] / close[-21] - 1)
        if ret_recent_10 > ret_prior_10 and ret_recent_10 > 0:
            mom_trend = 30
        elif ret_recent_10 > 0:
            mom_trend = 15
        elif ret_recent_10 > ret_prior_10:
            mom_trend = 10
        else:
            mom_trend = 0
    else:
        mom_trend = 10

    mom_score = mom_20_score + mom_60_score + mom_trend
    scores["momentum"] = round(mom_score, 1)
    scores["momentum_detail"] = {
        "ret_20d": round(ret_20d * 100, 2),
        "ret_60d": round(ret_60d * 100, 2),
        "mom_trend_score": mom_trend,
    }

    # =========================================================
    # 因子3: 成交量 (Volume)
    # =========================================================
    vol_ma20 = pd.Series(volume).rolling(20).mean().values
    vol_ratio = float(volume[-1] / vol_ma20[-1]) if vol_ma20[-1] > 0 else 1.0

    # 量价配合 (0-40)
    price_up_5d = close[-1] > close[-6] if n >= 6 else False
    vol_up_5d = volume[-1] > vol_ma20[-1]

    if price_up_5d and vol_up_5d and vol_ratio > 1.2:
        vol_price_score = 40
    elif price_up_5d and vol_up_5d:
        vol_price_score = 30
    elif not price_up_5d and vol_up_5d and vol_ratio > 1.3:
        vol_price_score = 20
    elif price_up_5d and not vol_up_5d:
        vol_price_score = 15
    else:
        vol_price_score = 5

    # 成交量趋势 (0-30)
    vol_trend_ratio = 1.0
    if n >= 40:
        vol_ma20_recent = float(vol_ma20[-1])
        vol_ma20_prior = float(vol_ma20[-20]) if n >= 40 else float(vol_ma20[0])
        vol_trend_ratio = vol_ma20_recent / vol_ma20_prior if vol_ma20_prior > 0 else 1.0

        if vol_trend_ratio > 1.2:
            vol_trend_score = 30
        elif vol_trend_ratio > 1.1:
            vol_trend_score = 20
        elif vol_trend_ratio > 0.9:
            vol_trend_score = 15
        else:
            vol_trend_score = 5
    else:
        vol_trend_score = 10

    # 成交量稳定性 (0-30)
    if n >= 20:
        vol_mean = float(np.mean(volume[-20:]))
        vol_std_ratio = float(np.std(volume[-20:]) / vol_mean) if vol_mean > 0 else 1.0
        if vol_std_ratio < 0.3:
            vol_stable = 30
        elif vol_std_ratio < 0.5:
            vol_stable = 20
        elif vol_std_ratio < 0.8:
            vol_stable = 10
        else:
            vol_stable = 0
    else:
        vol_stable = 10

    vol_score = vol_price_score + vol_trend_score + vol_stable
    scores["volume"] = round(vol_score, 1)
    scores["volume_detail"] = {
        "vol_ratio": round(vol_ratio, 2),
        "price_up_5d": price_up_5d,
        "vol_trend_ratio": round(vol_trend_ratio, 2),
    }

    # =========================================================
    # 因子4: 波动率 (Volatility)
    # =========================================================
    # 日收益率标准差 (0-40)
    if n >= 61:
        # 使用 close[-60:] 的60个价格计算59个日收益率
        segment = close[-60:]
        returns = segment[1:] / segment[:-1] - 1
        ret_std = float(np.std(returns))
        if ret_std < 0.02:
            vola_score = 40
        elif ret_std < 0.03:
            vola_score = 30
        elif ret_std < 0.04:
            vola_score = 20
        elif ret_std < 0.06:
            vola_score = 10
        else:
            vola_score = 0
    else:
        ret_std = 0
        vola_score = 20

    # 最大回撤 (0-30)
    if n >= 60:
        peak = np.maximum.accumulate(close[-60:])
        drawdown = (close[-60:] - peak) / peak
        max_dd = float(np.min(drawdown))

        if max_dd > -0.05:
            dd_score = 30
        elif max_dd > -0.10:
            dd_score = 20
        elif max_dd > -0.20:
            dd_score = 10
        else:
            dd_score = 0
    else:
        max_dd = 0
        dd_score = 15

    # ATR相对值 (0-30)
    if n >= 6:
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        atr5_val = float(pd.Series(tr).rolling(5).mean().iloc[-1])
    else:
        atr5_val = 0
    atr_ratio = atr5_val / latest_close if latest_close > 0 else 1

    if atr_ratio < 0.02:
        atr_score = 30
    elif atr_ratio < 0.03:
        atr_score = 20
    elif atr_ratio < 0.05:
        atr_score = 10
    else:
        atr_score = 0

    vola_total = vola_score + dd_score + atr_score
    scores["volatility"] = round(vola_total, 1)
    scores["volatility_detail"] = {
        "ret_std": round(ret_std, 4),
        "max_dd": round(max_dd * 100, 2),
        "atr_ratio": round(atr_ratio, 4),
    }

    # =========================================================
    # 因子5: 支撑位置 (Support / Safety Margin)
    # =========================================================
    # 价格与EMA55的距离 (0-35)
    ema55_dev = (latest_close - ema55[-1]) / ema55[-1]
    if -0.03 <= ema55_dev <= 0.05:
        ema55_score = 35
    elif -0.08 <= ema55_dev < -0.03:
        ema55_score = 25
    elif 0.05 < ema55_dev <= 0.12:
        ema55_score = 20
    elif ema55_dev < -0.08:
        ema55_score = 5
    else:
        ema55_score = 0

    # 价格与EMA120的距离 (0-30)
    ema120_dev = (latest_close - ema120[-1]) / ema120[-1]
    if -0.02 <= ema120_dev <= 0.08:
        ema120_score = 30
    elif 0.08 < ema120_dev <= 0.20:
        ema120_score = 20
    elif -0.10 <= ema120_dev < -0.02:
        ema120_score = 15
    elif ema120_dev > 0.20:
        ema120_score = 5
    else:
        ema120_score = 0

    # 短期回调幅度 (0-35)
    if n >= 20:
        high_20 = float(np.max(close[-20:]))
        ret_from_high = (latest_close - high_20) / high_20
        if -0.10 <= ret_from_high <= -0.03:
            pullback_score = 35
        elif -0.03 < ret_from_high <= 0:
            pullback_score = 20
        elif ret_from_high < -0.10:
            pullback_score = 5
        elif ret_from_high > 0:
            pullback_score = 15
        else:
            pullback_score = 10
    else:
        ret_from_high = 0
        pullback_score = 15

    support_score = ema55_score + ema120_score + pullback_score
    scores["support"] = round(support_score, 1)
    scores["support_detail"] = {
        "ema55_dev": round(ema55_dev * 100, 2),
        "ema120_dev": round(ema120_dev * 100, 2),
        "ret_from_high_20": round(ret_from_high * 100, 2),
    }

    # 总分
    total = (
        WEIGHTS["trend"] * scores["trend"]
        + WEIGHTS["momentum"] * scores["momentum"]
        + WEIGHTS["volume"] * scores["volume"]
        + WEIGHTS["volatility"] * scores["volatility"]
        + WEIGHTS["support"] * scores["support"]
    )
    scores["total"] = round(total, 1)
    scores["close"] = round(latest_close, 2)
    scores["date"] = str(df["date"].iloc[-1])

    return scores


def main():
    header = "=" * 80
    print(header)
    print("多因子选股策略 v1.0")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(header)

    conn = sqlite3.connect(DB_PATH)

    stocks = load_all_stocks(conn)
    print(f"\n加载 {len(stocks)} 支主板股票数据")

    results = []
    errors = []

    for i, row in stocks.iterrows():
        code = row["code"]
        name = row["name"]

        df = load_stock_data(conn, code, days=300)
        if len(df) < MIN_TRADING_DAYS:
            continue

        try:
            scores = calc_factors(df)
            scores["code"] = code
            scores["name"] = name
            results.append(scores)
        except Exception as e:
            errors.append((code, name, str(e)))

        if (i + 1) % 200 == 0:
            print(f"  进度: {i + 1}/{len(stocks)}")

    conn.close()

    print(f"\n计算完成: {len(results)} 支股票评分成功, {len(errors)} 支失败")
    if errors:
        print(f"  失败示例: {errors[0]}")

    # 按总分排序
    results.sort(key=lambda x: x["total"], reverse=True)
    top_n = results[:TOP_N]

    print(f"\n{header}")
    print(f"Top {TOP_N} 选股结果")
    print(header)

    print(f"\n{'代码':<8} {'名称':<10} {'最新价':<10} {'总分':<8} {'趋势':<8} {'动量':<8} {'量能':<8} {'波动':<8} {'支撑':<8}")
    print("-" * 80)

    for r in top_n:
        print(f"{r['code']:<8} {r['name']:<10} {r['close']:<10.2f} {r['total']:<8.1f} "
              f"{r['trend']:<8.1f} {r['momentum']:<8.1f} {r['volume']:<8.1f} "
              f"{r['volatility']:<8.1f} {r['support']:<8.1f}")

    print(f"\n{header}")
    print("详细分析:")
    print(header)

    for i, r in enumerate(top_n):
        print(f"\n--- {i + 1}. {r['code']} {r['name']} (总分: {r['total']}) ---")
        print(f"   最新价: {r['close']:.2f} | 日期: {r['date']}")
        print(f"   趋势(30%): {r['trend']}/100 "
              f"[多头比例={r['trend_detail']['bull_ratio']:.0%}, "
              f"位置分={r['trend_detail']['pos_score']}, "
              f"稳定分={r['trend_detail']['stability_score']}]")
        print(f"   动量(20%): {r['momentum']}/100 "
              f"[20日={r['momentum_detail']['ret_20d']:+.2f}%, "
              f"60日={r['momentum_detail']['ret_60d']:+.2f}%]")
        print(f"   量能(15%): {r['volume']}/100 "
              f"[量比={r['volume_detail']['vol_ratio']:.2f}, "
              f"价涨={r['volume_detail']['price_up_5d']}]")
        print(f"   波动(15%): {r['volatility']}/100 "
              f"[sigma={r['volatility_detail']['ret_std']:.4f}, "
              f"回撤={r['volatility_detail']['max_dd']:.1f}%]")
        print(f"   支撑(20%): {r['support']}/100 "
              f"[EMA55偏离={r['support_detail']['ema55_dev']:+.2f}%, "
              f"20日回撤={r['support_detail']['ret_from_high_20']:+.2f}%]")

    # 行业分布
    print(f"\n{header}")
    print("行业分布")
    print(header)

    sectors = {}
    for r in top_n:
        name = r['name']
        if any(b in name for b in ['银行','平安','招商','兴业','浦发','民生','中信','光大']):
            sector = '金融'
        elif any(b in name for b in ['证券','券商','建投','银河','海通']):
            sector = '券商'
        elif any(b in name for b in ['茅台','五粮液','汾酒','泸州','酒','啤酒']):
            sector = '白酒/食品'
        elif any(b in name for b in ['美的','格力','海尔','家电']):
            sector = '家电'
        elif any(b in name for b in ['药','医疗','生物','医','同仁堂']):
            sector = '医药'
        elif any(b in name for b in ['宁德','比亚迪','汽车']):
            sector = '新能源/汽车'
        else:
            sector = '其他'
        sectors[sector] = sectors.get(sector, 0) + 1

    for sector, count in sorted(sectors.items(), key=lambda x: -x[1]):
        print(f"  {sector}: {count}支")

    print(f"\n策略说明:")
    print(f"  - 趋势(30%): 均线多头排列 + 价格在均线上方 + 趋势稳定性")
    print(f"  - 动量(20%): 20/60日涨幅合理(非横盘非暴涨) + 动量加速")
    print(f"  - 量能(15%): 量价配合 + 成交量趋势上升 + 量能稳定")
    print(f"  - 波动(15%): 低日波动 + 小回撤 + 低ATR")
    print(f"  - 支撑(20%): 靠近EMA55/EMA120支撑 + 健康回调")
    print(f"  - 综合评分 = 各因子加权总分(0-100)")


if __name__ == "__main__":
    main()
