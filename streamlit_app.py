# -*- coding: utf-8 -*-
"""
A股智能分析助手 —— Streamlit 网页版（第三版）
========================================================

这一版把之前所有功能整合进一个网页界面，并新增专业指标与实时行情：

  ✅ 实时行情快照（最新价、涨跌幅、换手率等）
  ✅ 专业指标：MACD、RSI、均线、量比
  ✅ 多因子分析：技术面 / 消息面 / 基本面 / 政策面
  ✅ 未来 2~3 天方向判断 + 支撑/压力位 + 理由
  ✅ 网页交互：左侧输入股票代码，点「开始分析」即可

运行方法（在 VS Code 终端输入，注意是 streamlit 不是 python）：
  streamlit run streamlit_app.py

⚠️ 声明：本程序是教学演示的多因子参考工具，不构成投资建议。
"""

import warnings
warnings.filterwarnings("ignore")

import time
import datetime
import pandas as pd
pd.options.mode.string_storage = "python"   # 修复：akshare个股新闻在pyarrow字符串后端下正则不兼容
import matplotlib.pyplot as plt
import akshare as ak
import streamlit as st

# 网页标题与布局
st.set_page_config(page_title="A股智能分析助手", page_icon="📈", layout="wide")

# 让 matplotlib 正常显示中文
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False


# ============================================================
# 情感词典
# ============================================================
POSITIVE_WORDS = [
    "增长", "上涨", "突破", "中标", "增持", "回购", "超预期", "利好",
    "创新高", "签约", "获批", "扭亏", "大涨", "预增", "景气", "提价",
]
NEGATIVE_WORDS = [
    "下滑", "下跌", "减持", "亏损", "处罚", "诉讼", "违规", "立案",
    "低于预期", "利空", "跌停", "大跌", "风险", "退市", "预减", "降价",
]
POLICY_POSITIVE = [
    "降准", "降息", "减税", "稳增长", "扩大内需", "宽松", "放水", "利好", "支持", "刺激",
    "央行", "国务院", "国常会", "财政部", "发改委", "证监会",
    "补贴", "扶持", "鼓励", "促进", "批复", "批准", "放宽", "松绑",]
POLICY_NEGATIVE = [
    "加息", "收紧", "去杠杆", "上调存款准备金", "监管", "处罚", "利空", "风险提示",
    "立案", "调查", "违规", "警示", "约谈", "限制", "暂停", "罚",]


# ============================================================
# 工具函数
# ============================================================
def net_sentiment_to_score(pos, neg, scale=4.0):
    """把"利好词次数 - 利空词次数"换算成 -2 ~ +2 的得分。"""
    net = pos - neg
    if net >= scale:
        return 2.0
    elif net >= 2:
        return 1.0
    elif net >= 1:
        return 0.5
    elif net <= -scale:
        return -2.0
    elif net <= -2:
        return -1.0
    elif net <= -1:
        return -0.5
    else:
        return 0.0


def safe_float(value, default=0.0):
    """把可能为 NaN / None 的值安全转成浮点数。"""
    try:
        result = float(value)
        if pd.isna(result):
            return default
        return result
    except Exception:
        return default


# ============================================================
# 数据获取
# ============================================================
def fetch_stock_data(stock_code, start_date, end_date, adjust="qfq", max_retries=3):
    """获取历史日线数据（新浪源），失败自动重试。"""
    # 新浪源需要带交易所前缀：sh=上海、sz=深圳、bj=北京
    if stock_code.startswith(("60", "68")):
        symbol = "sh" + stock_code
    elif stock_code.startswith(("00", "30")):
        symbol = "sz" + stock_code
    elif stock_code.startswith(("8", "4")):
        symbol = "bj" + stock_code
    else:
        symbol = "sh" + stock_code

    for attempt in range(1, max_retries + 1):
        try:
            df = ak.stock_zh_a_daily(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            # 新浪源返回的已经是英文列名（date/open/close/volume 等），不用再改名
            df["date"] = pd.to_datetime(df["date"])
            # 新浪源没有"涨跌幅"列，自己算：当日相对前一日的涨跌百分比（%）
            df["pct_change"] = df["close"].pct_change() * 100
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception as e:
            if attempt < max_retries:
                time.sleep(attempt * 2)   # 等 2 秒、4 秒再重试
            else:
                print(f"❌ 历史数据获取失败：{e}")
                return None


def fetch_realtime(stock_code):
    """获取单只股票的实时报价。优先雪球，失败退回东方财富。"""
    # 根据股票代码判断交易所前缀（雪球需要 SH/SZ/BJ）
    if stock_code.startswith(("60", "68")):
        xq_symbol = "SH" + stock_code
    elif stock_code.startswith(("00", "30")):
        xq_symbol = "SZ" + stock_code
    elif stock_code.startswith(("8", "4")):
        xq_symbol = "BJ" + stock_code
    else:
        xq_symbol = "SH" + stock_code

    # 方法一：雪球（返回 item/value 两列的表格，转成字典再取）
    try:
        df = ak.stock_individual_spot_xq(symbol=xq_symbol)
        if df is not None and not df.empty:
            data = dict(zip(df["item"], df["value"]))   # 两列转成字典
            return {
                "最新": data.get("现价"),
                "涨幅": data.get("涨幅"),
                "今开": data.get("今开"),
                "最高": data.get("最高"),
                "最低": data.get("最低"),
                "换手": data.get("周转率"),   # 雪球把"换手率"叫"周转率"
                "量比": None,                  # 雪球没有量比，置空
            }
    except Exception:
        pass

    # 方法二：东方财富盘口（备用）
    try:
        df = ak.stock_bid_ask_em(symbol=stock_code)
        if df is not None and not df.empty:
            data = dict(zip(df["item"], df["value"]))
            return {
                "最新": data.get("最新"), "涨幅": data.get("涨幅"),
                "今开": data.get("今开"), "最高": data.get("最高"),
                "最低": data.get("最低"), "换手": data.get("换手"), "量比": data.get("量比"),
            }
    except Exception:
        pass

    return None


# ============================================================
# 指标计算（新增 MACD、RSI）
# ============================================================
def add_indicators(df):
    """计算均线、量比、MACD、RSI 等指标。"""
    # 均线
    for w in (5, 20, 60):
        df[f"MA{w}"] = df["close"].rolling(window=w).mean()

    # 量比
    df["VOL_MA20"] = df["volume"].rolling(window=20).mean()
    df["volume_ratio"] = df["volume"] / df["VOL_MA20"]

    # MACD（12 日与 26 日指数均线的差，再平滑 9 日）
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["DIF"] = ema12 - ema26
    df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["MACD"] = 2 * (df["DIF"] - df["DEA"])        # MACD 柱

    # RSI(14)：相对强弱指标，0~100，>70 超买、<30 超卖
    delta = df["close"].diff()
    gain = delta.clip(lower=0)                       # 上涨部分（负数归零）
    loss = (-delta).clip(lower=0)                    # 下跌部分（取正）
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    avg_loss = avg_loss.replace(0, 1e-10)            # 避免除以 0
    rs = avg_gain / avg_loss
    df["RSI14"] = 100 - 100 / (1 + rs)

    return df


# ============================================================
# 多因子打分
# ============================================================
def technical_score(df):
    """技术面打分：均线 + MACD + RSI + 量价。返回 (得分, 理由, 是否成功)。"""
    reasons = []
    score = 0.0
    latest = df.iloc[-1]

    # 1) 均线排列
    if latest["close"] > latest["MA5"] > latest["MA20"] > latest["MA60"]:
        score += 0.8
        reasons.append("均线多头排列，趋势向上")
    elif latest["close"] < latest["MA5"] < latest["MA20"] < latest["MA60"]:
        score -= 0.8
        reasons.append("均线空头排列，趋势向下")
    else:
        reasons.append("均线纠缠，方向不明")

    # 2) MACD
    if latest["DIF"] > latest["DEA"]:
        score += 0.5
        reasons.append("MACD 处于金叉状态（DIF > DEA），动能偏多")
    else:
        score -= 0.5
        reasons.append("MACD 处于死叉状态，动能偏空")

    # 3) RSI
    rsi = safe_float(latest["RSI14"], 50)
    if rsi > 70:
        score -= 0.3
        reasons.append(f"RSI = {rsi:.0f}，超买，短线或回调")
    elif rsi < 30:
        score += 0.3
        reasons.append(f"RSI = {rsi:.0f}，超卖，短线或反弹")
    else:
        reasons.append(f"RSI = {rsi:.0f}，中性")

    # 4) 量价配合
    if latest["volume_ratio"] > 1.2 and latest["pct_change"] > 0:
        score += 0.4
        reasons.append("放量上涨，量价配合较好")
    elif latest["volume_ratio"] > 1.2 and latest["pct_change"] < 0:
        score -= 0.4
        reasons.append("放量下跌，需警惕")

    score = max(-2.0, min(2.0, score))
    return score, reasons, True


def get_stock_name(stock_code):
    """根据股票代码查名称（从雪球查，单只股票，更快更稳）。"""
    # 雪球需要带交易所前缀
    if stock_code.startswith(("60", "68")):
        xq_symbol = "SH" + stock_code
    elif stock_code.startswith(("00", "30")):
        xq_symbol = "SZ" + stock_code
    elif stock_code.startswith(("8", "4")):
        xq_symbol = "BJ" + stock_code
    else:
        xq_symbol = "SH" + stock_code

    try:
        df = ak.stock_individual_spot_xq(symbol=xq_symbol)
        if df is not None and not df.empty:
            data = dict(zip(df["item"], df["value"]))
            name = data.get("名称")
            if name:
                return str(name)
    except Exception:
        pass
    return stock_code   # 实在查不到，才返回代码本身


def news_score(stock_code, industry_keywords=""):
    """消息面：个股新闻 +（可选）行业新闻，做关键词情感分析。
    返回 (得分, 理由, 是否成功, 新闻列表[(标题, 链接), ...])
    """
    reasons = []
    try:
        news_df = ak.stock_news_em(symbol=stock_code)
        if news_df is None or news_df.empty:
            return 0.0, ["暂无该股新闻"], False, []

        text = " ".join(
            news_df["新闻标题"].astype(str).tolist() + news_df["新闻内容"].astype(str).tolist()
        )

        if industry_keywords.strip():
            cls_df = ak.stock_info_global_cls(symbol="全部")
            if cls_df is not None and not cls_df.empty:
                full = cls_df["标题"].astype(str) + " " + cls_df["内容"].astype(str)
                for kw in industry_keywords.replace("、", " ").replace(",", " ").split():
                    kw = kw.strip()
                    if kw:
                        industry_text = " ".join(
                            cls_df[full.str.contains(kw, na=False, regex=False)]["标题"].astype(str).tolist()
                        )
                        text = text + " " + industry_text

        pos = sum(text.count(w) for w in POSITIVE_WORDS)
        neg = sum(text.count(w) for w in NEGATIVE_WORDS)
        score = net_sentiment_to_score(pos, neg)

        reasons.append(f"抓到 {len(news_df)} 条个股新闻")
        if industry_keywords.strip():
            reasons.append(f"已补充行业关键词「{industry_keywords}」相关新闻")
        reasons.append(f"利好词 {pos} 次、利空词 {neg} 次")

        # 收集最近 10 条新闻的标题和链接，方便点击查阅
        items = []
        for _, row in news_df.head(10).iterrows():
            items.append((str(row["新闻标题"]), str(row["新闻链接"])))

        return score, reasons, True, items
    except Exception as e:
        return 0.0, [f"消息面获取失败：{e}"], False, []


def fundamental_score(stock_code):
    """基本面：净利润增长率 + ROE。"""
    reasons = []
    try:
        start_year = str(datetime.date.today().year - 2)
        df = ak.stock_financial_analysis_indicator(symbol=stock_code, start_year=start_year)
        if df is None or df.empty:
            return 0.0, ["基本面数据不可用"], False
        if "日期" in df.columns:
            df = df.sort_values("日期").reset_index(drop=True)
        latest = df.iloc[-1]
        np_growth = safe_float(latest["净利润增长率(%)"])
        roe = safe_float(latest["净资产收益率(%)"])
        score = 0.0
        if np_growth >= 20:
            score += 1.0
            reasons.append(f"净利润同比增长 {np_growth:.1f}%，成长性强")
        elif np_growth >= 0:
            score += 0.5
            reasons.append(f"净利润同比增长 {np_growth:.1f}%")
        else:
            score -= 1.0
            reasons.append(f"净利润同比 {np_growth:.1f}%，下滑")
        if roe >= 15:
            score += 1.0
            reasons.append(f"ROE {roe:.1f}%，盈利优秀")
        elif roe >= 8:
            score += 0.5
            reasons.append(f"ROE {roe:.1f}%，盈利良好")
        else:
            score -= 0.5
            reasons.append(f"ROE {roe:.1f}%，偏弱")
        score = max(-2.0, min(2.0, score))
        return score, reasons, True
    except Exception as e:
        return 0.0, [f"基本面获取失败：{e}"], False


def policy_score():
    """政策面：财联社快讯关键词。"""
    reasons = []
    try:
        df = ak.stock_info_global_cls(symbol="全部")
        if df is None or df.empty:
            return 0.0, ["暂无政策快讯"], False
        text = " ".join(df["标题"].astype(str).tolist() + df["内容"].astype(str).tolist())
        pos = sum(text.count(w) for w in POLICY_POSITIVE)
        neg = sum(text.count(w) for w in POLICY_NEGATIVE)
        score = net_sentiment_to_score(pos, neg, scale=3.0)
        reasons.append(f"宽松类词 {pos} 次、收紧类词 {neg} 次")
        return score, reasons, True
    except Exception as e:
        return 0.0, [f"政策面获取失败：{e}"], False


# ============================================================
# 画图（返回 fig，供 streamlit 显示）
# ============================================================
def plot_price(df, stock_code):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(df["date"], df["close"], label="收盘价", color="#333333", linewidth=1.2)
    ax1.plot(df["date"], df["MA5"], label="MA5", linewidth=1)
    ax1.plot(df["date"], df["MA20"], label="MA20", linewidth=1)
    ax1.plot(df["date"], df["MA60"], label="MA60", linewidth=1)
    ax1.set_title(f"{stock_code} 走势与均线（前复权）")
    ax1.set_ylabel("价格（元）")
    ax1.legend(loc="best")
    ax1.grid(alpha=0.3)

    ax2.bar(df["date"], df["volume"], color="#d62728", alpha=0.6)
    ax2.set_ylabel("成交量（手）")
    ax2.grid(alpha=0.3)
    plt.tight_layout()
    return fig


def plot_macd(df):
    fig, ax = plt.subplots(figsize=(12, 3))
    ax.bar(df["date"], df["MACD"], color="#c0392b", alpha=0.6)
    ax.plot(df["date"], df["DIF"], label="DIF", linewidth=1)
    ax.plot(df["date"], df["DEA"], label="DEA", linewidth=1)
    ax.set_title("MACD")
    ax.legend(loc="best")
    ax.grid(alpha=0.3)
    plt.tight_layout()
    return fig


def plot_rsi(df):
    fig, ax = plt.subplots(figsize=(12, 3))
    ax.plot(df["date"], df["RSI14"], label="RSI(14)", color="#9467bd", linewidth=1)
    ax.axhline(70, color="red", linestyle="--", alpha=0.5, label="超买线 70")
    ax.axhline(30, color="green", linestyle="--", alpha=0.5, label="超卖线 30")
    ax.set_ylim(0, 100)
    ax.set_title("RSI(14)")
    ax.legend(loc="best")
    ax.grid(alpha=0.3)
    plt.tight_layout()
    return fig


# ============================================================
# 结论工具
# ============================================================
def verdict_text(total):
    if total >= 1.2:
        return "📈 偏乐观 —— 未来 2~3 天震荡偏强概率较大"
    elif total >= 0.4:
        return "📈 中性偏多 —— 短期略偏正面"
    elif total >= -0.4:
        return "➡️ 中性震荡 —— 多空均衡，方向不明"
    elif total >= -1.2:
        return "📉 中性偏空 —— 短期略偏谨慎"
    else:
        return "📉 偏谨慎 —— 未来 2~3 天震荡偏弱概率较大"


def screen_stocks(top_n=5, max_candidates=15):
    """扫描全市场，按技术面筛选强势股，返回得分最高的 top_n 只。

    返回：列表，每个元素是 (股票代码, 名称, 技术分, 理由列表, 实时行情那一行)
    """
    # 1) 拉全市场实时行情（新浪源，一次拿到全部股票）
    try:
        spot = ak.stock_zh_a_spot()
    except Exception as e:
        print(f"全市场行情获取失败：{e}")
        return []

    # 2) 把关键列转成数字（原始是字符串）
    for col in ["涨跌幅", "成交额", "最新价"]:
        spot[col] = pd.to_numeric(spot[col], errors="coerce")

    # 3) 筛选"强势股"条件
    cond = (
        (spot["涨跌幅"] > 1) & (spot["涨跌幅"] < 9) &   # 上涨但没涨停，还有空间
        (spot["成交额"] > 3e8) &                        # 成交额 > 3 亿（资金活跃）
        (spot["最新价"] > 3) & (spot["最新价"] < 200)    # 价格区间，排除仙股和超高价股
    )
    cand = spot[cond].copy()

    # 4) 排除北交所（bj 开头）和 ST 股
    cand = cand[~cand["代码"].str.startswith("bj")]
    cand = cand[~cand["名称"].str.contains("ST", na=False)]

    # 5) 按涨跌幅排序，只取前 max_candidates 只做深度分析（控制计算量）
    cand = cand.sort_values("涨跌幅", ascending=False).head(max_candidates)

    if cand.empty:
        return []

    # 6) 对每只候选股：抓历史数据 → 算指标 → 打技术分
    start_str = (datetime.date.today() - datetime.timedelta(days=365)).strftime("%Y%m%d")
    end_str = datetime.date.today().strftime("%Y%m%d")

    results = []
    for _, row in cand.iterrows():
        code = row["代码"][-6:]        # 去掉 sh/sz/bj 前缀，得到 6 位代码
        name = row["名称"]
        df = fetch_stock_data(code, start_str, end_str)
        if df is None or df.empty or len(df) < 60:
            continue                    # 历史数据不够就跳过
        df = add_indicators(df)
        score, reasons, _ = technical_score(df)
        # 建议买入价 = 20日均线（回调买点）；建议卖出价 = 近20日最高价（压力位）
        buy_price = safe_float(df["MA20"].iloc[-1])
        sell_price = safe_float(df["high"].tail(20).max())
        results.append((code, name, score, reasons, row, buy_price, sell_price))
        time.sleep(0.2)                 # 每次请求间隔，避免太快被限流

    # 7) 按技术分从高到低排序，取前 top_n
    results.sort(key=lambda x: x[2], reverse=True)
    return results[:top_n]


# ============================================================
# 主界面
# ============================================================
def main():
    st.title("📈 A股智能分析助手")
    st.caption("多因子参考工具 · 教学演示 · 不构成投资建议")

    # ---- 侧边栏（全局）----
    with st.sidebar:
        st.header("⚙️ 参数设置")
        language = st.selectbox("语言 / Language", ["简体中文"])
        stock_code = st.text_input("股票代码", value="600519")
        industry_keywords = st.text_input("行业关键词（可选）", placeholder="如：白酒、酿酒")
        start_date = st.date_input("开始日期", value=datetime.date(2023, 1, 1))
        analyze = st.button("开始分析", type="primary", use_container_width=True)
        st.divider()
        st.caption("数据来自 AkShare（新浪/雪球等），网络不稳时可能偶尔失败。")
    # ---- 两个标签页 ----
    tab1, tab2 = st.tabs(["🔍 单股分析", "🚀 智能选股"])

    # ============ 标签 1：单股分析 ============
    with tab1:
        if not analyze:
            st.info("👈 在左侧输入股票代码，点「开始分析」")
        else:
            stock_code = stock_code.strip()
            # 显示股票名称，方便核对有没有查错
            name = get_stock_name(stock_code)
            st.markdown(f"### 📋 正在分析：{name}（{stock_code}）")

            # 1) 实时行情
            rt = fetch_realtime(stock_code)
            if rt is not None and rt.get("最新") is not None:
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("最新价", f"{safe_float(rt['最新']):.2f}", f"{safe_float(rt['涨幅']):.2f}%")
                col2.metric("今开", f"{safe_float(rt['今开']):.2f}")
                col3.metric("最高", f"{safe_float(rt['最高']):.2f}")
                col4.metric("最低", f"{safe_float(rt['最低']):.2f}")
                info = f"换手率 {rt['换手']}%"
                if rt.get("量比") is not None:
                    info += f" · 量比 {rt['量比']}"
                st.caption(info)
            else:
                st.warning("⚠️ 实时行情暂时获取失败（网络原因），已跳过。")

            # 2) 历史数据 + 指标
            start_str = start_date.strftime("%Y%m%d")
            end_str = datetime.date.today().strftime("%Y%m%d")
            with st.spinner("正在抓取历史数据……"):
                df = fetch_stock_data(stock_code, start_str, end_str)
            if df is None or df.empty:
                st.error("历史数据获取失败，请稍后重试。")
            else:
                df = add_indicators(df)
                st.success(f"✅ 已获取 {len(df)} 条历史数据")

                st.subheader("📊 走势与指标")
                st.pyplot(plot_price(df, stock_code))
                st.pyplot(plot_macd(df))
                st.pyplot(plot_rsi(df))

                st.subheader("🧠 多因子分析")
                with st.spinner("正在分析（技术/消息/基本面/政策）……"):
                    factors = [
                        technical_score(df),
                        news_score(stock_code, industry_keywords),                        fundamental_score(stock_code),
                        policy_score(),
                    ]
                weights = [0.40, 0.25, 0.25, 0.10]
                total = sum(w * f[0] for w, f in zip(weights, factors))
                names = ["技术面", "消息面", "基本面", "政策面"]

                for name, weight, f in zip(names, weights, factors):
                    score, reasons, ok = f[0], f[1], f[2]
                    status = "✅" if ok else "⚠️ 数据缺失"
                    st.markdown(f"**{name}**（权重 {weight*100:.0f}%） 得分 `{score:+.1f}` {status}")
                    st.progress((score + 2) / 4)
                    for r in reasons:
                        st.write(f"· {r}")
                    # 消息面/政策面：附上可点击的相关新闻
                    if len(f) > 3 and f[3]:
                        with st.expander(f"📰 查看{name}相关新闻（点击展开）"):
                            for title, url in f[3]:
                                st.markdown(f"- [{title}]({url})")

                ok_count = sum(1 for f in factors if f[2])
                confidence = "高" if ok_count == 4 else ("中" if ok_count >= 3 else "低")
                support = safe_float(df["low"].tail(20).min())
                resistance = safe_float(df["high"].tail(20).max())
                latest_close = safe_float(df["close"].iloc[-1])

                st.divider()
                st.subheader("📌 综合结论")
                st.markdown(f"### {verdict_text(total)}")
                st.caption(f"综合得分 {total:+.2f} / +2.0 · 分析可信度：{confidence}")
                col1, col2, col3 = st.columns(3)
                col1.metric("最新收盘价", f"{latest_close:.2f}")
                col2.metric("近20日支撑位", f"{support:.2f}")
                col3.metric("近20日压力位", f"{resistance:.2f}")
                st.warning("⚠️ 以上为教学演示的多因子参考，不构成投资建议，据此操作盈亏自负。")

    # ============ 标签 2：智能选股 ============
    with tab2:
        st.subheader("🚀 智能选股")
        st.caption("扫描全市场，按技术面选出强势股。仅供学习参考，不构成投资建议。")

        col1, col2, _ = st.columns([2, 1, 1])
        top_n = col1.slider("选出前几名", 3, 10, 5)
        run_screen = col2.button("开始选股", type="primary")

        if run_screen:
            with st.spinner("正在扫描全市场并计算指标，可能需要 20~40 秒，请耐心等待……"):
                results = screen_stocks(top_n=top_n)

            if results:
                st.success(f"✅ 扫描完成，以下是技术面最强的 {len(results)} 只：")
                for i, (code, name, score, reasons, row, buy_price, sell_price) in enumerate(results, 1):
                    st.markdown(f"### {i}. {name}（{code}）")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("最新价", f"{safe_float(row['最新价']):.2f}")
                    c2.metric("涨跌幅", f"{safe_float(row['涨跌幅']):.2f}%")
                    c3.metric("成交额", f"{safe_float(row['成交额'])/1e8:.2f}亿")
                    c4.metric("技术分", f"{score:+.1f}")
                    b1, b2 = st.columns(2)
                    b1.metric("🟢 建议买入价（20日线）", f"{buy_price:.2f}")
                    b2.metric("🔴 建议卖出价（压力位）", f"{sell_price:.2f}")
                    for r in reasons:
                        st.write(f"· {r}")
                    st.divider()
            else:
                st.warning("没有找到符合条件的股票，或网络请求失败，请稍后再试。")


main()
