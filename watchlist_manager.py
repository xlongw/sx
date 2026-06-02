"""
自选股数据库操作模块
Watchlist database operations — add, remove, query user's favorite stocks.
"""

import pandas as pd
from datetime import datetime

from data_manager import _get_conn


def init_watchlist_table() -> None:
    """创建自选股表（如果不存在）。"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            code TEXT PRIMARY KEY,
            name TEXT,
            added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def add_to_watchlist(code: str, name: str) -> None:
    """添加或更新自选股。"""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO watchlist (code, name, added_time) VALUES (?, ?, ?)",
        (code, name, datetime.now()),
    )
    conn.commit()


def remove_from_watchlist(code: str) -> None:
    """从自选股中删除。"""
    conn = _get_conn()
    conn.execute("DELETE FROM watchlist WHERE code = ?", (code,))
    conn.commit()


def get_watchlist() -> pd.DataFrame:
    """返回自选股列表 DataFrame（按添加时间倒序）。"""
    conn = _get_conn()
    df = pd.read_sql(
        "SELECT code, name, added_time FROM watchlist ORDER BY added_time DESC",
        conn,
    )
    return df


def is_in_watchlist(code: str) -> bool:
    """检查股票是否已在自选中。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM watchlist WHERE code = ?", (code,)
    ).fetchone()
    return row is not None


def batch_add_to_watchlist(codes: list[str], names: list[str]) -> int:
    """批量添加自选股，返回新增数量。"""
    conn = _get_conn()
    rows = [(code, name, datetime.now()) for code, name in zip(codes, names)]
    conn.executemany(
        "INSERT OR REPLACE INTO watchlist (code, name, added_time) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)
