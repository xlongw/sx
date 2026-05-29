"""
数据库缓存清理工具
Database cleanup utility — scan, report & fix erroneous data in stock_data.db.

用法:
    # 仅扫描并报告（不修改数据）
    python db_cleanup.py --scan

    # 交互式清理
    python db_cleanup.py

    # 清除指定股票的全部数据
    python db_cleanup.py --remove-codes 000918,600823

    # 清除所有数据（重置数据库）
    python db_cleanup.py --reset

    # 非交互模式（配合 --fix-* 使用）
    python db_cleanup.py --fix-all --yes
"""

import sqlite3
import argparse
import sys
import os
from datetime import datetime

# 确保从 sx 目录运行也能找到数据库
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_data.db")


# ── 扫描函数 ──────────────────────────────────────────────
def scan_database(db_path: str = DB_PATH) -> dict:
    """
    全面扫描数据库，返回各类数据质量问题。

    Returns
    -------
    dict: 各类问题的统计和详情
    """
    issues = {}

    if not os.path.exists(db_path):
        print(f"❌ 数据库文件不存在: {db_path}")
        return issues

    conn = sqlite3.connect(db_path)

    # ─ 1. 基本统计 ─
    total_rows = conn.execute("SELECT COUNT(*) FROM daily_quotes").fetchone()[0]
    total_codes = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_quotes").fetchone()[0]
    issues["_meta"] = {
        "total_rows": total_rows,
        "total_codes": total_codes,
        "db_size_mb": os.path.getsize(db_path) / (1024 * 1024),
    }

    # ─ 2. 价格异常 (≤0) ─
    for col in ["open", "high", "low", "close"]:
        rows = conn.execute(
            f"SELECT COUNT(*) FROM daily_quotes WHERE {col} IS NULL OR {col} <= 0"
        ).fetchone()[0]
        if rows:
            issues[f"price_{col}_invalid"] = rows

    # ─ 3. high < low ─
    cnt = conn.execute(
        "SELECT COUNT(*) FROM daily_quotes WHERE high < low"
    ).fetchone()[0]
    if cnt:
        issues["high_less_than_low"] = cnt

    # ─ 4. 成交量异常 (0 或 NULL) ─
    cnt = conn.execute(
        "SELECT COUNT(*) FROM daily_quotes WHERE volume IS NULL OR volume <= 0"
    ).fetchone()[0]
    if cnt:
        issues["volume_invalid"] = cnt

    # ─ 5. 重复记录 ─
    dups = conn.execute(
        "SELECT code, date, COUNT(*) as cnt FROM daily_quotes "
        "GROUP BY code, date HAVING cnt > 1"
    ).fetchall()
    if dups:
        issues["duplicates"] = {
            "count": len(dups),
            "total_dup_rows": sum(r[2] - 1 for r in dups),
            "samples": dups[:10],
        }

    # ─ 6. 空名称 ─
    cnt = conn.execute(
        "SELECT COUNT(*) FROM daily_quotes WHERE name IS NULL OR name = ''"
    ).fetchone()[0]
    if cnt:
        issues["empty_name"] = cnt

    # ─ 7. 日期异常 ─
    cnt = conn.execute(
        "SELECT COUNT(*) FROM daily_quotes "
        "WHERE date > date('now', '+30 days') OR date < '2000-01-01'"
    ).fetchone()[0]
    if cnt:
        issues["date_anomaly"] = cnt

    # ─ 8. 数据不足的股票 (< 60天) ─
    thin = conn.execute(
        "SELECT code, name, COUNT(*) as cnt, MIN(date) as mind, MAX(date) as maxd "
        "FROM daily_quotes GROUP BY code HAVING cnt < 60 ORDER BY cnt"
    ).fetchall()
    if thin:
        issues["thin_data_stocks"] = thin

    # ─ 9. 无最新数据的股票 (> 30天未更新) ─
    stale = conn.execute(
        "SELECT code, name, MAX(date) as last_date FROM daily_quotes "
        "GROUP BY code HAVING last_date < date('now', '-30 days') ORDER BY last_date"
    ).fetchall()
    if stale:
        issues["stale_stocks"] = stale[:20]  # 最多显示 20 支

    # ─ 10. EMA 缺失 ─
    cnt = conn.execute(
        "SELECT COUNT(*) FROM daily_quotes WHERE ema21 IS NULL"
    ).fetchone()[0]
    if cnt:
        issues["ema_missing"] = cnt

    conn.close()
    return issues


def print_scan_report(issues: dict) -> None:
    """格式化打印扫描报告。"""
    if not issues:
        print("✅ 未发现问题。")
        return

    meta = issues.pop("_meta", {})
    print("=" * 60)
    print("  数据库清理工具 — 扫描报告")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据库: {DB_PATH}")
    print(f"  总记录数: {meta.get('total_rows', '?'):,}")
    print(f"  股票数: {meta.get('total_codes', '?')}")
    print(f"  文件大小: {meta.get('db_size_mb', 0):.1f} MB")
    print("=" * 60)

    if not issues:
        print("\n✅ 数据库状态良好，未发现异常数据。")
        return

    issue_labels = {
        "price_open_invalid":  "开盘价 ≤ 0 或 NULL",
        "price_high_invalid":  "最高价 ≤ 0 或 NULL",
        "price_low_invalid":   "最低价 ≤ 0 或 NULL",
        "price_close_invalid": "收盘价 ≤ 0 或 NULL",
        "high_less_than_low":  "最高价 < 最低价 (逻辑错误)",
        "volume_invalid":      "成交量 = 0 或 NULL",
        "empty_name":          "股票名称为空",
        "date_anomaly":        "日期异常（未来或过老）",
        "ema_missing":         "EMA 缓存缺失",
    }

    for key, label in issue_labels.items():
        if key in issues:
            print(f"  ⚠️  {label}: {issues[key]:,} 条")

    if "duplicates" in issues:
        d = issues["duplicates"]
        print(f"  ⚠️  重复记录: {d['count']} 组 (冗余 {d['total_dup_rows']} 条)")

    if "thin_data_stocks" in issues:
        thin = issues["thin_data_stocks"]
        print(f"\n  📉 数据不足60天的股票 ({len(thin)} 支):")
        for t in thin[:20]:
            print(f"     {t[0]} {t[1] or '(无名称)'}: {t[2]}条 ({t[3]}~{t[4]})")
        if len(thin) > 20:
            print(f"     ... 还有 {len(thin) - 20} 支")

    if "stale_stocks" in issues:
        stale = issues["stale_stocks"]
        print(f"\n  🕐 超过30天未更新的股票 ({len(stale)} 支):")
        for s in stale[:10]:
            print(f"     {s[0]} {s[1] or '(无名称)'}: 最后更新 {s[2]}")
        if len(stale) > 10:
            print(f"     ... 还有 {len(stale) - 10} 支")


# ── 修复函数 ──────────────────────────────────────────────
def fix_volume_invalid(conn: sqlite3.Connection) -> int:
    """删除成交量为 0 或 NULL 的记录。"""
    cur = conn.execute(
        "DELETE FROM daily_quotes WHERE volume IS NULL OR volume <= 0"
    )
    return cur.rowcount


def fix_price_invalid(conn: sqlite3.Connection) -> int:
    """删除价格异常的记录。"""
    cur = conn.execute(
        "DELETE FROM daily_quotes WHERE "
        "open IS NULL OR open <= 0 OR "
        "high IS NULL OR high <= 0 OR "
        "low IS NULL OR low <= 0 OR "
        "close IS NULL OR close <= 0"
    )
    return cur.rowcount


def fix_high_less_than_low(conn: sqlite3.Connection) -> int:
    """删除 high < low 的记录。"""
    cur = conn.execute("DELETE FROM daily_quotes WHERE high < low")
    return cur.rowcount


def fix_duplicates(conn: sqlite3.Connection) -> int:
    """删除重复的 (code, date) 记录，每组只保留 ROWID 最小的一条。"""
    cur = conn.execute(
        "DELETE FROM daily_quotes WHERE ROWID NOT IN ("
        "  SELECT MIN(ROWID) FROM daily_quotes GROUP BY code, date"
        ")"
    )
    return cur.rowcount


def fix_empty_name(conn: sqlite3.Connection) -> int:
    """删除名称为空的记录。"""
    cur = conn.execute(
        "DELETE FROM daily_quotes WHERE name IS NULL OR name = ''"
    )
    return cur.rowcount


def fix_date_anomaly(conn: sqlite3.Connection) -> int:
    """删除日期异常（未来或过老）的记录。"""
    cur = conn.execute(
        "DELETE FROM daily_quotes WHERE "
        "date > date('now', '+30 days') OR date < '2000-01-01'"
    )
    return cur.rowcount


def remove_stock_data(conn: sqlite3.Connection, codes: list[str]) -> int:
    """删除指定股票代码的所有数据。"""
    placeholders = ",".join("?" for _ in codes)
    cur = conn.execute(
        f"DELETE FROM daily_quotes WHERE code IN ({placeholders})", codes
    )
    return cur.rowcount


def remove_thin_data_stocks(conn: sqlite3.Connection, min_days: int = 60) -> tuple[int, list]:
    """删除数据不足指定天数的股票数据。"""
    thin = conn.execute(
        "SELECT code FROM daily_quotes GROUP BY code HAVING COUNT(*) < ?",
        (min_days,),
    ).fetchall()
    codes = [t[0] for t in thin]
    if codes:
        placeholders = ",".join("?" for _ in codes)
        cur = conn.execute(
            f"DELETE FROM daily_quotes WHERE code IN ({placeholders})", codes
        )
        return cur.rowcount, codes
    return 0, []


def vacuum_database(conn: sqlite3.Connection) -> None:
    """清理后压缩数据库文件。"""
    conn.execute("VACUUM")


# ── 交互式菜单 ────────────────────────────────────────────
def interactive_mode() -> None:
    """交互式数据库清理。"""
    print("=" * 60)
    print("  数据库清理工具 — 交互模式")
    print("=" * 60)

    # 先扫描
    issues = scan_database()
    if not issues:
        print("\n✅ 数据库状态良好，无需清理。")
        return

    print_scan_report(issues)

    print("\n" + "-" * 60)
    print("请选择要执行的清理操作:")
    print("-" * 60)
    print("  [1] 删除成交量为0的记录" + (f" ({issues.get('volume_invalid', 0)}条)" if "volume_invalid" in issues else ""))
    print("  [2] 删除价格异常的记录")
    print("  [3] 删除 high<low 的记录")
    print("  [4] 删除重复记录")
    print("  [5] 删除名称为空的记录")
    print("  [6] 删除数据不足60天的股票")
    print("  [7] 删除指定股票代码的数据")
    print("  [8] 一键修复所有问题")
    print("  [9] 压缩数据库 (VACUUM)")
    print("  [B] 补充历史数据 (目标300天)")
    print("  [0] 退出")
    print("-" * 60)

    try:
        choice = input("输入选项编号 (多个用逗号分隔, 如 1,4): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        return

    if not choice or choice == "0":
        print("已退出。")
        return

    choices = [c.strip() for c in choice.replace("，", ",").split(",")]

    conn = sqlite3.connect(DB_PATH)
    total_deleted = 0

    for c in choices:
        try:
            if c == "1":
                n = fix_volume_invalid(conn)
                print(f"  ✓ 删除成交量异常记录: {n} 条")
                total_deleted += n
            elif c == "2":
                n = fix_price_invalid(conn)
                print(f"  ✓ 删除价格异常记录: {n} 条")
                total_deleted += n
            elif c == "3":
                n = fix_high_less_than_low(conn)
                print(f"  ✓ 删除 high<low 记录: {n} 条")
                total_deleted += n
            elif c == "4":
                n = fix_duplicates(conn)
                print(f"  ✓ 删除重复记录: {n} 条")
                total_deleted += n
            elif c == "5":
                n = fix_empty_name(conn)
                print(f"  ✓ 删除空名称记录: {n} 条")
                total_deleted += n
            elif c == "6":
                n, codes = remove_thin_data_stocks(conn)
                print(f"  ✓ 删除数据不足的股票 ({len(codes)} 支): {n} 条")
                if codes:
                    print(f"    代码: {', '.join(codes[:20])}")
                total_deleted += n
            elif c == "7":
                code_input = input("  输入要删除的股票代码 (多个用逗号分隔): ").strip()
                if code_input:
                    codes = [x.strip() for x in code_input.replace("，", ",").split(",") if x.strip()]
                    n = remove_stock_data(conn, codes)
                    print(f"  ✓ 删除指定股票数据: {n} 条")
                    total_deleted += n
            elif c == "8":
                n = fix_volume_invalid(conn)
                n += fix_price_invalid(conn)
                n += fix_high_less_than_low(conn)
                n += fix_duplicates(conn)
                n += fix_empty_name(conn)
                n += fix_date_anomaly(conn)
                print(f"  ✓ 一键修复: 共删除 {n} 条异常记录")
                total_deleted += n
            elif c == "9":
                vacuum_database(conn)
                print("  ✓ 数据库已压缩")
            elif c.upper() == "B":
                print("\n  开始补充历史数据...")
                conn_backup = conn
                from data_manager import find_stocks_needing_backfill, backfill_all_stocks
                needing = find_stocks_needing_backfill(min_days=300)
                if needing:
                    print(f"  发现 {len(needing)} 支股票数据不足300天")
                    for s in needing[:10]:
                        print(f"    {s['code']} {s['name']}: {s['count']}天 ({s['earliest']}~{s['latest']})")
                    if len(needing) > 10:
                        print(f"    ... 还有 {len(needing)-10} 支")
                    confirm = input("\n  确认补充? [y/N]: ").strip().lower()
                    if confirm in ("y", "yes"):
                        result = backfill_all_stocks(
                            [(s["code"], s["name"]) for s in needing],
                            target_days=300,
                        )
                        print(f"  ✓ 补充完成: 处理 {result['total_checked']} 支, "
                              f"已补充 {result['backfilled']} 支, "
                              f"新增 {result['days_added']} 天")
                else:
                    print("  ✓ 所有股票数据充足 (>=300天)")
            else:
                print(f"  ⚠️ 忽略未知选项: {c}")
        except Exception as e:
            print(f"  ❌ 操作失败 [{c}]: {e}")

    conn.commit()
    conn.close()

    if total_deleted > 0:
        print(f"\n✅ 清理完成，共删除 {total_deleted} 条记录。")
        # 自动压缩
        conn2 = sqlite3.connect(DB_PATH)
        vacuum_database(conn2)
        conn2.close()
        print("  数据库已自动压缩。")
    else:
        print("\n✅ 未删除任何记录。")


# ── 非交互式 ──────────────────────────────────────────────
def non_interactive_fix(fix_all: bool = False, remove_codes: str = "",
                         reset: bool = False) -> None:
    """非交互式清理。"""
    if reset:
        _reset_database()
        return

    issues = scan_database()
    print_scan_report(issues)

    if not fix_all and not remove_codes:
        print("\n提示: 使用 --fix-all 自动修复所有问题，或 --remove-codes 删除指定股票。")
        return

    conn = sqlite3.connect(DB_PATH)
    total_deleted = 0

    if fix_all:
        total_deleted += fix_volume_invalid(conn)
        total_deleted += fix_price_invalid(conn)
        total_deleted += fix_high_less_than_low(conn)
        total_deleted += fix_duplicates(conn)
        total_deleted += fix_empty_name(conn)
        total_deleted += fix_date_anomaly(conn)

    if remove_codes:
        codes = [c.strip() for c in remove_codes.split(",") if c.strip()]
        total_deleted += remove_stock_data(conn, codes)

    conn.commit()

    if total_deleted > 0:
        vacuum_database(conn)
    conn.close()

    print(f"\n✅ 清理完成，共删除 {total_deleted} 条记录。")


def _reset_database() -> None:
    """重置数据库（删除所有数据，保留表结构）。"""
    if not os.path.exists(DB_PATH):
        print("数据库文件不存在，无需重置。")
        return

    conn = sqlite3.connect(DB_PATH)
    rowcount = conn.execute("DELETE FROM daily_quotes").rowcount
    conn.commit()
    vacuum_database(conn)
    conn.close()

    print(f"✅ 数据库已重置，删除了 {rowcount} 条记录。")


# ── 命令行入口 ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="数据库缓存清理工具 — 扫描并修复 stock_data.db 中的错误数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python db_cleanup.py                    交互式清理
  python db_cleanup.py --scan             仅扫描（不修改）
  python db_cleanup.py --fix-all --yes    自动修复所有问题
  python db_cleanup.py --remove-codes 000918,600823  删除指定股票数据
  python db_cleanup.py --reset --yes      重置整个数据库
        """,
    )
    parser.add_argument("--scan", action="store_true", help="仅扫描数据库并报告问题")
    parser.add_argument("--fix-all", action="store_true", help="自动修复所有发现的问题")
    parser.add_argument("--remove-codes", type=str, default="", help="删除指定股票代码（逗号分隔）")
    parser.add_argument("--backfill", action="store_true", help="补充历史数据（目标300天）")
    parser.add_argument("--reset", action="store_true", help="重置数据库（删除所有数据）")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认提示")
    args = parser.parse_args()

    # 仅扫描
    if args.scan:
        issues = scan_database()
        print_scan_report(issues)
        return

    # 重置数据库
    if args.reset:
        if not args.yes:
            confirm = input("⚠️ 确认要删除所有数据吗？此操作不可逆！[y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print("已取消。")
                return
        _reset_database()
        return

    # 补充历史数据
    if args.backfill:
        from data_manager import find_stocks_needing_backfill, backfill_all_stocks
        needing = find_stocks_needing_backfill(min_days=300)
        if not needing:
            print("✅ 所有股票数据充足 (>=300天)")
        else:
            print(f"发现 {len(needing)} 支股票数据不足300天:")
            for s in needing[:15]:
                print(f"  {s['code']} {s['name']}: {s['count']}天 ({s['earliest']}~{s['latest']})")
            if len(needing) > 15:
                print(f"  ... 还有 {len(needing) - 15} 支")
            if not args.yes:
                confirm = input(f"\n确认补充这 {len(needing)} 支股票? [y/N]: ").strip().lower()
                if confirm not in ("y", "yes"):
                    print("已取消。")
                    return
            result = backfill_all_stocks(
                [(s["code"], s["name"]) for s in needing],
                target_days=300,
            )
            print(f"\n✅ 补充完成: 处理 {result['total_checked']} 支, "
                  f"已补充 {result['backfilled']} 支, 新增 {result['days_added']} 天")
        return

    # 非交互式修复
    if args.fix_all or args.remove_codes:
        if not args.yes:
            print("即将在非交互模式下执行清理...")
            confirm = input("确认继续？[y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print("已取消。")
                return
        non_interactive_fix(
            fix_all=args.fix_all,
            remove_codes=args.remove_codes,
        )
        return

    # 默认：交互式模式
    interactive_mode()


if __name__ == "__main__":
    main()
