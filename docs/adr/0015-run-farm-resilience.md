# Run farm: exactly-once results, and why the queue is Postgres

A sweep is policies × scenarios × operating days × seeds. Each combination is one job. Workers can die in the middle of a job, or after the day has been scored and before that score is stored. The aggregate still has to match a sequential harness run, with each day counted once.

## Exactly-once

The identity of a result is the policy name, the policy version, the scenario content hash, the operating day, and the seed. That tuple is the primary key of `results`. The scenario hash is the file bytes, so editing a scenario is a new identity and `submit` inserts new jobs. Submitting the same sweep again inserts nothing that is already there.

**Claim.** A worker takes the oldest job that is queued and due, or leased and past its deadline, with `SELECT … FOR UPDATE SKIP LOCKED`. The claim records the worker, a lease deadline, and an attempt number. The attempt is the fence token.

**Run.** The harness scores that one day outside the commit transaction, and a renewal thread pushes the deadline forward while the day is running. Two chaos points sit on this path, chosen by a seed:

- mid-job: the worker is stopped while it holds the lease, before a result exists;
- before commit: the day has been scored in memory, and the worker is stopped before the insert.

A chaos kill is not a handled error. The job stays leased until the deadline, then another attempt runs it. Nothing was inserted, so the replacement is free to write.

**Commit.** One transaction inserts the scorecard and marks the job done, and only if the lease owner, the attempt, and the status still match. A worker that lost the fence writes nothing. The insert is `ON CONFLICT DO NOTHING`, so a loser that races a winner also writes nothing. Scorecards combine by summing per-day totals, and a day is stored once, so a retry cannot double-count.

**Handled failure.** A job that raises is different from a kill. It is queued again with `not_before = now + backoff × 2^(attempt − 1)`. Claim skips it until that time. After a configurable maximum number of attempts it is marked failed and keeps the last error. The rest of the sweep keeps running. `aggregate` refuses while any job is still queued, leased, or failed, so a partial sweep is not reported as the result.

## Why a Postgres queue instead of Temporal

Base uses Temporal in BaseOS. Temporal would add a durable history of every step, activity retries and backoff timers, heartbeats, and a view of which day is running. The farm needs a small piece of that: a queue, a lease, a retry time, and an idempotent write of the scorecard.

Temporal would not make a harness day deterministic, and it would not by itself stop two workers from recording the same day. The exactly-once part is the result key and the fence. Those stay in Postgres either way, because that is where the scorecards are. Putting the queue in the same database lets the claim and the result commit in one transaction, with no second cluster. The harness has to run from a laptop against a copied market dataset, so a workflow service would be another thing to operate for a queue we can see in SQL.

## If Base ran this under BaseOS

The sweep would be a workflow and each day an activity with a heartbeat. A dead worker would be a missed heartbeat and an automatic retry, which is the lease and the chaos kills above. Completing the activity would still have to store the scorecard under the same identity key. Without that key, a replayed activity could write the day twice and the combined scorecard would not match a sequential run. BaseOS would replace the compose Postgres and the way `chaos` stops workers. It would not replace the scorecard key.
