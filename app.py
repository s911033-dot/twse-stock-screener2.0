import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 頁面基礎配置
st.set_page_config(page_title="台股全方位量價籌碼戰情室", layout="wide")
st.title("📈 台股全方位量價籌碼戰情室 & 技術選股神器")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ====================================================
# 1. 抓取全台股上市上櫃股票清單 (快取 24 小時)
# ====================================================
@st.cache_data(ttl=86400)
def fetch_all_stocks():
    stocks = []
    # (1) 證交所 (上市)
    try:
        url_twse = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        res = requests.get(url_twse, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                code = item.get("Code", "").strip()
                name = item.get("Name", "").strip()
                if len(code) == 4 and code.isdigit():
                    stocks.append({
                        "ticker": f"{code}.TW",
                        "display": f"{code} {name} (上市)",
                        "market": "上市",
                        "code": code,
                        "name": name
                    })
    except Exception:
        pass

    # (2) 櫃買中心 (上櫃)
    try:
        url_tpex = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        res = requests.get(url_tpex, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                code = item.get("SecuritiesCompanyCode", "").strip()
                name = item.get("CompanyName", "").strip()
                if len(code) == 4 and code.isdigit():
                    stocks.append({
                        "ticker": f"{code}.TWO",
                        "display": f"{code} {name} (上櫃)",
                        "market": "上櫃",
                        "code": code,
                        "name": name
                    })
    except Exception:
        pass

    return pd.DataFrame(stocks)

all_stocks_df = fetch_all_stocks()

# ====================================================
# 2. 探測最近開盤交易日 (回溯 15 天)
# ====================================================
def get_recent_trading_dates(count=5):
    dates = []
    curr = datetime.now()
    while len(dates) < count and (datetime.now() - curr).days < 15:
        if curr.weekday() < 5:  # 排除週六、日
            d_str = curr.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d_str}&selectType=ALL&response=json"
            try:
                r = requests.get(url, headers=HEADERS, timeout=4).json()
                if r.get("stat") == "OK" and "data" in r:
                    dates.append(d_str)
            except Exception:
                pass
        curr -= timedelta(days=1)
    return dates

# ====================================================
# 3. 抓取三大法人多日買賣超 (快取 2 小時)
# ====================================================
@st.cache_data(ttl=7200)
def fetch_multi_day_inst(dates):
    records = []
    for d in dates:
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALL&response=json"
        try:
            res = requests.get(url, headers=HEADERS, timeout=8).json()
            if res.get("stat") == "OK" and "data" in res:
                for row in res["data"]:
                    code = str(row[0]).strip()
                    name = str(row[1]).strip()
                    if len(code) == 4 and code.isdigit():
                        def to_lots(val):
                            try:
                                return int(str(val).replace(",", "")) // 1000
                            except:
                                return 0
                        records.append({
                            "date": d,
                            "code": code,
                            "name": name,
                            "foreign": to_lots(row[4]),    # 外資
                            "trust": to_lots(row[10]),      # 投信
                            "dealer": to_lots(row[11]),     # 自營商
                            "total_inst": to_lots(row[18])  # 三大法人合計
                        })
        except Exception:
            continue
    return pd.DataFrame(records)

# ====================================================
# 4. 抓取融資融券 (MI_MARGN) 與借券賣出 (TWT93U)
# ====================================================
@st.cache_data(ttl=7200)
def fetch_margin_and_sbl(target_date):
    margin_dict = {}
    
    # 融資融券
    url_margin = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={target_date}&selectType=ALL&response=json"
    try:
        res = requests.get(url_margin, headers=HEADERS, timeout=8).json()
        tables = res.get("tables", [])
        raw_data = []
        for t in tables:
            if "融資" in t.get("title", "") and "data" in t:
                raw_data = t.get("data", [])
                break
        if not raw_data and "data" in res:
            raw_data = res.get("data", [])

        for row in raw_data:
            code = str(row[0]).strip()
            name = str(row[1]).strip()
            if len(code) == 4 and code.isdigit():
                def parse_val(v):
                    try:
                        return int(str(v).replace(",", ""))
                    except:
                        return 0
                margin_dict[code] = {
                    "name": name,
                    "margin_buy": parse_val(row[2]),
                    "margin_diff": parse_val(row[6]) - parse_val(row[5]),
                    "short_sell": parse_val(row[9]),
                    "short_diff": parse_val(row[12]) - parse_val(row[11]),
                    "sbl_short_sell": 0
                }
    except Exception:
        pass

    # 借券賣出
    url_sbl = f"https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U?date={target_date}&response=json"
    try:
        res_sbl = requests.get(url_sbl, headers=HEADERS, timeout=8).json()
        if res_sbl.get("stat") == "OK" and "data" in res_sbl:
            for row in res_sbl["data"]:
                code = str(row[0]).strip()
                if code in margin_dict:
                    try:
                        margin_dict[code]["sbl_short_sell"] = int(str(row[8]).replace(",", "")) // 1000
                    except:
                        pass
    except Exception:
        pass

    return margin_dict

# 載入近 5 個開盤日基礎數據
with st.spinner("同步臺灣證券交易所官方最新數據中..."):
    trade_dates = get_recent_trading_dates(count=5)
    latest_date = trade_dates[0] if trade_dates else datetime.now().strftime("%Y%m%d")
    df_inst_all = fetch_multi_day_inst(trade_dates)
    margin_data = fetch_margin_and_sbl(latest_date)

# ====================================================
# 5. 技術指標計算輔助函數
# ====================================================
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

def compute_kd(df, n=9):
    low_min = df['Low'].rolling(window=n).min()
    high_max = df['High'].rolling(window=n).max()
    rsv = (df['Close'] - low_min) / (high_max - low_min + 1e-9) * 100

    k_list, d_list = [50.0], [50.0]
    for r in rsv.fillna(50):
        k = (2/3) * k_list[-1] + (1/3) * r
        d = (2/3) * d_list[-1] + (1/3) * k
        k_list.append(k)
        d_list.append(d)
    
    df['K'] = k_list[1:]
    df['D'] = d_list[1:]
    return df

def compute_indicators(df):
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    df['Vol_MA5'] = df['Volume'].rolling(5).mean()
    df['RSI_6'] = compute_rsi(df['Close'], 6)
    df['RSI_12'] = compute_rsi(df['Close'], 12)
    df = compute_kd(df, 9)

    df['Prev_Close'] = df['Close'].shift(1)
    df['Prev_MA20'] = df['MA20'].shift(1)
    df['Prev_MA60'] = df['MA60'].shift(1)
    df['Prev_K'] = df['K'].shift(1)
    df['Prev_D'] = df['D'].shift(1)
    df['Prev_RSI6'] = df['RSI_6'].shift(1)
    df['Prev_RSI12'] = df['RSI_12'].shift(1)

    df['Signal_MA20'] = (df['Prev_Close'] <= df['Prev_MA20']) & (df['Close'] > df['MA20'])
    df['Signal_MA60'] = (df['Prev_Close'] <= df['Prev_MA60']) & (df['Close'] > df['MA60'])
    df['Signal_KD'] = (df['Prev_K'] <= df['Prev_D']) & (df['K'] > df['D'])
    df['Signal_RSI'] = (df['Prev_RSI6'] <= df['Prev_RSI12']) & (df['RSI_6'] > df['RSI_12'])
    return df

# ====================================================
# 6. Plotly 互動式 K 線與訊號圖
# ====================================================
def plot_stock_chart(ticker, title_name):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="6mo")
        if len(df) < 30:
            st.warning("歷史數據不足以繪圖。")
            return

        df = compute_indicators(df)
        plot_df = df.tail(80).copy()

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            row_heights=[0.7, 0.3],
            subplot_titles=(f"{title_name} 近日 K 線走勢與買進訊號", "KD (9, 3, 3)")
        )

        # K 線
        fig.add_trace(go.Candlestick(
            x=plot_df.index.strftime('%Y-%m-%d'),
            open=plot_df['Open'], high=plot_df['High'],
            low=plot_df['Low'], close=plot_df['Close'],
            name="K線",
            increasing_line_color='#FF4B4B',
            decreasing_line_color='#00873E'
        ), row=1, col=1)

        # 均線
        fig.add_trace(go.Scatter(
            x=plot_df.index.strftime('%Y-%m-%d'), y=plot_df['MA20'],
            mode='lines', name='20MA (月線)', line=dict(color='#FFA500', width=1.5)
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=plot_df.index.strftime('%Y-%m-%d'), y=plot_df['MA60'],
            mode='lines', name='60MA (季線)', line=dict(color='#8A2BE2', width=1.5)
        ), row=1, col=1)

        # 訊號三角形標註
        ma_signals = plot_df[plot_df['Signal_MA20']]
        if not ma_signals.empty:
            fig.add_trace(go.Scatter(
                x=ma_signals.index.strftime('%Y-%m-%d'),
                y=ma_signals['Low'] * 0.985,
                mode='markers', name='突破20MA',
                marker=dict(symbol='triangle-up', size=11, color='#E60000')
            ), row=1, col=1)

        kd_signals = plot_df[plot_df['Signal_KD']]
        if not kd_signals.empty:
            fig.add_trace(go.Scatter(
                x=kd_signals.index.strftime('%Y-%m-%d'),
                y=kd_signals['Low'] * 0.97,
                mode='markers', name='KD金叉',
                marker=dict(symbol='triangle-up', size=9, color='#0066FF')
            ), row=1, col=1)

        # KD 副圖
        fig.add_trace(go.Scatter(x=plot_df.index.strftime('%Y-%m-%d'), y=plot_df['K'], mode='lines', name='K值', line=dict(color='#E60000', width=1.5)), row=2, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index.strftime('%Y-%m-%d'), y=plot_df['D'], mode='lines', name='D值', line=dict(color='#0066FF', width=1.5)), row=2, col=1)
        fig.add_hline(y=80, line_dash="dash", line_color="gray", line_width=1, row=2, col=1)
        fig.add_hline(y=20, line_dash="dash", line_color="gray", line_width=1, row=2, col=1)

        fig.update_layout(
            height=600,
            margin=dict(l=10, r=10, t=35, b=10),
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified"
        )
        fig.update_xaxes(type='category')
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"線圖載入失敗: {e}")

# ====================================================
# 7. 主介面分頁導航
# ====================================================
tab1, tab2, tab3 = st.tabs(["🚀 全方位技術與量價選股", "📊 法人與信用交易排行 Top 20", "⚔️ 主力籌碼對作模型"])

# ----------------------------------------------------
# TAB 1: 全方位技術與量價選股
# ----------------------------------------------------
with tab1:
    st.subheader("1️⃣ 設定掃描範圍")
    c_m1, c_m2 = st.columns([1, 2])
    with c_m1:
        market_choice = st.radio("範圍選擇", ["市值核心股 (30檔)", "全部上市", "全部上櫃", "自訂挑選"], index=0)

    target_tickers = []
    if market_choice == "市值核心股 (30檔)":
        top30 = ["2330", "2317", "2454", "2382", "2308", "2603", "3711", "2881", "2882", "2891",
                 "2357", "3008", "2886", "2303", "3231", "2412", "2609", "2615", "3034", "3037",
                 "3443", "6415", "3661", "2379", "6669", "2345", "6274", "8069", "3529", "6515"]
        target_tickers = all_stocks_df[all_stocks_df["code"].isin(top30)]["ticker"].tolist()
        st.info(f"已帶入流動性佳之代表股 {len(target_tickers)} 檔。")
    elif market_choice in ["全部上市", "全部上櫃"]:
        m_tag = "上市" if market_choice == "全部上市" else "上櫃"
        sub = all_stocks_df[all_stocks_df["market"] == m_tag]
        scan_limit = st.slider("掃描檔數 (建議 30~50 檔維持手機順暢度)", 10, len(sub), 30, step=10)
        target_tickers = sub["ticker"].head(scan_limit).tolist()
    else:
        selected_display = st.multiselect("搜尋股票 (支援中文或代碼)", options=all_stocks_df["display"].tolist(), default=all_stocks_df["display"].head(5).tolist())
        target_tickers = all_stocks_df[all_stocks_df["display"].isin(selected_display)]["ticker"].tolist()

    st.subheader("2️⃣ 勾選過濾條件")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**【均線突破】**")
        chk_ma20 = st.checkbox("突破 20 日線 (月線)", value=True)
        chk_ma60 = st.checkbox("突破 60 日線 (季線)")
        chk_wma30 = st.checkbox("突破 30 週均線")
    with col2:
        st.markdown("**【KD / RSI 指標】**")
        chk_daily_kd = st.checkbox("日 KD 黃金交叉", value=True)
        chk_weekly_kd = st.checkbox("週 KD 黃金交叉")
        chk_daily_rsi = st.checkbox("日 RSI 黃金交叉 (6穿12)")
        chk_weekly_rsi = st.checkbox("週 RSI 黃金交叉")
    with col3:
        st.markdown("**【量能與法人買超】**")
        chk_vol_burst = st.checkbox("今日爆量 (>5日均量)", value=True)
        vol_multiple = st.selectbox("爆量倍數", [1.5, 2.0, 3.0], index=0)
        min_vol_limit = st.number_input("今日最低成交量 (張)", value=500, step=500)
        chk_trust_buy = st.checkbox("投信買超 (> 0 張)")
        chk_foreign_buy = st.checkbox("外資買超 (> 0 張)")

    if st.button("🚀 開始全方位篩選", use_container_width=True):
        if not target_tickers:
            st.warning("請先選定股票觀察名單！")
        else:
            results = []
            progress_bar = st.progress(0, text="下載走勢與指標計算中...")
            
            # 取得最新法人字典
            today_inst_dict = {}
            if not df_inst_all.empty:
                df_today = df_inst_all[df_inst_all["date"] == latest_date]
                for _, r in df_today.iterrows():
                    today_inst_dict[r["code"]] = r

            for idx, ticker in enumerate(target_tickers):
                progress_bar.progress((idx + 1) / len(target_tickers), text=f"分析中 ({idx+1}/{len(target_tickers)}): {ticker}")
                try:
                    stock = yf.Ticker(ticker)
                    daily_df = stock.history(period="2y")
                    if len(daily_df) < 65:
                        continue

                    # 日線指標
                    daily_df = compute_indicators(daily_df)

                    # 週線重組
                    weekly_df = daily_df.resample('W-FRI').agg({
                        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                    }).dropna()
                    weekly_df['WMA30'] = weekly_df['Close'].rolling(30).mean()
                    weekly_df['RSI_6'] = compute_rsi(weekly_df['Close'], 6)
                    weekly_df['RSI_12'] = compute_rsi(weekly_df['Close'], 12)
                    weekly_df = compute_kd(weekly_df, 9)

                    d_today = daily_df.iloc[-1]
                    d_prev = daily_df.iloc[-2]
                    w_today = weekly_df.iloc[-1]
                    w_prev = weekly_df.iloc[-2]

                    code = ticker.split(".")[0]
                    inst_info = today_inst_dict.get(code, {"foreign": 0, "trust": 0, "total_inst": 0})
                    today_vol_lots = d_today['Volume'] / 1000

                    pass_filter = True

                    # 條件檢查
                    if chk_ma20 and not d_today['Signal_MA20']: pass_filter = False
                    if chk_ma60 and not d_today['Signal_MA60']: pass_filter = False
                    if chk_wma30 and not (pd.notna(w_today['WMA30']) and d_today['Close'] > w_today['WMA30'] and d_prev['Close'] <= w_prev['WMA30']):
                        pass_filter = False

                    if chk_daily_kd and not d_today['Signal_KD']: pass_filter = False
                    if chk_weekly_kd and not (w_prev['K'] <= w_prev['D'] and w_today['K'] > w_today['D']): pass_filter = False
                    if chk_daily_rsi and not d_today['Signal_RSI']: pass_filter = False
                    if chk_weekly_rsi and not (w_prev['RSI_6'] <= w_prev['RSI_12'] and w_today['RSI_6'] > w_today['RSI_12']): pass_filter = False

                    if today_vol_lots < min_vol_limit: pass_filter = False
                    if chk_vol_burst and (d_today['Volume'] < (d_prev['Vol_MA5'] * vol_multiple)): pass_filter = False

                    if chk_trust_buy and inst_info["trust"] <= 0: pass_filter = False
                    if chk_foreign_buy and inst_info["foreign"] <= 0: pass_filter = False

                    if pass_filter:
                        match = all_stocks_df[all_stocks_df["ticker"] == ticker]
                        stock_title = match["display"].values[0] if not match.empty else ticker
                        results.append({
                            "ticker": ticker,
                            "股票": stock_title,
                            "收盤價": round(d_today['Close'], 2),
                            "今日成交量(張)": int(today_vol_lots),
                            "外資買賣超(張)": inst_info["foreign"],
                            "投信買賣超(張)": inst_info["trust"],
                            "日K / 日D": f"{round(d_today['K'], 1)} / {round(d_today['D'], 1)}",
                            "週K / 週D": f"{round(w_today['K'], 1)} / {round(w_today['D'], 1)}"
                        })
                except Exception:
                    continue

            progress_bar.empty()
            st.session_state["screen_res"] = results

    if "screen_res" in st.session_state:
        res = st.session_state["screen_res"]
        if res:
            st.success(f"🎯 符合篩選條件共 **{len(res)}** 檔：")
            st.dataframe(pd.DataFrame(res).drop(columns=["ticker"]), use_container_width=True)

            st.divider()
            st.subheader("🔍 查看互動走勢圖與買點")
            opt_map = {item["ticker"]: item["股票"] for item in res}
            target_t = st.selectbox("請選擇查看線圖的標的：", list(opt_map.keys()), format_func=lambda x: opt_map[x])
            if target_t:
                plot_stock_chart(target_t, opt_map[target_t])
        else:
            st.warning("⚠️ 沒有任何股票同時符合所有勾選條件，建議放寬條件後再試。")

# ----------------------------------------------------
# TAB 2: 法人與信用交易排行 Top 20
# ----------------------------------------------------
with tab2:
    st.subheader(f"📊 臺灣證券交易所官方 Top 20 排行 (最新日期：{latest_date})")
    rank_target = st.selectbox(
        "選擇欲查看的排行榜類別：",
        [
            "外資買超 Top 20", "外資賣超 Top 20",
            "投信買超 Top 20", "投信賣超 Top 20",
            "融資買進 Top 20", "融資增加 Top 20",
            "融券賣出 Top 20", "融券增加 Top 20",
            "借券賣出 Top 20"
        ]
    )

    if "外資" in rank_target or "投信" in rank_target:
        p_choice = st.radio("統計天期", ["每日 (當日)", "三日累積", "每週 (五日累積)"], horizontal=True)
        day_n = {"每日 (當日)": 1, "三日累積": 3, "每週 (五日累積)": 5}[p_choice]
        use_dates = trade_dates[:day_n]

        sub_inst = df_inst_all[df_inst_all["date"].isin(use_dates)]
        grouped = sub_inst.groupby(["code", "name"])[["foreign", "trust", "total_inst"]].sum().reset_index()

        col_name = "foreign" if "外資" in rank_target else "trust"
        is_buy = "買超" in rank_target
        sorted_df = grouped.sort_values(by=col_name, ascending=not is_buy).head(20).copy()
        sorted_df.rename(columns={
            "code": "代碼", "name": "名稱",
            "foreign": "外資買賣超(張)", "trust": "投信買賣超(張)", "total_inst": "三大法人合計(張)"
        }, inplace=True)
        sorted_df.reset_index(drop=True, inplace=True)
        sorted_df.index += 1
        st.dataframe(sorted_df, use_container_width=True)

    else:
        margin_rows = []
        for code, info in margin_data.items():
            margin_rows.append({
                "代碼": code, "名稱": info["name"],
                "融資買進(張)": info["margin_buy"], "融資增減(張)": info["margin_diff"],
                "融券賣出(張)": info["short_sell"], "融券增減(張)": info["short_diff"],
                "借券賣出(張)": info["sbl_short_sell"]
            })
        m_df = pd.DataFrame(margin_rows)
        if not m_df.empty:
            rank_map = {
                "融資買進 Top 20": ("融資買進(張)", False),
                "融資增加 Top 20": ("融資增減(張)", False),
                "融券賣出 Top 20": ("融券賣出(張)", False),
                "融券增加 Top 20": ("融券增減(張)", False),
                "借券賣出 Top 20": ("借券賣出(張)", False)
            }
            sort_k, is_asc = rank_map[rank_target]
            res_df = m_df.sort_values(by=sort_k, ascending=is_asc).head(20).copy()
            res_df.reset_index(drop=True, inplace=True)
            res_df.index += 1
            st.dataframe(res_df, use_container_width=True)

# ----------------------------------------------------
# TAB 3: 主力籌碼對作模型
# ----------------------------------------------------
with tab3:
    st.subheader("🎯 主力與散戶多空對作快速篩選 (前 20 名)")
    model_choice = st.selectbox(
        "選擇籌碼動態篩選模式：",
        [
            "🔥 外資買超 + 融資減少 (外資吃貨/散戶退場)",
            "⚠️ 外資賣超 + 融資增加 (主力出貨/散戶接刀)",
            "🚀 投信買超 + 融資減少 (投信認養/浮額洗清)",
            "⚠️ 投信賣超 + 融資增加 (投信倒貨/散戶承接)",
            "💎 外資買超 + 投信買超 (土洋齊買/合力抬轎)",
            "⚔️ 外資買超 + 投信賣超 (土洋對作/多空分歧)"
        ]
    )

    df_inst_today = df_inst_all[df_inst_all["date"] == latest_date].copy()
    merged_pool = []
    for _, row in df_inst_today.iterrows():
        c = row["code"]
        if c in margin_data:
            minfo = margin_data[c]
            merged_pool.append({
                "代碼": c, "名稱": row["name"],
                "外資買賣超(張)": row["foreign"],
                "投信買賣超(張)": row["trust"],
                "融資增減(張)": minfo["margin_diff"],
                "借券賣出(張)": minfo["sbl_short_sell"]
            })
    pool_df = pd.DataFrame(merged_pool)

    if not pool_df.empty:
        out_df = pd.DataFrame()
        if "外資買超 + 融資減少" in model_choice:
            out_df = pool_df[(pool_df["外資買賣超(張)"] > 0) & (pool_df["融資增減(張)"] < 0)].sort_values(by="外資買賣超(張)", ascending=False).head(20)
        elif "外資賣超 + 融資增加" in model_choice:
            out_df = pool_df[(pool_df["外資買賣超(張)"] < 0) & (pool_df["融資增減(張)"] > 0)].sort_values(by="外資買賣超(張)", ascending=True).head(20)
        elif "投信買超 + 融資減少" in model_choice:
            out_df = pool_df[(pool_df["投信買賣超(張)"] > 0) & (pool_df["融資增減(張)"] < 0)].sort_values(by="投信買賣超(張)", ascending=False).head(20)
        elif "投信賣超 + 融資增加" in model_choice:
            out_df = pool_df[(pool_df["投信買賣超(張)"] < 0) & (pool_df["融資增減(張)"] > 0)].sort_values(by="投信買賣超(張)", ascending=True).head(20)
        elif "外資買超 + 投信買超" in model_choice:
            c_both = (pool_df["外資買賣超(張)"] > 0) & (pool_df["投信買賣超(張)"] > 0)
            pool_df["雙法人合買"] = pool_df["外資買賣超(張)"] + pool_df["投信買賣超(張)"]
            out_df = pool_df[c_both].sort_values(by="雙法人合買", ascending=False).head(20).drop(columns=["雙法人合買"])
        elif "外資買超 + 投信賣超" in model_choice:
            out_df = pool_df[(pool_df["外資買賣超(張)"] > 0) & (pool_df["投信買賣超(張)"] < 0)].sort_values(by="外資買賣超(張)", ascending=False).head(20)

        if not out_df.empty:
            out_df.reset_index(drop=True, inplace=True)
            out_df.index += 1
            st.success(f"符合「{model_choice}」共 {len(out_df)} 檔：")
            st.dataframe(out_df, use_container_width=True)

            st.divider()
            st.subheader("📈 點選查看該股 K 線圖")
            code_opts = {r["代碼"]: f"{r['代碼']} {r['名稱']}" for _, r in out_df.iterrows()}
            chosen = st.selectbox("選擇要繪製走勢圖的股票：", list(code_opts.keys()), format_func=lambda x: code_opts[x])
            if chosen:
                plot_stock_chart(f"{chosen}.TW", code_opts[chosen])
        else:
            st.warning("今日暫無符合該條件之標的。")
