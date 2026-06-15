"""
数据质量仪表盘渲染模块
Data quality dashboard — DB stats + expandable overview.
"""

import streamlit as st
from datetime import datetime

from data_manager import _get_conn, get_mainboard_db_stats
from utils import setup_logger

logger = setup_logger(__name__)


@st.cache_data(ttl=10, show_spinner=False)  # 短 TTL，避免错误结果被长时间缓存
def get_db_stats() -> dict:
    """查询数据库统计数据，同时返回全库和仅主板的统计。"""
    conn = _get_conn()
    total_stocks = 0
    total_records = 0
    latest_date = "N/A"
    coverage = (0, 0, 0, 0)
    ema_pct = 0
    mb_stats = {}
    errors = []

    try:
        total_stocks = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM daily_quotes"
        ).fetchone()[0]
    except Exception as e:
        errors.append(f"股票总数: {e}")

    try:
        total_records = conn.execute(
            "SELECT COUNT(*) FROM daily_quotes"
        ).fetchone()[0]
    except Exception as e:
        errors.append(f"记录数: {e}")

    try:
        latest_date = conn.execute(
            "SELECT MAX(date) FROM daily_quotes"
        ).fetchone()[0] or "N/A"
    except Exception as e:
        errors.append(f"最新日期: {e}")

    try:
        coverage = conn.execute("""
            SELECT
                COUNT(CASE WHEN cnt >= 300 THEN 1 END) as good,
                COUNT(CASE WHEN cnt >= 120 AND cnt < 300 THEN 1 END) as fair,
                COUNT(CASE WHEN cnt >= 60 AND cnt < 120 THEN 1 END) as low,
                COUNT(CASE WHEN cnt < 60 THEN 1 END) as poor
            FROM (SELECT code, COUNT(*) as cnt FROM daily_quotes GROUP BY code)
        """).fetchone()
    except Exception as e:
        errors.append(f"覆盖度: {e}")

    try:
        ema_total = conn.execute("SELECT COUNT(*) FROM daily_quotes").fetchone()[0]
        ema_cached = conn.execute(
            "SELECT COUNT(*) FROM daily_quotes WHERE ema21 IS NOT NULL"
        ).fetchone()[0]
        ema_pct = round(ema_cached / ema_total * 100, 1) if ema_total > 0 else 0
    except Exception as e:
        errors.append(f"EMA: {e}")

    try:
        mb_stats = get_mainboard_db_stats()
    except Exception as e:
        errors.append(f"主板统计: {e}")

    if errors:
        logger.warning(f"获取数据库统计部分失败 ({len(errors)}): {'; '.join(errors[:3])}")

    return {
        "total_stocks": total_stocks,
        "total_records": total_records,
        "latest_date": latest_date,
        "coverage_good": coverage[0] if coverage else 0,
        "coverage_fair": coverage[1] if coverage else 0,
        "coverage_low": coverage[2] if coverage else 0,
        "coverage_poor": coverage[3] if coverage else 0,
        "ema_cached_pct": ema_pct,
        "mainboard": mb_stats,
        "errors": errors,
    }


def render_db_dashboard(
    stats: dict,
    first_visit: bool = False,
    coverage_warning: str = "",
    cleanup_warning: str = "",
) -> None:
    """渲染数据质量仪表盘。首次访问自动展开，后续折叠不遮挡页面。"""
    # 允许缺少部分数据的仪表盘继续渲染（不因个别查询失败而完全空白）
    if stats.get("total_stocks", 0) == 0 and stats.get("total_records", 0) == 0:
        st.info("📊 数据仪表盘正在加载中，请稍候…")
        return

    errors = stats.get("errors", [])
    if errors:
        st.warning(f"⚠️ 部分统计数据加载失败 ({len(errors)} 项)，显示结果可能不完整")

    mb = stats.get("mainboard", {})

    # 生成摘要文本（折叠时显示在标题右侧）
    latest_str = stats.get("latest_date", "N/A")
    freshness_icon = ""
    if latest_str and latest_str != "N/A":
        try:
            latest_dt = datetime.strptime(str(latest_str)[:10], "%Y-%m-%d")
            age_days = (datetime.now() - latest_dt).days
            freshness_icon = (
                "🟢" if age_days == 0
                else "🟡" if age_days <= 1
                else "🟠" if age_days <= 3
                else "🔴"
            )
        except ValueError:
            pass

    summary = (
        f"{freshness_icon} {stats['total_stocks']:,}支 · "
        f"{stats['total_records']:,}条 · "
        f"EMA {stats['ema_cached_pct']}%"
    )

    if first_visit and (coverage_warning or cleanup_warning):
        summary += " ⚡"

    with st.expander(f"📊 数据概览 — {summary}", expanded=first_visit):
        # 首次访问检测提醒
        if coverage_warning:
            st.info(coverage_warning)
        if cleanup_warning:
            st.warning(cleanup_warning)

        # ── 第一行：核心指标 ──
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
            latest_str = stats.get("latest_date", "N/A")
            if latest_str and latest_str != "N/A":
                try:
                    latest_dt = datetime.strptime(str(latest_str)[:10], "%Y-%m-%d")
                    age_days = (datetime.now() - latest_dt).days
                    if age_days == 0:
                        freshness = "🟢 今日"
                    elif age_days == 1:
                        freshness = "🟡 1天前"
                    elif age_days <= 3:
                        freshness = f"🟠 {age_days}天前"
                    else:
                        freshness = f"🔴 {age_days}天前"
                except ValueError:
                    freshness = "⚪ N/A"
            else:
                freshness = "⚪ N/A"
            st.metric("🕐 数据新鲜度", freshness)
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
