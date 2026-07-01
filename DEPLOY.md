# Guía de deploy — bot-discord-purg

Guía completa para levantar Purgito de cero en un VPS Ubuntu. Cubre setup local para desarrollo y deploy en producción con systemd + nginx + Cloudflare.

---

## Índice

1. [Prerrequisitos](#1-prerrequisitos)
2. [Crear el bot en Discord](#2-crear-el-bot-en-discord)
3. [Clonar e instalar](#3-clonar-e-instalar)
4. [Variables de entorno — referencia completa](#4-variables-de-entorno--referencia-completa)
5. [Servicios opcionales](#5-servicios-opcionales)
   - [Cloudflare R2 (persistencia de GIFs)](#cloudflare-r2-persistencia-de-gifs)
   - [Groq (captions de memes con IA)](#groq-captions-de-memes-con-ia)
   - [cookies.txt (música de YouTube)](#cookiestxt-música-de-youtube)
6. [Correr en desarrollo](#6-correr-en-desarrollo)
7. [Deploy en producción](#7-deploy-en-producción)
   - [Paquetes del sistema](#paquetes-del-sistema)
   - [Clonar en el servidor](#clonar-en-el-servidor)
   - [Configurar systemd](#configurar-systemd)
   - [Configurar nginx](#configurar-nginx)
   - [Cloudflare (DNS + SSL)](#cloudflare-dns--ssl)
8. [Actualizar en producción](#8-actualizar-en-producción)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Prerrequisitos

### Para desarrollo local

- Python 3.11+
- FFmpeg (`sudo apt install ffmpeg` / `brew install ffmpeg`)
- Una cuenta de Discord con permisos para crear bots

### Para producción (VPS)

- Ubuntu 22.04 o 24.04
- Python 3.11+ (incluido en Ubuntu 22.04+)
- Node.js 20+ — requerido por yt-dlp para resolver firmas de YouTube
- FFmpeg
- nginx
- Dominio apuntando al servidor (para la galería web)

---

## 2. Crear el bot en Discord

1. Entrá al [Discord Developer Portal](https://discord.com/developers/applications)
2. **New Application** → poné el nombre
3. En el menú izquierdo: **Bot**
   - Copiá el **Token** (lo vas a necesitar como `DISCORD_TOKEN`)
   - Activá **Message Content Intent** (imprescindible para el corpus de Markov)
   - Activá **Server Members Intent**
4. En **OAuth2 → URL Generator**:
   - Scopes: `bot` + `applications.commands`
   - Permisos de bot: `Read Messages`, `Send Messages`, `Read Message History`, `Add Reactions`, `Embed Links`, `Connect`, `Speak`
5. Copiá la URL generada y abrila para invitar el bot a tu servidor

> **Tip:** para que los slash commands aparezcan al instante en un servidor específico (sin esperar hasta 1 hora de propagación global), poné `GUILD_ID` en el `.env` con el ID de ese servidor.

---

## 3. Clonar e instalar

```bash
git clone https://github.com/punkyyy01/bot-discord-purg.git
cd bot-discord-purg

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

Luego copiá el archivo de ejemplo:

```bash
cp .env.example .env
```

Editá `.env` con tus valores (ver sección siguiente).

---

## 4. Variables de entorno — referencia completa

```env
# ═══════════════════════════════════════════════════════════
#  OBLIGATORIO
# ═══════════════════════════════════════════════════════════

# Token del bot de Discord.
# Obtenerlo en: Discord Developer Portal → tu aplicación → Bot → Token
# ⚠️ Nunca commitees este valor.
DISCORD_TOKEN=

# ═══════════════════════════════════════════════════════════
#  CONFIGURACIÓN GENERAL
# ═══════════════════════════════════════════════════════════

# Habilita el intent "Message Content" para leer el texto de los mensajes.
# Debe estar activado también en el Developer Portal (Bot → Privileged Gateway Intents).
# Default: true
ENABLE_MESSAGE_CONTENT=true

# ID del servidor donde el sistema de memes, la galería y las frases especiales
# están habilitados. Si no se define, esas funciones quedan desactivadas globalmente.
# Obtenerlo: click derecho en el ícono del servidor → Copiar ID (modo dev activado).
HOME_GUILD_ID=

# ID del servidor para sincronización instantánea de slash commands (útil en desarrollo).
# Sin esto, los comandos nuevos pueden tardar hasta 1 hora en aparecer globalmente.
GUILD_ID=

# Nombre con el que se activa el trigger de memes por texto plano.
# Ej: si ponés "artemis", escribir "artemis generar" en un reply a una imagen genera un meme.
# Default: artemis
BOT_TRIGGER_NAME=artemis

# Puerto del servidor web de la galería pública (gifs.purg4t0ry.com).
# nginx hace proxy a este puerto. No exponer directamente a internet.
# Default: 8080
WEB_PORT=8080

# ═══════════════════════════════════════════════════════════
#  MARKOV — límites de entrenamiento
# ═══════════════════════════════════════════════════════════

# Máximo de mensajes a leer del canal actual con /refeed.
# Default: 80000
REFEED_MAX_MESSAGES=80000

# Máximo de mensajes por canal con /refeed_all (todos los canales).
# Default: 20000
REFEED_ALL_MAX_MESSAGES=20000

# Cuántos mensajes del corpus se cargan a RAM para entrenar el modelo del servidor.
# Valores altos = mejor calidad, más RAM.
# Default: 5000
MARKOV_TRAINING_MESSAGES=5000

# Igual que el anterior pero para el modelo de usuario (/imitar).
# Default: 2000
USER_MARKOV_TRAINING_MESSAGES=2000

# ═══════════════════════════════════════════════════════════
#  OPCIONAL — Groq (captions de memes con visión IA)
# ═══════════════════════════════════════════════════════════

# API Key de Groq para captions con llama-4-scout (modelo de visión).
# Sin esta key los captions se generan con Markov local.
# Obtener en: https://console.groq.com → API Keys
GROQ_API_KEY=

# ═══════════════════════════════════════════════════════════
#  OPCIONAL — Cloudflare R2 (persistencia de GIFs)
# ═══════════════════════════════════════════════════════════

# Sin R2, las URLs de Discord CDN pueden expirar.
# Todas las variables R2_* deben estar presentes para que R2 se active.

# URL del endpoint S3-compatible. Formato: https://<account-id>.r2.cloudflarestorage.com
R2_ENDPOINT_URL=

# Access Key ID del token R2 con permisos "Object Read & Write".
R2_ACCESS_KEY_ID=

# Secret del token R2.
R2_SECRET_ACCESS_KEY=

# Nombre del bucket R2.
R2_BUCKET_NAME=

# URL pública del bucket. Formato: https://pub-xxx.r2.dev
R2_PUBLIC_URL=

# ═══════════════════════════════════════════════════════════
#  OPCIONAL — cookies.txt para yt-dlp (música de YouTube)
# ═══════════════════════════════════════════════════════════

# Ruta al archivo cookies.txt de tu sesión de YouTube.
# Necesario si YouTube bloquea descargas (error "Sign in to confirm you're not a bot").
# Ver DEPLOY.md → Servicios opcionales → cookies.txt para generarlo.
# Default en producción: /opt/bot-discord-purg/cookies.txt
YTDLP_COOKIES=/opt/bot-discord-purg/cookies.txt
```

---

## 5. Servicios opcionales

### Cloudflare R2 (persistencia de GIFs)

1. Cloudflare Dashboard → **R2 Object Storage** → creá un bucket
2. **R2 → Manage R2 API Tokens** → token con permisos **Object Read & Write**
3. Copiá el **Access Key ID** y el **Secret Access Key**
4. El **Endpoint URL** está en la página del bucket bajo "S3 API"
5. Completá las variables `R2_*` en `.env`

### Groq (captions de memes con IA)

1. Creá cuenta en [console.groq.com](https://console.groq.com)
2. **API Keys** → **Create API Key**
3. Copiá la key → `GROQ_API_KEY` en `.env`

El bot usa `meta-llama/llama-4-scout-17b-16e-instruct` para analizar imágenes. Si la key no está o Groq falla, hace fallback automático a Markov.

### cookies.txt (música de YouTube)

Si YouTube bloquea las descargas del bot:

1. Instalá la extensión [Get cookies.txt LOCALLY](https://github.com/kairi003/Get-cookies.txt-LOCALLY) en Chrome/Firefox
2. Logueate en [youtube.com](https://youtube.com)
3. Exportá las cookies en formato Netscape
4. Copiá el archivo al servidor:

```bash
scp cookies.txt user@tu-servidor:/opt/bot-discord-purg/cookies.txt
```

> Las cookies expiran. Si YouTube vuelve a bloquear después de meses, repetí el proceso.

---

## 6. Correr en desarrollo

```bash
source .venv/bin/activate
python src/bot.py
```

La galería arranca en el mismo proceso en `http://localhost:8080`. Los slash commands aparecen instantáneamente en el servidor de `GUILD_ID`.

---

## 7. Deploy en producción

### Paquetes del sistema

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv python3-dev ffmpeg nginx

# Node.js 20 — necesario para que yt-dlp resuelva firmas de YouTube
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Verificar
python3 --version  # 3.11+
ffmpeg -version
node --version     # v20+
```

### Clonar en el servidor

```bash
sudo mkdir -p /opt/bot-discord-purg
sudo chown $USER:$USER /opt/bot-discord-purg

git clone https://github.com/punkyyy01/bot-discord-purg.git /opt/bot-discord-purg
cd /opt/bot-discord-purg

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env
```

### Configurar systemd

El unit file canónico vive en el repo: [`deploy/bot-purg.service`](deploy/bot-purg.service).

```bash
sudo cp deploy/bot-purg.service /etc/systemd/system/bot-purg.service
```

> ⚠️ **Seguridad**: crea un usuario dedicado con `sudo useradd -r -s /bin/false bot-purg` y dale permisos sobre `/opt/bot-discord-purg`.

Claves del unit:
- `Restart=on-failure` + `RestartSec=15` — reinicio automático si el proceso muere, con espera entre intentos para no entrar en loops agresivos.
- `StartLimitIntervalSec=0` — systemd nunca deja de reintentar, aunque el problema persista (una caída de red larga no deja el bot muerto de forma permanente).
- `After=network-online.target` — no arranca antes de tener red.

```bash
sudo systemctl daemon-reload
sudo systemctl enable bot-purg
sudo systemctl start bot-purg
sudo systemctl status bot-purg
```

Ver logs:

```bash
journalctl -u bot-purg -f
```

### Configurar nginx

```bash
sudo nano /etc/nginx/sites-available/gifs-purg
```

```nginx
server {
    listen 80;
    server_name gifs.purg4t0ry.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/gifs-purg /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Cloudflare (DNS + SSL)

1. DNS → Add record: tipo `A`, nombre `gifs`, valor = IP del droplet, proxy ✅ (naranja)
2. Cloudflare maneja el SSL automáticamente. No necesitás certbot ni HTTPS en nginx.

---

## 8. Actualizar en producción

```bash
cd /opt/bot-discord-purg
git pull
source .venv/bin/activate
pip install -r requirements.txt   # solo si requirements.txt cambió
sudo systemctl restart bot-purg
sudo systemctl status bot-purg
```

> Si `.env.example` tiene variables nuevas, añadilas manualmente a tu `.env` antes de reiniciar.

---

## 9. Troubleshooting

| Problema | Causa probable | Fix |
|---|---|---|
| Slash commands no aparecen | `GUILD_ID` no configurado o sin scope `applications.commands` | Poner `GUILD_ID` en `.env` y reiniciar, o esperar 1h si es global |
| YouTube no reproduce (`Sign in to confirm`) | YouTube detectó el bot | Generar `cookies.txt` y ponerlo en `YTDLP_COOKIES` |
| GIFs de Discord CDN no se suben a R2 | Faltan vars `R2_*` | Completar todas las `R2_*` en `.env` |
| La galería no carga | nginx caído o DNS sin propagar | `systemctl status nginx` + verificar DNS |
| El bot arranca pero no lee mensajes | `ENABLE_MESSAGE_CONTENT=false` o intent desactivado en el portal | Activar Message Content Intent en el Developer Portal |
| Música no funciona | FFmpeg no instalado o `cookies.txt` desactualizado | `ffmpeg -version` + regenerar cookies |
| `ModuleNotFoundError` | venv no activado o `pip install` no corrió | `source .venv/bin/activate && pip install -r requirements.txt` |
| Bot se cae y no reinicia | `Restart=always` no está en el `.service` | Verificar el `.service` y `systemctl daemon-reload` |
