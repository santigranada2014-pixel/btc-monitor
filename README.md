# BTC Monitor — Telegram Bot

Corre 24/7 en la nube y te manda alertas a Telegram cuando tu checklist detecta una señal LONG o SHORT.

## Setup en 5 minutos

### 1. Crear el bot en Telegram
1. Abrí Telegram → buscá **@BotFather**
2. Escribí `/newbot`
3. Seguí las instrucciones → te da un **TOKEN**

### 2. Obtener tu Chat ID
1. Buscá **@userinfobot** en Telegram
2. Escribile cualquier mensaje
3. Te responde con tu **Chat ID** (un número)

### 3. Subir a Railway (gratis)
1. Creá cuenta en [railway.app](https://railway.app)
2. New Project → Deploy from GitHub repo
3. O: New Project → Deploy from template → Python
4. Subí los archivos `bot.py` y `requirements.txt`
5. En Variables de entorno agregá:
   - `TELEGRAM_TOKEN` = tu token del BotFather
   - `CHAT_ID` = tu chat ID numérico

### 4. Listo
El bot arranca solo y te manda un mensaje confirmando que está activo.
Cada 2 minutos chequea Binance y te alerta si hay señal.

## Variables de entorno
| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| TELEGRAM_TOKEN | Token del BotFather | `7234567890:AAFxxx...` |
| CHAT_ID | Tu Chat ID numérico | `123456789` |

## Personalización (opcional)
En `bot.py` podés cambiar:
- `MIN_SCORE = 70` → umbral para alertar (%)
- `TF = "1h"` → timeframe (1h o 4h)
- `CHECK_INTERVAL = 120` → segundos entre chequeos
