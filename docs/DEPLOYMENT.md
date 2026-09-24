# ForexWizard AI Market Brain — Deployment Guide

## A. Windows always-on PC

### Prerequisites
- Python 3.12+
- Node.js 20+
- Git

### Backend

```powershell
cd apps\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env — set DATABASE_URL (see below)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend

```powershell
cd apps\web
npm install
npm run build
npm start
```

### Keep alive on Windows

Use **NSSM** (Non-Sucking Service Manager):

```powershell
nssm install forexwizard-backend "C:\path\to\.venv\Scripts\uvicorn.exe" "app.main:app --host 0.0.0.0 --port 8000"
nssm install forexwizard-frontend "C:\path\to\npx.exe" "next start --port 3000"
nssm start forexwizard-backend
nssm start forexwizard-frontend
```

Both services auto-restart on crash or reboot.

---

## B. Linux/VPS production deployment

### Prerequisites
- Python 3.12+
- Node.js 20+
- PostgreSQL 15+ (recommended for continuous forward validation)
- Git

### Database setup

**SQLite (development — zero setup):**
```bash
# In .env:
DATABASE_URL=sqlite:///./forexwizard.db
# WAL mode is enabled automatically for concurrent read/write support.
```

**PostgreSQL (recommended for production):**
```bash
sudo apt install postgresql postgresql-contrib
sudo -u postgres psql -c "CREATE DATABASE forexwizard;"
sudo -u postgres psql -c "CREATE USER fxuser WITH PASSWORD 'your_secure_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE forexwizard TO fxuser;"

# In .env:
DATABASE_URL=postgresql+psycopg://fxuser:your_secure_password@localhost:5432/forexwizard
```

### Database migrations (Alembic)

```bash
# From the project root:
cd apps/api
pip install -r requirements.txt  # includes alembic

# Set DATABASE_URL to your PostgreSQL URL:
export DATABASE_URL=postgresql+psycopg://fxuser:password@localhost:5432/forexwizard

# Run migrations:
alembic -c ../../alembic.ini upgrade head

# Check current revision:
alembic -c ../../alembic.ini current

# Generate new migration after schema changes:
alembic -c ../../alembic.ini revision --autogenerate -m "description of change"
```

### Backend deployment

```bash
# Clone from GitHub:
git clone https://github.com/musawar127/forexwizard-brain.git /opt/forexwizard
cd /opt/forexwizard/apps/api

# Create venv + install:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure:
cp .env.example .env
# Edit .env:
#   DATABASE_URL=postgresql+psycopg://fxuser:password@localhost:5432/forexwizard
#   CORS_ORIGINS=https://your-frontend-domain.com
#   FRONTEND_ORIGIN=https://your-frontend-domain.com

# Run migrations:
DATABASE_URL=postgresql+psycopg://fxuser:password@localhost:5432/forexwizard \
  /home/z/.venv/bin/python3 -m alembic -c ../../alembic.ini upgrade head

# Start (production):
# Use $PORT for cloud environments (Railway, Render, Fly.io, etc.):
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

### Frontend deployment

```bash
cd /opt/forexwizard/apps/web
npm install

# Configure backend URL:
cat > .env.local << 'EOF'
NEXT_PUBLIC_API_URL=https://your-backend-domain.com
EOF

npm run build
npm start
```

### Process supervision with systemd

**Backend service** (`/etc/systemd/system/forexwizard-backend.service`):

```ini
[Unit]
Description=ForexWizard Backend API
After=network.target postgresql.service

[Service]
Type=simple
User=fxuser
WorkingDirectory=/opt/forexwizard/apps/api
Environment=PYTHONPATH=/opt/forexwizard/apps/api
Environment=DATABASE_URL=postgresql+psycopg://fxuser:password@localhost:5432/forexwizard
Environment=CORS_ORIGINS=https://your-frontend-domain.com
EnvironmentFile=/opt/forexwizard/apps/api/.env
ExecStart=/opt/forexwizard/apps/api/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Frontend service** (`/etc/systemd/system/forexwizard-frontend.service`):

```ini
[Unit]
Description=ForexWizard Frontend
After=network.target forexwizard-backend.service

[Service]
Type=simple
User=fxuser
WorkingDirectory=/opt/forexwizard/apps/web
Environment=NEXT_PUBLIC_API_URL=https://your-backend-domain.com
Environment=NODE_ENV=production
ExecStart=/usr/bin/npx next start --port 3000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable forexwizard-backend forexwizard-frontend
sudo systemctl start forexwizard-backend forexwizard-frontend
```

Both services auto-restart on crash or machine reboot.

---

## Environment variables reference

### Backend (.env)

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | `sqlite:///./forexwizard.db` | SQLAlchemy URL. Use `postgresql+psycopg://` for production. |
| `CORS_ORIGINS` | Prod | (empty) | Comma-separated allowed frontend origins. Takes priority over FRONTEND_ORIGIN. |
| `FRONTEND_ORIGIN` | No | `http://localhost:3000` | Fallback CORS origin for local dev. |
| `PORT` | Cloud | `8000` | Port for uvicorn (cloud providers set this automatically). |
| `TWELVE_DATA_API_KEY` | No | (empty) | Optional historical bootstrap provider. |
| `GOLD_API_BASE_URL` | No | `https://api.gold-api.com` | Live spot price source. |

### Frontend (.env.local)

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | Yes | `http://localhost:8000` | Backend API URL. Use your hosted domain in production. |

---

## Backup procedures

### SQLite backup
```bash
sqlite3 /opt/forexwizard/apps/api/forexwizard.db ".backup /backup/forexwizard-$(date +%Y%m%d).db"
find /backup/ -name "forexwizard-*.db" -mtime +7 -delete
```

### PostgreSQL backup
```bash
pg_dump -U fxuser -d forexwizard -F c -f /backup/forexwizard-$(date +%Y%m%d).dump
pg_restore -U fxuser -d forexwizard -c /backup/forexwizard-20260923.dump
```

---

## Environment security

- `.env` files are in `.gitignore` — NEVER committed.
- `.env.example` contains placeholders only — no real credentials.
- `NEXT_PUBLIC_*` variables are visible to website visitors — NEVER put secrets there.
- API keys, database passwords, GitHub tokens stay in `.env` or OS environment only.

---

## Log rotation

```bash
# /etc/logrotate.d/forexwizard
/var/log/forexwizard/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 644 fxuser fxuser
}
```

---

## Health endpoints

- `GET /health` — basic backend health
- `GET /api/system/status` — system mode (STARTING/CATCHING_UP/LIVE/DEGRADED)
- `GET /api/system/health` — all subsystems summary
- `GET /api/forward/health` — forward collector status
- `GET /api/catchup/status` — catch-up coordinator status

---

## Quick start checklist

1. ☐ Clone repo
2. ☐ Install Python deps (`pip install -r requirements.txt`)
3. ☐ Install npm deps (`npm install`)
4. ☐ Copy `.env.example` → `.env`, set `DATABASE_URL`
5. ☐ Copy `.env.local.example` → `.env.local`, set `NEXT_PUBLIC_API_URL`
6. ☐ Run migrations: `alembic -c ../../alembic.ini upgrade head`
7. ☐ Start backend: `uvicorn app.main:app --port ${PORT:-8000}`
8. ☐ Start frontend: `npm run build && npm start`
9. ☐ Verify: `curl http://localhost:8000/health`
10. ☐ Open: `http://localhost:3000`
11. ☐ Sync historical data: `POST /api/data/sync`
12. ☐ Build learning states: `POST /api/learning/build-states`
13. ☐ Forward validation starts automatically on first capture
14. ☐ Catch-up recovery runs automatically on restart
