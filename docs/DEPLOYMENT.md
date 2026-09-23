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
# Install NSSM from https://nssm.cc/
nssm install forexwizard-backend "C:\path\to\.venv\Scripts\uvicorn.exe" "app.main:app --host 0.0.0.0 --port 8000"
nssm install forexwizard-frontend "C:\path\to\node\npx.exe" "next start --port 3000"
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

### Backend

```bash
cd /opt/forexwizard/apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your DATABASE_URL
```

### Frontend

```bash
cd /opt/forexwizard/apps/web
npm install
npm run build
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
ExecStart=/opt/forexwizard/apps/api/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
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
ExecStart=/usr/bin/npx next start --port 3000
Restart=always
RestartSec=5
Environment=NODE_ENV=production

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

## Backup procedures

### SQLite backup
```bash
# Consistent backup (uses .backup command for WAL-aware snapshot):
sqlite3 /opt/forexwizard/apps/api/forexwizard.db ".backup /backup/forexwizard-$(date +%Y%m%d).db"

# Or copy the WAL + main DB:
cp /opt/forexwizard/apps/api/forexwizard.db /backup/
cp /opt/forexwizard/apps/api/forexwizard.db-wal /backup/ 2>/dev/null || true

# Retention: keep 7 days
find /backup/ -name "forexwizard-*.db" -mtime +7 -delete
```

### PostgreSQL backup
```bash
pg_dump -U fxuser -d forexwizard -F c -f /backup/forexwizard-$(date +%Y%m%d).dump

# Restore:
pg_restore -U fxuser -d forexwizard -c /backup/forexwizard-20260923.dump
```

### Restore
```bash
# SQLite:
cp /backup/forexwizard-20260923.db /opt/forexwizard/apps/api/forexwizard.db

# PostgreSQL:
pg_restore -U fxuser -d forexwizard -c /backup/forexwizard-20260923.dump
```

---

## Environment security

- `.env` files are in `.gitignore` — NEVER committed.
- `.env.example` contains placeholders only — no real credentials.
- API keys, database passwords, GitHub tokens stay in `.env` only.
- `NEXT_PUBLIC_*` variables are visible to website visitors — NEVER put secrets there.

---

## Log rotation

### Backend logs (uvicorn)
Logs go to `/var/log/forexwizard/`. Configure logrotate:

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

### Frontend logs
Next.js logs to stdout — systemd journal captures them. Use `journalctl`:

```bash
journalctl -u forexwizard-frontend -f --since "1 hour ago"
```

---

## Health monitoring

- `GET /health` — basic backend health
- `GET /api/forward/health` — forward collector + evaluator status
- `GET /api/system/health` — all subsystems summary
- `GET /api/learning/status` — historical learning engine status

---

## Quick start checklist

1. ☐ Clone repo
2. ☐ Install Python deps (`pip install -r requirements.txt`)
3. ☐ Install npm deps (`npm install`)
4. ☐ Copy `.env.example` → `.env`, set `DATABASE_URL`
5. ☐ Start backend: `uvicorn app.main:app --port 8000`
6. ☐ Start frontend: `npm run build && npm start`
7. ☐ Verify: `curl http://localhost:8000/health`
8. ☐ Open: `http://localhost:3000`
9. ☐ Sync historical data: `POST /api/data/sync`
10. ☐ Build learning states: `POST /api/learning/build-states`
11. ☐ Forward validation starts automatically on first capture
