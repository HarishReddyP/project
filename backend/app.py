from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import sqlite3
from datetime import datetime
from twilio.rest import Client
import smtplib
from email.mime.text import MIMEText
import uvicorn
import os
from fastapi.staticfiles import StaticFiles

TICKERS_FILE = "tickers.txt"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def init_db():
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        signal TEXT,
        price REAL,
        rsi REAL,
        macd_trend TEXT,
        volume INTEGER,
        ema_status TEXT,
        strength TEXT,
        timestamp TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

SENDGRID_FROM_EMAIL = "Palugulla.harishreddy225@gmail.com"
SENDGRID_TO_EMAIL = "harish.palugulla225@gmail.com"
TWILIO_SID = "AC03ef71bdf590764c8cb7bab4fde4121d"
TWILIO_AUTH = "eaf6f74684fa589209075e75bd03aa05"
TWILIO_FROM = "+15705359734"
TWILIO_TO = "+15705359734"

def send_email_alert(content):
    try:
        msg = MIMEText(content)
        msg['Subject'] = 'Stock Signal Alert'
        msg['From'] = SENDGRID_FROM_EMAIL
        msg['To'] = SENDGRID_TO_EMAIL
        with smtplib.SMTP('smtp.sendgrid.net', 587) as server:
            server.login("apikey", "your_sendgrid_api_key")
            server.sendmail(SENDGRID_FROM_EMAIL, [SENDGRID_TO_EMAIL], msg.as_string())
        print("Email sent:", content)
    except Exception as e:
        print(f"Email error: {e}")

def read_tickers():
    if not os.path.exists(TICKERS_FILE):
        return []
    with open(TICKERS_FILE, "r") as f:
        return [line.strip().upper() for line in f if line.strip()]

def save_ticker(ticker):
    tickers = read_tickers()
    ticker = ticker.upper()
    if ticker not in tickers:
        with open(TICKERS_FILE, "a") as f:
            f.write(ticker + "\n")

def send_sms_alert(content):
    try:
        client = Client(TWILIO_SID, TWILIO_AUTH)
        message = client.messages.create(
            body=content,
            from_=TWILIO_FROM,
            to=TWILIO_TO
        )
        print("SMS sent:", content)
    except Exception as e:
        print(f"SMS error: {e}")

def store_signal(row):
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("SELECT signal FROM signals WHERE ticker = ? ORDER BY timestamp DESC LIMIT 1", (row["ticker"],))
    last = c.fetchone()
    if not last or last[0] != row["signal"]:
        c.execute(
            "INSERT INTO signals (ticker, signal, price, rsi, macd_trend, volume, ema_status, strength, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["ticker"],
                row["signal"],
                row["price"],
                row["rsi"],
                row["macd_trend"],
                row["volume"],
                row["ema_status"],
                row["strength"],
                row["timestamp"],
            ),
        )
        conn.commit()
    conn.close()

def get_signals(filtered_signal: str = None, strength: str = None):
    results = []
    tickers = read_tickers()
    print("Fetching signals...")
    for ticker in tickers:
        try:
            print(f"Downloading data for: {ticker}")
            data = yf.download(ticker, period="3mo", interval="1d", progress=False)
            print(f"Data rows for {ticker}: {len(data)}")

            if data.empty or len(data) < 50:
                print(f"Skipping {ticker}: Not enough data")
                continue

            data["EMA50"] = data["Close"].ewm(span=50, adjust=False).mean()
            data["MACD"] = data["Close"].ewm(span=12, adjust=False).mean() - data["Close"].ewm(span=26, adjust=False).mean()
            data["Signal"] = data["MACD"].ewm(span=9, adjust=False).mean()
            delta = data["Close"].diff()
            gain = delta.where(delta > 0, 0).rolling(window=14).mean()
            loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
            rs = gain / loss
            data["RSI"] = 100 - (100 / (1 + rs))
            data["Volume_MA"] = data["Volume"].rolling(window=10).mean()
            data.dropna(inplace=True)

            last_close = data["Close"].iloc[-1].item()
            last_ema50 = data["EMA50"].iloc[-1].item()
            last_macd = data["MACD"].iloc[-1].item()
            last_signal = data["Signal"].iloc[-1].item()
            last_rsi = data["RSI"].iloc[-1].item()
            last_volume = data["Volume"].iloc[-1].item()
            last_volume_ma = data["Volume_MA"].iloc[-1].item()

            signal = "HOLD"
            strength_val = "Weak"

            if last_close > last_ema50 and last_macd > last_signal and 50 < last_rsi < 70:
                signal = "BUY"
                strength_val = "Strong" if last_volume > last_volume_ma else "Moderate"
            elif last_close < last_ema50 and last_macd < last_signal and last_rsi < 45:
                signal = "SELL"
                strength_val = "Strong" if last_volume > last_volume_ma else "Moderate"

            if (not filtered_signal or signal == filtered_signal) and (not strength or strength_val == strength):
                row = {
                    "ticker": ticker,
                    "signal": signal,
                    "price": round(last_close, 2),
                    "rsi": round(last_rsi, 2),
                    "macd_trend": "Bullish" if last_macd > last_signal else "Bearish",
                    "volume": int(last_volume),
                    "ema_status": "Above EMA50" if last_close > last_ema50 else "Below EMA50",
                    "strength": strength_val,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                print("Generated signal:", row)
                store_signal(row)
                if signal in ["BUY", "SELL"] and strength_val == "Strong":
                    alert = f"{ticker} signal: {signal} at ${row['price']} [{row['timestamp']}]"
                    send_email_alert(alert)
                    send_sms_alert(alert)
                results.append(row)
        except Exception as e:
            print(f"Error processing {ticker}: {e}")
    print(f"Total signals generated: {len(results)}")
    return results

@app.get("/signals")
def read_signals(signal: str = Query(None), strength: str = Query(None)):
    return get_signals(signal, strength)

@app.get("/backtest")
def run_backtest():
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    c.execute("SELECT ticker, signal, price, timestamp FROM signals ORDER BY timestamp ASC")
    rows = c.fetchall()
    conn.close()
    trades = {}
    for row in rows:
        ticker, signal, price, ts = row
        if ticker not in trades:
            trades[ticker] = {"position": None, "entry": 0.0, "pl": 0.0}
        if signal == "BUY" and trades[ticker]["position"] is None:
            trades[ticker]["position"] = "LONG"
            trades[ticker]["entry"] = price
        elif signal == "SELL" and trades[ticker]["position"] == "LONG":
            entry = trades[ticker]["entry"]
            pnl = ((price - entry) / entry) * 1000
            trades[ticker]["pl"] += pnl
            trades[ticker]["position"] = None
    return [{"ticker": t, "total_pl": round(v["pl"], 2)} for t, v in trades.items()]

@app.get("/signal_history")
def signal_history(ticker: str = None, start_date: str = None, end_date: str = None):
    conn = sqlite3.connect("signals.db")
    c = conn.cursor()
    query = "SELECT ticker, signal, price, rsi, macd_trend, volume, ema_status, strength, timestamp FROM signals WHERE 1=1"
    params = []
    if ticker:
        query += " AND ticker = ?"
        params.append(ticker.upper())
    if start_date:
        query += " AND date(timestamp) >= date(?)"
        params.append(start_date)
    if end_date:
        query += " AND date(timestamp) <= date(?)"
        params.append(end_date)
    query += " ORDER BY timestamp DESC"
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [
        {
            "ticker": row[0],
            "signal": row[1],
            "price": row[2],
            "rsi": row[3],
            "macd_trend": row[4],
            "volume": row[5],
            "ema_status": row[6],
            "strength": row[7],
            "timestamp": row[8],
        }
        for row in rows
    ]

@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.get("/tickers")
def get_tickers():
    return read_tickers()

@app.post("/tickers")
def add_ticker(ticker: str):
    save_ticker(ticker)
    return {"status": f"{ticker.upper()} added"}

app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
