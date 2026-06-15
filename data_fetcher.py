"""
数据拉取模块 — 通过 Baostock 获取 A 股日线数据。
Data fetcher via Baostock (free, no registration needed).
"""

import time
import random
import threading
import pandas as pd
import baostock as bs
from datetime import datetime

from utils import setup_logger

logger = setup_logger(__name__)

# 全局登录状态标记
_bs_logged_in = False
_bs_lock = threading.RLock()  # 可重入锁：retry 路径中 _reconnect_if_needed 会再次调用 bs_login

# ── 重试配置 ──────────────────────────────────────────────
MAX_RETRIES = 3              # 最大重试次数
RETRY_BASE_DELAY = 1.0       # 重试基础延迟（秒），指数增长: 1→2→4
SOCKET_TIMEOUT = 30          # 网络请求超时（秒），防止 Baostock 挂起


def bs_login() -> bool:
    """Baostock 登录（幂等，已登录则跳过，线程安全）。"""
    global _bs_logged_in
    if _bs_logged_in:
        return True
    with _bs_lock:
        if _bs_logged_in:  # 双重检查
            return True
        try:
            import socket as _socket
            _old_timeout = _socket.getdefaulttimeout()
            _socket.setdefaulttimeout(SOCKET_TIMEOUT)
            try:
                lg = bs.login()
            finally:
                _socket.setdefaulttimeout(_old_timeout)
            if lg.error_code == "0":
                _bs_logged_in = True
                logger.info("Baostock 登录成功")
                return True
            else:
                logger.error(f"Baostock 登录失败: {lg.error_msg}")
                return False
        except Exception as e:
            logger.error(f"Baostock 登录异常: {e}")
            return False


def bs_logout() -> None:
    """Baostock 登出。"""
    global _bs_logged_in
    if _bs_logged_in:
        try:
            bs.logout()
        except Exception:
            pass
        _bs_logged_in = False


def _to_bs_code(code: str) -> str:
    """将纯数字代码转为 Baostock 格式（sh.600xxx 或 sz.000xxx）。"""
    c = str(code).strip()
    if c.startswith("6"):
        return f"sh.{c}"
    else:
        return f"sz.{c}"


def fetch_stock_data_baostock(code: str, start_date: str, end_date: str):
    """
    通过 Baostock 获取单支股票的日线数据（带自动重试）。

    Parameters
    ----------
    code : str
        股票代码（纯数字，如 '600030'）。
    start_date : str
        起始日期 YYYY-MM-DD。
    end_date : str
        结束日期 YYYY-MM-DD。

    Returns
    -------
    pd.DataFrame or None
        包含 date, open, high, low, close, volume 列的 DataFrame。
    """
    if not bs_login():
        return None

    bs_code = _to_bs_code(code)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # 设置网络超时，防止 Baostock 请求永久挂起
            import socket as _socket
            _old_timeout = _socket.getdefaulttimeout()
            _socket.setdefaulttimeout(SOCKET_TIMEOUT)
            try:
                # Baostock 不支持并发，查询 + 数据检索全程串行化
                with _bs_lock:
                    rs = bs.query_history_k_data_plus(
                        bs_code,
                        "date,open,high,low,close,volume",
                        start_date=start_date,
                        end_date=end_date,
                        frequency="d",
                        adjustflag="2",  # 前复权
                    )

                    if rs is None or rs.error_code != "0":
                        err_msg = rs.error_msg if rs else "rs is None"
                        # 网络错误类 → 重试；业务错误类 → 直接返回
                        if _is_network_error(err_msg):
                            logger.warning(
                                f"Baostock 网络错误 {code} (第{attempt}次): {err_msg}"
                            )
                            if attempt < MAX_RETRIES:
                                _retry_delay(attempt, code)
                                _reconnect_if_needed()
                                continue
                        logger.warning(f"Baostock 查询失败 {code}: {err_msg}")
                        return None

                    data_list = []
                    while rs.next():
                        data_list.append(rs.get_row_data())
            finally:
                _socket.setdefaulttimeout(_old_timeout)

            # 锁外处理数据（pandas 操作不需要锁）
            if not data_list:
                return None

            df = pd.DataFrame(
                data_list, columns=["date", "open", "high", "low", "close", "volume"]
            )
            df["date"] = pd.to_datetime(df["date"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            return df[["date", "open", "high", "low", "close", "volume"]]

        except Exception as e:
            err_str = str(e).lower()
            logger.warning(f"Baostock 获取异常 {code} (第{attempt}次): {e}")
            if attempt < MAX_RETRIES and _is_retryable(err_str):
                _retry_delay(attempt, code)
                _reconnect_if_needed()
                continue
            logger.error(f"Baostock 获取失败 {code} (已重试{MAX_RETRIES}次): {e}")
            return None

    return None


def _is_network_error(msg: str) -> bool:
    """判断是否为可重试的网络错误。"""
    keywords = [
        "error -5", "decompressing", "incomplete", "truncated",
        "网络接收错误", "接收数据异常", "请稍后再试",
        "timeout", "connection", "reset", "broken pipe",
        "用户未登录", "未登录", "登录",  # Baostock 会话过期
    ]
    msg_lower = msg.lower()
    return any(kw in msg_lower for kw in keywords)


def _is_retryable(err_str: str) -> bool:
    """判断异常是否可重试。"""
    keywords = [
        "timeout", "connection", "reset", "broken pipe",
        "incomplete", "truncated", "decompressing",
    ]
    return any(kw in err_str for kw in keywords)


def _retry_delay(attempt: int, code: str, source: str = "baostock") -> None:
    """指数退避延迟，带随机抖动防止重试风暴。"""
    base = RETRY_BASE_DELAY * (2 ** (attempt - 1))
    if source == "akshare":
        base *= 2  # AkShare 对高频更敏感，加重延迟
    jitter = random.uniform(0, base * 0.5)  # ±50% 随机抖动
    delay = base + jitter
    logger.info(f"等待 {delay:.1f}s 后重试 {code}...")
    time.sleep(delay)


def _reconnect_if_needed() -> None:
    """断线/会话过期后强制重连 Baostock（线程安全）。"""
    global _bs_logged_in
    try:
        bs.logout()
    except Exception:
        pass
    _bs_logged_in = False  # 强制标记未登录，触发全新登录
    time.sleep(0.5)        # 短暂等待释放连接
    bs_login()


# ── AkShare 数据源 ────────────────────────────────────────
def fetch_stock_data_akshare(code: str, start_date: str, end_date: str):
    """
    通过 AkShare 获取单支股票的日线数据（前复权）。

    Parameters
    ----------
    code : str
        股票代码（纯数字，如 '600030'）。
    start_date : str
        起始日期 YYYYMMDD（AkShare 格式）。
    end_date : str
        结束日期 YYYYMMDD（AkShare 格式）。

    Returns
    -------
    pd.DataFrame or None
        包含 date, open, high, low, close, volume 列的 DataFrame。
    """
    import akshare as ak
    import os as _os
    import urllib.request as _urllib_req

    for attempt in range(1, MAX_RETRIES + 1):
        # 临时绕过系统代理（akshare 底层用 requests，它通过 urllib 获取系统代理配置）
        # 环境变量 + Windows 注册表代理都会被清除
        _old_proxies = {}
        for _key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                      "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
            if _key in _os.environ:
                _old_proxies[_key] = _os.environ.pop(_key)
        _os.environ["NO_PROXY"] = "*"  # 强制绕过所有代理

        # 同时禁用 urllib 级别的系统代理检测
        _original_getproxies = _urllib_req.getproxies
        _urllib_req.getproxies = lambda: {}

        try:
            # 设置网络超时 + 代理清理
            import socket as _socket
            _old_timeout = _socket.getdefaulttimeout()
            _socket.setdefaulttimeout(SOCKET_TIMEOUT)
            try:
                # AkShare 的日期格式为 YYYYMMDD
                start_fmt = start_date.replace("-", "")
                end_fmt = end_date.replace("-", "")

                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_fmt,
                    end_date=end_fmt,
                    adjust="qfq",  # 前复权
                )

                if df is None or df.empty:
                    logger.warning(f"AkShare 查询为空 {code}")
                    return None

                # 统一列名：中文 → 英文
                col_map = {
                    "日期": "date",
                    "开盘": "open",
                    "最高": "high",
                    "最低": "low",
                    "收盘": "close",
                    "成交量": "volume",
                }
                df = df.rename(columns=col_map)
                df["date"] = pd.to_datetime(df["date"])
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                return df[["date", "open", "high", "low", "close", "volume"]]

            finally:
                _socket.setdefaulttimeout(_old_timeout)

        except Exception as e:
            err_str = str(e).lower()
            logger.warning(f"AkShare 获取异常 {code} (第{attempt}次): {e}")
            if attempt < MAX_RETRIES and _is_retryable(err_str):
                _retry_delay(attempt, code, source="akshare")
                continue
            logger.error(f"AkShare 获取失败 {code} (已重试{MAX_RETRIES}次): {e}")
            return None
        finally:
            # 恢复代理设置
            _urllib_req.getproxies = _original_getproxies
            for _k, _v in _old_proxies.items():
                _os.environ[_k] = _v
            # 清理临时设置的 NO_PROXY（如果原本不存在）
            if "NO_PROXY" not in _old_proxies:
                _os.environ.pop("NO_PROXY", None)

    return None


# ── 统一调度 ──────────────────────────────────────────────
def fetch_stock_data_unified(
    code: str, start_date: str, end_date: str, source: str = "baostock"
):
    """
    根据指定数据源获取单支股票的日线数据。

    Parameters
    ----------
    code : str
        股票代码（纯数字）。
    start_date : str
        起始日期 YYYY-MM-DD。
    end_date : str
        结束日期 YYYY-MM-DD。
    source : str
        数据源: "baostock" 或 "akshare"。

    Returns
    -------
    pd.DataFrame or None
    """
    if source == "akshare":
        return fetch_stock_data_akshare(code, start_date, end_date)
    else:
        return fetch_stock_data_baostock(code, start_date, end_date)
