# app_full.py - 沪深主板全市场版本
import streamlit as st
import pandas as pd
import numpy as np
import baostock as bs
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import time
import random

st.set_page_config(page_title="EMA均线形态筛选工具 - 沪深主板全市场", layout="wide")

# ============ 获取所有沪深主板股票 ============
@st.cache_data(ttl=86400)  # 缓存24小时
def get_all_mainboard_stocks():
    """
    获取沪深主板所有股票列表
    包括：上海主板(60开头)、深圳主板(00开头)
    不包括：科创板(688)、创业板(30)、北交所(8)
    """
    stocks = []
    
    try:
        bs.login()
        
        # 获取所有股票
        rs = bs.query_all_stock(datetime.now().strftime('%Y-%m-%d'))
        
        while rs.next():
            row = rs.get_row_data()
            code = row[0]  # 如 'sh.600000' 或 'sz.000001'
            name = row[1]
            
            # 提取代码数字部分
            if code.startswith('sh.'):
                num_code = code[3:]
                # 上海主板：60开头
                if num_code.startswith('60'):
                    stocks.append({
                        'code': f"{num_code}.SH",
                        'name': name,
                        'display': f"{num_code}.SH - {name}"
                    })
            elif code.startswith('sz.'):
                num_code = code[3:]
                # 深圳主板：00开头
                if num_code.startswith('00'):
                    stocks.append({
                        'code': f"{num_code}.SZ",
                        'name': name,
                        'display': f"{num_code}.SZ - {name}"
                    })
        
        bs.logout()
        
    except Exception as e:
        st.error(f"获取股票列表失败: {e}")
        # 返回预设的常用股票作为备选
        return get_fallback_stocks()
    
    return pd.DataFrame(stocks)

def get_fallback_stocks():
    """备选股票列表（当网络获取失败时使用）"""
    fallback = [
        ("000001.SZ", "平安银行"), ("000002.SZ", "万科A"), ("000858.SZ", "五粮液"),
        ("600036.SH", "招商银行"), ("600030.SH", "中信证券"), ("600519.SH", "贵州茅台"),
        ("601318.SH", "中国平安"), ("000333.SZ", "美的集团"), ("600276.SH", "恒瑞医药"),
        ("002415.SZ", "海康威视"), ("000651.SZ", "格力电器"), ("000568.SZ", "泸州老窖"),
        ("600900.SH", "长江电力"), ("601166.SH", "兴业银行"), ("600016.SH", "民生银行"),
        ("000776.SZ", "广发证券"), ("002594.SZ", "比亚迪"), ("000725.SZ", "京东方A"),
        ("600050.SH", "中国联通"), ("601888.SH", "中国中免"), ("002352.SZ", "顺丰控股"),
        ("000100.SZ", "TCL科技"), ("600309.SH", "万华化学"), ("002304.SZ", "洋河股份"),
        ("600585.SH", "海螺水泥"), ("000625.SZ", "长安汽车"), ("002142.SZ", "宁波银行"),
    ]
    return pd.DataFrame([{'code': c, 'name': n, 'display': f"{c} - {n}"} for c, n in fallback])

# ============ 数据获取函数 ============
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_stock_data(symbol, start_date, end_date):
    """使用 Baostock 获取单支股票数据"""
    # 转换代码格式
    if symbol.endswith('.SH'):
        bs_code = f"sh.{symbol[:6]}"
    else:
        bs_code = f"sz.{symbol[:6]}"
    
    try:
        login_result = bs.login()
        if login_result.error_code != '0':
            return None
        
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"
        )
        
        data = []
        while rs.next():
            data.append(rs.get_row_data())
        
        bs.logout()
        
        if not data or len(data) < 120:  # 至少需要120个交易日
            return None
        
        df = pd.DataFrame(data, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        df['date'] = pd.to_datetime(df['date'])
        
        return df
        
    except Exception:
        return None

# ============ 技术指标计算 ============
def calculate_ema(df, n):
    """计算 EMA"""
    return df['close'].ewm(span=n, adjust=False).mean()

def calculate_atr(df, period=14):
    """计算 ATR"""
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def check_conditions(df, glue_threshold=1.02, low_vol_threshold=0.03):
    """检查三个条件，返回最新一天的信号"""
    if df is None or len(df) < 120:
        return None
    
    df = df.copy()
    
    # 计算均线
    df['ema21'] = calculate_ema(df, 21)
    df['ema55'] = calculate_ema(df, 55)
    df['ema120'] = calculate_ema(df, 120)
    
    # 条件A：首次站上三条均线
    df['above_all'] = (df['close'] > df['ema21']) & \
                      (df['close'] > df['ema55']) & \
                      (df['close'] > df['ema120'])
    df['first_break'] = df['above_all'] & (~df['above_all'].shift(1).fillna(False))
    
    # 条件B：均线粘合
    ema_max = df[['ema21', 'ema55', 'ema120']].max(axis=1)
    ema_min = df[['ema21', 'ema55', 'ema120']].min(axis=1)
    df['glue_ratio'] = ema_max / ema_min
    ema_mean = df[['ema21', 'ema55', 'ema120']].mean(axis=1)
    df['glue_signal'] = (df['glue_ratio'] <= glue_threshold) & \
                        (abs(df['close'] - ema_mean) / ema_mean <= 0.03)
    
    # 条件C：均线附近低波动
    atr = calculate_atr(df, 5)
    df['low_vol_signal'] = (abs(df['close'] - df['ema21']) / df['ema21'] <= 0.02) & \
                           ((atr / df['close']) < low_vol_threshold)
    
    # 获取最新一天的数据
    latest = df.iloc[-1]
    signals = []
    
    if latest['first_break']:
        signals.append('A-首次站上')
    if latest['glue_signal']:
        signals.append('B-均线粘合')
    if latest['low_vol_signal']:
        signals.append('C-低波动')
    
    if not signals:
        return None
    
    return {
        'signal': ', '.join(signals),
        'close': latest['close'],
        'ema21': latest['ema21'],
        'ema55': latest['ema55'],
        'ema120': latest['ema120'],
        'glue_ratio': latest['glue_ratio'] if 'glue_ratio' in latest else None,
        'df': df
    }

# ============ K线图 ============
def plot_kline(df, code, name, signal):
    """绘制交互式K线图"""
    df_60 = df.tail(60)  # 最近60个交易日
    
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.7, 0.3], vertical_spacing=0.03)
    
    # 蜡烛图
    fig.add_trace(go.Candlestick(
        x=df_60['date'], open=df_60['open'], high=df_60['high'],
        low=df_60['low'], close=df_60['close'], name='K线'
    ), row=1, col=1)
    
    # 均线
    fig.add_trace(go.Scatter(x=df_60['date'], y=df_60['ema21'],
                             line=dict(color='#1E88E5', width=1.5), name='EMA21'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_60['date'], y=df_60['ema55'],
                             line=dict(color='#FF8C00', width=1.5), name='EMA55'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_60['date'], y=df_60['ema120'],
                             line=dict(color='#9C27B0', width=1.5), name='EMA120'), row=1, col=1)
    
    # 成交量
    colors = ['#EF5350' if c >= o else '#4CAF50' 
              for c, o in zip(df_60['close'], df_60['open'])]
    fig.add_trace(go.Bar(x=df_60['date'], y=df_60['volume'],
                         name='成交量', marker_color=colors), row=2, col=1)
    
    fig.update_layout(
        title=f"{code} {name} | 当前价: {df_60['close'].iloc[-1]:.2f} | 信号: {signal}",
        height=550,
        xaxis_rangeslider_visible=False,
        template='plotly_dark'
    )
    
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)
    
    return fig

# ============ 批量处理 ============
def process_stocks_batch(stocks, start_date, end_date, cond_a, cond_b, cond_c, logic, glue_threshold, low_vol_threshold, progress_bar, status_text):
    """批量处理股票"""
    results = []
    total = len(stocks)
    
    for i, row in stocks.iterrows():
        # 更新进度
        progress_bar.progress((i + 1) / total)
        status_text.text(f"正在处理: {row['display']} ({i+1}/{total})")
        
        # 获取数据
        df = fetch_stock_data(row['code'], start_date, end_date)
        if df is None:
            continue
        
        # 计算信号
        info = check_conditions(df, glue_threshold, low_vol_threshold)
        if info is None:
            continue
        
        # 根据逻辑组合筛选
        signal_types = info['signal'].split(', ')
        match = False
        
        if logic == "任一条件(OR)":
            if (cond_a and 'A-首次站上' in signal_types) or \
               (cond_b and 'B-均线粘合' in signal_types) or \
               (cond_c and 'C-低波动' in signal_types):
                match = True
        else:  # AND
            required = []
            if cond_a:
                required.append('A-首次站上')
            if cond_b:
                required.append('B-均线粘合')
            if cond_c:
                required.append('C-低波动')
            if required and all(r in signal_types for r in required):
                match = True
        
        if match:
            results.append({
                'code': row['code'],
                'name': row['name'],
                'signal': info['signal'],
                'close': info['close'],
                'ema21': info['ema21'],
                'ema55': info['ema55'],
                'ema120': info['ema120'],
                'df': info['df']
            })
        
        # 随机延迟，避免请求过快
        time.sleep(random.uniform(0.3, 0.8))
    
    return results

# ============ 主界面 ============
st.title("📈 基于EMA均线形态的股票筛选工具")
st.markdown("### 沪深主板全市场扫描 | 实时计算EMA21/55/120")

# 初始化 session state
if 'results' not in st.session_state:
    st.session_state.results = []
if 'processed' not in st.session_state:
    st.session_state.processed = False

# 侧边栏配置
with st.sidebar:
    st.header("⚙️ 筛选条件")
    
    logic = st.radio("逻辑组合", ["任一条件(OR)", "同时满足(AND)"], help="OR: 满足任一条件即入选；AND: 需同时满足所有选中条件")
    
    st.markdown("---")
    
    cond_a = st.checkbox("✅ 条件A：首次站上三条均线", value=True, 
                         help="今日收盘价首次同时站上EMA21/55/120")
    
    cond_b = st.checkbox("✅ 条件B：三条均线粘合", value=True)
    glue_threshold = st.slider("粘合阈值", 1.01, 1.05, 1.02, 0.005, 
                               disabled=not cond_b, help="三条均线最大值/最小值 ≤ 此值")
    
    cond_c = st.checkbox("✅ 条件C：均线附近低波动", value=True)
    low_vol_threshold = st.slider("波动阈值", 0.01, 0.08, 0.03, 0.005,
                                   disabled=not cond_c, help="ATR/收盘价 < 此值")
    
    st.markdown("---")
    
    if st.button("🚀 开始扫描全市场", type="primary", use_container_width=True):
        st.session_state.processed = False
        st.session_state.results = []
        st.rerun()
    
    st.markdown("---")
    st.caption("数据源: Baostock | 均线周期: 21/55/120 | 数据缓存: 1小时")

# 主区域
tab1, tab2 = st.tabs(["📊 筛选结果", "📖 使用说明"])

with tab1:
    if st.button("🚀 开始扫描全市场", type="primary"):
        with st.spinner("正在获取股票列表..."):
            stocks_df = get_all_mainboard_stocks()
            st.info(f"📊 共获取 {len(stocks_df)} 支沪深主板股票")
        
        if len(stocks_df) == 0:
            st.error("无法获取股票列表，请检查网络后重试")
        else:
            # 计算日期范围
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=250)).strftime('%Y-%m-%d')
            
            # 进度条
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # 批量处理
            results = process_stocks_batch(
                stocks_df, start_date, end_date,
                cond_a, cond_b, cond_c, logic,
                glue_threshold, low_vol_threshold,
                progress_bar, status_text
            )
            
            # 清空进度条
            progress_bar.empty()
            status_text.empty()
            
            if results:
                st.session_state.results = results
                st.session_state.processed = True
                st.balloons()
                st.success(f"✅ 扫描完成！共找到 {len(results)} 支符合条件的股票")
            else:
                st.warning("⚠️ 未找到符合条件的股票，请调整筛选条件后重试")
    
    # 显示结果
    if st.session_state.processed and st.session_state.results:
        st.markdown(f"### 📋 筛选结果 (共 {len(st.session_state.results)} 支)")
        
        # 表格
        table_data = []
        for r in st.session_state.results:
            table_data.append({
                "股票代码": r['code'],
                "股票名称": r['name'],
                "当前价": f"{r['close']:.2f}",
                "EMA21": f"{r['ema21']:.2f}",
                "EMA55": f"{r['ema55']:.2f}",
                "EMA120": f"{r['ema120']:.2f}",
                "触发条件": r['signal']
            })
        
        df_display = pd.DataFrame(table_data)
        st.dataframe(df_display, use_container_width=True, hide_index=True)
        
        # 导出 CSV
        csv = df_display.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 导出为CSV", csv, "stock_signals.csv", "text/csv")
        
        # K线图展示
        st.markdown("### 📈 K线详情（点击展开）")
        for r in st.session_state.results:
            with st.expander(f"{r['code']} {r['name']} | {r['signal']} | 当前价: {r['close']:.2f}"):
                st.plotly_chart(plot_kline(r['df'], r['code'], r['name'], r['signal']), 
                               use_container_width=True)

with tab2:
    st.markdown("""
    ### 📖 使用说明
    
    #### 筛选条件详解
    
    **条件A：首次站上EMA均线**
    - 今日收盘价同时大于 EMA21、EMA55、EMA120
    - 且昨日不满足此条件（今日是突破的第一天）
    
    **条件B：三条均线粘合**
    - 三条均线的最大值/最小值 ≤ 粘合阈值（默认1.02，即相差2%以内）
    - 同时收盘价偏离均线平均值 ≤ 3%
    
    **条件C：均线附近低波动**
    - 股价在 EMA21 附近（偏离 < 2%）
    - 且 ATR/收盘价 < 波动阈值（默认3%）
    
    #### 技术参数
    - 数据源：Baostock（免费，无需注册）
    - 均线周期：21日、55日、120日
    - 数据需求：每支股票至少120个交易日
    
    #### 性能说明
    - 首次扫描约需 10-15 分钟（3000+支股票）
    - 后续使用缓存，速度更快
    - 可在左侧边栏调整筛选条件
    
    #### 注意事项
    - 数据为前复权价格
    - 排除 ST 股、退市股、次新股（上市不足120天）
    """)

st.markdown("---")
st.caption("⚠️ 本工具仅供学习研究，不构成投资建议。股市有风险，投资需谨慎。")