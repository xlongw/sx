"""
EMA 均线形态股票筛选工具 — Streamlit 主界面
EMA Crossover Pattern Stock Screening Tool

启动: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import time
import os
from datetime import datetime

# ── 本地模块导入 ─────────────────────────────────────────
from config import (
    DB_PATH,
    DEFAULT_STOCKS,
    DEFAULT_THRESHOLD_B,
    DEFAULT_THRESHOLD_C,
    DEFAULT_MAX_STOCKS,
    DEFAULT_DAYS,
    MIN_DAYS_REQUIRED,
    DATA_SOURCE,
)
from data_fetcher import bs_login, bs_logout
from data_manager import (
    init_db,
    load_from_db,
    get_all_cached_codes,
    fetch_and_cache_stock,
    fetch_and_cache_stocks_parallel,
    fetch_stock_data,
    save_to_db,
    backfill_ema_cache,
)
from screen_engine import screen_single_stock
from kline_plotter import plot_kline_with_emas
from utils import setup_logger, format_code, get_stock_name
from watchlist_manager import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    is_in_watchlist,
    batch_add_to_watchlist,
)

logger = setup_logger(__name__)


# ── Streamlit 页面配置 ────────────────────────────────────
st.set_page_config(
    page_title="EMA均线形态筛选",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_list() -> pd.DataFrame:
    """返回内置默认股票列表。"""
    return pd.DataFrame(DEFAULT_STOCKS, columns=["code", "name"])


def parse_custom_codes(text: str) -> list[tuple[str, str]]:
    """解析用户自定义股票代码，返回 [(code, name), ...]。"""
    codes_raw = [c.strip() for c in text.split("\n") if c.strip()]
    codes_clean = [format_code(c) for c in codes_raw]
    return [(c, "") for c in codes_clean]


# ── K线图独立绘制函数 ────────────────────────────────────
def plot_kline_for_stock(code: str, name: str, days: int = 60):
    """
    加载股票数据并绘制 K 线图（不依赖筛选结果）。
    供筛选结果和自选股两处共用。

    Parameters
    ----------
    code : str
        股票代码。
    name : str
        股票名称。
    days : int
        显示最近多少个交易日。
    """
    df = load_from_db(code, limit=max(days, DEFAULT_DAYS))
    if df is None or len(df) < 60:
        st.warning(f"股票 {code} 数据不足（需要至少60个交易日），请先刷新数据。")
        return

    fig = plot_kline_with_emas(
        df,
        code,
        name,
        signal_date=str(df["date"].iloc[-1])[:10],
        signal_types=[],  # 自选股查看无特定信号标记
        days=days,
    )
    st.plotly_chart(fig, use_container_width=True)


# ── 主函数 ───────────────────────────────────────────────
def main():
    st.title("📈 EMA均线形态股票筛选工具")
    st.caption("基于 EMA21 / EMA55 / EMA120 三条均线的技术形态筛选")

    init_db()
    backfill_ema_cache()  # 对存量数据回填 EMA（新数据库自动跳过）

    # ── 左侧边栏 ──────────────────────────────────────────
    with st.sidebar:
        # ── 数据源选择 ──
        st.header("📡 数据源")
        data_source = st.radio(
            "选择数据源",
            options=["baostock", "akshare"],
            format_func=lambda x: "Baostock（免费、稳定）" if x == "baostock" else "AkShare（免费、数据全）",
            index=0 if DATA_SOURCE == "baostock" else 1,
            help="Baostock: 需登录，数据稳定；AkShare: 无需登录，数据更全",
        )

        st.divider()

        st.header("🔧 筛选设置")

        logic_mode = st.radio(
            "逻辑组合方式",
            options=["OR", "AND"],
            format_func=lambda x: "任一条件 (OR)" if x == "OR" else "同时满足 (AND)",
            help="OR: 满足任一勾选条件即可; AND: 需同时满足所有勾选条件",
        )

        st.divider()

        st.subheader("筛选条件")
        enabled_a = st.checkbox(
            "条件A: 首次站上三线",
            value=True,
            help="今日收盘价首次同时大于EMA21/55/120",
        )
        enabled_b = st.checkbox(
            "条件B: 均线粘合",
            value=True,
            help="三条EMA均线相互靠近（粘合）",
        )
        threshold_b = st.slider(
            "粘合阈值",
            min_value=1.01,
            max_value=1.05,
            value=DEFAULT_THRESHOLD_B,
            step=0.005,
            help="EMA最大值/最小值 <= 此值",
            disabled=not enabled_b,
        )

        enabled_c = st.checkbox(
            "条件C: 低波动",
            value=False,
            help="股价在均线附近且波动率低",
        )
        threshold_c = st.slider(
            "偏离阈值 (%)",
            min_value=1,
            max_value=5,
            value=int(DEFAULT_THRESHOLD_C * 100),
            step=1,
            help="收盘价偏离均线 < 此百分比",
            disabled=not enabled_c,
        )

        st.divider()

        st.subheader("📋 股票范围")
        use_custom = st.checkbox("使用自定义股票列表", value=False)

        if use_custom:
            custom_codes = st.text_area(
                "输入股票代码（每行一个，如: 000001.SZ,600036.SH）",
                value="000001\n600036\n600030\n000858\n600519",
                height=120,
                help="支持格式: 000001 或 000001.SZ",
            )
        else:
            custom_codes = None

        max_stocks = st.slider(
            "刷新/筛选股票数量",
            min_value=50,
            max_value=4000,
            value=DEFAULT_MAX_STOCKS,
            step=50,
            help="控制【刷新数据】和【开始筛选】每次处理的股票数量上限",
        )

        st.divider()

        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            start_btn = st.button("🔍 开始筛选", type="primary", use_container_width=True)
        with col_btn2:
            refresh_btn = st.button(
                "🔄 刷新数据",
                use_container_width=True,
                help="重新获取所有股票数据（增量更新）",
            )

        st.divider()

        # ── 自选股管理 ──
        st.header("📌 自选股管理")

        with st.form("add_watchlist_form", clear_on_submit=True):
            col_code, col_name = st.columns(2)
            code_input = col_code.text_input("股票代码", placeholder="如: 000001")
            name_input = col_name.text_input("名称（可选）", placeholder="自动获取")
            wl_submitted = st.form_submit_button("➕ 添加到自选", use_container_width=True)
            if wl_submitted and code_input:
                clean_code = format_code(code_input)
                stock_name = name_input if name_input else get_stock_name(clean_code)
                add_to_watchlist(clean_code, stock_name)
                st.success(f"已添加 {clean_code}")
                st.rerun()

        # 显示自选列表
        watchlist_df = get_watchlist()
        if not watchlist_df.empty:
            st.write(f"**我的自选 ({len(watchlist_df)})**")
            for _, wl_row in watchlist_df.iterrows():
                col_info, col_kline, col_del = st.columns([3, 1, 1])
                wl_code = str(wl_row["code"])
                wl_name = str(wl_row.get("name", ""))
                col_info.write(f"`{wl_code}` {wl_name}")
                # K线图按钮
                if col_kline.button("📊", key=f"kline_{wl_code}", help="查看K线图"):
                    st.session_state["selected_kline_code"] = wl_code
                    st.session_state["selected_kline_name"] = wl_name
                    st.session_state["show_kline_from_watch"] = True
                # 删除按钮
                if col_del.button("❌", key=f"del_{wl_code}", help="删除"):
                    remove_from_watchlist(wl_code)
                    st.rerun()
        else:
            st.info("暂无自选股")

        # 仅筛选自选股（默认不勾选，不影响原有全市场筛选）
        filter_watchlist_only = st.checkbox(
            "🔍 仅筛选自选股中的股票",
            value=False,
            help="勾选后仅对自选股列表执行筛选",
        )

        st.divider()
        source_label = "Baostock" if data_source == "baostock" else "AkShare"
        st.caption(f"当前数据源: {source_label} | 缓存: {os.path.abspath(DB_PATH)}")

    # Baostock 全局登录（仅在需要时，data_source 已在侧边栏中定义）
    if data_source == "baostock":
        try:
            bs_login()
        except Exception as e:
            logger.warning(f"Baostock 登录失败: {e}")

    # ── 会话状态初始化 ────────────────────────────────────
    for key, default in [
        ("results", None),
        ("selected_code", None),
        ("selected_name", ""),
        ("show_kline", False),
        ("screening", False),
        ("progress", 0),
        ("status_text", ""),
        # 自选股K线图
        ("selected_kline_code", None),
        ("selected_kline_name", ""),
        ("show_kline_from_watch", False),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # ── 刷新数据 ──
    if refresh_btn:
        st.session_state["screening"] = True
        st.session_state["status_text"] = "正在刷新数据..."

        with st.spinner("正在获取股票数据..."):
            stock_df = get_stock_list()
            if use_custom and custom_codes:
                codes_parsed = parse_custom_codes(custom_codes)
                stock_df = pd.DataFrame(codes_parsed, columns=["code", "name"])

            total = min(len(stock_df), max_stocks)
            stock_list = [
                (str(row["code"]), str(row.get("name", "")))
                for _, row in stock_df.head(total).iterrows()
            ]

            progress_bar = st.progress(0, text="准备中...")

            def _update_progress(done: int, total_count: int, code: str):
                pct = int(done / total_count * 100)
                progress_bar.progress(pct, text=f"已处理 {done}/{total_count}: {code}")

            fetched, _ = fetch_and_cache_stocks_parallel(
                stock_list,
                source=data_source,
                progress_callback=_update_progress,
            )

            progress_bar.empty()
            st.success(f"数据刷新完成！成功获取 {fetched}/{total} 支股票数据")

        st.session_state["screening"] = False

    # ── 开始筛选 ──
    if start_btn:
        st.session_state["screening"] = True
        st.session_state["results"] = None
        st.session_state["selected_code"] = None
        st.session_state["show_kline"] = False

    if st.session_state["screening"] and st.session_state["results"] is None:
        start_time = time.time()

        # 构建股票列表
        if filter_watchlist_only:
            # 仅筛选自选股
            wl = get_watchlist()
            if wl.empty:
                st.warning("自选股列表为空，请先在侧边栏添加自选股。")
                st.session_state["screening"] = False
                st.stop()
            stock_list = [
                (str(r["code"]), str(r.get("name", "")))
                for _, r in wl.iterrows()
            ]
        elif use_custom and custom_codes:
            codes_parsed = parse_custom_codes(custom_codes)
            stock_list = []
            for c, _ in codes_parsed:
                cached = load_from_db(c, limit=1)
                name = str(cached["name"].iloc[0]) if not cached.empty else ""
                stock_list.append((c, name))
        else:
            stock_df = get_stock_list()
            stock_list = [
                (str(row["code"]), str(row.get("name", "")))
                for _, row in stock_df.head(max_stocks).iterrows()
            ]

        if not stock_list:
            stock_list = list(DEFAULT_STOCKS)

        total = len(stock_list)
        progress_bar = st.progress(0, text="正在加载数据并计算...")
        all_signals = []
        screened = 0

        for i, (code, name) in enumerate(stock_list):
            try:
                df = load_from_db(code, limit=DEFAULT_DAYS)
                if df is None or len(df) < MIN_DAYS_REQUIRED:
                    df = fetch_and_cache_stock(code, name, days=DEFAULT_DAYS, source=data_source)

                if df is not None and len(df) >= MIN_DAYS_REQUIRED:
                    signals = screen_single_stock(
                        df,
                        code,
                        name,
                        logic_mode,
                        threshold_b,
                        threshold_c / 100.0,
                        enabled_a,
                        enabled_b,
                        enabled_c,
                    )
                    all_signals.extend(signals)
                screened += 1
            except Exception as e:
                logger.error(f"筛选异常 {code}: {e}")

            if (i + 1) % 10 == 0 or i == total - 1:
                progress = int((i + 1) / total * 100)
                progress_bar.progress(
                    progress, text=f"已筛选 {i+1}/{total}: {code}"
                )

        progress_bar.empty()
        elapsed = time.time() - start_time

        if all_signals:
            df_results = pd.DataFrame(all_signals)
            df_results = df_results.sort_values("deviation_pct")
            st.session_state["results"] = df_results
        else:
            st.session_state["results"] = pd.DataFrame()

        st.session_state["elapsed"] = elapsed
        st.session_state["screened_count"] = screened
        st.session_state["screening"] = False

    # ── 显示结果 ──
    results = st.session_state.get("results")
    if results is not None:
        elapsed = st.session_state.get("elapsed", 0)
        screened = st.session_state.get("screened_count", 0)

        st.divider()
        st.subheader(f"筛选结果: 共 {len(results)} 支股票触发信号")
        st.caption(f"已筛选 {screened} 支股票 · 耗时 {elapsed:.1f} 秒")

        if not results.empty:
            # ── 加入自选股 ──
            col_wl1, col_wl2 = st.columns([1, 3])
            with col_wl1:
                if st.button("⭐ 全部加入自选", use_container_width=True):
                    codes_list = [str(r["code"]) for _, r in results.iterrows()]
                    names_list = [str(r.get("name", "")) for _, r in results.iterrows()]
                    n_added = batch_add_to_watchlist(codes_list, names_list)
                    st.success(f"已将 {n_added} 支股票加入自选")
                    st.rerun()

            # 表格展示
            display_cols = {
                "code": "股票代码",
                "name": "股票名称",
                "date": "信号日期",
                "close": "当前价",
                "ema21": "EMA21",
                "ema55": "EMA55",
                "ema120": "EMA120",
                "deviation_pct": "偏离度(%)",
                "signal": "触发条件",
            }
            df_display = results[list(display_cols.keys())].rename(columns=display_cols)

            st.dataframe(
                df_display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "股票代码": st.column_config.TextColumn(width="small"),
                    "当前价": st.column_config.NumberColumn(format="%.2f"),
                    "EMA21": st.column_config.NumberColumn(format="%.2f"),
                    "EMA55": st.column_config.NumberColumn(format="%.2f"),
                    "EMA120": st.column_config.NumberColumn(format="%.2f"),
                    "偏离度(%)": st.column_config.NumberColumn(format="%.2f"),
                },
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
                if (
                    st.button("📊 查看K线图", type="primary")
                    or st.session_state.get("show_kline")
                ):
                    st.session_state["show_kline"] = True
                    st.session_state["selected_code"] = sel_code
                    st.session_state["selected_name"] = sel_name

            if st.session_state.get("show_kline") and st.session_state.get("selected_code"):
                st.divider()
                col_k, col_close = st.columns([4, 1])
                with col_k:
                    st.subheader(f"📊 {st.session_state['selected_code']} {st.session_state.get('selected_name', '')} K线图")
                with col_close:
                    if st.button("❌ 关闭K线图", key="close_kline"):
                        st.session_state["show_kline"] = False
                        st.session_state["selected_code"] = None
                        st.rerun()
                # 查找信号类型（如果有）
                sel_code = st.session_state["selected_code"]
                sel_row = results[results["code"] == sel_code]
                signal_types = []
                if not sel_row.empty:
                    signal_types = str(sel_row.iloc[0]["signal"]).split(" + ")
                df_kline = load_from_db(sel_code, limit=DEFAULT_DAYS)
                if df_kline is not None and len(df_kline) >= 60:
                    fig = plot_kline_with_emas(
                        df_kline,
                        sel_code,
                        st.session_state.get("selected_name", ""),
                        str(df_kline["date"].iloc[-1])[:10],
                        signal_types,
                        days=60,
                    )
                    st.plotly_chart(fig, use_container_width=True)
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
        else:
            st.info("未发现符合条件的股票，请调整筛选条件后重试。")

    # ── 自选股 K 线图显示 ──
    if st.session_state.get("show_kline_from_watch") and st.session_state.get("selected_kline_code"):
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

    # ── Footer ──
    st.divider()
    st.caption(
        "⚠️ 免责声明: 本工具仅供学习和研究使用，不构成任何投资建议。"
        "股市有风险，投资需谨慎。"
    )


# ── 验收测试用例说明 ──────────────────────────────────────
#
# 测试用例1: 中信证券 (600030) 2024年9月突破三线
#   预期: 条件A应在2024年9月24日附近被触发。
#   验证: 自定义股票列表输入 600030，仅勾选条件A，OR模式，点击筛选。
#
# 测试用例2: 中际旭创 (300308) 2023年12月均线粘合
#   预期: 条件B应被触发（300308为创业板，需手动添加）。
#   验证: 自定义输入 300308，仅勾选条件B，阈值1.02，OR模式。
#
# 自测: 平安银行 (000001) K线图
#   验证: 红涨绿跌、三条EMA线平滑、成交量对齐。


if __name__ == "__main__":
    main()
