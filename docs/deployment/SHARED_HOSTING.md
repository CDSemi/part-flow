# PartFlow on Shared Hosting

> **Language:** English is the source of truth. [Tiếng Việt](./SHARED_HOSTING.vi.md).

This document records why shared web hosting is only conditionally possible for PartFlow, and what a provider must
confirm in writing before anyone considers it. Read it before you spend money on a shared-hosting plan. The decision
is: shared hosting is not the preferred PartFlow target and is not a drop-in replacement for the Docker deployment.
Use it only after the provider confirms every requirement below in writing for the exact plan. The hosts that
PartFlow supports are the company Synology NAS ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md)) and, as an alternative, a Linux
virtual private server ([`VPS.md`](./VPS.md)).

## 1. Why compatibility is conditional

PartFlow is not a static site. It requires:

- a Python 3.12-compatible process that serves ASGI (Asynchronous Server Gateway Interface), so that FastAPI and
  Uvicorn behave as designed;
- PostgreSQL with migrations, constraints, triggers, JSONB, arrays, indexes, transaction and locking behavior, and
  adequate privileges;
- a built React and Vite frontend with single-page application (SPA) fallback, that is, every unknown route returns
  the application's `index.html`;
- same-origin routing, so that `/api` reaches the backend on the same host name as the frontend;
- persistent environment variables and a controlled process restart;
- scheduled backups and maintenance commands;
- logs, health checks, and enough process and database resources;
- a way to release new versions and to recover.

Most shared hosting offers no Docker daemon and no root-level reverse-proxy control. Adapting PartFlow to a
provider-specific runtime would create a second deployment architecture that is harder to test and to migrate.

## 2. Hawk Host finding as of 2026-09-01

Hawk Host's documentation on 2026-09-01 states that Python applications are deployed through the cPanel tool **Setup
Python App**, which uses `mod_passenger`. The tool offers environment variables, start, stop and restart controls,
configuration management, and installation of several modules at once. Hawk Host also documents PostgreSQL access and
support-side whitelisting of remote PostgreSQL clients.

These facts do **not** show that the exact shared-hosting plan supports a native long-running ASGI (FastAPI)
application, control of the Uvicorn process, the PostgreSQL extensions and privileges PartFlow needs, custom `/api`
proxying, background schedules, or PartFlow's recovery procedures. Documentation of `mod_passenger` alone is not proof
of ASGI compatibility.

Official references:

- [Hawk Host: How to create a Python application](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
- [Hawk Host: Remote PostgreSQL connections](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)

## 3. Questions the provider must answer

Send the provider's support team these exact requirements and obtain written answers. The questions use Hawk Host as
the example, but they apply to any shared-hosting provider.

1. Does this plan support FastAPI as a native ASGI application, and what is the documented entry point? If an adapter
   is required, is it officially supported?
2. Can an application run Uvicorn or another ASGI server continuously, and how are restarts, timeouts, worker counts
   and logs controlled?
3. Which Python versions are available, and is Python 3.12 supported for the lifetime of the deployment?
4. Is PostgreSQL 16 available locally? If not, which version is available and what compatibility limits apply?
5. Can the database role create the schema objects the PartFlow migrations require, including triggers, constraints,
   expression and partial indexes, JSONB, arrays, and row locks?
6. Can Alembic (the database migration tool) run through SSH during a controlled release?
7. Can the domain route `/api/*` to Python while serving the Vite `dist/` directory with SPA fallback for every other
   route?
8. Are environment variables and secrets persistent, and are they excluded from web access and from backups shared
   with other users?
9. Are cron jobs, custom PostgreSQL dumps, an off-site copy, and a restore into a new database supported?
10. What limits apply to CPU, memory (RAM), processes, input and output, connections, execution time and inodes, and
    how are limit events reported?
11. Can application and database logs be kept or exported for diagnosis?
12. Is HTTPS automatic, and can access be restricted to company IP ranges or a virtual private network (VPN)?

Any unresolved "no", or any answer that depends on an unsupported workaround, makes the plan unsuitable.

## 4. If every requirement is confirmed

A shared-hosting deployment would be a separate deployment target with its own procedures:

- build the frontend in continuous integration (CI) and publish only `dist/`;
- package the backend using the provider's documented ASGI entry point;
- maintain a provider-specific procedure to install dependencies and restart the application;
- configure same-origin `/api` routing and SPA fallback;
- point `DATABASE_URL` to the provider's PostgreSQL database;
- run Alembic explicitly during maintenance;
- implement backup, manifest, restore test, monitoring and rollback procedures that work within the provider's limits;
- pass the same application, migration, quantity-integrity and authorization checks as the Docker production stack.

Do not replace PostgreSQL with MySQL, and do not move backend business rules into the frontend merely to fit shared
hosting.

## 5. Recommended decision

PartFlow runs in production on the company Synology NAS. When an external host becomes necessary, choose a small
Linux VPS with Docker Compose. It preserves one deployment architecture, gives proper control of ASGI and PostgreSQL,
and makes the move from the Synology NAS to the VPS a database migration rather than an application port.

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-09 | not yet committed | Rewritten as current content for new readers; the host recommendation now names the company Synology NAS as production. |
| 2026-09-02 | `8e5ab18` | Moved the Vietnamese mirror beside the English source and repointed links. |
| 2026-09-02 | `d2f68de` | First version of the shared-hosting assessment with its Vietnamese mirror. |
