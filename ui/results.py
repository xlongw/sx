"""
筛选结果展示 + K 线图查看器
Screening results display & K-line viewer.
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from config import DEFAULT_DAYS, KLINE_DISPLAY_DAYS
from data_manager import load_from_db
from kline_plotter import plot_kline_with_emas
from watchlist_manager import batch_add_to_watchlist
from utils import setup_logger

logger = setup_logger(__name__)


def plot_kline_for_stock(code: str, name: str, days: int = KLINE_DISPLAY_DAYS):
    """
    加载股票数据并绘制 K 线图（不依赖筛选结果）。
    供筛选结果和自选股两处共用。
    """
    df = load_from_db(code, limit=max(days, DEFAULT_DAYS))
    if df is None or len(df) < KLINE_DISPLAY_DAYS:
        st.warning(f"股票 {code} 数据不足（需要至少{KLINE_DISPLAY_DAYS}个交易日），请先刷新数据。")
        return

    fig = plot_kline_with_emas(
        df, code, name,
        signal_date=str(df["date"].iloc[-1])[:10],
        signal_types=[],
        days=days,
    )
    st.plotly_chart(fig, width="stretch")


def render_screening_results() -> None:
    """渲染筛选结果：统计摘要、表格、K 线查看器、CSV 导出。"""
    results = st.session_state.get("results")
    if results is None:
        return

    elapsed = st.session_state.get("elapsed", 0)
    screened = st.session_state.get("screened_count", 0)
    failed = st.session_state.get("failed_count", 0)
    skipped = st.session_state.get("skipped_count", 0)
    failed_list = st.session_state.get("failed_list", [])

    st.divider()
    st.subheader(f"筛选结果: 共 {len(results)} 支股票触发信号")

    # 统计摘要
    col_s1, col_s2, col_s3, col_s4 = st.columns(4)
    with col_s1:
        st.metric("📊 已筛选", f"{screened}")
    with col_s2:
        st.metric("🔔 触发信号", f"{len(results)}")
    with col_s3:
        st.metric("⏭️ 跳过(数据不足)", f"{skipped}")
    with col_s4:
        st.metric("❌ 失败", f"{failed}", delta=None if failed == 0 else "⚠️")

    st.caption(f"耗时 {elapsed:.1f} 秒")

    # 失败详情
    if failed > 0 and failed_list:
        with st.expander(f"⚠️ 失败详情 ({failed} 支股票)", expanded=False):
            for code, name, err in failed_list:
                st.text(f"• {code} {name}: {err[:120]}")

    if results.empty:
        st.info("未发现符合条件的股票，请调整筛选条件后重试。")
        return

    # ── 全部加入自选 ──
    col_wl1, col_wl2 = st.columns([1, 3])
    with col_wl1:
        if st.button("⭐ 全部加入自选", width="stretch"):
            codes_list = [str(r["code"]) for _, r in results.iterrows()]
            names_list = [str(r.get("name", "")) for _, r in results.iterrows()]
            n_added = batch_add_to_watchlist(codes_list, names_list)
            st.success(f"已将 {n_added} 支股票加入自选")
            st.rerun()

    # ── 表格展示 ──
    display_cols = {
        "code": "股票代码", "name": "股票名称", "date": "信号日期",
        "close": "当前价", "ema21": "EMA21", "ema55": "EMA55",
        "ema120": "EMA120", "deviation_pct": "偏离度(%)", "signal": "触发条件",
    }
    has_vol = "vol_ratio" in results.columns and results["vol_ratio"].sum() > 0
    if has_vol:
        display_cols["vol_ratio"] = "量比"
    df_display = results[list(display_cols.keys())].rename(columns=display_cols)

    col_config = {
        "股票代码": st.column_config.TextColumn(width="small"),
        "当前价": st.column_config.NumberColumn(format="%.2f"),
        "EMA21": st.column_config.NumberColumn(format="%.2f"),
        "EMA55": st.column_config.NumberColumn(format="%.2f"),
        "EMA120": st.column_config.NumberColumn(format="%.2f"),
        "偏离度(%)": st.column_config.NumberColumn(format="%.2f"),
    }
    if has_vol:
        col_config["量比"] = st.column_config.NumberColumn(format="%.1f")

    st.dataframe(
        df_display, width="stretch", hide_index=True, column_config=col_config,
    )

    # ── K 线图查看 ──
    st.divider()
    st.subheader("📊 K线图查看")

    code_options = ["-- 选择股票 --"] + [
        f"{r['code']} {r['name']} | {r['signal']}"
        for _, r in results.iterrows()
    ]
    selected = st.selectbox("选择股票查看K线图", code_options, key="kline_select")

    if selected and selected != "-- 选择股票 --":
        sel_code = selected.split()[0]
        sel_row = results[results["code"] == sel_code]
        sel_name = str(sel_row.iloc[0]["name"]) if not sel_row.empty else ""
        if st.button("📊 查看K线图", type="primary") or st.session_state.get("show_kline"):
            st.session_state["show_kline"] = True
            st.session_state["selected_code"] = sel_code
            st.session_state["selected_name"] = sel_name

    if st.session_state.get("show_kline") and st.session_state.get("selected_code"):
        st.divider()
        col_k, col_close = st.columns([4, 1])
        with col_k:
            st.subheader(
                f"📊 {st.session_state['selected_code']} "
                f"{st.session_state.get('selected_name', '')} K线图"
            )
        with col_close:
            if st.button("❌ 关闭K线图", key="close_kline"):
                st.session_state["show_kline"] = False
                st.session_state["selected_code"] = None
                st.rerun()

        sel_code = st.session_state["selected_code"]
        sel_row = results[results["code"] == sel_code]
        signal_types = []
        if not sel_row.empty:
            signal_types = str(sel_row.iloc[0]["signal"]).split(" + ")
        df_kline = load_from_db(sel_code, limit=DEFAULT_DAYS)
        if df_kline is not None and len(df_kline) >= KLINE_DISPLAY_DAYS:
            fig = plot_kline_with_emas(
                df_kline, sel_code,
                st.session_state.get("selected_name", ""),
                str(df_kline["date"].iloc[-1])[:10],
                signal_types,
                days=KLINE_DISPLAY_DAYS,
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.warning(f"股票 {sel_code} 数据不足，无法绘制K线图")

    # ── 导出 CSV ──
    st.divider()
    csv = df_display.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="📥 导出CSV",
        data=csv,
        file_name=f"ema_screening_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
    )


def render_kline_from_watch() -> None:
    """渲染从自选股列表触发的 K 线图。"""
    if not st.session_state.get("show_kline_from_watch"):
        return
    if not st.session_state.get("selected_kline_code"):
        return

    st.divider()
    col_k, col_close = st.columns([4, 1])
    with col_k:
        st.subheader(
            f"📊 {st.session_state['selected_kline_code']} "
            f"{st.session_state.get('selected_kline_name', '')} K线图"
        )
    with col_close:
        if st.button("❌ 关闭K线图", key="close_watch_kline"):
            st.session_state["show_kline_from_watch"] = False
            st.session_state["selected_kline_code"] = None
            st.rerun()

    plot_kline_for_stock(
        st.session_state["selected_kline_code"],
        st.session_state.get("selected_kline_name", ""),
    )
