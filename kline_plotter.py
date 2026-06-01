"""
Plotly 交互式 K 线图绘制模块。
K-line chart with EMA overlays, volume, and signal markers.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from indicator import calc_indicators
from utils import setup_logger

logger = setup_logger(__name__)


def plot_kline_with_emas(
    df: pd.DataFrame,
    code: str,
    name: str,
    signal_date: str,
    signal_types: list[str],
    days: int = 120,
) -> go.Figure:
    """
    绘制交互式 K 线图（含 EMA 均线、成交量、信号标记）。
    红涨绿跌（国内习惯）。

    Parameters
    ----------
    df : pd.DataFrame
        包含 date, open, high, low, close, volume 的历史数据。
    code : str
        股票代码。
    name : str
        股票名称。
    signal_date : str
        信号触发日期 (YYYY-MM-DD)。
    signal_types : list[str]
        触发条件列表，如 ["A-首次站上三线", "B-均线粘合"]。
    days : int
        显示最近多少个交易日，默认 60。

    Returns
    -------
    plotly.graph_objects.Figure
    """
    df = df.tail(days).copy()
    df = calc_indicators(df)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.03,
        subplot_titles=(f"{code} {name}", "成交量"),
    )

    # ── 蜡烛图（红涨绿跌） ──
    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="K线",
            increasing=dict(line=dict(color="red"), fillcolor="red"),
            decreasing=dict(line=dict(color="green"), fillcolor="green"),
            hovertext=[
                f"日期: {d.strftime('%Y-%m-%d')}<br>"
                f"开: {o:.2f}<br>高: {h:.2f}<br>低: {l:.2f}<br>收: {c:.2f}"
                for d, o, h, l, c in zip(
                    df["date"], df["open"], df["high"], df["low"], df["close"]
                )
            ],
            hoverinfo="text",
        ),
        row=1,
        col=1,
    )

    # ── 三条 EMA 均线 ──
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["ema21"],
            line=dict(color="blue", width=1.2),
            name="EMA21",
            hovertemplate="EMA21: %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["ema55"],
            line=dict(color="orange", width=1.2),
            name="EMA55",
            hovertemplate="EMA55: %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["ema120"],
            line=dict(color="purple", width=1.2),
            name="EMA120",
            hovertemplate="EMA120: %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    # ── 信号标记 ──
    marker_symbols = {
        "A": ("triangle-up", "limegreen", 14, "⬆ 首次站上三线"),
        "B": ("circle", "gold", 12, "🟡 均线粘合"),
        "C": ("square", "dodgerblue", 12, "🟦 低波动"),
    }
    for stype in signal_types:
        key = stype[0]  # 取首字母: A, B, C
        if key in marker_symbols:
            sym, color, size, label = marker_symbols[key]
            try:
                sig_dt = pd.to_datetime(signal_date)
                mask = df["date"] == sig_dt
                if mask.any():
                    y_val = float(df.loc[mask, "high"].values[0]) * 1.02
                    x_val = df.loc[mask, "date"].values[0]
                    fig.add_trace(
                        go.Scatter(
                            x=[x_val],
                            y=[y_val],
                            mode="markers",
                            marker=dict(
                                symbol=sym,
                                color=color,
                                size=size,
                                line=dict(width=1, color="black"),
                            ),
                            name=label,
                            showlegend=True,
                            hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<extra></extra>",
                        ),
                        row=1,
                        col=1,
                    )
            except Exception as e:
                logger.warning(f"添加信号标记失败 {stype}: {e}")

    # ── 成交量 ──
    vol_colors = [
        "red" if df["close"].iloc[i] >= df["open"].iloc[i] else "green"
        for i in range(len(df))
    ]
    fig.add_trace(
        go.Bar(
            x=df["date"],
            y=df["volume"],
            name="成交量",
            marker_color=vol_colors,
            hovertemplate="成交量: %{y:,.0f}<extra></extra>",
        ),
        row=2,
        col=1,
    )

    # ── 布局 ──
    current_price = float(df["close"].iloc[-1])
    signal_str = " + ".join(signal_types) if signal_types else "无信号"

    fig.update_layout(
        title=dict(
            text=f"<b>{code} {name}</b> | 当前价: {current_price:.2f} | 触发条件: {signal_str}",
            font=dict(size=16),
        ),
        height=650,
        xaxis_rangeslider_visible=False,
        xaxis2_rangeslider_visible=False,
        hovermode="closest",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
        ),
        margin=dict(l=10, r=10, t=60, b=10),
        template="plotly_white",
    )
    # ── 动态检测非交易日（周末 + 节假日），隐藏空白 ──
    # 从数据中找出所有非周末但缺数据的日期 → 即节假日
    all_calendar_dates = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    trading_dates = set(d.date() for d in df["date"])
    holidays = [
        d.strftime("%Y-%m-%d")
        for d in all_calendar_dates
        if d.date() not in trading_dates and d.dayofweek < 5  # 工作日但无交易 → 节假日
    ]

    rangebreak_cfg = [dict(bounds=["sat", "mon"])]  # 隐藏周末
    if holidays:
        rangebreak_cfg.append(dict(values=holidays))  # 隐藏节假日

    fig.update_xaxes(rangebreaks=rangebreak_cfg, title_text="日期", row=2, col=1)
    fig.update_xaxes(rangebreaks=rangebreak_cfg, row=1, col=1)
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    return fig
