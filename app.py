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

# ── 启动时清理 .pyc 缓存 ──────────────────────────────────
def _clean_pycache() -> None:
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


# ── 端口检测（启动前检查，避免多实例冲突） ─────────────────
def _check_port(port: int = 8501) -> bool:
    """检查端口是否已被占用，返回 True 表示端口可用。"""
    import socket as _socket
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _sock:
        try:
            _sock.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


if not _check_port(8501):
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "⚠️ 端口 8501 已被占用，可能存在旧 Streamlit 进程。"
        "建议运行 stop.bat 清理后重新启动。"
    )

# ── 本地模块导入 ─────────────────────────────────────────
from config import (
    DB_PATH, DEFAULT_STOCKS, DEFAULT_THRESHOLD_B, DEFAULT_THRESHOLD_C,
    DEFAULT_MAX_STOCKS, DEFAULT_DAYS, MIN_DAYS_REQUIRED, MIN_CACHE_DAYS,
    KLINE_DISPLAY_DAYS, DATA_SOURCE,
    get_default_stocks, filter_stock_list, is_mainboard,
)
from data_fetcher import bs_login, bs_logout
from data_manager import (
    init_db, load_from_db, get_all_cached_codes,
    fetch_and_cache_stocks_parallel,
    backfill_ema_cache, backfill_all_stocks,
    find_stocks_needing_backfill,
    cleanup_invalid_stocks, get_mainboard_db_stats,
)
from screen_engine import screen_single_stock
from utils import setup_logger, format_code
from watchlist_manager import get_watchlist

# ── UI 模块 ──────────────────────────────────────────────
from ui.dashboard import get_db_stats, render_db_dashboard
from ui.sidebar import render_sidebar, SidebarSettings
from ui.results import (
    render_screening_results,
    render_kline_from_watch,
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
def get_stock_list(mainboard_only: bool = True) -> pd.DataFrame:
    """返回股票列表。mainboard_only=True 时仅返回沪深主板。"""
    if mainboard_only:
        filtered = get_default_stocks()
        return pd.DataFrame(filtered, columns=["code", "name"])
    else:
        cached_codes = get_all_cached_codes()
        stock_map = {c: n for c, n in DEFAULT_STOCKS}
        result = [(code, stock_map.get(code, "")) for code in sorted(cached_codes)]
        return pd.DataFrame(result, columns=["code", "name"])


def parse_custom_codes(text: str) -> list[tuple[str, str]]:
    """解析用户自定义股票代码。"""
    codes_raw = [c.strip() for c in text.split("\n") if c.strip()]
    return [(format_code(c), "") for c in codes_raw]


def _screen_one_stock(
    code: str, name: str, logic_mode: str,
    threshold_b: float, threshold_c: float,
    threshold_a_dev: float, threshold_d_vol: float,
    enabled_a: bool, enabled_b: bool, enabled_c: bool, enabled_d: bool,
) -> dict:
    """加载并筛选单支股票。"""
    result = {"code": code, "name": name, "signals": [], "error": None, "skipped": False}
    try:
        df = load_from_db(code, limit=DEFAULT_DAYS)
        if df is None or len(df) < MIN_DAYS_REQUIRED:
            result["skipped"] = True
            return result
        result["signals"] = screen_single_stock(
            df, code, name,
            logic_mode=logic_mode,
            threshold_b=threshold_b, threshold_c=threshold_c,
            threshold_a_dev=threshold_a_dev, threshold_d_vol=threshold_d_vol,
            enabled_a=enabled_a, enabled_b=enabled_b,
            enabled_c=enabled_c, enabled_d=enabled_d,
        )
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"筛选异常 {code} {name}: {e}")
    return result


# ── 主函数 ───────────────────────────────────────────────
def main():
    st.title("📈 EMA均线形态股票筛选工具")
    st.caption(
        f"基于 EMA21 / EMA55 / EMA120 三条均线的技术形态筛选  |  "
        f"🕐 启动时间: {datetime.now().strftime('%H:%M:%S')}"
    )

    init_db()

    # EMA 缓存回填（首次运行）
    if "ema_backfill_done" not in st.session_state:
        backfill_ema_cache()
        st.session_state["ema_backfill_done"] = True

    # ── 侧边栏（提前渲染，获取按钮状态） ──
    s: SidebarSettings = render_sidebar()

    # Baostock 登录
    if s.data_source == "baostock":
        try:
            bs_login()
        except Exception as e:
            logger.warning(f"Baostock 登录失败: {e}")

    # ── 会话状态初始化 ──
    for key, default in [
        ("results", None), ("selected_code", None), ("selected_name", ""),
        ("show_kline", False), ("screening", False), ("progress", 0), ("status_text", ""),
        ("selected_kline_code", None), ("selected_kline_name", ""),
        ("show_kline_from_watch", False),
        ("failed_count", 0), ("skipped_count", 0), ("failed_list", []),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # ── 首次启动检测 + 数据仪表盘 ──
    first_visit = "startup_check_done" not in st.session_state
    coverage_warning = ""
    cleanup_warning = ""

    # 有操作进行中时强制折叠仪表盘，不遮挡进度条/结果
    has_action = (
        st.session_state.get("_btn_start")
        or st.session_state.get("_btn_refresh")
        or st.session_state.get("_btn_backfill")
        or st.session_state.get("_btn_cleanup")
        or st.session_state.get("screening")
    )

    if first_visit:
        st.session_state["startup_check_done"] = True

        mb_stats = get_mainboard_db_stats()
        if mb_stats:
            mb_total = mb_stats.get("total_stocks", 0)
            mb_good = mb_stats.get("coverage_good", 0)
            mb_fair = mb_stats.get("coverage_fair", 0)
            backfill_pct = (mb_good / mb_total * 100) if mb_total > 0 else 100
            if backfill_pct < 70 and mb_fair > 100:
                coverage_warning = (
                    f"💡 **数据覆盖提醒**：仅 {backfill_pct:.0f}% 的主板股票(≥300天)数据充足，"
                    f"建议使用侧边栏 **📦 补充历史数据** 提升信号质量。"
                )

        preview = cleanup_invalid_stocks(dry_run=True)
        invalid_total = preview.get("non_mainboard_codes", 0) + preview.get("bad_codes", 0)
        if invalid_total > 0:
            cleanup_warning = (
                f"⚠️ **数据库冗余提醒**：检测到 {invalid_total} 支无效股票数据"
                f"（非主板 {preview['non_mainboard_codes']} 支 + "
                f"问题股票 {preview['bad_codes']} 支），"
                f"占用 {preview['non_mainboard_records'] + preview['bad_records']:,} 条记录。"
                f"可使用侧边栏 **🧹 清理无效数据** 释放空间。"
            )

    # 仅在没有操作时自动展开（首次访问且无按钮点击）
    expand_dashboard = first_visit and not has_action and not st.session_state.get("screening")

    db_stats = get_db_stats()
    render_db_dashboard(
        db_stats,
        first_visit=expand_dashboard,
        coverage_warning=coverage_warning,
        cleanup_warning=cleanup_warning,
    )

    # ── 刷新数据 ──
    if st.session_state.get("_btn_refresh"):
        st.session_state["screening"] = True
        st.session_state["status_text"] = "正在刷新数据..."

        stock_df = get_stock_list(mainboard_only=s.mainboard_only)
        if s.use_custom and s.custom_codes:
            parsed = parse_custom_codes(s.custom_codes)
            stock_df = pd.DataFrame(parsed, columns=["code", "name"])

        total = min(len(stock_df), s.max_stocks)
        stock_list = [
            (str(row["code"]), str(row.get("name", "")))
            for _, row in stock_df.head(total).iterrows()
        ]

        status_placeholder = st.empty()
        progress_bar = st.progress(0, text="正在检查缓存新鲜度...")

        def _update_progress(done: int, total_count: int, code: str):
            pct = int(done / total_count * 100)
            progress_bar.progress(pct, text=f"已处理 {done}/{total_count}: {code}")

        fetched, _, skipped_fresh = fetch_and_cache_stocks_parallel(
            stock_list, source=s.data_source, progress_callback=_update_progress,
        )

        progress_bar.empty()
        status_placeholder.empty()

        msg_parts = ["数据刷新完成！"]
        if skipped_fresh > 0:
            msg_parts.append(f"已新鲜跳过 {skipped_fresh} 支")
        msg_parts.append(f"成功获取 {fetched}/{total} 支股票数据")
        st.success(" | ".join(msg_parts))

        # 回填提示
        needing = find_stocks_needing_backfill(min_days=MIN_CACHE_DAYS)
        if s.mainboard_only:
            needing = [n for n in needing if is_mainboard(n["code"])]
        if needing and len(needing) <= 200:
            st.info(
                f"💡 **数据覆盖提示**：{len(needing)} 支股票数据不足 {MIN_CACHE_DAYS} 天，"
                f"可使用侧边栏 **📦 补充历史数据** 提升信号质量。"
            )

        st.session_state["screening"] = False

    # ── 补充历史数据 ──
    if st.session_state.get("_btn_backfill"):
        with st.spinner("正在扫描数据不足的股票..."):
            needing = find_stocks_needing_backfill(min_days=MIN_CACHE_DAYS)
            stock_list = [(n["code"], n["name"]) for n in needing]

        if not stock_list:
            st.success(f"所有股票数据充足 (>= {MIN_CACHE_DAYS} 天)")
        else:
            st.info(f"发现 {len(stock_list)} 支股票数据不足 {MIN_CACHE_DAYS} 天，开始补充...")
            progress_bar = st.progress(0, text="准备中...")

            def _bf_progress(done: int, total_count: int, code: str, msg: str):
                pct = int(done / total_count * 100)
                progress_bar.progress(pct, text=f"已处理 {done}/{total_count}: {code} — {msg}")

            result = backfill_all_stocks(
                stock_list, target_days=MIN_CACHE_DAYS,
                source=s.data_source, progress_callback=_bf_progress,
            )
            progress_bar.empty()
            st.success(
                f"历史数据补充完成！"
                f"已补充 {result['backfilled']} 支 (+{result['days_added']}天) | "
                f"已充足 {result['skipped_ok']} 支 | "
                f"无更多数据 {result.get('skipped_no_data', 0)} 支"
            )

    # ── 清理无效数据 ──
    if st.session_state.get("_btn_cleanup"):
        with st.spinner("正在扫描无效数据..."):
            preview = cleanup_invalid_stocks(dry_run=True)

        st.warning(
            f"⚠️ 确认清理？将删除以下数据：\n\n"
            f"• 非主板股票: **{preview['non_mainboard_codes']} 支** "
            f"({preview['non_mainboard_records']:,} 条)\n"
            f"• 已退市/ST: **{preview['bad_codes']} 支** "
            f"({preview['bad_records']:,} 条)\n\n"
            f"总计删除 **{preview['non_mainboard_codes'] + preview['bad_codes']} 支**。"
        )

        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            confirm_cleanup = st.button("✅ 确认清理", width="stretch")
        with col_cancel:
            cancel_cleanup = st.button("❌ 取消", width="stretch")

        if confirm_cleanup:
            with st.spinner("正在清理..."):
                result = cleanup_invalid_stocks(dry_run=False)
            st.success(f"✅ 清理完成！已删除 {result['deleted_total']:,} 条记录。")
            logger.info(f"用户清理数据库: 删除 {result['deleted_total']} 条记录")
        elif cancel_cleanup:
            st.info("已取消清理操作。")

    # ── 开始筛选 ──
    if st.session_state.get("_btn_start"):
        st.session_state["screening"] = True
        st.session_state["results"] = None
        st.session_state["selected_code"] = None
        st.session_state["show_kline"] = False
        st.toast("🔍 正在执行筛选...", icon="🔍")

    if st.session_state.get("screening") and st.session_state.get("results") is None:
        try:
            start_time = time.time()

            # 构建股票列表
            if s.filter_watchlist_only:
                wl = get_watchlist()
                if wl.empty:
                    st.warning("自选股列表为空，请先在侧边栏添加自选股。")
                    st.session_state["screening"] = False
                    st.stop()
                stock_list = [(str(r["code"]), str(r.get("name", ""))) for _, r in wl.iterrows()]
            elif s.use_custom and s.custom_codes:
                parsed = parse_custom_codes(s.custom_codes)
                stock_list = []
                for c, _ in parsed:
                    cached = load_from_db(c, limit=1)
                    name = str(cached["name"].iloc[0]) if not cached.empty else ""
                    stock_list.append((c, name))
            else:
                stock_df = get_stock_list(mainboard_only=s.mainboard_only)
                stock_list = [
                    (str(row["code"]), str(row.get("name", "")))
                    for _, row in stock_df.head(s.max_stocks).iterrows()
                ]

            if not stock_list:
                stock_list = (
                    get_default_stocks() if s.mainboard_only
                    else [(c, "") for c in get_all_cached_codes()]
                )

            if s.mainboard_only and not (s.use_custom and s.custom_codes) and not s.filter_watchlist_only:
                stock_list = filter_stock_list(stock_list)

            total = len(stock_list)
            progress_bar = st.progress(0, text="正在加载数据并计算...")
            status_text = st.empty()

            all_signals = []
            screened = failed = skipped = 0
            failed_list = []

            t_a_dev = s.threshold_a_dev / 100.0
            t_c = s.threshold_c / 100.0

            for i, (code, name) in enumerate(stock_list):
                try:
                    result = _screen_one_stock(
                        code, name,
                        logic_mode=s.logic_mode, threshold_b=s.threshold_b,
                        threshold_c=t_c, threshold_a_dev=t_a_dev,
                        threshold_d_vol=s.threshold_d_vol,
                        enabled_a=s.enabled_a, enabled_b=s.enabled_b,
                        enabled_c=s.enabled_c, enabled_d=s.enabled_d,
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
                    status_text.caption(f"信号: {len(all_signals)} | 跳过: {skipped} | 失败: {failed}")

            progress_bar.empty()
            status_text.empty()
            elapsed = time.time() - start_time

            if all_signals:
                df_results = pd.DataFrame(all_signals).sort_values("deviation_pct")
                st.session_state["results"] = df_results
            else:
                st.session_state["results"] = pd.DataFrame()

            st.session_state["elapsed"] = elapsed
            st.session_state["screened_count"] = screened
            st.session_state["screening"] = False
            st.session_state["failed_count"] = failed
            st.session_state["skipped_count"] = skipped
            st.session_state["failed_list"] = failed_list

        except Exception as e:
            st.session_state["screening"] = False
            st.session_state["results"] = pd.DataFrame()
            st.error(f"筛选过程出错: {e}")
            logger.error(f"筛选流程异常: {e}", exc_info=True)

    # ── 结果展示 ──
    render_screening_results()

    # ── 自选股 K 线 ──
    render_kline_from_watch()

    # ── Footer ──
    st.divider()
    st.caption(
        "⚠️ 免责声明: 本工具仅供学习和研究使用，不构成任何投资建议。"
        "股市有风险，投资需谨慎。"
    )


if __name__ == "__main__":
    main()
