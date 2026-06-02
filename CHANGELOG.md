# 更新日志 (CHANGELOG)

本文件记录 EMA均线形态股票筛选工具 的所有重要变更。
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

---

## [2.1.0] — 2026-06-02

### 新增
- **条件A偏离度约束** — `check_condition_a()` 新增 `max_deviation` 参数（默认15%），防止选出已大幅远离均线的股票。UI 增加偏离度上限滑块。
- **条件D放量确认** — 新增 `check_condition_d()`，判断今日成交量是否超过20日均量指定倍数（默认1.5x）。UI 增加复选框和放量倍数滑块。
- **数据质量仪表盘** — 主页面顶部新增数据概览面板：股票总数、总记录数、最新日期、EMA缓存率、数据充足率、覆盖度分布。
- **筛选失败汇总** — 筛选完成后展示4列统计（已筛选/触发信号/跳过/失败）+ 可展开失败详情列表。
- **股票池过滤系统** — `config.py` 新增 `is_mainboard()`、`filter_stock_list()`、`get_default_stocks()`、`KNOWN_BAD_CODES`，自动过滤非主板和退市股票。
- **增量数据拉取** — `fetch_stock_data()` 改为增量模式，仅拉取缓存最新日期之后的新交易日数据。

### 变更
- **撤销并行筛选** — 将 `ThreadPoolExecutor` 并行改回串行循环，实测证明串行更快（4.7ms/支 vs 线程开销10ms+）。
- **EMA回填批量化** — `backfill_ema_cache()` 从逐行 `UPDATE` 改为 `executemany()` 批量更新。
- **数据库连接复用** — 模块级 `threading.local()` 连接池 + WAL 模式 + 8MB 缓存，所有读写共用连接。

### 修复
- **日志重复写入 Bug** — `utils.py` `log_skip()` 删除了多余的手动 `open().write()`（logger 本身已写入文件）。
- **股票池混杂问题** — 从默认股票池移除 799 支非主板股票（300/301/688 创业板/科创板）和退市股票。
- **EMA回填重复执行** — 使用 `st.session_state` 标记，每次会话仅执行一次。

### 数据
- 股票池：1,444 → 645 支（仅沪深主板）
- 筛选耗时：6.8s → 3.5s（645支）
- 信号命中率：22.6% → 17.5%
- 平均偏离度：14.8% → 4.8%
- >15%偏离信号：172支 → 0支

---

## [2.0.0] — 2026-05-29

### 新增
- Streamlit Web 界面（app.py）
- EMA21/55/120 三条均线技术指标计算（indicator.py）
- 三个筛选条件：A-首次站上三线、B-均线粘合、C-低波动
- AND/OR 逻辑组合筛选模式
- SQLite 数据库缓存（stock_data.db）
- Baostock + AkShare 双数据源支持
- 自选股管理（watchlist_manager.py）
- Plotly 交互式 K 线图（kline_plotter.py）
- 历史数据补充回填功能（db_cleanup.py）
- 1444 支沪深股票默认列表（config.py）

---

## 版本号规则

- **主版本号**：重大架构变更或不兼容的 API 修改
- **次版本号**：新增功能、性能优化
- **修订版本号**：Bug 修复、小改进

格式：`[主.次.修订] — YYYY-MM-DD`

---

- **[新增]** 2026-06-02 — 创建了 CHANGELOG.md 和 changelog.sh 更新日志系统
*最后更新: 2026-06-02*
