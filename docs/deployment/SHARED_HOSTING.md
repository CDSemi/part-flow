# PartFlow on Shared Hosting

> **Decision:** Shared hosting is not the preferred PartFlow target and is not a
> drop-in replacement for the Docker deployment. Use only after the provider
> confirms every compatibility requirement in writing for the exact plan.
>
> **Language:** English is the source of truth. [Tiếng Việt](./SHARED_HOSTING.vi.md).

## 1. Why compatibility is conditional

PartFlow is not a static site. It requires:

- a Python 3.12-compatible ASGI process for FastAPI/Uvicorn semantics;
- PostgreSQL with migrations, constraints, triggers, JSONB, arrays, indexes,
  transaction/locking behavior, and adequate privileges;
- a built React/Vite frontend with SPA fallback;
- same-origin routing from `/api` to the backend;
- persistent environment variables and controlled process restart;
- scheduled backups and maintenance commands;
- logs, health checks, and enough process/database resources;
- an operational path for releases and recovery.

Most shared hosting gives no Docker daemon or root-level reverse-proxy control.
Adapting PartFlow to a provider-specific runtime can create a second deployment
architecture that is harder to test and migrate.

## 2. Hawk Host finding as of 2026-09-01

Hawk Host's current documentation states that Python applications are deployed
through cPanel **Setup Python App** using `mod_passenger`, with environment
variables, start/stop/restart controls, configuration management, and bulk
module installation. Hawk Host also documents PostgreSQL access and support-side
whitelisting for remote PostgreSQL clients.

Those facts do **not** establish that the exact shared-hosting plan supports a
native long-running ASGI/FastAPI application, Uvicorn process control, required
PostgreSQL extensions/privileges, custom `/api` proxying, background schedules,
or PartFlow's recovery procedures. `mod_passenger` documentation alone is not
proof of ASGI compatibility.

Official references:

- [Hawk Host: How to create a Python application](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
- [Hawk Host: Remote PostgreSQL connections](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)

## 3. Questions Hawk Host must answer

Send support the exact requirements and obtain written answers:

1. Does this plan support FastAPI as a native ASGI application, and what is the
   documented entry point? If an adapter is required, is it officially
   supported?
2. Can an application run Uvicorn or another ASGI server continuously, and how
   are restarts, timeouts, worker counts, and logs controlled?
3. Which Python versions are available, and is Python 3.12 supported for the
   lifetime of the deployment?
4. Is PostgreSQL 16 available locally? If not, which version and compatibility
   limits apply?
5. Can the database role create the schema objects PartFlow migrations require,
   including triggers, constraints, expression/partial indexes, JSONB, arrays,
   and row locks?
6. Can Alembic run through SSH during a controlled release?
7. Can the domain route `/api/*` to Python while serving the Vite `dist/` tree
   with SPA fallback for every other route?
8. Are environment variables/secrets persistent and excluded from web access
   and backups shared with other users?
9. Are cron jobs, custom PostgreSQL dumps, off-site copy, and restore into a new
   database supported?
10. What CPU, RAM, process, I/O, connection, execution-time, and inode limits
    apply, and how are limit events reported?
11. Can application and database logs be retained/exported for operational
    diagnosis?
12. Is HTTPS automatic, and can access be restricted to company IP/VPN ranges?

Any unresolved “no” or unsupported workaround makes the plan unsuitable.

## 4. If every requirement is confirmed

A shared-hosting deployment would be a separate Phase 16 target:

- build the frontend in CI and publish only `dist/`;
- package the backend using the provider's documented ASGI entry point;
- maintain a provider-specific dependency install and restart procedure;
- configure same-origin `/api` routing and SPA fallback;
- point `DATABASE_URL` to the provider PostgreSQL database;
- run Alembic explicitly during maintenance;
- implement provider-compatible backup, manifest, restore test, monitoring, and
  rollback procedures;
- run the same application, migration, quantity-integrity, and authorization
  gates as Docker production.

Do not replace PostgreSQL with MySQL and do not move backend business rules into
the frontend merely to fit shared hosting.

## 5. Recommended decision

Use Synology for restricted internal staging. When an external host becomes
necessary, choose a small Linux VPS with Docker Compose. It preserves one
deployment architecture, provides proper ASGI/PostgreSQL control, and makes the
Synology-to-VPS move a database migration rather than an application port.

