import datetime
import io
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st
import yfinance as yf

# 頁面設定
st.set_page_config(
    page_title="台股全方位選股與籌碼監控系統",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("📈 台股全方位選股與主力籌碼監控系統")


# 載入全市場股票清單
@st.cache_data(ttl=86400)
def fetch_tw_stock_universe():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    stocks = []
    modes = [("2", "上市", ".TW"), ("4", "上櫃", ".TWO")]
    for mode, market_type, suffix in modes:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = "cp950"
            dfs = pd.read_html(io.StringIO(resp.text))
            if dfs:
                df = dfs[0]
                df.columns = df.iloc[0]
                df = df.iloc[1:].copy()
                for item in df["有價證券代號及名稱"]:
                    if isinstance(item, str) and "\u3000" in item:
                        parts = item.split("\u3000")
                        code = parts[0].strip()
                        name = parts.strip()
                        if len(code) == 4 and code.isdigit():
                            stocks.append(
                                {
                                    "code": code,
                                    "name": name,
                                    "display": f"{code} {name} ({market_type})",
                                    "market": market_type,
                                    "yf_symbol": f"{code}{suffix}",
                                }
                            )
        except Exception:
            pass
    df_res = pd.DataFrame(stocks)
    if df_res.empty:
        df_res = pd.DataFrame(
            [
                {
                    "code": "2330",
                    "name": "台積電",
                    "display": "2330 台積電 (上市)",
                    "market": "上市",
                    "yf_symbol": "2330.TW",
                },
                {
                    "code": "2317",
                    "name": "鴻海",
                    "display": "2317 鴻海 (上市)",
                    "market": "上市",
                    "yf_symbol": "2317.TW",
                },
                {
                    "code": "2454",
                    "name": "聯發科",
                    "display": "2454 聯發科 (上市)",
                    "market": "上市",
                    "yf_symbol": "2454.TW",
                },
            ]
        )
    return df_res


universe_df = fetch_tw_stock_universe()


# 計算技術指標
def calculate_indicators(df):
    data = df.copy()
    close = data["Close"]
    high = data["High"]
    low = data["Low"]

    data["MA20"] = close.rolling(20).mean()
    data["MA60"] = close.rolling(60).mean()
    data["MA150"] = close.rolling(150).mean()

    # KD 指標
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = ((close - low9) / (high9 - low9) * 100).fillna(50)
    k_vals, d_vals = [50.0], [50.0]
    for r in rsv:
        k_vals.append((2 / 3) * k_vals[-1] + (1 / 3) * r)
        d_vals.append((2 / 3) * d_vals[-1] + (1 / 3) * k_vals[-1])
    data["K"] = k_vals[1:]
    data["D"] = d_vals[1:]

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    rs6 = gain.rolling(6).mean() / loss.rolling(6).mean().replace(0, np.nan)
    data["RSI6"] = 100 - (100 / (1 + rs6))
    rs12 = gain.rolling(12).mean() / loss.rolling(12).mean().replace(0, np.nan)
    data["RSI12"] = 100 - (100 / (1 + rs12))

    data["Cross_20MA"] = (data["Close"] > data["MA20"]) & (
        data["Close"].shift(1) <= data["MA20"].shift(1)
    )
    data["Cross_60MA"] = (data["Close"] > data["MA60"]) & (
        data["Close"].shift(1) <= data["MA60"].shift(1)
    )
    data["Cross_30W"] = (data["Close"] > data["MA150"]) & (
        data["Close"].shift(1) <= data["MA150"].shift(1)
    )
    data["KD_Gold"] = (data["K"] > data["D"]) & (
        data["K"].shift(1) <= data["D"].shift(1)
    )
    data["RSI_Gold"] = (data["RSI6"] > data["RSI12"]) & (
        data["RSI6"].shift(1) <= data["RSI12"].shift(1)
    )
    return data


# 抓取籌碼資料
@st.cache_data(ttl=1800)
def fetch_twse_daily_data(target_date_str=None):
    if target_date_str is None:
        target_date_str = datetime.datetime.now().strftime("%Y%m%d")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    t86_url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={target_date_str}&selectType=ALLBUT0999&response=json"
    margin_url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={target_date_str}&selectType=ALL&response=json"
    twt93u_url = f"https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U?date={target_date_str}&response=json"

    result = {}
    try:
        r1 = requests.get(t86_url, headers=headers, timeout=10).json()
        if r1.get("stat") == "OK" and "data" in r1:
            cols = [
                c.replace(" ", "").replace("<br>", "") for c in r1["fields"]
            ]
            df_inst = pd.DataFrame(r1["data"], columns=cols)
            df_inst = df_inst.rename(
                columns={
                    cols[0]: "code",
                    cols: "name",
                    cols: "foreign_diff",
                    cols[10]: "trust_diff",
                }
            )
            for col in ["foreign_diff", "trust_diff"]:
                if col in df_inst.columns:
                    df_inst[col] = (
                        df_inst[col]
                        .astype(str)
                        .str.replace(",", "")
                        .apply(pd.to_numeric, errors="coerce")
                        .fillna(0)
                        / 1000
                    )
            result["institutional"] = df_inst[
                ["code", "name", "foreign_diff", "trust_diff"]
            ]
    except Exception:
        result["institutional"] = pd.DataFrame()

    try:
        r2 = requests.get(margin_url, headers=headers, timeout=10).json()
        if r2.get("stat") == "OK" and "tables" in r2:
            table = next(
                (t for t in r2["tables"] if "信用交易統計" in t.get("title", "")),
                r2["tables"][0],
            )
            fields = [
                f.replace(" ", "").replace("<br>", "") for f in table["fields"]
            ]
            df_margin = pd.DataFrame(table["data"], columns=fields)
            clean_df = pd.DataFrame()
            clean_df["code"] = df_margin.iloc[:, 0].astype(str).str.strip()
            clean_df["name"] = df_margin.iloc.astype(str).str.strip()
            clean_df["margin_buy"] = (
                df_margin.iloc
                .astype(str)
                .str.replace(",", "")
                .apply(pd.to_numeric, errors="coerce")
                .fillna(0)
            )
            clean_df["margin_diff"] = (
                df_margin.iloc[:, 5]
                .astype(str)
                .str.replace(",", "")
                .apply(pd.to_numeric, errors="coerce")
                .fillna(0)
            )
            clean_df["short_sell"] = (
                df_margin.iloc[:, 8]
                .astype(str)
                .str.replace(",", "")
                .apply(pd.to_numeric, errors="coerce")
                .fillna(0)
            )
            clean_df["short_diff"] = (
                df_margin.iloc[:, 11]
                .astype(str)
                .str.replace(",", "")
                .apply(pd.to_numeric, errors="coerce")
                .fillna(0)
            )
            result["margin"] = clean_df
    except Exception:
        result["margin"] = pd.DataFrame()

    try:
        r3 = requests.get(twt93u_url, headers=headers, timeout=10).json()
        if r3.get("stat") == "OK" and "data" in r3:
            cols = [
                c.replace(" ", "").replace("<br>", "") for c in r3["fields"]
            ]
            df_borrow = pd.DataFrame(r3["data"], columns=cols)
            clean_borrow = pd.DataFrame()
            clean_borrow["code"] = (
                df_borrow.iloc[:, 0].astype(str).str.strip()
            )
            clean_borrow["name"] = (
                df_borrow.iloc.astype(str).str.strip()
            )
            clean_borrow["borrow_sell"] = (
                df_borrow.iloc[:, 11]
                .astype(str)
                .str.replace(",", "")
                .apply(pd.to_numeric, errors="coerce")
                .fillna(0)
                / 1000
            )
            result["borrow"] = clean_borrow
    except Exception:
        result["borrow"] = pd.DataFrame()

    return result


def get_integrated_chip_summary():
    for delta_days in range(0, 5):
        chk_date = (
            datetime.datetime.now() - datetime.timedelta(days=delta_days)
        ).strftime("%Y%m%d")
        data = fetch_twse_daily_data(chk_date)
        if (
            not data.get("institutional", pd.DataFrame()).empty
            and not data.get("margin", pd.DataFrame()).empty
        ):
            inst = data["institutional"]
            margin = data["margin"]
            borrow = data.get("borrow", pd.DataFrame())
            merged = pd.merge(inst, margin, on=["code", "name"], how="inner")
            if not borrow.empty:
                merged = pd.merge(
                    merged,
                    borrow[["code", "borrow_sell"]],
                    on="code",
                    how="left",
                )
            else:
                merged["borrow_sell"] = 0
            merged["borrow_sell"] = merged["borrow_sell"].fillna(0)
            return merged, chk_date
    return pd.DataFrame(), datetime.datetime.now().strftime("%Y%m%d")


# Plotly 繪圖
def plot_candlestick(df, title=""):
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.7, 0.3],
    )
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="K線",
            increasing_line_color="#eb4d4b",
            increasing_fillcolor="#eb4d4b",
            decreasing_line_color="#6ab04c",
            decreasing_fillcolor="#6ab04c",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["MA20"],
            line=dict(color="#f0932b", width=1.5),
            name="20MA",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["MA60"],
            line=dict(color="#22a6b3", width=1.5),
            name="60MA",
        ),
        row=1,
        col=1,
    )

    sig_20ma = df[df["Cross_20MA"]]
    if not sig_20ma.empty:
        fig.add_trace(
            go.Scatter(
                x=sig_20ma.index,
                y=sig_20ma["Low"] * 0.98,
                mode="markers",
                marker=dict(symbol="triangle-up", size=10, color="#e056fd"),
                name="突破20MA",
            ),
            row=1,
            col=1,
        )

    sig_kd = df[df["KD_Gold"]]
    if not sig_kd.empty:
        fig.add_trace(
            go.Scatter(
                x=sig_kd.index,
                y=sig_kd["Low"] * 0.96,
                mode="markers",
                marker=dict(symbol="triangle-up", size=8, color="#f9ca24"),
                name="KD金叉",
            ),
            row=1,
            col=1,
        )

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["K"],
            line=dict(color="#e74c3c", width=1.2),
            name="K",
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["D"],
            line=dict(color="#3498db", width=1.2),
            name="D",
        ),
        row=2,
        col=1,
    )
    fig.add_hline(
        y=80,
        line_dash="dash",
        line_color="#95a5a6",
        line_width=1,
        row=2,
        col=1,
    )
    fig.add_hline(
        y=20,
        line_dash="dash",
        line_color="#95a5a6",
        line_width=1,
        row=2,
        col=1,
    )
    fig.update_xaxes(
        rangebreaks=[dict(bounds=["sat", "mon"])], rangeslider_visible=False
    )
    fig.update_layout(
        title=title,
        height=600,
        margin=dict(l=30, r=30, t=40, b=30),
        template="plotly_white",
        hovermode="x unified",
    )
    return fig


# 介面分頁
tab1, tab2, tab3, tab4 = st.tabs(
    [
        "📊 個股K線",
        "🏆 籌碼排行Top20",
        "⚔️ 主力對作模型",
        "🔍 跨週期快篩",
    ]
)

with tab1:
    st.subheader("個股雙層 K 線圖")
    selected_option = st.selectbox(
        "選擇股票：", universe_df["display"].tolist(), index=0
    )
    selected_stock = universe_df[
        universe_df["display"] == selected_option
    ].iloc[0]
    period = st.selectbox("歷史區間", ["3mo", "6mo", "1y"], index=1)
    with st.spinner("讀取行情中..."):
        df_k = yf.Ticker(selected_stock["yf_symbol"]).history(period=period)
        if not df_k.empty and len(df_k) >= 20:
            df_k = calculate_indicators(df_k)
            st.plotly_chart(
                plot_candlestick(df_k, title=selected_stock["display"]),
                use_container_width=True,
            )
        else:
            st.error("暫無足夠行情資料。")

with tab2:
    st.subheader("主力籌碼排行榜 (Top 20)")
    chip_df, data_date = get_integrated_chip_summary()
    st.caption(f"資料統計日期：{data_date}")
    if not chip_df.empty:
        rank_type = st.selectbox(
            "排行榜項目：",
            [
                "外資買超前 20 名",
                "投信買超前 20 名",
                "融資增加前 20 名",
                "融券賣出前 20 名",
                "借券賣出前 20 名",
            ],
        )
        if "外資買超" in rank_type:
            show = chip_df.sort_values(by="foreign_diff", ascending=False)
        elif "投信買超" in rank_type:
            show = chip_df.sort_values(by="trust_diff", ascending=False)
        elif "融資增加" in rank_type:
            show = chip_df.sort_values(by="margin_diff", ascending=False)
        elif "融券賣出" in rank_type:
            show = chip_df.sort_values(by="short_sell", ascending=False)
        else:
            show = chip_df.sort_values(by="borrow_sell", ascending=False)
        st.dataframe(show.head(20), use_container_width=True)
    else:
        st.info("非交易時間或暫無籌碼資料。")

with tab3:
    st.subheader("主力對作與動向模型 (Top 20)")
    chip_df, data_date = get_integrated_chip_summary()
    st.caption(f"資料統計日期：{data_date}")
    if not chip_df.empty:
        model = st.selectbox(
            "動向模型：",
            [
                "土洋齊買 (外資買超 + 投信買超)",
                "土洋對作 (外資買超 + 投信賣超)",
                "外資吃貨散戶退 (外資買超 + 融資減少)",
                "外資倒貨散戶接 (外資賣超 + 融資增加)",
            ],
        )
        if "土洋齊買" in model:
            cond = (chip_df["foreign_diff"] > 0) & (chip_df["trust_diff"] > 0)
            chip_df["total_buy"] = (
                chip_df["foreign_diff"] + chip_df["trust_diff"]
            )
            filtered = chip_df[cond].sort_values(
                by="total_buy", ascending=False
            )
        elif "土洋對作" in model:
            cond = (chip_df["foreign_diff"] > 0) & (chip_df["trust_diff"] < 0)
            filtered = chip_df[cond].sort_values(
                by="foreign_diff", ascending=False
            )
        elif "外資吃貨散戶退" in model:
            cond = (chip_df["foreign_diff"] > 0) & (chip_df["margin_diff"] < 0)
            filtered = chip_df[cond].sort_values(
                by="foreign_diff", ascending=False
            )
        else:
            cond = (chip_df["foreign_diff"] < 0) & (chip_df["margin_diff"] > 0)
            filtered = chip_df[cond].sort_values(
                by="foreign_diff", ascending=True
            )
        st.dataframe(filtered.head(20), use_container_width=True)
    else:
        st.info("非交易時間或暫無籌碼資料。")

with tab4:
    st.subheader("技術面與量能倍增快篩")
    c_ma20 = st.checkbox("突破 20 日線 (月線)", value=True)
    c_kd = st.checkbox("日 KD 黃金交叉", value=True)
    vol_mult = st.selectbox(
        "量能倍增門檻 (相對前5日均量)：", [1.0, 1.5, 2.0], index=1
    )

    tw50_codes = [
        "2330",
        "2317",
        "2454",
        "2308",
        "2382",
        "2412",
        "2881",
        "2882",
        "2303",
        "2891",
        "3711",
        "3231",
        "2886",
        "2884",
        "1216",
        "2892",
    ]
    if st.button("開始篩選", use_container_width=True):
        match_list = []
        bar = st.progress(0)
        for idx, code in enumerate(tw50_codes):
            bar.progress((idx + 1) / len(tw50_codes))
            row = universe_df[universe_df["code"] == code]
            if row.empty:
                continue
            item = row.iloc[0]
            try:
                df = yf.Ticker(item["yf_symbol"]).history(period="6mo")
                if len(df) < 30:
                    continue
                df = calculate_indicators(df)
                latest = df.iloc[-1]
                vol_avg = df["Volume"].iloc[-6:-1].mean()
                if c_ma20 and not latest["Cross_20MA"]:
                    continue
                if c_kd and not latest["KD_Gold"]:
                    continue
                if vol_mult > 1.0 and latest["Volume"] < (vol_avg * vol_mult):
                    continue
                match_list.append(
                    {
                        "代碼": code,
                        "名稱": item["name"],
                        "最新收盤價": f"{latest['Close']:.2f}",
                        "成交量(張)": int(latest["Volume"] / 1000),
                        "K / D": f"{latest['K']:.1f} / {latest['D']:.1f}",
                    }
                )
            except Exception:
                continue
        bar.empty()
        if match_list:
            st.success(f"找到 {len(match_list)} 檔符合條件標的：")
            st.dataframe(pd.DataFrame(match_list), use_container_width=True)
        else:
            st.warning("查無符合條件標的。")

