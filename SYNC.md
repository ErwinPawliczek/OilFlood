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
| T2 | Data: currents/wind source + historical spill set (e.g. NOAA) | unclaimed | todo |
| T3 | Trajectory model (particle drift + AI correction) | unclaimed | todo |
| T4 | Backtest on historical spills + metrics | unclaimed | todo |
| T5 | Responder UI: map + response brief | unclaimed | todo |
| T6 | Demo video (2–3 min) | unclaimed | todo |
| T7 | Devpost write-up + README setup steps | unclaimed | todo |

## Done log
- 2026-11-21 Erwin: skeleton pushed.

## Blockers
- Task split between Erwin and brother not decided yet.
