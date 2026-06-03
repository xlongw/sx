"""
SQLite 缓存管理 + 增量更新
SQLite cache management and incremental data refresh.
"""

import sqlite3
import time
import random
import threading
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from config import (
    DB_PATH, DEFAULT_DAYS, CACHE_VALIDITY_DAYS, MIN_DAYS_KLINE,
    MIN_CACHE_DAYS, BACKFILL_LOOKBACK_DAYS,
    MAX_PARALLEL_FETCHES, FETCH_RANDOM_DELAY_MIN, FETCH_RANDOM_DELAY_MAX,
    DATA_SOURCE,
)
from data_fetcher import fetch_stock_data_unified
from indicator import calc_indicators
from utils import setup_logger, log_skip, log_error

logger = setup_logger(__name__)

# ── 数据库连接池（模块级单例 + WAL 模式） ──────────────────
_conn_local = threading.local()
_conn_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接（线程安全 + WAL 模式）。"""
    conn = getattr(_conn_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
        _conn_local.conn = conn
    return conn


def _close_conn() -> None:
    """关闭当前线程的数据库连接。"""
    conn = getattr(_conn_local, "conn", None)
    if conn:
        try:
            conn.close()
        except Exception:
            pass
        _conn_local.conn = None


# ── 数据库初始化 ─────────────────────────────────────────
def init_db() -> None:
    """创建 SQLite 表及索引（如不存在），并确保 EMA 缓存列存在。"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_quotes (
            code TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            name TEXT,
            PRIMARY KEY (code, date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_code ON daily_quotes(code)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_date ON daily_quotes(date)
    """)
    # 为旧数据库添加 EMA 缓存列（幂等）
    for col in ["ema21", "ema55", "ema120"]:
        try:
            conn.execute(f"ALTER TABLE daily_quotes ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass  # 列已存在，忽略
    conn.commit()

    # 初始化自选股表（不影响原有 daily_quotes 表）
    from watchlist_manager import init_watchlist_table
    init_watchlist_table()


# ── 写入 ─────────────────────────────────────────────────
def save_to_db(df: pd.DataFrame, code: str, name: str) -> None:
    """将单支股票 DataFrame 写入 SQLite（UPSERT），同时计算并缓存 EMA。"""
    if df is None or df.empty:
        return
    df = df.copy()
    # 预计算 EMA 并随原始数据一起持久化
    df = calc_indicators(df)
    df["code"] = code
    df["name"] = name
    conn = _get_conn()
    rows = [
        (
            code,
            str(row["date"])[:10],
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
            str(name),
            float(row["ema21"]) if pd.notna(row["ema21"]) else None,
            float(row["ema55"]) if pd.notna(row["ema55"]) else None,
            float(row["ema120"]) if pd.notna(row["ema120"]) else None,
        )
        for _, row in df.iterrows()
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO daily_quotes
           (code, date, open, high, low, close, volume, name, ema21, ema55, ema120)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


# ── 读取 ─────────────────────────────────────────────────
def load_from_db(code: str, limit: int = DEFAULT_DAYS) -> pd.DataFrame:
    """从 SQLite 加载股票数据，按日期升序返回。"""
    conn = _get_conn()
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume, name, ema21, ema55, ema120 "
        "FROM daily_quotes WHERE code = ? ORDER BY date DESC LIMIT ?",
        conn,
        params=(code, limit),
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def get_cached_date(code: str) -> str | None:
    """返回某股票在缓存中的最新日期，无缓存则返回 None。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT MAX(date) FROM daily_quotes WHERE code = ?", (code,)
    ).fetchone()
    return row[0] if row and row[0] else None


def get_all_cached_codes() -> set:
    """返回所有已缓存股票代码的集合。"""
    conn = _get_conn()
    rows = conn.execute("SELECT DISTINCT code FROM daily_quotes").fetchall()
    return {r[0] for r in rows}


def is_cache_fresh(code: str) -> bool:
    """检查缓存是否在有效期内。"""
    latest = get_cached_date(code)
    if latest is None:
        return False
    latest_dt = datetime.strptime(latest, "%Y-%m-%d")
    return (datetime.now() - latest_dt).days < CACHE_VALIDITY_DAYS


# ── 获取并缓存 ───────────────────────────────────────────
def fetch_stock_data(code: str, days: int = DEFAULT_DAYS,
                     force_refresh: bool = False,
                     source: str = DATA_SOURCE) -> pd.DataFrame | None:
    """
    获取股票数据（优先缓存，增量拉取新数据）。

    增量模式：如果数据库已有历史数据，仅拉取上次缓存日期之后的新交易日，
    大幅减少网络传输量。首次拉取或 force_refresh 时拉取完整历史。

    Parameters
    ----------
    code : str
        股票代码。
    days : int
        获取历史数据天数（仅首次拉取时使用）。
    force_refresh : bool
        是否强制完整刷新。
    source : str
        数据源: "baostock" 或 "akshare"。

    Returns
    -------
    pd.DataFrame or None
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 先查缓存
    if not force_refresh:
        cached = load_from_db(code, limit=days)
        if len(cached) >= MIN_DAYS_KLINE:
            latest_cached_dt = cached["date"].max()
            if latest_cached_dt:
                latest_str = latest_cached_dt.strftime("%Y-%m-%d")
                # 缓存已覆盖今天 → 直接返回
                if (datetime.now() - latest_cached_dt).days < CACHE_VALIDITY_DAYS:
                    return cached.tail(days)

                # 增量拉取：仅获取缓存最新日期之后的数据
                # start_date = 缓存最新日期 + 1天
                inc_start = (latest_cached_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                logger.info(f"增量拉取 {code}: {inc_start} ~ {today_str}")
                df_new = fetch_stock_data_unified(code, inc_start, today_str, source=source)

                if df_new is not None and len(df_new) > 0:
                    # 合并新旧数据
                    old_cols = [c for c in cached.columns if c not in ("ema21", "ema55", "ema120")]
                    cached_clean = cached[old_cols].copy()
                    df_merged = pd.concat([cached_clean, df_new], ignore_index=True)
                    df_merged = df_merged.drop_duplicates(subset=["date"], keep="last")
                    df_merged = df_merged.sort_values("date").reset_index(drop=True)
                    logger.info(f"增量合并 {code}: 原有{len(cached_clean)}天 + 新增{len(df_new)}天 = {len(df_merged)}天")
                    return df_merged.tail(days)
                else:
                    # 无新数据（如周末/节假日），返回缓存
                    return cached.tail(days)

    # 首次拉取或强制刷新：完整历史数据
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    df = fetch_stock_data_unified(code, start_date, today_str, source=source)
    return df


def fetch_and_cache_stock(code: str, name: str, days: int = DEFAULT_DAYS,
                          source: str = DATA_SOURCE) -> pd.DataFrame | None:
    """
    获取股票数据并存入缓存。返回 DataFrame 或 None。
    数据不足 60 天时记录日志但仍保存。
    """
    try:
        df = fetch_stock_data(code, days=days, force_refresh=True, source=source)
        if df is not None and len(df) >= MIN_DAYS_KLINE:
            save_to_db(df, code, name)
            return df
        elif df is not None:
            log_skip(logger, code, name, f"数据不足 (仅{len(df)}天)")
            save_to_db(df, code, name)  # 仍然保存已有数据
            return None
        else:
            return None
    except Exception as e:
        log_error(logger, code, name, e)
        return None


# ── 历史数据补充（保留现有数据，只拉取缺失的早期数据）─────
def get_cached_date_range(code: str) -> tuple[str | None, str | None, int]:
    """
    返回缓存中某股票的日期范围和数据天数。

    Returns
    -------
    tuple[earliest_date, latest_date, count]
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*) FROM daily_quotes WHERE code = ?",
        (code,),
    ).fetchone()
    if row and row[0]:
        return row[0], row[1], row[2]
    return None, None, 0


def find_stocks_needing_backfill(min_days: int = MIN_CACHE_DAYS) -> list[dict]:
    """
    扫描数据库，找出数据天数不足的股票。

    Returns
    -------
    list[dict]: [{code, name, count, earliest, latest}, ...]
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT code, name, COUNT(*) as cnt, MIN(date) as mind, MAX(date) as maxd "
        "FROM daily_quotes GROUP BY code HAVING cnt < ? ORDER BY cnt",
        (min_days,),
    ).fetchall()
    return [
        {"code": r[0], "name": r[1], "count": r[2], "earliest": r[3], "latest": r[4]}
        for r in rows
    ]


def backfill_stock_history(
    code: str,
    name: str = "",
    target_days: int = MIN_CACHE_DAYS,
    lookback_days: int = BACKFILL_LOOKBACK_DAYS,
    source: str = DATA_SOURCE,
) -> tuple[int, int]:
    """
    为单支股票补充缺失的历史数据（保留现有数据，仅拉取更早期数据）。

    工作流程:
    1. 查询缓存中该股票的最早日期和数据天数
    2. 若数据天数 >= target_days，跳过
    3. 计算需要补充的日期范围，从网络拉取更早期数据
    4. 将新旧数据合并后，重新计算 EMA 并全量写回数据库

    Parameters
    ----------
    code : str
        股票代码。
    name : str
        股票名称（已缓存则自动获取）。
    target_days : int
        目标最少交易日数，默认 300。
    lookback_days : int
        往前追溯的最大日历天数，默认 730（约2年）。
    source : str
        数据源。

    Returns
    -------
    tuple[int, int]
        (new_days_fetched, total_days_after)
    """
    # 1. 检查当前缓存状态
    earliest, latest, count = get_cached_date_range(code)
    if count >= target_days:
        logger.info(f"补充历史: {code} 已有 {count} 天数据，无需补充")
        return 0, count

    if earliest is None:
        logger.info(f"补充历史: {code} 无缓存数据，请先执行首次拉取")
        return 0, 0

    # 获取名称
    if not name:
        conn = _get_conn()
        r = conn.execute(
            "SELECT name FROM daily_quotes WHERE code = ? AND name != '' LIMIT 1",
            (code,),
        ).fetchone()
        name = r[0] if r else ""

    # 2. 计算需要补充的日期范围
    # 从缓存最早日期往前追溯 lookback_days
    earliest_dt = datetime.strptime(earliest, "%Y-%m-%d")
    backfill_start = (earliest_dt - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    backfill_end = (earliest_dt - timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(
        f"补充历史: {code} {name} | 当前 {count} 天 ({earliest}~{latest}) | "
        f"目标 {target_days} 天 | 拉取范围 {backfill_start}~{backfill_end}"
    )

    # 3. 拉取更早期的数据
    from data_fetcher import fetch_stock_data_unified

    df_old = fetch_stock_data_unified(code, backfill_start, backfill_end, source=source)
    if df_old is None or df_old.empty:
        logger.info(f"补充历史: {code} 无更早期数据可拉取")
        return 0, count

    # 4. 加载现有缓存数据，合并后重算 EMA
    df_existing = load_from_db(code, limit=99999)
    if df_existing.empty:
        return 0, count

    # 合并：旧数据 + 现有数据，按日期去重
    df_existing = df_existing.drop(columns=["ema21", "ema55", "ema120"], errors="ignore")
    df_merged = pd.concat([df_old, df_existing], ignore_index=True)
    df_merged = df_merged.drop_duplicates(subset=["date"], keep="last")
    df_merged = df_merged.sort_values("date").reset_index(drop=True)

    new_count = len(df_merged)
    days_added = new_count - count

    # 5. 全量写回数据库（重算 EMA）
    save_to_db(df_merged, code, name)

    logger.info(
        f"补充历史完成: {code} {name} | "
        f"新增 {days_added} 天 (旧范围外), 总计 {new_count} 天 "
        f"({df_merged['date'].iloc[0].strftime('%Y-%m-%d')}~{df_merged['date'].iloc[-1].strftime('%Y-%m-%d')})"
    )
    return days_added, new_count


def backfill_all_stocks(
    stock_list: list[tuple[str, str]] | None = None,
    target_days: int = MIN_CACHE_DAYS,
    source: str = DATA_SOURCE,
    progress_callback=None,
) -> dict:
    """
    批量补充所有数据不足的股票。

    Parameters
    ----------
    stock_list : list[tuple[str, str]] or None
        要检查的股票列表。None = 扫描数据库中所有股票。
    target_days : int
        目标最少交易日数。
    source : str
        数据源。
    progress_callback : callable or None
        进度回调 signed callback(done: int, total: int, code: str, msg: str)。

    Returns
    -------
    dict: {total_checked, backfilled, days_added, skipped_ok, skipped_no_data}
    """
    if stock_list is None:
        needing = find_stocks_needing_backfill(target_days)
        stock_list = [(s["code"], s["name"]) for s in needing]
    else:
        # 只处理数据不足的
        needing = find_stocks_needing_backfill(target_days)
        need_codes = {s["code"] for s in needing}
        stock_list = [(c, n) for c, n in stock_list if c in need_codes]

    total = len(stock_list)
    if total == 0:
        logger.info(f"补充历史: 所有股票数据充足 (>= {target_days} 天)，无需补充")
        return {"total_checked": 0, "backfilled": 0, "days_added": 0,
                "skipped_ok": 0, "skipped_no_data": 0}

    result = {"total_checked": total, "backfilled": 0, "days_added": 0,
              "skipped_ok": 0, "skipped_no_data": 0}

    for i, (code, name) in enumerate(stock_list):
        try:
            days_added, total_after = backfill_stock_history(
                code, name, target_days=target_days, source=source
            )
            if days_added > 0:
                result["backfilled"] += 1
                result["days_added"] += days_added
            elif total_after >= target_days:
                result["skipped_ok"] += 1
            else:
                result["skipped_no_data"] += 1
        except Exception as e:
            logger.error(f"补充历史异常 {code}: {e}")

        if progress_callback:
            progress_callback(i + 1, total, code,
                              f"已补充 {result['backfilled']} 支, 新增 {result['days_added']} 天")

    logger.info(
        f"批量补充完成: 检查 {total} 支 | "
        f"已补充 {result['backfilled']} 支 (+{result['days_added']}天) | "
        f"已充足 {result['skipped_ok']} 支 | 无更多数据 {result['skipped_no_data']} 支"
    )
    return result


# ── EMA 缓存回填 ─────────────────────────────────────────
def backfill_ema_cache() -> int:
    """
    对数据库中有 OHLCV 但缺少 EMA 的存量数据批量计算并回填 EMA。
    返回更新的记录数。
    """
    conn = _get_conn()
    # 查找需要回填的股票代码
    rows = conn.execute(
        "SELECT DISTINCT code FROM daily_quotes WHERE ema21 IS NULL"
    ).fetchall()
    codes = [r[0] for r in rows]
    if not codes:
        logger.info("EMA 缓存回填: 无需更新，所有数据均已缓存 EMA")
        return 0

    total_updated = 0
    for code in codes:
        try:
            df = load_from_db(code, limit=99999)  # 全量加载
            if df.empty:
                continue
            df = calc_indicators(df)  # 计算 EMA（此时 DataFrame 无 EMA 列）
            # 批量 UPDATE — 使用 executemany 替代逐行 execute
            update_rows = [
                (
                    float(row["ema21"]) if pd.notna(row["ema21"]) else None,
                    float(row["ema55"]) if pd.notna(row["ema55"]) else None,
                    float(row["ema120"]) if pd.notna(row["ema120"]) else None,
                    code,
                    str(row["date"])[:10],
                )
                for _, row in df.iterrows()
            ]
            conn.executemany(
                "UPDATE daily_quotes SET ema21=?, ema55=?, ema120=? WHERE code=? AND date=?",
                update_rows,
            )
            conn.commit()
            total_updated += len(df)
        except Exception as e:
            logger.error(f"EMA 回填异常 {code}: {e}")

    logger.info(f"EMA 缓存回填完成: {len(codes)} 支股票, {total_updated} 条记录")
    return total_updated


# ── 数据库清理：移除非主板和退市股票 ────────────────────
def cleanup_invalid_stocks(
    dry_run: bool = False,
) -> dict:
    """
    清理数据库中非主板股票和已知退市/ST股票的记录。

    非主板判断：config.is_mainboard() 返回 False。
    已知问题股票：config.KNOWN_BAD_CODES。

    Parameters
    ----------
    dry_run : bool
        True 时仅扫描统计，不执行删除。

    Returns
    -------
    dict: {non_mainboard_codes, non_mainboard_records, bad_codes, bad_records, deleted_total}
    """
    from config import is_mainboard, KNOWN_BAD_CODES

    conn = _get_conn()
    all_codes = conn.execute("SELECT DISTINCT code FROM daily_quotes").fetchall()
    all_codes = [r[0] for r in all_codes]

    # 找出非主板股票
    non_mainboard = [c for c in all_codes if not is_mainboard(c)]
    # 找出已知问题股票
    known_bad = [c for c in all_codes if c in KNOWN_BAD_CODES]

    # 合并去重
    to_delete = list(set(non_mainboard + known_bad))

    # 统计
    non_main_records = 0
    bad_records = 0
    if non_mainboard:
        placeholders = ",".join(["?"] * len(non_mainboard))
        non_main_records = conn.execute(
            f"SELECT COUNT(*) FROM daily_quotes WHERE code IN ({placeholders})",
            non_mainboard,
        ).fetchone()[0]
    if known_bad:
        placeholders = ",".join(["?"] * len(known_bad))
        bad_records = conn.execute(
            f"SELECT COUNT(*) FROM daily_quotes WHERE code IN ({placeholders})",
            known_bad,
        ).fetchone()[0]

    result = {
        "non_mainboard_codes": len(non_mainboard),
        "non_mainboard_records": non_main_records,
        "bad_codes": len(known_bad),
        "bad_records": bad_records,
        "deleted_total": 0,
    }

    if dry_run:
        logger.info(
            f"数据库扫描: 非主板 {len(non_mainboard)} 支 ({non_main_records} 条) | "
            f"问题股票 {len(known_bad)} 支 ({bad_records} 条) | 待清理共 {len(to_delete)} 支"
        )
        return result

    # 执行删除
    if not to_delete:
        logger.info("数据库清理: 无需清理")
        return result

    for batch_start in range(0, len(to_delete), 50):
        batch = to_delete[batch_start:batch_start + 50]
        placeholders = ",".join(["?"] * len(batch))
        conn.execute(
            f"DELETE FROM daily_quotes WHERE code IN ({placeholders})",
            batch,
        )

    conn.commit()

    # 统计实际删除数
    deleted = non_main_records + bad_records
    result["deleted_total"] = deleted

    logger.info(
        f"数据库清理完成: 移除 {len(to_delete)} 支股票 / {deleted} 条记录 | "
        f"非主板 {len(non_mainboard)} 支 + 问题股票 {len(known_bad)} 支"
    )
    return result


def get_mainboard_db_stats() -> dict:
    """
    获取仅限主板股票的数据库统计（用于仪表盘对比展示）。

    Returns
    -------
    dict: {total_stocks, total_records, latest_date, coverage_*, ema_cached_pct}
    """
    from config import is_mainboard, KNOWN_BAD_CODES

    conn = _get_conn()
    all_codes = conn.execute("SELECT DISTINCT code FROM daily_quotes").fetchall()
    all_codes = [r[0] for r in all_codes]

    # 仅保留主板且不在黑名单中的股票
    valid_codes = [
        c for c in all_codes if is_mainboard(c) and c not in KNOWN_BAD_CODES
    ]

    if not valid_codes:
        return {}

    placeholders = ",".join(["?"] * len(valid_codes))
    total_stocks = len(valid_codes)
    total_records = conn.execute(
        f"SELECT COUNT(*) FROM daily_quotes WHERE code IN ({placeholders})",
        valid_codes,
    ).fetchone()[0]
    latest_date = conn.execute(
        f"SELECT MAX(date) FROM daily_quotes WHERE code IN ({placeholders})",
        valid_codes,
    ).fetchone()[0] or "N/A"

    # 覆盖度分布
    coverage = conn.execute(f"""
        SELECT
            COUNT(CASE WHEN cnt >= 300 THEN 1 END),
            COUNT(CASE WHEN cnt >= 120 AND cnt < 300 THEN 1 END),
            COUNT(CASE WHEN cnt >= 60 AND cnt < 120 THEN 1 END),
            COUNT(CASE WHEN cnt < 60 THEN 1 END)
        FROM (
            SELECT code, COUNT(*) as cnt FROM daily_quotes
            WHERE code IN ({placeholders})
            GROUP BY code
        )
    """, valid_codes).fetchone()

    # EMA 缓存率
    ema_total = conn.execute(
        f"SELECT COUNT(*) FROM daily_quotes WHERE code IN ({placeholders})",
        valid_codes,
    ).fetchone()[0]
    ema_cached = conn.execute(
        f"SELECT COUNT(*) FROM daily_quotes WHERE code IN ({placeholders}) AND ema21 IS NOT NULL",
        valid_codes,
    ).fetchone()[0]
    ema_pct = round(ema_cached / ema_total * 100, 1) if ema_total > 0 else 0

    return {
        "total_stocks": total_stocks,
        "total_records": total_records,
        "latest_date": latest_date,
        "coverage_good": coverage[0],
        "coverage_fair": coverage[1],
        "coverage_low": coverage[2],
        "coverage_poor": coverage[3],
        "ema_cached_pct": ema_pct,
    }


# ── 并行拉取数据 ─────────────────────────────────────────
def fetch_and_cache_stocks_parallel(
    stock_list: list[tuple[str, str]],
    max_workers: int = MAX_PARALLEL_FETCHES,
    days: int = DEFAULT_DAYS,
    source: str = DATA_SOURCE,
    progress_callback=None,
) -> tuple[int, int]:
    """
    使用线程池并行拉取并缓存多支股票数据。

    Parameters
    ----------
    stock_list : list[tuple[str, str]]
        [(code, name), ...] 股票列表。
    max_workers : int
        线程池并发数，默认取配置 MAX_PARALLEL_FETCHES。
    days : int
        获取历史数据天数。
    source : str
        数据源: "baostock" 或 "akshare"。
    progress_callback : callable or None
        进度回调，签名为 callback(done: int, total: int, code: str)。

    Returns
    -------
    tuple[int, int]
        (成功数, 总数)
    """
    total = len(stock_list)
    fetched = 0
    completed = 0

    # Baostock 不支持并发连接，强制串行
    if source == "baostock":
        max_workers = 1

    def _fetch_one(code: str, name: str) -> bool:
        """带随机延迟的单股拉取，返回是否成功。"""
        delay = random.uniform(FETCH_RANDOM_DELAY_MIN, FETCH_RANDOM_DELAY_MAX)
        time.sleep(delay)
        try:
            df = fetch_stock_data(code, days=days, force_refresh=True, source=source)
            if df is not None and len(df) >= MIN_DAYS_KLINE:
                save_to_db(df, code, name)
                return True
            elif df is not None:
                log_skip(logger, code, name, f"数据不足 (仅{len(df)}天)")
                save_to_db(df, code, name)
                return False
            return False
        except Exception as e:
            log_error(logger, code, name, e)
            return False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_fetch_one, code, name): (code, name)
            for code, name in stock_list
        }
        for future in as_completed(future_map):
            code, _ = future_map[future]
            completed += 1
            try:
                if future.result():
                    fetched += 1
            except Exception as e:
                logger.error(f"并行拉取异常 {code}: {e}")
            if progress_callback:
                progress_callback(completed, total, code)

    return fetched, total
