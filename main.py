import os
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("signal-gold")

# Secrets/configuration are read only from environment variables.
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
TWELVE_DATA_SYMBOL = os.getenv("TWELVE_DATA_SYMBOL", "XAU/USD")

client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
latest_report: dict[str, Any] | None = None

TIMEFRAMES = {"M1": "1min", "M5": "5min", "M15": "15min", "H1": "1h"}

class AnalyzeRequest(BaseModel):
    timeframe: str = "M15"

def require_config():
    missing = []
    for key, value in [
        ("TWELVE_DATA_API_KEY", TWELVE_DATA_API_KEY),
        ("OPENAI_API_KEY", OPENAI_API_KEY),
    ]:
        if not value:
            missing.append(key)
    if missing:
        raise HTTPException(status_code=503, detail="Environment variable belum diisi: " + ", ".join(missing))

async def fetch_gold_candles(timeframe: str):
    require_config()
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Timeframe harus M1, M5, M15, atau H1.")
    params = {
        "symbol": TWELVE_DATA_SYMBOL,
        "interval": TIMEFRAMES[timeframe],
        "outputsize": 80,
        "apikey": TWELVE_DATA_API_KEY,
        "timezone": "UTC",
        "order": "ASC",
    }
    url = "https://api.twelvedata.com/time_series"
    async with httpx.AsyncClient(timeout=25) as http:
        response = await http.get(url, params=params)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Penyedia data pasar gagal merespons.")
    data = response.json()
    if data.get("status") == "error" or not data.get("values"):
        message = data.get("message", "Data candle tidak tersedia.")
        raise HTTPException(status_code=502, detail=f"API data pasar: {message}")
    candles = []
    for item in data["values"]:
        try:
            candles.append({
                "time": item["datetime"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item.get("volume") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if len(candles) < 25:
        raise HTTPException(status_code=502, detail="Jumlah candle dari API kurang untuk analisis.")
    return candles

def ema(values, period):
    alpha = 2 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result

def calc_metrics(candles):
    closes = [c["close"] for c in candles]
    e9 = ema(closes[-30:], 9)
    e21 = ema(closes[-60:], 21) if len(closes) >= 60 else ema(closes, 21)
    diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    recent = diffs[-14:]
    gains = sum(max(x, 0) for x in recent) / len(recent)
    losses = sum(abs(min(x, 0)) for x in recent) / len(recent)
    rsi = 100 if losses == 0 else 100 - 100 / (1 + gains / losses)
    avg_range = sum(c["high"] - c["low"] for c in candles[-14:]) / 14
    if e9 > e21 and closes[-1] > e9:
        regime = "Bullish"
    elif e9 < e21 and closes[-1] < e9:
        regime = "Bearish"
    else:
        regime = "Sideways / Mixed"
    return {
        "last_price": closes[-1],
        "ema_9": round(e9, 5),
        "ema_21": round(e21, 5),
        "rsi_14": round(rsi, 2),
        "average_candle_range_14": round(avg_range, 5),
        "technical_regime": regime,
        "last_candle_time_utc": candles[-1]["time"],
    }

async def ai_analysis(timeframe, candles, metrics):
    if not client:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY belum dikonfigurasi.")
    input_data = {
        "symbol": TWELVE_DATA_SYMBOL,
        "timeframe": timeframe,
        "metrics": metrics,
        "recent_candles": candles[-20:],
    }
    instructions = """
Anda adalah analis EDUKASI pasar. Analisis data OHLC yang diberikan saja.
Jangan mengarang data atau menyatakan harga ini real-time bila timestamp tidak mendukung.
Jangan memberikan rekomendasi BUY/SELL, entry, stop-loss, take-profit, leverage,
atau ajakan melakukan transaksi. Jangan membuat prediksi pasti.
Kembalikan JSON valid berisi:
trend: salah satu Bullish, Bearish, Sideways, Mixed
momentum: Menguat, Melemah, Netral
volatility: Rendah, Sedang, Tinggi
observations: array 2 sampai 4 pengamatan singkat dalam Bahasa Indonesia
limitations: satu kalimat keterbatasan analisis.
"""
    try:
        result = await client.responses.create(
            model=OPENAI_MODEL,
            instructions=instructions,
            input=json.dumps(input_data, ensure_ascii=False),
        )
        return json.loads(result.output_text.strip())
    except Exception as exc:
        log.exception("OpenAI request failed")
        raise HTTPException(status_code=502, detail="Analisis OpenAI gagal. Periksa API key, kuota, dan log Railway.") from exc

async def run_analysis(timeframe):
    global latest_report
    candles = await fetch_gold_candles(timeframe)
    metrics = calc_metrics(candles)
    report = await ai_analysis(timeframe, candles, metrics)
    latest_report = {
        "symbol": TWELVE_DATA_SYMBOL,
        "timeframe": timeframe,
        "price": metrics["last_price"],
        "metrics": metrics,
        "analysis": report,
        "data_timestamp_utc": metrics["last_candle_time_utc"],
        "mode": "EDUCATIONAL MARKET ANALYSIS",
    }
    return latest_report

async def telegram_send(chat_id, message):
    if not TELEGRAM_BOT_TOKEN:
        log.warning("Telegram token not configured; message not sent.")
        return
    async with httpx.AsyncClient(timeout=20) as http:
        res = await http.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        )
        if res.status_code >= 400:
            log.error("Telegram send failed: %s", res.text[:300])

def report_text():
    if not latest_report:
        return (
            "SIGNAL GOLD EDITION\n\nBelum ada analisis. Kirim /market untuk mengambil "
            "data pasar dan menjalankan analisis edukasi."
        )
    m = latest_report["metrics"]
    a = latest_report["analysis"]
    obs = "\n".join("• " + str(x) for x in a.get("observations", []))
    return (
        "SIGNAL GOLD EDITION — LAPORAN EDUKASI\n\n"
        f"Pair: {latest_report['symbol']}\n"
        f"Timeframe: {latest_report['timeframe']}\n"
        f"Harga candle terakhir: {latest_report['price']}\n"
        f"Waktu candle (UTC): {latest_report['data_timestamp_utc']}\n"
        f"Trend: {a.get('trend', 'N/A')}\n"
        f"Momentum: {a.get('momentum', 'N/A')}\n"
        f"Volatilitas: {a.get('volatility', 'N/A')}\n"
        f"RSI(14): {m['rsi_14']}\n\n"
        f"Pengamatan:\n{obs}\n\n"
        f"{a.get('limitations', '')}\n\n"
        "Bukan rekomendasi transaksi atau jaminan hasil."
    )

app = FastAPI(title="SIGNAL GOLD EDITION", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, replace with your exact CodePen/site origin.
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Telegram-Bot-Api-Secret-Token"],
)

@app.get("/")
async def home():
    return {"app": "SIGNAL GOLD EDITION", "status": "online", "mode": "educational"}

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/api/market")
async def market(timeframe: str = "M15"):
    return await run_analysis(timeframe)

@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest):
    return await run_analysis(payload.timeframe)

@app.get("/api/latest")
async def latest():
    if latest_report is None:
        return {"available": False, "message": "Belum ada analisis. Panggil /api/market."}
    return {"available": True, **latest_report}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if TELEGRAM_WEBHOOK_SECRET:
        received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if received != TELEGRAM_WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Webhook secret tidak valid.")
    update = await request.json()
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id:
        return {"ok": True}
    if text.startswith("/start") or text.startswith("/market"):
        try:
            await run_analysis("M15")
            await telegram_send(chat_id, report_text())
        except HTTPException as exc:
            await telegram_send(chat_id, "SIGNAL GOLD EDITION\n\nAnalisis belum tersedia: " + str(exc.detail))
    elif text.startswith("/m1"):
        try:
            await run_analysis("M1")
            await telegram_send(chat_id, report_text())
        except HTTPException as exc:
            await telegram_send(chat_id, "Gagal mengambil data: " + str(exc.detail))
    elif text.startswith("/m5"):
        try:
            await run_analysis("M5")
            await telegram_send(chat_id, report_text())
        except HTTPException as exc:
            await telegram_send(chat_id, "Gagal mengambil data: " + str(exc.detail))
    elif text.startswith("/m15"):
        try:
            await run_analysis("M15")
            await telegram_send(chat_id, report_text())
        except HTTPException as exc:
            await telegram_send(chat_id, "Gagal mengambil data: " + str(exc.detail))
    elif text.startswith("/h1"):
        try:
            await run_analysis("H1")
            await telegram_send(chat_id, report_text())
        except HTTPException as exc:
            await telegram_send(chat_id, "Gagal mengambil data: " + str(exc.detail))
    elif text.startswith("/help"):
        await telegram_send(
            chat_id,
            "Perintah SIGNAL GOLD EDITION:\n/start atau /market — analisis M15\n/m1 — timeframe 1 menit\n/m5 — 5 menit\n/m15 — 15 menit\n/h1 — 1 jam\n/help — bantuan\n\nLaporan hanya untuk edukasi."
        )
    else:
        await telegram_send(chat_id, "Perintah tidak dikenal. Ketik /help.")
    return {"ok": True}

@app.on_event("startup")
async def configure_telegram_webhook():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_WEBHOOK_SECRET or not PUBLIC_BASE_URL:
        log.warning("Telegram webhook setup dilewati: isi token, secret, dan public base URL.")
        return
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            res = await http.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
                json={
                    "url": f"{PUBLIC_BASE_URL}/telegram/webhook",
                    "secret_token": TELEGRAM_WEBHOOK_SECRET,
                    "allowed_updates": ["message"],
                },
            )
            log.info("Telegram webhook setup status: %s", res.status_code)
    except Exception:
        log.exception("Telegram webhook setup failed")
