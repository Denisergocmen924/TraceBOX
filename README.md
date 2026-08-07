# BlackBox

Remote log-shipping and monitoring for machines that might not survive to tell you what happened.

An agent on each monitored machine continuously collects metrics and system logs and ships them to the cloud **before** the machine goes down. When a box becomes unreachable, the events leading up to the failure are already somewhere else — readable from any browser.

Like an aircraft's black box: it records up to the last moment, and it lives where the crash can't reach it.

## The core idea

The naive framing is "store logs in the cloud." That is not the problem. The problem is **getting data out while the event is still happening**. A machine that tries to upload its final state *during* a crash has already lost — the network stack, the disk, or the process itself is usually the thing that failed.

So BlackBox inverts it: ship continuously, and ship *harder* when thresholds are crossed. By the time a machine dies, the interesting data is already gone.

## Architecture

```
WRITE path:  [Agent] --device key / TLS--> [Collector: Fly.io]  --service key--> [Supabase: Postgres]
READ  path:  [Dashboard: Next.js]         --user JWT / RLS-------------------->  [Supabase: Postgres + Auth]
```

Two paths, two credentials, two trust levels — deliberately never crossing.

| Component | Stack | Role |
|---|---|---|
| **Agent** | Python, systemd | Collects, spools to disk, ships. Runs unprivileged. |
| **Collector** | FastAPI on Fly.io | The only write door. Resolves device identity from a key hash. |
| **Database** | Supabase (Postgres) | Storage, auth, row-level security, retention via `pg_cron`. |
| **Dashboard** | Next.js + Tailwind | Read-only window. Talks to Postgres directly, guarded by RLS. |

### Design decisions worth calling out

**Devices never claim their own identity.** Payloads contain no `device_id`. The agent presents a key; the collector hashes it, matches it against `devices.key_hash`, and derives both `device_id` and `account_id` server-side. A compromised agent cannot write into another account's data because it has no way to name one.

**The service key never leaves the collector.** Agents hold per-device keys that can be revoked individually. Distributing a Supabase service key to every monitored machine would turn one compromised box into a full database breach.

**Single writer.** Exactly one component owns each piece of state. The agent alone writes `state.json`; the collector alone writes `last_seen`, `key_hash`, and command status. Column-level grants enforce this at the database level, not just by convention.

**At-least-once delivery, with idempotency.** The agent spools to SQLite and only deletes a record after a `200`. Retries are therefore expected, so every record carries an agent-generated UUID and the server inserts with `ON CONFLICT DO NOTHING`. Duplicates are impossible; loss requires the disk itself to fail.

**Pause keeps recording.** Pausing a device stops *uploads*, not *collection*. Data accumulates locally and drains on resume. Command polling continues during pause — otherwise `resume` could never arrive.

**Deletion is ordered.** Removing a device does not delete the row immediately. It enqueues a `delete` command; the agent wipes itself locally, acknowledges, and only then does the collector drop the row. Deleting first would invalidate the key and the agent would never learn it was supposed to uninstall.

**The agent is bounded.** The disk spool is a ring buffer capped by both age and size. A monitoring tool that fills the disk of the machine it monitors has caused the outage it was meant to explain.

## Status

Under active development. Built in vertical slices — each milestone is a thin end-to-end path, not a horizontal layer.

| | Milestone | State |
|---|---|---|
| M0 | Repo skeleton, database schema, RLS, deployable collector | ✅ |
| M1 | Agent skeleton — config, state, tick loop | ⬜ |
| M2 | Metric and inventory collection | ⬜ |
| M3 | End-to-end write: spool → ship → Supabase | ⬜ |
| M4 | Log sources — journald behind an interface | ⬜ |
| M5 | Device registration + `install.sh` | ⬜ |
| M6 | Remote commands — pause / resume / delete | ⬜ |
| M7 | Threshold-triggered emergency flush + add-ons | ⬜ |
| M8 | Retention via `pg_cron` | ⬜ |
| M9 | Dashboard | ⬜ |

## Repository layout

```
agent/        Python agent — runs on the monitored machine
  core/         platform-independent: loop, config, state, spool, shipper
  logsources/   OS-specific log readers behind a common interface
collector/    FastAPI service on Fly.io — the write door
dashboard/    Next.js read UI
db/           schema, triggers, row-level security, retention
```

## Running the collector locally

```bash
cd collector
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
curl localhost:8080/health
```

## Database setup

Run against a Supabase project, in order:

```
db/schema.sql  →  db/triggers.sql  →  db/rls.sql
```

Order matters: triggers reference `accounts`, and the policies reference every table.

## License

MIT
