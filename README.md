# OilFlood

Oil spill trajectory forecasting for first responders. Built at EvoHacks 2 (Projects for Social Good).

## Setup
Backend (FastAPI + OpenDrift, port 5200): see [backend/README.md](backend/README.md).
```bash
cd backend && ~/miniforge3/bin/mamba env create -f environment.yml
~/miniforge3/envs/oilflood/bin/uvicorn app.main:app --host 127.0.0.1 --port 5200
~/miniforge3/envs/oilflood/bin/python smoke_test.py
```

Frontend (Vite, port 5273 in dev, proxies `/api` to 5200): see [web/README.md](web/README.md).
```bash
cd web && npm install
npm run dev     # http://localhost:5273  (add ?mock=1 to run without the backend)
npm run build   # -> web/dist, served by the backend at http://127.0.0.1:5200
```
