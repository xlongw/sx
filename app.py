"""
EMA 均线形态股票筛选工具 — Streamlit 主界面
EMA Crossover Pattern Stock Screening Tool

启动: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import time
import os
import glob as _glob
from datetime import datetime

# ── 启动时清理 .pyc 缓存，防止旧版本代码被加载 ─────────
def _clean_pycache() -> None:
    """清理项目目录下所有 __pycache__ 和 .pyc 文件。"""
    _root = os.path.dirname(os.path.abspath(__file__))
    _count = 0
    for _d in _glob.glob(os.path.join(_root, "**", "__pycache__"), recursive=True):
        try:
            for _f in os.listdir(_d):
                os.unlink(os.path.join(_d, _f))
                _count += 1
            os.rmdir(_d)
        except OSError:
            pass
    if _count:
        import logging
        logging.getLogger(__name__).info(f"启动时清理了 {_count} 个 .pyc 缓存文件")

_clean_pycache()

# ── 本地模块导入 ─────────────────────────────────────────
from config import (
    DB_PATH,
    DEFAULT_STOCKS,
    DEFAULT_THRESHOLD_B,
    DEFAULT_THRESHOLD_C,
    DEFAULT_MAX_STOCKS,
    DEFAULT_DAYS,
    MIN_DAYS_REQUIRED,
    MIN_CACHE_DAYS,
    KLINE_DISPLAY_DAYS,
    DATA_SOURCE,
    get_default_stocks,
    filter_stock_list,
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
    backfill_all_stocks,
    find_stocks_needing_backfill,
    cleanup_invalid_stocks,
    get_mainboard_db_stats,
    _get_conn,
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
    """返回内置默认股票列表（已过滤非主板/退市股票）。"""
    filtered = get_default_stocks()
    return pd.DataFrame(filtered, columns=["code", "name"])


def parse_custom_codes(text: str) -> list[tuple[str, str]]:
    """解析用户自定义股票代码，返回 [(code, name), ...]。"""
    codes_raw = [c.strip() for c in text.split("\n") if c.strip()]
    codes_clean = [format_code(c) for c in codes_raw]
    return [(c, "") for c in codes_clean]


# ── 数据质量仪表盘 ──────────────────────────────────────
def get_db_stats() -> dict:
    """查询数据库统计数据，同时返回全库和仅主板的统计。"""
    try:
        conn = _get_conn()
        total_stocks = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM daily_quotes"
        ).fetchone()[0]
        total_records = conn.execute(
            "SELECT COUNT(*) FROM daily_quotes"
        ).fetchone()[0]
        latest_date = conn.execute(
            "SELECT MAX(date) FROM daily_quotes"
        ).fetchone()[0] or "N/A"

        # 数据覆盖健康度：统计各股票数据天数分布
        coverage = conn.execute("""
            SELECT
                COUNT(CASE WHEN cnt >= 300 THEN 1 END) as good,
                COUNT(CASE WHEN cnt >= 120 AND cnt < 300 THEN 1 END) as fair,
                COUNT(CASE WHEN cnt >= 60 AND cnt < 120 THEN 1 END) as low,
                COUNT(CASE WHEN cnt < 60 THEN 1 END) as poor
            FROM (SELECT code, COUNT(*) as cnt FROM daily_quotes GROUP BY code)
        """).fetchone()

        # EMA 缓存覆盖率
        ema_total = conn.execute(
            "SELECT COUNT(*) FROM daily_quotes"
        ).fetchone()[0]
        ema_cached = conn.execute(
            "SELECT COUNT(*) FROM daily_quotes WHERE ema21 IS NOT NULL"
        ).fetchone()[0]
        ema_pct = round(ema_cached / ema_total * 100, 1) if ema_total > 0 else 0

        # 仅主板股票统计
        mb_stats = get_mainboard_db_stats()

        return {
            "total_stocks": total_stocks,
            "total_records": total_records,
            "latest_date": latest_date,
            "coverage_good": coverage[0],
            "coverage_fair": coverage[1],
            "coverage_low": coverage[2],
            "coverage_poor": coverage[3],
            "ema_cached_pct": ema_pct,
            "mainboard": mb_stats,  # 仅主板统计
        }
    except Exception as e:
        logger.warning(f"获取数据库统计失败: {e}")
        return {}


def render_db_dashboard(stats: dict) -> None:
    """渲染数据质量仪表盘（全部缓存 vs 有效主板对比）。"""
    if not stats:
        return

    mb = stats.get("mainboard", {})

    st.divider()
    st.subheader("📊 数据概览")

    # ── 第一行：核心指标（全部缓存 / 有效主板 对比） ──
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("📋 股票总数",
                  f"{stats['total_stocks']:,}",
                  delta=f"有效主板 {mb.get('total_stocks', '—')}" if mb else None)
    with col2:
        st.metric("📝 总记录数",
                  f"{stats['total_records']:,}",
                  delta=f"主板 {mb.get('total_records', '—'):,}" if mb else None)
    with col3:
        st.metric("📅 最新数据", stats["latest_date"])
    with col4:
        st.metric("💾 EMA缓存率", f"{stats['ema_cached_pct']}%")
    with col5:
        all_total = stats["total_stocks"]
        all_good_pct = round(stats["coverage_good"] / all_total * 100, 1) if all_total else 0
        mb_total = mb.get("total_stocks", 0)
        mb_good_pct = round(mb.get("coverage_good", 0) / mb_total * 100, 1) if mb_total else 0
        st.metric("✅ 数据充足率(≥300天)",
                  f"{all_good_pct}%",
                  delta=f"主板 {mb_good_pct}%" if mb else None,
                  delta_color="off")

    # ── 第二行：覆盖度分布（全部缓存） ──
    st.caption("**全部缓存** 覆盖度分布")
    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        st.metric("🟢 ≥300天", stats["coverage_good"])
    with col_b:
        st.metric("🟡 120-299天", stats["coverage_fair"])
    with col_c:
        st.metric("🟠 60-119天", stats["coverage_low"])
    with col_d:
        st.metric("🔴 <60天", stats["coverage_poor"])

    # ── 第三行：覆盖度分布（仅有效主板） ──
    if mb:
        st.caption("**有效主板** 覆盖度分布")
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        with col_m1:
            st.metric("🟢 ≥300天", mb.get("coverage_good", 0))
        with col_m2:
            st.metric("🟡 120-299天", mb.get("coverage_fair", 0))
        with col_m3:
            st.metric("🟠 60-119天", mb.get("coverage_low", 0))
        with col_m4:
            st.metric("🔴 <60天", mb.get("coverage_poor", 0))


# ── 单股筛选辅助函数 ────────────────────────────────────
def _screen_one_stock(
    code: str,
    name: str,
    logic_mode: str,
    threshold_b: float,
    threshold_c: float,
    threshold_a_dev: float,
    threshold_d_vol: float,
    enabled_a: bool,
    enabled_b: bool,
    enabled_c: bool,
    enabled_d: bool,
) -> dict:
    """
    加载并筛选单支股票。

    Returns
    -------
    dict: {"signals": [...], "error": str|None, "skipped": bool, "code": str, "name": str}
    """
    result = {
        "code": code,
        "name": name,
        "signals": [],
        "error": None,
        "skipped": False,
    }
    try:
        df = load_from_db(code, limit=DEFAULT_DAYS)
        if df is None or len(df) < MIN_DAYS_REQUIRED:
            result["skipped"] = True
            return result

        signals = screen_single_stock(
            df, code, name,
            logic_mode=logic_mode,
            threshold_b=threshold_b,
            threshold_c=threshold_c,
            threshold_a_dev=threshold_a_dev,
            threshold_d_vol=threshold_d_vol,
            enabled_a=enabled_a,
            enabled_b=enabled_b,
            enabled_c=enabled_c,
            enabled_d=enabled_d,
        )
        result["signals"] = signals
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"筛选异常 {code} {name}: {e}")

    return result


# ── K线图独立绘制函数 ────────────────────────────────────
def plot_kline_for_stock(code: str, name: str, days: int = KLINE_DISPLAY_DAYS):
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
    if df is None or len(df) < KLINE_DISPLAY_DAYS:
        st.warning(f"股票 {code} 数据不足（需要至少{KLINE_DISPLAY_DAYS}个交易日），请先刷新数据。")
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

    # EMA 缓存回填：仅在会话首次运行时执行，后续交互跳过
    if "ema_backfill_done" not in st.session_state:
        backfill_ema_cache()
        st.session_state["ema_backfill_done"] = True

    # ── 数据质量仪表盘 ───────────────────────────────────
    db_stats = get_db_stats()
    render_db_dashboard(db_stats)

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
            help="今日收盘价首次同时大于EMA21/55/120，且未过度远离均线",
        )
        threshold_a_dev = st.slider(
            "A-偏离度上限 (%)",
            min_value=3,
            max_value=100,
            value=15,
            step=1,
            help="收盘价偏离EMA均值 < 此值（防止选出已大幅远离均线的股票）",
            disabled=not enabled_a,
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

        enabled_d = st.checkbox(
            "条件D: 放量确认",
            value=False,
            help="今日成交量 > 20日均量 × 倍数（有资金介入）",
        )
        threshold_d_vol = st.slider(
            "放量倍数",
            min_value=1.2,
            max_value=3.0,
            value=1.5,
            step=0.1,
            help="今日成交量 / 20日均量 > 此值",
            disabled=not enabled_d,
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

        # ── 数据库维护 ──
        st.header("🛠️ 数据库维护")

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            backfill_btn = st.button(
                "📦 补充历史数据",
                use_container_width=True,
                help=f"扫描数据不足{MIN_CACHE_DAYS}天的股票，自动补充更早期的历史数据",
            )
        with col_m2:
            cleanup_btn = st.button(
                "🧹 清理无效数据",
                use_container_width=True,
                help="移除非主板股票（创业板/科创板/北交所）和已退市/ST股票的数据",
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
        # 筛选统计
        ("failed_count", 0),
        ("skipped_count", 0),
        ("failed_list", []),
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

    # ── 补充历史数据 ──
    if backfill_btn:
        with st.spinner("正在扫描数据不足的股票..."):
            needing = find_stocks_needing_backfill(min_days=MIN_CACHE_DAYS)
            stock_list = [(s["code"], s["name"]) for s in needing]

        if not stock_list:
            st.success(f"所有股票数据充足 (>= {MIN_CACHE_DAYS} 天)")
        else:
            st.info(f"发现 {len(stock_list)} 支股票数据不足 {MIN_CACHE_DAYS} 天，开始补充...")
            progress_bar = st.progress(0, text="准备中...")

            def _backfill_progress(done: int, total_count: int, code: str, msg: str):
                pct = int(done / total_count * 100)
                progress_bar.progress(pct, text=f"已处理 {done}/{total_count}: {code} — {msg}")

            result = backfill_all_stocks(
                stock_list,
                target_days=MIN_CACHE_DAYS,
                source=data_source,
                progress_callback=_backfill_progress,
            )
            progress_bar.empty()
            st.success(
                f"历史数据补充完成！"
                f"已补充 {result['backfilled']} 支 (+{result['days_added']}天) | "
                f"已充足 {result['skipped_ok']} 支 | "
                f"无更多数据 {result.get('skipped_no_data', 0)} 支"
            )

    # ── 清理无效数据 ──
    if cleanup_btn:
        # 先扫描预览
        with st.spinner("正在扫描无效数据..."):
            preview = cleanup_invalid_stocks(dry_run=True)

        st.warning(
            f"⚠️ 确认清理？将删除以下数据：\n\n"
            f"• 非主板股票（创业板/科创板/北交所）: **{preview['non_mainboard_codes']} 支** "
            f"({preview['non_mainboard_records']:,} 条记录)\n"
            f"• 已退市/ST 问题股票: **{preview['bad_codes']} 支** "
            f"({preview['bad_records']:,} 条记录)\n\n"
            f"总计将删除 **{(preview['non_mainboard_codes'] + preview['bad_codes'])} 支** 股票的数据。"
        )

        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            confirm_cleanup = st.button(
                "✅ 确认清理",
                use_container_width=True,
                help="确认删除上述无效数据（不可撤销）",
            )
        with col_cancel:
            cancel_cleanup = st.button("❌ 取消", use_container_width=True)

        if confirm_cleanup:
            with st.spinner("正在清理..."):
                result = cleanup_invalid_stocks(dry_run=False)
            st.success(
                f"✅ 清理完成！已删除 {result['deleted_total']:,} 条记录。"
                f"请重启应用以刷新仪表盘。"
            )
            logger.info(
                f"用户手动清理数据库: 删除 {result['deleted_total']} 条记录"
            )
        elif cancel_cleanup:
            st.info("已取消清理操作。")

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
            stock_list = get_default_stocks()

        # 非自定义模式：自动过滤非主板/退市股票
        if not (use_custom and custom_codes) and not filter_watchlist_only:
            stock_list = filter_stock_list(stock_list)

        total = len(stock_list)
        progress_bar = st.progress(0, text="正在加载数据并计算...")
        status_text = st.empty()

        all_signals = []
        screened = 0
        failed = 0
        skipped = 0
        failed_list = []  # 记录失败的股票

        # 串行筛选（每支 ~5ms，无需并行开销）
        t_a_dev = threshold_a_dev / 100.0
        t_c = threshold_c / 100.0

        for i, (code, name) in enumerate(stock_list):
            try:
                result = _screen_one_stock(
                    code, name,
                    logic_mode=logic_mode,
                    threshold_b=threshold_b,
                    threshold_c=t_c,
                    threshold_a_dev=t_a_dev,
                    threshold_d_vol=threshold_d_vol,
                    enabled_a=enabled_a,
                    enabled_b=enabled_b,
                    enabled_c=enabled_c,
                    enabled_d=enabled_d,
                )
                if result["error"]:
                    failed += 1
                    failed_list.append((code, name, result["error"]))
                elif result["skipped"]:
                    skipped += 1
                elif result["signals"]:
                    all_signals.extend(result["signals"])
                screened += 1
            except Exception as e:
                failed += 1
                failed_list.append((code, name, str(e)))
                logger.error(f"筛选异常 {code}: {e}")

            if (i + 1) % 50 == 0 or i == total - 1:
                pct = int((i + 1) / total * 100)
                progress_bar.progress(pct, text=f"已筛选 {i+1}/{total}")
                status_text.caption(
                    f"信号: {len(all_signals)} | 跳过: {skipped} | 失败: {failed}"
                )

        progress_bar.empty()
        status_text.empty()
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
        st.session_state["failed_count"] = failed
        st.session_state["skipped_count"] = skipped
        st.session_state["failed_list"] = failed_list

    # ── 显示结果 ──
    results = st.session_state.get("results")
    if results is not None:
        elapsed = st.session_state.get("elapsed", 0)
        screened = st.session_state.get("screened_count", 0)
        failed = st.session_state.get("failed_count", 0)
        skipped = st.session_state.get("skipped_count", 0)
        failed_list = st.session_state.get("failed_list", [])

        st.divider()
        st.subheader(f"筛选结果: 共 {len(results)} 支股票触发信号")

        # 筛选统计摘要
        col_s1, col_s2, col_s3, col_s4 = st.columns(4)
        with col_s1:
            st.metric("📊 已筛选", f"{screened}")
        with col_s2:
            st.metric("🔔 触发信号", f"{len(results)}")
        with col_s3:
            st.metric("⏭️ 跳过(数据不足)", f"{skipped}")
        with col_s4:
            st.metric("❌ 失败", f"{failed}", delta=None if failed == 0 else f"⚠️")

        st.caption(f"耗时 {elapsed:.1f} 秒")

        # 失败详情（可展开）
        if failed > 0 and failed_list:
            with st.expander(f"⚠️ 失败详情 ({failed} 支股票)", expanded=False):
                for code, name, err in failed_list:
                    st.text(f"• {code} {name}: {err[:120]}")

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
            # 如有放量数据，追加显示
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
                df_display,
                use_container_width=True,
                hide_index=True,
                column_config=col_config,
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
                if df_kline is not None and len(df_kline) >= KLINE_DISPLAY_DAYS:
                    fig = plot_kline_with_emas(
                        df_kline,
                        sel_code,
                        st.session_state.get("selected_name", ""),
                        str(df_kline["date"].iloc[-1])[:10],
                        signal_types,
                        days=KLINE_DISPLAY_DAYS,
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
