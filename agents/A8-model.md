# Agent A8: our own oil-spill model + OpenOil baseline compare (PLAN P3)
Repo: OilFlood. Read SYNC.md, docs/PLAN.md, docs/RESEARCH.md, docs/ARCHITECTURE.md first. Follow SYNC.md rules (small commits, push often to YOUR branch `a8-model`, never force-push, never commit secrets or large data). Deadline Sun Nov 22 12:00 PM PST.
Do not touch production port 5200. Test on 5203. data/cache and data/raw are already symlinked. Keep the single-panel UI.

Goal: two models a user can compare: (a) the existing OpenDrift OpenOil baseline (copied from established practice: advection by currents + ~2–3% wind drift + Stokes if available, random-walk diffusion, weathering, beaching). (b) OUR OWN model in `backend/app/ourmodel/`: Lagrangian particles on the same live/historical forcing, Fay spreading, simple weathering (evaporation/emulsification), beaching on the coastline, plus an ML drift correction trained on NOAA Global Drifter Program tracks vs the forcing (this satisfies the hackathon rule to fine-tune or use a pre-existing AI model; a small sklearn/pytorch model is fine; document training data, features, and hold-out skill).
- API: POST /api/spill accepts `model=openoil|ours`. Tracks and status say which model ran.
- Frontend: in the one panel, a model toggle or side-by-side compare (our particles vs OpenOil) in prediction and historical modes. Real data only, no mocks.
- Write a short `docs/OURMODEL.md` (equations, ML setup, how to retrain). If GDP training can't finish in time, ship a trained-on-a-subset model and note the limitation; do not fake scores.
Update SYNC.md (Done log, Blockers) when done.
