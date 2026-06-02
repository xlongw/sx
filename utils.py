"""
日志、进度条、异常处理工具
Logging, progress bar, and exception handling utilities.
"""

import logging

from config import LOG_PATH


def setup_logger(name: str = __name__) -> logging.Logger:
    """配置并返回 logger 实例，同时输出到文件和 stderr。"""
    logger = logging.getLogger(name)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # 文件 handler
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

    # 控制台 handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


def log_skip(logger: logging.Logger, code: str, name: str, reason: str) -> None:
    """记录跳过的股票及原因到日志文件。"""
    msg = f"跳过 {code} {name}: {reason}"
    logger.warning(msg)


def log_error(logger: logging.Logger, code: str, name: str, error: Exception) -> None:
    """记录异常到日志。"""
    msg = f"异常 {code} {name}: {error}"
    logger.error(msg)


def format_code(code: str) -> str:
    """清理股票代码，去除 .SZ/.SH/.ss/.sz 后缀。"""
    for suffix in (".SZ", ".SH", ".ss", ".sz"):
        code = code.replace(suffix, "")
    return code.strip()


def get_stock_name(code: str) -> str:
    """
    根据股票代码获取名称。

    优先从数据库已有数据中查找，找不到则从默认股票列表中匹配，
    最终降级返回代码本身。

    Parameters
    ----------
    code : str
        股票代码（纯数字）。

    Returns
    -------
    str
        股票名称。
    """
    clean = format_code(code)
    # 方式1：从 daily_quotes 表中查找
    try:
        import sqlite3
        from config import DB_PATH
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT name FROM daily_quotes WHERE code = ? AND name != '' LIMIT 1",
                (clean,),
            ).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass

    # 方式2：从默认股票列表匹配
    from config import DEFAULT_STOCKS
    for stock_code, stock_name in DEFAULT_STOCKS:
        if stock_code == clean:
            return stock_name

    # 方式3：返回代码本身作为降级
    return clean
