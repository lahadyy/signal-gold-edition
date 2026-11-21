# SIGNAL GOLD EDITION — Railway + Twelve Data + OpenAI + Telegram

FastAPI backend untuk laporan edukasi kondisi XAU/USD dari candle OHLC Twelve Data.

## Fitur
- Candle XAU/USD M1, M5, M15, H1 (bergantung dukungan provider/plan).
- EMA 9/21, RSI 14, rentang candle rata-rata.
- OpenAI merangkum trend, momentum, volatilitas, dan pengamatan.
- Telegram: /start, /market, /m1, /m5, /m15, /h1, /help.
- Semua credential hanya dari environment variables.

## Batasan
Alat ini hanya analisis deskriptif untuk pembelajaran. Tidak memberikan BUY/SELL, entry, stop-loss, take-profit, leverage, atau instruksi transaksi. Data API bisa tertunda, dibatasi kuota, dan berbeda dari harga broker Exness. Verifikasi dukungan XAU/USD dan interval di akun provider. Tambahkan autentikasi/rate limit dan batasi CORS sebelum publikasi luas. Jangan commit token/API key.

## Deploy melalui iPhone
1. Buat repository GitHub baru `signal-gold-edition`.
2. Upload file `main.py`, `requirements.txt`, `Procfile`, `railway.json`, `.gitignore`, `README.md`.
3. Buka https://railway.com/ di Safari dan login.
4. Buat project baru → Deploy from GitHub Repo → pilih repository.
5. Pada Variables, masukkan:
   - `TWELVE_DATA_API_KEY` = API key dari Twelve Data
   - `TWELVE_DATA_SYMBOL` = `XAU/USD`
   - `OPENAI_API_KEY` = API key OpenAI
   - `OPENAI_MODEL` = `gpt-4.1-mini`
   - `TELEGRAM_BOT_TOKEN` = token BARU dari BotFather
   - `TELEGRAM_WEBHOOK_SECRET` = rahasia acak panjang (32+ karakter)
   - `PUBLIC_BASE_URL` = domain publik Railway, misalnya `https://nama-service.up.railway.app`
6. Buat/generate public domain di Railway Networking → Domains.
7. Isi `PUBLIC_BASE_URL` dengan domain persis (tanpa slash akhir), lalu redeploy.
8. Tes `https://DOMAIN-KAMU/health`; seharusnya `{"ok":true}`.
9. Di Telegram, buka bot dan kirim `/start`.

## Dapatkan credential
- Twelve Data: https://twelvedata.com/
- OpenAI API key: https://platform.openai.com/api-keys
- Telegram: @BotFather → buat bot baru. Simpan token hanya di Railway Variables.

## Endpoint
- `GET /health`
- `GET /api/market?timeframe=M15`
- `POST /api/analyze` body `{"timeframe":"M5"}`
- `GET /api/latest`
- `GET /docs`

## CodePen
```javascript
const response = await fetch("https://DOMAIN-KAMU/api/analyze", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({timeframe: "M15"})
});
const result = await response.json();
```
