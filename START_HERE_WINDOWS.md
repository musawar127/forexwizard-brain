# Start here on Windows

## Backend terminal

```powershell
cd apps\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```

## Frontend terminal

```powershell
cd apps\web
npm install
Copy-Item .env.local.example .env.local
npm run dev
```

Then visit:

http://localhost:3000

## Give the project to GLM/ZCode

Open this entire project folder in ZCode and tell it:

> Read GLM5_CONTINUE_PROMPT.md and follow it exactly. Do not rebuild the project from scratch.

That is all you need to paste.
