# Submission

Paste-ready fields for the hackathon form. The demo recording and a public repo are the two items this file cannot finish.

## Project title

Capacity-policy test harness

## Tracks

Open Grid Data and Orchestration.

## Repo

https://github.com/AayushKSharma/Base-Power-Hackathon

The repo is private as of 2026-09-27. The form asks for a link anyone can open. Flip it to public before submitting (`gh repo edit --visibility public --accept-visibility-change-consequences`), after this file, the README additions, and `.env.example` are on `main`.

## Deployed URL

None. The harness is a local command-line tool plus charts in the repo. The five-minute demo video is the screen capture of the working run.

## Team roster

| Name | Role | Contact |
|---|---|---|
| Aayush Kumar Sharma | Built the harness | aayushksharma1@gmail.com |

Git history on this repo has one author. Add anyone else who should be on the form before you submit.

## Short write-up

Base Power reports a real-time capability number for ECRS and Non-Spin. Since RTC+B went live on 5 December 2025, a day-ahead reserve award settles both ways at the real-time price, so fleet failures no longer change the best day-ahead quantity. The remaining exposure is that real-time report. Base engineers said there is no solid test bench for a change to the number, and that the score they want is not a net dollar. Under-serving a deployment is bad for the grid. They asked for a map: for each algorithm, the chance of under-serving by at least X megawatts in an hour, against the revenue given up to stay that reliable.

The harness is that bench. It replays a policy on public ERCOT prices from 5 December 2025 onward and prints the map. Prices are measured. Fleet size, dropouts, deployment chances, and the unconfirmed shortfall charge live in a scenario file the desk can replace. Four built-in policies (a fixed haircut, two newsvendors, and a reliability target) exist so a scorecard can tell them apart. Base points `--policy` at its own process: JSON in, megawatts out. The same day also runs on a two-second clock, with seeded failures and a coordinator restart from Postgres. A run farm retries a killed day without counting it twice.

On 11–17 August 2026 the bench separates those references on dollars. On the fleet each policy planned for, physical shortfall is zero. Rankings hold when the pilot cap lifts from 100 MW to 500 MW, and they change when Non-Spin drops from four hours to two: the correlated newsvendor passes the haircut, because the same stored energy supports more megawatts.

## Demo video

Record on Loom, 2–5 minutes, following [docs/demo.md](demo.md). That script is the shot list: findings, frontier, rankings, one P10 tolerance table, `scenarios/baseline.yaml`, the chaos timeline, `chaos/demo.yaml`, then `docs/bench/bench.md`. Show `make replay` or the chaos chart live. Do not re-run `make backtest` or `make bench` on camera; those commands rewrite the published files.

Open the files before you start. The spoken assumptions and the numbers in the script match the published comparison (11–17 August 2026, seed 7) and the last chaos run of 17 August 2026.
