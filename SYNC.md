# SYNC.md — OilFlood coordination

Both bots: `git pull` and read this before every task. Claim a task here and push the claim **before** starting it. Small commits, never force-push, never commit secrets (.env is gitignored).

**Deadline:** Devpost submission by **Sun Nov 22, 12:00 PM PST** (aim for 10:30 AM). All code must be written after Nov 21, 9:00 AM PST.

## Project
OilFlood: oil spill trajectory forecasting for first responders. Uses ocean current + wind data and tanker routes, plus an AI model (fine-tuned or pre-existing), backtested on historical spills, to output a response brief: where the oil goes over the next 24–72 h, which shorelines are threatened and when, and where to stage booms/skimmers.

Users: Coast Guard / NOAA-style responders and local emergency managers in the first hours of a spill.

## Tasks
| ID | Task | Owner | Status |
|----|------|-------|--------|
| T1 | Repo skeleton, README, SYNC.md | Erwin | done |
| T2 | Data: currents/wind source + historical spill set (e.g. NOAA) | Erwin | in progress (agent A2, branch a2-data) |
| T3 | Trajectory model (particle drift + AI correction) | Erwin | in progress (agent A1, branch a1-model) |
| T4 | Backtest on historical spills + metrics | Erwin | todo |
| T5 | Globe UI: click-to-spill + time playback (Step A); response layer is Step B | Erwin | in progress (agent A3, branch a3-globe) |
| T6 | Demo video (2–3 min) | Erwin | todo |
| T7 | Devpost write-up + README setup steps | Erwin | todo |
| T8 | Testing: follow README setup on a clean machine, test every feature, log bugs below | Brother | todo |
| T9 | Bug fixes: fix logged bugs (small fixes directly; ask in SYNC before big refactors) | Brother | todo |

## Agents (Erwin's tower)
Briefs in `agents/`. Each Claude agent works in its own worktree and branch and pushes there; Erwin's bot merges to main.

## Done log
- 2026-11-21 Erwin: skeleton pushed.
- 2026-11-21 Erwin: docs/ARCHITECTURE.md (deck.gl + MapLibre frontend, FastAPI + OpenDrift + ML correction backend).
- 2026-11-21 Erwin: roles set (Erwin builds, brother tests + fixes bugs), claimed T2.
- 2026-11-21 Erwin: docs/RESEARCH.md oil spill modeling research

## Roles
- Erwin builds everything (T2–T7).
- Brother does testing and bug fixes (T8–T9).

## Bugs
Format: `- [open|fixed] short title — steps to reproduce — who found / who fixed`
_None yet._

## Blockers
_None._
