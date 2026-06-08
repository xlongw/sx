"""
侧边栏渲染 + 设置数据类
Sidebar settings — all user-configurable options in one dataclass.
"""

import os
from dataclasses import dataclass, field

import streamlit as st

from config import (
    DB_PATH,
    DATA_SOURCE,
    DEFAULT_THRESHOLD_B,
    DEFAULT_THRESHOLD_C,
    DEFAULT_MAX_STOCKS,
    MIN_CACHE_DAYS,
)
from utils import format_code, get_stock_name
from watchlist_manager import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
)


@dataclass
class SidebarSettings:
    """收纳侧边栏所有控件的返回值。"""

    data_source: str = "baostock"
    logic_mode: str = "OR"
    enabled_a: bool = True
    threshold_a_dev: int = 15
    enabled_b: bool = True
    threshold_b: float = 1.02
    enabled_c: bool = False
    threshold_c: int = 2
    enabled_d: bool = False
    threshold_d_vol: float = 1.5
    use_custom: bool = False
    custom_codes: str | None = None
    mainboard_only: bool = True
    max_stocks: int = 200
    filter_watchlist_only: bool = False
    refresh_btn: bool = False
    start_btn: bool = False
    backfill_btn: bool = False
    cleanup_btn: bool = False


def render_sidebar() -> SidebarSettings:
    """渲染侧边栏，返回所有用户设置的 SidebarSettings。"""
    s = SidebarSettings()

    with st.sidebar:
        # ── 数据源选择 ──
        st.header("📡 数据源")
        s.data_source = st.radio(
            "选择数据源",
            options=["baostock", "akshare"],
            format_func=lambda x: "Baostock（免费、稳定）" if x == "baostock" else "AkShare（免费、数据全）",
            index=0 if DATA_SOURCE == "baostock" else 1,
            help="Baostock: 需登录，数据稳定；AkShare: 无需登录，数据更全",
        )

        st.divider()

        st.header("🔧 筛选设置")

        s.logic_mode = st.radio(
            "逻辑组合方式",
            options=["OR", "AND"],
            format_func=lambda x: "任一条件 (OR)" if x == "OR" else "同时满足 (AND)",
            help="OR: 满足任一勾选条件即可; AND: 需同时满足所有勾选条件",
        )

        st.divider()

        st.subheader("筛选条件")
        s.enabled_a = st.checkbox(
            "条件A: 首次站上三线",
            value=True,
            help="今日收盘价首次同时大于EMA21/55/120，且未过度远离均线",
        )
        s.threshold_a_dev = st.slider(
            "A-偏离度上限 (%)",
            min_value=3, max_value=100, value=15, step=1,
            help="收盘价偏离EMA均值 < 此值（防止选出已大幅远离均线的股票）",
            disabled=not s.enabled_a,
        )

        s.enabled_b = st.checkbox(
            "条件B: 均线粘合",
            value=True,
            help="三条EMA均线相互靠近（粘合）",
        )
        s.threshold_b = st.slider(
            "粘合阈值",
            min_value=1.01, max_value=1.05, value=DEFAULT_THRESHOLD_B, step=0.005,
            help="EMA最大值/最小值 <= 此值",
            disabled=not s.enabled_b,
        )

        s.enabled_c = st.checkbox(
            "条件C: 低波动",
            value=False,
            help="股价在均线附近且波动率低",
        )
        s.threshold_c = st.slider(
            "偏离阈值 (%)",
            min_value=1, max_value=5, value=int(DEFAULT_THRESHOLD_C * 100), step=1,
            help="收盘价偏离均线 < 此百分比",
            disabled=not s.enabled_c,
        )

        s.enabled_d = st.checkbox(
            "条件D: 放量确认",
            value=False,
            help="今日成交量 > 20日均量 × 倍数（有资金介入）",
        )
        s.threshold_d_vol = st.slider(
            "放量倍数",
            min_value=1.2, max_value=3.0, value=1.5, step=0.1,
            help="今日成交量 / 20日均量 > 此值",
            disabled=not s.enabled_d,
        )

        st.divider()

        st.subheader("📋 股票范围")
        s.use_custom = st.checkbox("使用自定义股票列表", value=False)
        s.mainboard_only = st.checkbox(
            "仅沪深主板",
            value=True,
            help="勾选：仅筛选沪深主板股票（60xxxx/00xxxx）\n取消：筛选全市场（含创业板/科创板）",
        )

        if s.use_custom:
            s.custom_codes = st.text_area(
                "输入股票代码（每行一个，如: 000001.SZ,600036.SH）",
                value="000001\n600036\n600030\n000858\n600519",
                height=120,
                help="支持格式: 000001 或 000001.SZ",
            )

        s.max_stocks = st.slider(
            "刷新/筛选股票数量",
            min_value=50, max_value=4000, value=DEFAULT_MAX_STOCKS, step=50,
            help="控制【刷新数据】和【开始筛选】每次处理的股票数量上限",
        )

        st.divider()

        # ── 操作按钮 ──
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            s.start_btn = st.button("🔍 开始筛选", type="primary", width="stretch", key="btn_start_screen")
        with col_btn2:
            s.refresh_btn = st.button(
                "🔄 刷新数据",
                width="stretch",
                help="重新获取所有股票数据（增量更新）",
                key="btn_refresh_data",
            )
        # 按钮状态同步写入 session_state（每轮强制更新，确保状态一致）
        for _key, _val in [("_btn_start", s.start_btn), ("_btn_refresh", s.refresh_btn)]:
            st.session_state[_key] = _val

        st.divider()

        # ── 数据库维护 ──
        st.header("🛠️ 数据库维护")

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            s.backfill_btn = st.button(
                "📦 补充历史数据",
                width="stretch",
                help=f"扫描数据不足{MIN_CACHE_DAYS}天的股票，自动补充更早期的历史数据",
                key="btn_backfill",
            )
        with col_m2:
            s.cleanup_btn = st.button(
                "🧹 清理无效数据",
                width="stretch",
                help="移除非主板股票（创业板/科创板/北交所）和已退市/ST股票的数据",
                key="btn_cleanup",
            )
        # 维护按钮状态同步写入 session_state（每轮强制更新）
        for _key, _val in [("_btn_backfill", s.backfill_btn), ("_btn_cleanup", s.cleanup_btn)]:
            st.session_state[_key] = _val

        st.divider()

        # ── 自选股管理 ──
        st.header("📌 自选股管理")

        with st.form("add_watchlist_form", clear_on_submit=True):
            col_code, col_name = st.columns(2)
            code_input = col_code.text_input("股票代码", placeholder="如: 000001")
            name_input = col_name.text_input("名称（可选）", placeholder="自动获取")
            wl_submitted = st.form_submit_button("➕ 添加到自选", width="stretch")
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
                if col_kline.button("📊", key=f"kline_{wl_code}", help="查看K线图"):
                    st.session_state["selected_kline_code"] = wl_code
                    st.session_state["selected_kline_name"] = wl_name
                    st.session_state["show_kline_from_watch"] = True
                if col_del.button("❌", key=f"del_{wl_code}", help="删除"):
                    remove_from_watchlist(wl_code)
                    st.rerun()
        else:
            st.info("暂无自选股")

        s.filter_watchlist_only = st.checkbox(
            "🔍 仅筛选自选股中的股票",
            value=False,
            help="勾选后仅对自选股列表执行筛选",
        )

        st.divider()
        source_label = "Baostock" if s.data_source == "baostock" else "AkShare"
        st.caption(f"当前数据源: {source_label} | 缓存: {os.path.abspath(DB_PATH)}")

    return s
