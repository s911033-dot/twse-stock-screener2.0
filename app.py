import streamlit as st
import pandas as pd
import numpy as np
import requests
import re
from datetime import datetime, timedelta
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 頁面基礎配置
st.set_page_config(page_title="台股全方位量價籌碼戰情室", layout="wide")
st.title("📈 台股全方位量價籌碼戰情室 & 券商分點集中度神器")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ====================================================
# 1. 抓取全台股上市上櫃清單 (快取 24 小時)
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
        if curr.weekday() < 5:
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
                            "foreign": to_lots(row[4]),
                            "trust": to_lots(row[10]),
                            "dealer": to_lots(row[11]),
                            "total_inst": to_lots(row[18])
                        })
        except Exception:
            continue
    return pd.DataFrame(records)

# ====================================================
# 4. 抓取多日融資融券與借券賣出 (快取 2 小時)
# ====================================================
@st.cache_data(ttl=7200)
def fetch_multi_day_margin_and_sbl(dates):
    records = []
    for d in dates:
        day_dict = {}
        # 融資融券
        url_margin = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=ALL&response=json"
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
                    day_dict[code] = {
                        "date": d,
                        "code": code,
                        "name": name,
                        "margin_buy": parse_val(row[2]),
                        "margin_diff": parse_val(row[6]) - parse_val(row[5]),
                        "short_sell": parse_val(row[9]),
                        "short_diff": parse_val(row[12]) - parse_val(row[11]),
                        "sbl_short_sell": 0,
                        "sbl_diff": 0
                    }
        except Exception:
            pass

        # 借券賣出
        url_sbl = f"https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U?date={d}&response=json"
        try:
            res_sbl = requests.get(url_sbl, headers=HEADERS, timeout=8).json()
            if res_sbl.get("stat") == "OK" and "data" in res_sbl:
                for row in res_sbl["data"]:
                    code = str(row[0]).strip()
                    if code in day_dict:
                        try:
                            def parse_sbl(v):
                                return int(str(v).replace(",", "")) // 1000
                            prev_bal = parse_sbl(row[7])
                            today_sell = parse_sbl(row[8])
                            today_return = parse_sbl(row[10]) if len(row) > 10 else 0
                            today_bal = parse_sbl(row[12]) if len(row) > 12 else (prev_bal + today_sell - today_return)
                            
                            day_dict[code]["sbl_short_sell"] = today_sell
                            day_dict[code]["sbl_diff"] = today_bal - prev_bal
                        except:
                            pass
        except Exception:
            pass

        records.extend(list(day_dict.values()))

    return pd.DataFrame(records)

# 載入開盤日數據
with st.spinner("同步臺灣證券交易所最新法人與信用交易大數據中..."):
    trade_dates = get_recent_trading_dates(count=5)
    latest_date = trade_dates[0] if trade_dates else datetime.now().strftime("%Y%m%d")
    df_inst_all = fetch_multi_day_inst(trade_dates)
    df_margin_all = fetch_multi_day_margin_and_sbl(trade_dates)

latest_margin_df = df_margin_all[df_margin_all["date"] == latest_date] if not df_margin_all.empty else pd.DataFrame()
margin_data = {r["code"]: r.to_dict() for _, r in latest_margin_df.iterrows()} if not latest_margin_df.empty else {}

# ====================================================
# 5. 券商分點籌碼集中度計算 (FinMind 公開 API，快取 12 小時)
# ====================================================
@st.cache_data(ttl=43200)
def fetch_broker_concentration(stock_code, days=10):
    """
    抓取特定個股在近 N 個交易日內所有券商分點買賣明細，
    計算籌碼集中度 (%) = (前15大買超張數 - 前15大賣超張數) / 總成交量 * 100
    並回傳前 3 大買超主力分點名稱。
    """
    end_d = datetime.now()
    # 考量假日，往前抓取足夠天數
    start_d = end_d - timedelta(days=int(days * 1.8))
    
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockPriceBidAsk",
        "data_id": stock_code,
        "start_date": start_d.strftime("%Y-%m-%d"),
        "end_date": end_d.strftime("%Y-%m-%d")
    }
    
    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=8).json()
        if res.get("msg") == "success" and "data" in res and len(res["data"]) > 0:
            df_bs = pd.DataFrame(res["data"])
            # 只取最近 N 個交易日
            unique_dates = sorted(df_bs["date"].unique(), reverse=True)[:days]
            df_recent = df_bs[df_bs["date"].isin(unique_dates)].copy()

            # 計算各券商分點在該天期內的買賣超張數
            df_recent["diff"] = (df_recent["buy"] - df_recent["sell"]) // 1000  # 轉為張
            broker_summary = df_recent.groupby("broker_name")["diff"].sum().reset_index()

            # 前 15 大買超券商合計
            top_buyers = broker_summary.sort_values(by="diff", ascending=False).head(15)
            buy_sum = top_buyers[top_buyers["diff"] > 0]["diff"].sum()

            # 前 15 大賣超券商合計 (轉正數方便相減)
            top_sellers = broker_summary.sort_values(by="diff", ascending=True).head(15)
            sell_sum = abs(top_sellers[top_sellers["diff"] < 0]["diff"].sum())

            # 該期間個股總成交張數
            total_vol = (df_recent["buy"].sum() + df_recent["sell"].sum()) // 2000

            if total_vol > 0:
                concentration = round(((buy_sum - sell_sum) / total_vol) * 100, 2)
            else:
                concentration = 0.0

            # 抓出前 3 大吃貨主力券商名單
            top3_names = []
            for _, r in top_buyers.head(3).iterrows():
                if r["diff"] > 0:
                    top3_names.append(f"{r['broker_name']}(+{int(r['diff'])}張)")
            top_brokers_str = ", ".join(top3_names) if top3_names else "無明顯買超主力"

            return concentration, top_brokers_str
    except Exception:
        pass
        
    return None, "數據不足"

# ====================================================
# 6. 技術指標計算輔助
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
# 7. Plotly 互動式走勢圖
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
            subplot_titles=(f"{title_name} 近日走勢與買進訊號", "KD (9, 3, 3)")
        )

        fig.add_trace(go.Candlestick(
            x=plot_df.index.strftime('%Y-%m-%d'),
            open=plot_df['Open'], high=plot_df['High'],
            low=plot_df['Low'], close=plot_df['Close'],
            name="K線", increasing_line_color='#FF4B4B', decreasing_line_color='#00873E'
        ), row=1, col=1)

        fig.add_trace(go.Scatter(x=plot_df.index.strftime('%Y-%m-%d'), y=plot_df['MA20'], mode='lines', name='20MA', line=dict(color='#FFA500', width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index.strftime('%Y-%m-%d'), y=plot_df['MA60'], mode='lines', name='60MA', line=dict(color='#8A2BE2', width=1.5)), row=1, col=1)

        ma_signals = plot_df[plot_df['Signal_MA20']]
        if not ma_signals.empty:
            fig.add_trace(go.Scatter(
                x=ma_signals.index.strftime('%Y-%m-%d'), y=ma_signals['Low'] * 0.985,
                mode='markers', name='突破20MA', marker=dict(symbol='triangle-up', size=11, color='#E60000')
            ), row=1, col=1)

        kd_signals = plot_df[plot_df['Signal_KD']]
        if not kd_signals.empty:
            fig.add_trace(go.Scatter(
                x=kd_signals.index.strftime('%Y-%m-%d'), y=kd_signals['Low'] * 0.97,
                mode='markers', name='KD金叉', marker=dict(symbol='triangle-up', size=9, color='#0066FF')
            ), row=1, col=1)

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
# 8. 主介面分頁導航
# ====================================================
tab1, tab2, tab3 = st.tabs(["🚀 全方位技術量價 & 分點選股", "📊 法人與信用交易排行 Top 20", "⚔️ 主力籌碼對作模型"])

# ----------------------------------------------------
# TAB 1: 全方位技術量價 & 分點集中度選股
# ----------------------------------------------------
with tab1:
    st.subheader("1️⃣ 設定掃描範圍")
    c_m1, c_m2 = st.columns([1, 2])
    with c_m1:
        market_choice = st.radio(
            "範圍選擇",
            ["市值核心股 (30檔)", "全部上市", "全部上櫃", "自訂挑選", "📥 元大/自選清單匯入"],
            index=0
        )

    target_tickers = []
    if market_choice == "市值核心股 (30檔)":
        top30 = ["2330", "2317", "2454", "2382", "2308", "2603", "3711", "2881", "2882", "2891",
                 "2357", "3008", "2886", "2303", "3231", "2412", "2609", "2615", "3034", "3037",
                 "3443", "6415", "3661", "2379", "6669", "2345", "6274", "8069", "3529", "6515"]
        target_tickers = all_stocks_df[all_stocks_df["code"].isin(top30)]["ticker"].tolist()
        st.info(f"已帶入核心焦點股共 {len(target_tickers)} 檔。")
    elif market_choice in ["全部上市", "全部上櫃"]:
        m_tag = "上市" if market_choice == "全部上市" else "上櫃"
        sub = all_stocks_df[all_stocks_df["market"] == m_tag]
        scan_limit = st.slider("掃描檔數 (建議 30~40 檔維持手機順暢度)", 10, len(sub), 25, step=5)
        target_tickers = sub["ticker"].head(scan_limit).tolist()
    elif market_choice == "📥 元大/自選清單匯入":
        import_mode = st.radio("匯入模式", ["文字直接貼上 (推薦)", "上傳 CSV / 檔案"], horizontal=True)
        raw_codes = []
        if import_mode == "文字直接貼上 (推薦)":
            txt = st.text_area("請直接貼上元大 App 複製的文字或股票清單 (含中文字、符號自動精準解析)：", height=80, placeholder="例如：2330 台積電 2317 鴻海 (2454聯發科)")
            if txt:
                raw_codes = re.findall(r'\b\d{4}\b', txt)
        else:
            up_file = st.file_uploader("上傳元大或自選股 CSV / TXT", type=["csv", "txt"])
            if up_file:
                content = up_file.read().decode("utf-8", errors="ignore")
                raw_codes = re.findall(r'\b\d{4}\b', content)

        if raw_codes:
            unique_codes = list(set(raw_codes))
            matched = all_stocks_df[all_stocks_df["code"].isin(unique_codes)]
            target_tickers = matched["ticker"].tolist()
            st.success(f"✅ 成功辨識並匯入 {len(target_tickers)} 檔股票！")
        else:
            st.caption("請貼上文字以載入股票。")
    else:
        selected_display = st.multiselect("搜尋股票 (支援中文或代碼)", options=all_stocks_df["display"].tolist(), default=all_stocks_df["display"].head(5).tolist())
        target_tickers = all_stocks_df[all_stocks_df["display"].isin(selected_display)]["ticker"].tolist()

    st.subheader("2️⃣ 勾選篩選條件")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown("**【均線突破】**")
        chk_ma20 = st.checkbox("近3日突破 20 日線 (月線)", value=True)
        chk_ma60 = st.checkbox("近3日突破 60 日線 (季線)")
        chk_wma30 = st.checkbox("近3週突破 30 週均線")
    with col2:
        st.markdown("**【KD / RSI 指標】**")
        chk_daily_kd = st.checkbox("近3日 KD 黃金交叉", value=True)
        chk_daily_rsi = st.checkbox("近3日 RSI 黃金交叉")
        chk_weekly_kd = st.checkbox("當日/當週 KD 黃金交叉")
        chk_weekly_rsi = st.checkbox("當日/當週 RSI 黃金交叉")
    with col3:
        st.markdown("**【量能與法人門檻】**")
        chk_vol_burst = st.checkbox("今日爆量 (>5日均量)", value=True)
        vol_multiple = st.selectbox("爆量倍數", [1.5, 2.0, 3.0], index=0)
        min_vol_limit = st.number_input("最低成交量 (張)", value=500, step=500)
        chk_trust_buy = st.checkbox("限制投信買超")
        min_trust_lots = st.number_input("投信買超至少 (張)", value=100, step=100, disabled=not chk_trust_buy)
        chk_foreign_buy = st.checkbox("限制外資買超")
        min_foreign_lots = st.number_input("外資買超至少 (張)", value=500, step=200, disabled=not chk_foreign_buy)
    
    with col4:
        st.markdown("**【🔥 券商分點集中買進】**")
        chk_broker_10 = st.checkbox("10日 券商集中買進")
        min_conc_10 = st.number_input("10日 集中度大於 (%)", value=10.0, step=2.0, disabled=not chk_broker_10)
        chk_broker_20 = st.checkbox("20日 券商集中買進")
        min_conc_20 = st.number_input("20日 集中度大於 (%)", value=15.0, step=2.0, disabled=not chk_broker_20)

    if st.button("🚀 開始全方位篩選", use_container_width=True):
        if not target_tickers:
            st.warning("請先選定股票觀察名單！")
        else:
            results = []
            progress_bar = st.progress(0, text="下載走勢與籌碼分點大數據中...")
            
            today_inst_dict = {}
            if not df_inst_all.empty:
                df_today = df_inst_all[df_inst_all["date"] == latest_date]
                for _, r in df_today.iterrows():
                    today_inst_dict[r["code"]] = r

            for idx, ticker in enumerate(target_tickers):
                progress_bar.progress((idx + 1) / len(target_tickers), text=f"分析中 ({idx+1}/{len(target_tickers)}): {ticker}")
                try:
                    code = ticker.split(".")[0]
                    stock = yf.Ticker(ticker)
                    daily_df = stock.history(period="2y")
                    if len(daily_df) < 65:
                        continue

                    # 日線指標計算
                    daily_df = compute_indicators(daily_df)

                    # 週線重組
                    weekly_df = daily_df.resample('W-FRI').agg({
                        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                    }).dropna()
                    weekly_df['WMA30'] = weekly_df['Close'].rolling(30).mean()
                    weekly_df['RSI_6'] = compute_rsi(weekly_df['Close'], 6)
                    weekly_df['RSI_12'] = compute_rsi(weekly_df['Close'], 12)
                    weekly_df = compute_kd(weekly_df, 9)

                    weekly_df['Prev_Close'] = weekly_df['Close'].shift(1)
                    weekly_df['Prev_WMA30'] = weekly_df['WMA30'].shift(1)
                    weekly_df['Prev_K'] = weekly_df['K'].shift(1)
                    weekly_df['Prev_D'] = weekly_df['D'].shift(1)
                    weekly_df['Prev_RSI6'] = weekly_df['RSI_6'].shift(1)
                    weekly_df['Prev_RSI12'] = weekly_df['RSI_12'].shift(1)

                    weekly_df['Signal_WMA30'] = (weekly_df['Prev_Close'] <= weekly_df['Prev_WMA30']) & (weekly_df['Close'] > weekly_df['WMA30'])
                    weekly_df['Signal_W_KD'] = (weekly_df['Prev_K'] <= weekly_df['Prev_D']) & (weekly_df['K'] > weekly_df['D'])
                    weekly_df['Signal_W_RSI'] = (weekly_df['Prev_RSI6'] <= weekly_df['Prev_RSI12']) & (weekly_df['RSI_6'] > weekly_df['RSI_12'])

                    d_today = daily_df.iloc[-1]
                    d_prev = daily_df.iloc[-2]
                    w_today = weekly_df.iloc[-1]

                    inst_info = today_inst_dict.get(code, {"foreign": 0, "trust": 0, "total_inst": 0})
                    today_vol_lots = d_today['Volume'] / 1000

                    recent3_daily = daily_df.tail(3)
                    recent3_weekly = weekly_df.tail(3)

                    pass_filter = True

                    # 1. 均線與技術面檢查
                    if chk_ma20 and not recent3_daily['Signal_MA20'].any(): pass_filter = False
                    if chk_ma60 and not recent3_daily['Signal_MA60'].any(): pass_filter = False
                    if chk_wma30 and not recent3_weekly['Signal_WMA30'].any(): pass_filter = False

                    if chk_daily_kd and not recent3_daily['Signal_KD'].any(): pass_filter = False
                    if chk_daily_rsi and not recent3_daily['Signal_RSI'].any(): pass_filter = False
                    if chk_weekly_kd and not w_today['Signal_W_KD']: pass_filter = False
                    if chk_weekly_rsi and not w_today['Signal_W_RSI']: pass_filter = False

                    # 2. 量能與法人檢查
                    if today_vol_lots < min_vol_limit: pass_filter = False
                    if chk_vol_burst and (d_today['Volume'] < (d_prev['Vol_MA5'] * vol_multiple)): pass_filter = False
                    if chk_trust_buy and inst_info["trust"] < min_trust_lots: pass_filter = False
                    if chk_foreign_buy and inst_info["foreign"] < min_foreign_lots: pass_filter = False

                    # 3. 券商分點集中度檢查 (核心新增)
                    conc_10_val, brokers_10_str = None, "-"
                    conc_20_val, brokers_20_str = None, "-"

                    if pass_filter and (chk_broker_10 or chk_broker_20):
                        if chk_broker_10:
                            conc_10_val, brokers_10_str = fetch_broker_concentration(code, days=10)
                            if conc_10_val is None or conc_10_val < min_conc_10:
                                pass_filter = False
                        
                        if chk_broker_20 and pass_filter:
                            conc_20_val, brokers_20_str = fetch_broker_concentration(code, days=20)
                            if conc_20_val is None or conc_20_val < min_conc_20:
                                pass_filter = False

                    if pass_filter:
                        match = all_stocks_df[all_stocks_df["ticker"] == ticker]
                        stock_title = match["display"].values[0] if not match.empty else ticker
                        
                        row_item = {
                            "ticker": ticker,
                            "股票": stock_title,
                            "收盤價": round(d_today['Close'], 2),
                            "今日成交量(張)": int(today_vol_lots),
                            "外資買賣超(張)": inst_info["foreign"],
                            "投信買賣超(張)": inst_info["trust"],
                            "日K / 日D": f"{round(d_today['K'], 1)} / {round(d_today['D'], 1)}"
                        }
                        if chk_broker_10:
                            row_item["10日集中度"] = f"{conc_10_val}%" if conc_10_val is not None else "-"
                            row_item["10日主要吃貨券商"] = brokers_10_str
                        if chk_broker_20:
                            row_item["20日集中度"] = f"{conc_20_val}%" if conc_20_val is not None else "-"
                            row_item["20日主要吃貨券商"] = brokers_20_str

                        results.append(row_item)
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
        p_choice = st.radio("統計天期", ["每日 (當日)", "三日累積", "每週 (五日累積)"], horizontal=True, key="rank_period")
        day_n = {"每日 (當日)": 1, "三日累積": 3, "每週 (五日累積)": 5}[p_choice]
        use_dates = trade_dates[:day_n]

        sub_inst = df_inst_all[df_inst_all["date"].isin(use_dates)]
        grouped = sub_inst.groupby(["code", "name"])[["foreign", "trust", "total_inst"]].sum().reset_index()

        col_name = "foreign" if "外資" in rank_target else "trust"
        is_buy = "買超" in rank_target
        sorted_df = grouped.sort_values(by=col_name, ascending=not is_buy).head(20).copy()
        sorted_df.rename(columns={
            "code": "代碼", "name": "名稱",
            "foreign": f"外資買賣超(張)", "trust": f"投信買賣超(張)", "total_inst": f"三大法人合計(張)"
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
# TAB 3: 主力籌碼對作模型 (支援每日、三日、五日累積)
# ----------------------------------------------------
with tab3:
    st.subheader("🎯 主力與散戶多空對作累計篩選 (前 20 名)")
    
    model_period = st.radio(
        "選擇統計累積天期：",
        ["每日 (當日)", "三日累積", "每週 (五日累積)"],
        horizontal=True,
        key="model_period_choice"
    )
    period_days = {"每日 (當日)": 1, "三日累積": 3, "每週 (五日累積)": 5}[model_period]
    active_dates = trade_dates[:period_days]
    period_label = "當日" if period_days == 1 else f"近{period_days}日"

    st.caption(f"目前累計交易日：{', '.join(active_dates)} (共 {len(active_dates)} 天)")

    model_choice = st.selectbox(
        "選擇籌碼動態篩選模式：",
        [
            f"🔥 外資買超 + 融資減少 + 借券減少 ({period_label} 外資進場/空單大補/籌碼極度安定)",
            f"⚠️ 外資賣超 + 融資增加 ({period_label} 主力出貨/散戶接刀)",
            f"🚀 投信買超 + 融資減少 ({period_label} 投信認養/浮額洗清)",
            f"⚠️ 投信賣超 + 融資增加 ({period_label} 投信倒貨/散戶承接)",
            f"💎 外資買超 + 投信買超 ({period_label} 土洋齊買/合力抬轎)",
            f"⚔️ 外資買超 + 投信賣超 ({period_label} 土洋對作/多空分歧)"
        ]
    )

    sub_inst = df_inst_all[df_inst_all["date"].isin(active_dates)]
    inst_agg = sub_inst.groupby(["code", "name"])[["foreign", "trust", "total_inst"]].sum().reset_index()

    sub_margin = df_margin_all[df_margin_all["date"].isin(active_dates)]
    margin_agg = sub_margin.groupby("code")[["margin_diff", "sbl_diff", "sbl_short_sell"]].sum().reset_index()

    pool_df = pd.merge(inst_agg, margin_agg, on="code", how="inner")
    
    col_foreign = f"{period_label}外資(張)"
    col_trust = f"{period_label}投信(張)"
    col_margin_diff = f"{period_label}融資增減(張)"
    col_sbl_diff = f"{period_label}借券賣出增減(張)"
    col_sbl_sell = f"{period_label}借券賣出量(張)"

    pool_df.rename(columns={
        "code": "代碼",
        "name": "名稱",
        "foreign": col_foreign,
        "trust": col_trust,
        "margin_diff": col_margin_diff,
        "sbl_diff": col_sbl_diff,
        "sbl_short_sell": col_sbl_sell
    }, inplace=True)

    if not pool_df.empty:
        out_df = pd.DataFrame()
        
        if "外資買超 + 融資減少 + 借券減少" in model_choice:
            cond = (pool_df[col_foreign] > 0) & (pool_df[col_margin_diff] < 0) & (pool_df[col_sbl_diff] < 0)
            out_df = pool_df[cond].sort_values(by=col_foreign, ascending=False).head(20)

        elif "外資賣超 + 融資增加" in model_choice:
            cond = (pool_df[col_foreign] < 0) & (pool_df[col_margin_diff] > 0)
            out_df = pool_df[cond].sort_values(by=col_foreign, ascending=True).head(20)

        elif "投信買超 + 融資減少" in model_choice:
            cond = (pool_df[col_trust] > 0) & (pool_df[col_margin_diff] < 0)
            out_df = pool_df[cond].sort_values(by=col_trust, ascending=False).head(20)

        elif "投信賣超 + 融資增加" in model_choice:
            cond = (pool_df[col_trust] < 0) & (pool_df[col_margin_diff] > 0)
            out_df = pool_df[cond].sort_values(by=col_trust, ascending=True).head(20)

        elif "外資買超 + 投信買超" in model_choice:
            cond = (pool_df[col_foreign] > 0) & (pool_df[col_trust] > 0)
            pool_df["雙法人合買"] = pool_df[col_foreign] + pool_df[col_trust]
            out_df = pool_df[cond].sort_values(by="雙法人合買", ascending=False).head(20).drop(columns=["雙法人合買"])

        elif "外資買超 + 投信賣超" in model_choice:
            cond = (pool_df[col_foreign] > 0) & (pool_df[col_trust] < 0)
            out_df = pool_df[cond].sort_values(by=col_foreign, ascending=False).head(20)

        if not out_df.empty:
            out_df.reset_index(drop=True, inplace=True)
            out_df.index += 1
            st.success(f"🎯 符合「{model_choice}」共 **{len(out_df)}** 檔：")
            st.dataframe(out_df, use_container_width=True)

            st.divider()
            st.subheader("📈 點選查看該股互動走勢圖")
            code_opts = {r["代碼"]: f"{r['代碼']} {r['名稱']}" for _, r in out_df.iterrows()}
            chosen = st.selectbox("選擇要繪製走勢圖的股票：", list(code_opts.keys()), format_func=lambda x: code_opts[x], key="model_chart_select")
            if chosen:
                plot_stock_chart(f"{chosen}.TW", code_opts[chosen])
        else:
            st.warning(f"在 {period_label} 的統計期間內，暫無符合該條件的標的。")
