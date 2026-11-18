# Installing PartFlow on a Synology NAS

> **Language:** English is the source of truth. [Tiếng Việt](./SYNOLOGY_NAS.vi.md).

This guide installs PartFlow production on the company Synology NAS, step by step, from an empty NAS to people working
in PartFlow over HTTPS. It then sets up the hourly backup and the monitoring, and shows how a new release is
installed. Follow the sections in order and do not skip a step: each step says what to do, what you should see, and
what to do when you see something else.

You need a DiskStation Manager (DSM) administrator account and a computer with a Secure Shell (SSH) client; no other
experience is assumed. Before you start, read [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §1 and §2 (ten minutes): they
explain what you are installing, the terms used here and the safety rules. Day-to-day operation after the
installation is in [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

## 1. Before you begin

### What you will have at the end

- PartFlow running on the NAS in the Compose project `partflow-production`, reachable at `https://<HOSTNAME>` from the
  company network, and, if you choose it, from the Internet through Cloudflare;
- the first Administrator created and the Scan Station devices enrolled;
- after §11, a verified backup every hour with an encrypted copy outside the NAS, email alerts when a check fails,
  and the history archive task.

### What you need

- A Synology NAS with DSM 7.2 or later on which the **Container Manager** package can be installed (Package Center
  offers it only for the models that support it).
- Free memory for PartFlow beyond what the NAS already uses: the three PartFlow containers are limited to about
  2.2 GB together, plus 512 MB while a one-shot job runs (§13).
- Free space on the volume for the images, the database and the local backups (§13).
- Internet access from the NAS: the build downloads the source code, the base images and the software packages.
- A DSM administrator account, and an SSH client on your computer (Windows 10 and 11 include `ssh` in Terminal and
  PowerShell).
- A host name for PartFlow, for example `partflow.yourdomain.com`, and a decision on how people reach it:
  - **Option A:** only from the company network or a virtual private network (VPN);
  - **Option B:** also from the Internet, through Cloudflare ([`SYNOLOGY_PUBLIC_HTTPS.md`](./SYNOLOGY_PUBLIC_HTTPS.md)).
- About one to two hours. Building the images takes most of that time.

### Values used in this guide

Replace these placeholders with your own values wherever they appear:

| Placeholder | Meaning | Example |
| --- | --- | --- |
| `<NAS_IP>` | The fixed local area network (LAN) address of the NAS | `192.168.0.11` |
| `<HOSTNAME>` | The name people type in the browser | `partflow.yourdomain.com` |
| `<TAG>` | The release tag to install ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §10.2) | `v1.0.0` |
| `<TIMEZONE>` | The factory's time zone as an IANA name | `America/Los_Angeles` |
| `/volume1` | The volume that holds the shared folder `docker`. If yours is another volume, change it in every command. | `/volume1` |

The port `18080` in this guide is the default of `PARTFLOW_HTTP_PORT`. To try a new release before installing it, use
the development stack on a workstation (root [`README.md`](../../README.md)); never install a second PartFlow stack on
this NAS.

## 2. Prepare DSM

Do these one-time settings in the DSM web interface.

1. **Install the packages.** Open **Package Center**, search for **Container Manager** and install it. During the
   installation DSM creates the shared folder `docker` on the volume you choose; note that volume (this guide uses
   `/volume1`). Then search for **Git Server** and install it: it provides the `git` command that the installation
   and every release need.
2. **Give the NAS a fixed address.** Reserve the NAS address in the DHCP settings of your router (recommended), or
   set it in **Control Panel → Network → Network Interface**, select the LAN connection, **Edit → IPv4 → Use manual
   configuration**. Write the address down as `<NAS_IP>`.
3. **Check the clock.** In **Control Panel → Regional Options → Time**, choose the time zone and select **Synchronize
   with NTP server**. Backups, certificates and logs depend on a correct clock.
4. **Enable SSH.** In **Control Panel → Terminal & SNMP → Terminal**, select **Enable SSH service** (port 22) and
   click **Apply**.
5. **Connect the uninterruptible power supply (UPS), recommended.** In **Control Panel → Hardware & Power → UPS**,
   enable UPS support so that the NAS shuts down safely during a power failure.

## 3. Open an SSH session as root

Every PartFlow command and every scheduled task runs as `root`, because Docker on DSM needs it. `root` is the
operating account of this installation ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §2.1); its records go to
`/root/partflow-deployments` and its monitoring state to `/root/partflow-monitoring`.

1. On your computer, open a terminal and connect with your DSM administrator account. The first time, answer `yes`
   to trust the NAS.

   ```sh
   ssh <your DSM administrator>@<NAS_IP>
   ```

2. Become `root`. `sudo` asks for the same password again; the prompt then ends with `#`.

   ```sh
   sudo -i
   ```

3. Check the tools. Each command prints a version. `python3` must be 3.8 or newer. If `git` is not found, Git Server
   is not installed (§2 step 1); if `docker` reports a permission error, you are not `root`.

   ```sh
   docker version --format '{{.Server.Version}}'
   docker compose version
   git --version
   python3 --version
   ```

4. Check whether the NAS can limit the processor use of a container. Some DSM kernels cannot, and Docker then refuses
   to start any container that has a processor limit (`NanoCPUs can not be set`):

   ```sh
   docker info 2>&1 | grep -i 'cfs'
   ```

   If this prints `No cpu cfs quota support`, remember it: §5 step 2 then switches the processor limits off. If it
   prints nothing, there is nothing to do.

5. Check that no other PartFlow stack exists on this NAS:

   ```sh
   docker ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u
   ```

   The list must contain neither `partflow-staging` nor `partflow`. If one of them appears, an older staging or
   development stack is still installed: stop it with `docker compose -p <that name> down` (without `-v`) and delete
   its Project in Container Manager. Its data volume only holds test data; delete it with
   `docker volume rm <that name>_postgres_data` once you are sure nobody needs it.

**Every later session** starts the same way: `ssh`, `sudo -i`, then the two lines below, which open the release
checkout and set the `PF` shorthand. A new session forgets `PF`, so set it again every time.

```sh
cd /volume1/docker/partflow/repo
PF="docker compose -f compose.production.yaml --env-file .env.production"
```

## 4. Create the PartFlow folders

Create the secrets folder and the backup folder, readable only by `root`:

```sh
mkdir -p /volume1/docker/partflow/secrets /volume1/docker/partflow/backups
chmod 700 /volume1/docker/partflow/secrets /volume1/docker/partflow/backups
ls -ld /volume1/docker/partflow/secrets /volume1/docker/partflow/backups
```

Both lines of the output start with `drwx------` and name `root`. The complete layout, once §5 has created `repo/`,
is:

```text
/volume1/docker/partflow/
  repo/       the release checkout; .env.production lives here
  secrets/    the three password files (PARTFLOW_SECRETS_DIR)
  backups/    one sub-folder per database backup (PARTFLOW_BACKUP_DIR)
  archive/    one sub-folder per history archive (PARTFLOW_ARCHIVE_DIR), created in §11.8
```

Never give the DSM group `Everyone` access to these folders, never share them with ordinary DSM users, and never put
them in a folder that a web server publishes.

## 5. Download the release and configure it

1. Download the repository and check out the release tag. The tags are listed on the GitHub Releases page of the
   repository, or with `git tag --list 'v*'` after the clone.

   ```sh
   cd /volume1/docker/partflow
   git clone https://github.com/CDSemi/part-flow.git repo
   cd repo
   git checkout <TAG>
   git describe --tags
   ```

   `git checkout` reports a "detached HEAD": that is normal for a release. `git describe --tags` prints `<TAG>`.

2. Create the configuration file from its template and fill in the four values that have no default:

   ```sh
   cp .env.production.example .env.production
   chmod 600 .env.production
   sed -i 's|^PARTFLOW_RELEASE=.*|PARTFLOW_RELEASE=<TAG>|' .env.production
   sed -i 's|^PARTFLOW_SECRETS_DIR=.*|PARTFLOW_SECRETS_DIR=/volume1/docker/partflow/secrets|' .env.production
   sed -i 's|^PARTFLOW_BACKUP_DIR=.*|PARTFLOW_BACKUP_DIR=/volume1/docker/partflow/backups|' .env.production
   sed -i 's|^PARTFLOW_SITE_TIMEZONE=.*|PARTFLOW_SITE_TIMEZONE=<TIMEZONE>|' .env.production
   grep -E '^PARTFLOW_(RELEASE|SECRETS_DIR|BACKUP_DIR|SITE_TIMEZONE|HTTP_PORT|EDGE_SUBNET)=' .env.production
   ```

   The `grep` line prints the four values you set and two defaults that later steps use (`PARTFLOW_HTTP_PORT` and
   `PARTFLOW_EDGE_SUBNET`); check each one. `PARTFLOW_SITE_TIMEZONE` is the factory's time zone: it dates Work Order
   completions, so set it before the first start. Leave every other value as it is; each key is explained in
   [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1.

   **Only if §3 step 4 printed `No cpu cfs quota support`**, switch the processor limits off (`0` means no limit; the
   memory limits stay):

   ```sh
   sed -i 's/^\(PARTFLOW_[A-Z]*_CPUS\)=.*/\1=0/' .env.production
   grep '_CPUS=' .env.production
   ```

3. Check that the subnet of the internal `edge` network, `172.30.250.0/24`, is not used by the NAS or the company
   network. This command must print nothing:

   ```sh
   ip -4 route | grep '172\.30\.250\.'
   ```

   If it prints a line, or if the company network uses addresses `172.30.250.x`, choose another unused subnet, for
   example `sed -i 's|^PARTFLOW_EDGE_SUBNET=.*|PARTFLOW_EDGE_SUBNET=172.30.251.0/24|' .env.production`.

## 6. Create the secret files

Create the three password files **before** any command that starts `backend` or a one-shot job. If a file is missing
at that moment, Docker Compose creates an empty folder with its name instead; delete that folder with `rmdir` and
create the file.

```sh
cd /volume1/docker/partflow/secrets
for name in postgres_password partflow_app_password partflow_maintenance_password; do
    openssl rand -base64 32 > "$name"
done
chmod 0444 postgres_password partflow_app_password partflow_maintenance_password
ls -l
cd /volume1/docker/partflow/repo
```

`ls -l` shows three files with the permissions `-r--r--r--` and a size of 45 bytes. Each file holds one random
password on one line. Copy the three values (`cat <file name>` displays one) into the company password vault.

Now set the `PF` shorthand and validate the configuration. `$PF config --quiet` prints nothing when the configuration
is valid; a message such as `Set PARTFLOW_SITE_TIMEZONE in .env.production` names an empty value to fill in.

```sh
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF config --quiet
```

## 7. Build the images and create the database

`deploy/production/pf install` runs the steps of this section and of §8 in one command, in this order, and stops at
the first one that fails; the steps below show what it does.

1. Build the two images, `backend` and `web`, from the checked-out release. This takes 10 to 30 minutes. If it stops
   with a network error, run the same command again.

   ```sh
   PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
   docker image ls 'partflow/*'
   ```

   The list shows `partflow/backend` and `partflow/web`, both with the tag `<TAG>`.

2. Start the database and wait until it is healthy. Run `$PF ps` again until the `db` line shows `(healthy)`, which
   takes about 30 seconds.

   ```sh
   $PF up -d db
   $PF ps
   ```

3. Create the two database roles that PartFlow uses, then create the database schema and its permissions:

   ```sh
   $PF --profile ops run --rm -T db-roles
   $PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"
   ```

   Each command prints a JSON report. The report of `migrate` contains `"result": "upgraded"`. `db-roles` creates the
   roles `partflow_app`, which `backend` uses, and `partflow_maintenance`; `backend` never connects as the database
   owner. If `migrate` reports `roles_not_provisioned`, run the `db-roles` command first.

The database starts empty, in the new volume `partflow-production_postgres_data`. To start from an existing backup
instead, for example when moving PartFlow from another host, follow the runbook §6 (New-instance restore) in place
of step 3, and create the history archive task (§11.8) only after you have checked the runs and the permanent
deletion requests in Administration → History archival & purge: a restored run in `PURGING` resumes its purge, and a
restored deletion request in `APPROVED` is carried out, at the task's first start (cancel such a request unless the
deletion is still wanted).

## 8. Start PartFlow and check it on the NAS

Start `backend` with **one** worker process, so that there is exactly one setup token for §10, and start `web`:

```sh
PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web
$PF ps
curl -fsS http://127.0.0.1:18080/web-health
curl -fsS http://127.0.0.1:18080/api/health
```

What you should see:

- `$PF ps` lists `db`, `backend` and `web`, each `(healthy)` after about 30 seconds;
- `/web-health` prints `live`;
- `/api/health` prints one line of JSON that contains `"status":"ok"`, `"release":"<TAG>"` and `"schema":"current"`.

If a check fails, read the last log lines with `$PF logs --tail=100 backend web`. The address
`http://<NAS_IP>:18080` does not work from another computer: `web` listens on the NAS's own loopback address only, by
design. People reach PartFlow through HTTPS, which §9 sets up.

## 9. Set up HTTPS

PartFlow requires HTTPS: its sign-in cookie is marked secure, so a browser keeps it only over HTTPS. The DSM reverse
proxy receives the HTTPS connections for `<HOSTNAME>` on port 443 and forwards them to `http://127.0.0.1:18080`.

- **Option A, company network or VPN only:** do §9.1, §9.2 and §9.3, then continue with §10.
- **Option B, also from the Internet:** follow [`SYNOLOGY_PUBLIC_HTTPS.md`](./SYNOLOGY_PUBLIC_HTTPS.md) from start to
  end. It uses §9.2 below for the reverse proxy rule and ends with its own test. Then continue with §10.

### 9.1 Host name and certificate (option A)

1. **Make the host name point to the NAS.** In the company's internal DNS server, or in the local DNS settings of the
   router, create an `A` record from `<HOSTNAME>` to `<NAS_IP>`. On a workstation, `nslookup <HOSTNAME>` must answer
   with `<NAS_IP>`.
2. **Get a certificate for the host name.** Open **Control Panel → Security → Certificate → Add → Add a new
   certificate → Next** and choose one source:
   - **Import certificate**, when the company has its own certificate authority (CA): select the private key, the
     certificate and the intermediate certificate that the CA issued for `<HOSTNAME>`.
   - **Get a certificate from Let's Encrypt**, only when `<HOSTNAME>` is a public DNS name that resolves to the
     company's public address and port 80 from the Internet reaches the NAS. DSM renews it automatically. For public
     access, option B is the documented design.
   - **Create self-signed certificate**, when neither is available. DSM first creates a root certificate, which is
     your private CA, then the PartFlow certificate signed by it: enter `<HOSTNAME>` as the common name and as the
     subject alternative name. Then select the new certificate, choose **Export certificate**, and take only the root
     certificate out of the downloaded archive (DSM usually names it `syno-ca-cert.pem`). The archive also holds
     private keys: never hand out the archive or a key file.
3. **Make every device trust the certificate.** For a company CA or the DSM root certificate, install the root
   certificate as trusted on every workstation and every Scan Station device, through the company's device management
   or by hand. On Windows: open the file, **Install Certificate → Local Machine → Place all certificates in the
   following store → Trusted Root Certification Authorities**. Never tell users to click past a browser warning.
4. **Note the expiry date.** DSM renews only Let's Encrypt certificates; replace any other certificate before it
   expires. The `check.sh` task of §11 warns 21 days before.

### 9.2 Create the reverse proxy rule

This rule is the same for option A and option B.

1. Open **Control Panel → Login Portal → Advanced → Reverse Proxy** and click **Create**.
2. On the **General** tab enter:

   | Field | Value |
   | --- | --- |
   | Reverse Proxy Name | `PartFlow` |
   | Source: Protocol, Hostname, Port | `HTTPS`, `<HOSTNAME>`, `443` |
   | Source: Enable HSTS | not selected for now; select it only once HTTPS works everywhere |
   | Destination: Protocol, Hostname, Port | `HTTP`, `127.0.0.1`, `18080` |

3. On the **Custom Header** tab, open **Create ▼**, choose **Create** (not WebSocket), and add the first row below.
   Do the same for the second row. If an identical row is already listed, do not add it twice.

   | Header Name | Value |
   | --- | --- |
   | `X-Forwarded-For` | `$proxy_add_x_forwarded_for` |
   | `X-Forwarded-Proto` | `$scheme` |

4. On the **Advanced Settings** tab set **Proxy send timeout** and **Proxy read timeout** to `300` seconds. If your
   DSM version shows a request body size setting, set it to at least 5 MiB. Leave the other settings as they are.
5. Click **Save**.
6. Assign the certificate to the rule: open **Control Panel → Security → Certificate → Settings**, find `<HOSTNAME>`
   in the list of services, choose the PartFlow certificate and click **OK**. Do not rely on DSM's default
   certificate.

Why these values:

- The destination is `127.0.0.1`, never `localhost` (which can mean the IPv6 address, where `web` does not listen),
  never the NAS address and never a Docker `172.x.x.x` address, because `web` listens on the loopback address only.
- The destination protocol is HTTP, because the connection stays inside the NAS and `web` has no certificate.
- `X-Forwarded-Proto` tells PartFlow that the browser used HTTPS; `X-Forwarded-For` gives `web` the user's address,
  which its sign-in rate limits use.
- Work Order imports can take up to 180 seconds and pictures can be 2 MiB, so shorter timeouts or a smaller request
  size make them fail.
- Never use the DSM ports 5000 and 5001 for PartFlow, and never make DSM administration reachable through
  `<HOSTNAME>`.

### 9.3 Test HTTPS (option A)

On a workstation of the company network:

1. Open `https://<HOSTNAME>/api/health`. The page shows JSON with `"status":"ok"`.
2. Open `https://<HOSTNAME>`. PartFlow opens and shows the **Set up PartFlow** button, and the browser shows a secure
   connection without any warning.

A certificate warning means that this workstation does not trust the root certificate yet (§9.1 step 3), or that the
name in the address bar is not the name in the certificate. A DSM error page means that the reverse proxy rule does
not match the name (§9.2).

## 10. Create the first Administrator and enroll the Scan Stations

Complete this section before you give the address to anyone: until the first Administrator exists, anyone who can
open PartFlow and read the backend log could create one.

1. On the NAS, read the setup token:

   ```sh
   $PF logs backend | grep "Setup token" | tail -n 1
   ```

   The line is a JSON log record whose message reads
   `PartFlow first-run setup is open: no administrator exists. Setup token: <token> — enter it in PartFlow …`.
   Copy only the token: the eight groups of four characters after `Setup token:`. Every restart of `backend` writes a
   new token, which is why the command keeps only the newest line.

2. In the browser, open `https://<HOSTNAME>`, click **Set up PartFlow**, paste the token, and create the first
   Administrator: a login name, a display name, a role among those offered (each of them may administer PartFlow) and
   a password. Setup then closes for good.
3. On the NAS, return `backend` to its configured number of workers and check that it is healthy again:

   ```sh
   $PF up -d backend
   $PF ps
   ```

4. Check that the database protection is in place. The output shows the line `"status": "pass"`:

   ```sh
   $PF run --rm --no-deps -T backend python -m app.cli reconcile --check h | grep -A2 '"id": "h"'
   ```

   This is the guard check "Append-only guards and database-role privileges intact" (reconcile check (h)). Any other
   status is an incident: do not start using PartFlow, and follow the runbook §7 (Repairing check (h) findings).

5. Check that uploads pass through the reverse proxy: in **Administration → Users**, open your own user, give it a
   picture of 1 to 2 MB and save. If it is refused as too large, see the runbook §8 (Common problems).
6. Enroll each Scan Station device: sign in, open **Administration → Scan Stations**, choose **Devices…** for the
   station, then **Enroll device…**, and enter the one-time code on the station within 15 minutes.

PartFlow is now in production. Give people the address `https://<HOSTNAME>`.

## 11. After go-live: backups and monitoring

These tasks are recommended and never block use. Until the backup task runs, the only backups are the ones that
`release.sh` takes before each release. Do them in this order; §11.8, the history archive task, is needed only before
the first archival run or the first permanent deletion of test data.

### 11.1 Email notifications

DSM sends an email when a scheduled task fails; this is the only alert channel. Open **Control Panel → Notification →
Email**, select **Enable email notifications**, enter the mail server (SMTP) settings and the recipient, and click
**Send a test email**. Continue only when the test email arrives.

### 11.2 Backup task

1. Open **Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**.
2. **General:** Task `PartFlow backup`, User `root`, **Enabled** selected.
3. **Schedule:** **Run on the following days: Daily**, first run time `00:00`, **Frequency** every hour, last run
   time `23:00`.
4. **Task Settings:** select **Send run details by email**, enter your address, and select **Send run details only
   when the script terminates abnormally**. In **Run command** enter:

   ```sh
   cd /volume1/docker/partflow/repo && deploy/production/backup.sh --kind scheduled --operator scheduler
   ```

5. Click **OK**. Select the task and click **Run** to test it. After about a minute, the backup folder has a new
   sub-folder whose name ends with `-scheduled`:

   ```sh
   ls /volume1/docker/partflow/backups
   ```

The task runs every hour, and **Administration → Backups** in PartFlow decides how often it takes a backup (initially
every hour) and how many it keeps (initially 48 hourly, 30 daily and 52 weekly backups, and the newest 3 pre-purge
backups that the history archive task of §11.8 takes before a purge or a permanent deletion; the two kinds share that
number, so raise **Pre-purge backups kept** before a session of test-data deletions when the pre-purge backup of an
earlier purge still matters, the runbook §3). A run with no backup due ends without writing anything. With a backup
every hour, at most one hour of work can be lost. Start releases a few minutes after the full hour: a running backup
stops a release before it changes anything (start the release again once the backup has finished), and a backup that
starts during a release is refused (you receive its failure email; the next hour's backup runs normally). Backups are
explained in the runbook §3.

A task of an earlier release with `--kind daily` now fails with a message that names this command: change its
schedule and command as above.

### 11.3 Encrypted copy outside the NAS

A backup that stays on the NAS is lost with the NAS. Copy the backup folder elsewhere with **Hyper Backup**:

1. Install **Hyper Backup** from Package Center, open it and create a **Data backup task**.
2. Choose a destination outside this NAS: another NAS, Synology C2 Storage or an S3-compatible service.
3. As the source, select the folders `docker/partflow/backups` and `docker/partflow/archive` (the archive folder of
   §11.8; select it once it exists). Once a run has purged its history from the database, its archive is the only
   copy of that history apart from the pre-purge backups that are still kept, so this copy is the archive's only copy
   outside the NAS unless you set a copy folder (§11.8). The backup folder also holds those pre-purge backups: this task
   covers them like every backup.
4. Enable **client-side encryption**. Keep the password and the key file outside the NAS: without them, the copies
   cannot be read.
5. Schedule it daily at `03:30`, between two backups. Set the version rotation as you decide, enable the weekly
   integrity check, and enable the email notification for failures. After the loss of the NAS, everything since the
   last copy is lost: schedule it more often if a day is too much.

Snapshots of the NAS volume are only a second layer: they never replace these backups and their copy outside the NAS.

### 11.4 Restore drill

Once after go-live, and then every quarter, prove that a backup can be restored and measure how long it takes. Use the
name of the newest scheduled backup (the folder name from §11.2):

```sh
cd /volume1/docker/partflow/repo
deploy/production/restore-test.sh --backup <backup name> --operator "<your name>"
```

The drill restores the backup into a separate, temporary stack, checks it and removes it; it never touches the
production database. Exit code 0 means it passed. The evidence file and its timings are explained in the runbook §4.

### 11.5 Monitoring tasks

Create two more tasks as in §11.2 (User `root`, email only when the script terminates abnormally):

| Task | Schedule | Run command |
| --- | --- | --- |
| `PartFlow check` | Daily, first run `00:00`, repeat every 15 minutes, last run `23:45` | `cd /volume1/docker/partflow/repo && HOME=/root deploy/production/check.sh --url https://<HOSTNAME> --quiet` |
| `PartFlow reconcile` | Daily at `04:00` | `cd /volume1/docker/partflow/repo && HOME=/root deploy/production/scheduled-reconcile.sh` |

`HOME=/root` tells the scripts where to keep their state, in case Task Scheduler does not set it. Adjust the check
command to your HTTPS option:

- **Option A with a company or DSM certificate authority:** copy the root certificate to the NAS, for example to
  `/volume1/docker/partflow/ca.pem`, and add `--cacert /volume1/docker/partflow/ca.pem`. If the NAS cannot resolve
  `<HOSTNAME>` itself, also add `--resolve-to 127.0.0.1`.
- **Option B (Cloudflare):** add nothing. The check reaches PartFlow through Cloudflare, like a user.

Test the check task: select it, click **Run**, then read its result on the NAS. The first line shows the time and the
exit code (`exit 0`); every other line starts with `PASS`, or `SKIP` for a check that does not apply (the lines
`disk_archive` and `archival_proposal` are `SKIP` until §11.8 is done; before that, `archival_proposal` fails only for a
permanent deletion request that has waited longer than 15 minutes, since nothing carries it out):

```sh
cat /root/partflow-monitoring/last-check.txt
```

A `FAIL` line names the problem; the runbook §2 explains each check. `check.sh --quiet` prints nothing and ends
normally when everything passes, so you receive an email only for a failure, and an unchanged failure is repeated
every 6 hours.

### 11.6 Test the alert email

Make the check fail on purpose once, to prove that the email reaches you:

1. Edit the `PartFlow check` task and add `--only disk_backup --min-free-percent 100 --renotify-hours 0` at the end of
   its command. A minimum of 100 % free space fails on purpose.
2. Click **Run**. Within a few minutes you receive an email from DSM that the task ended abnormally.
3. Edit the task again and remove the three added options.

### 11.7 Firewall

Allow only the networks that need PartFlow. Open **Control Panel → Security → Firewall**, select **Enable firewall**
and edit the rules of the active profile:

1. First add allow rules for your administration: ports 5000 and 5001 (DSM) and 22 (SSH) from the computers or subnet
   you administer from. Without them, the firewall locks you out of DSM.
2. Add an allow rule for port 443 from the company subnets or the VPN (option A). For option B, follow
   [`SYNOLOGY_PUBLIC_HTTPS.md`](./SYNOLOGY_PUBLIC_HTTPS.md) §12.
3. Set the action for everything else to **Deny access**, apply, and check that you can still open DSM and PartFlow.
4. Check that the containers still work behind the firewall: run the full check by hand (with the same options as
   the scheduled task) and expect only `PASS` and `SKIP` lines.

   ```sh
   deploy/production/check.sh --url https://<HOSTNAME> --no-state
   ```

   If a check fails only since the firewall was enabled, the firewall blocks the containers' own networks. List their
   subnets and add an allow rule for each of them:

   ```sh
   docker network inspect partflow-production_internal partflow-production_edge --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}}{{end}}'
   ```

### 11.8 History archive task

PartFlow can move old Movement history into archive files and then delete it from the database (History archival &
purge in Administration; the runbook §11). **A run deletes history:** once its archive is copied off the NAS, the job
takes a pre-purge backup and purges the archived Movements from the database. **A permanent deletion request deletes
too:** the same job carries out the deletion of Work Orders and Part Numbers entered only for testing, requested in
PartFlow (**Delete permanently…**) or with `pf delete-permanently` (the runbook §10), within 5 minutes of the request:
it takes a pre-purge backup and deletes them from the database. Once **Automatic runs** are switched on in
Administration (they are off initially), the same task also creates the runs that their measure calls for, at most once
an hour: a run then waits for a person to approve it, or, with the approval step switched off, runs at once. The
history archive job needs an archive folder on the NAS, optionally a copy folder on another device, and a task that
starts it every 5 minutes. Do this before the first run is requested in Administration, before automatic runs are
switched on and before the first deletion is requested: **without this task, a deletion request is never carried out
and no automatic run is ever created**, `pf delete-permanently --confirm` refuses while the archive folder is not set,
and `check.sh` reports a request made in PartFlow meanwhile as `FAIL archival_proposal deletion_request_waiting` once
it has waited 15 minutes (cancel it in PartFlow, or finish this section).

1. **Create the archive folder**, owned by `root` (the account that runs the task) and readable by `root` only:

   ```sh
   mkdir -p /volume1/docker/partflow/archive
   chown root:root /volume1/docker/partflow/archive
   chmod 700 /volume1/docker/partflow/archive
   ls -ld /volume1/docker/partflow/archive
   ```

   The line starts with `drwx------` and names `root`. The history archive job runs as `root` too and writes there.

2. **Set it in the configuration file.** The two commands work whether or not the line already exists:

   ```sh
   cd /volume1/docker/partflow/repo
   sed -i '/^PARTFLOW_ARCHIVE_DIR=/d' .env.production
   echo 'PARTFLOW_ARCHIVE_DIR=/volume1/docker/partflow/archive' >> .env.production
   grep '^PARTFLOW_ARCHIVE' .env.production
   ```

3. **Optional: a copy folder on another device.** Without it, every archival run waits until you confirm in
   Administration that its archive was copied off the NAS (the Hyper Backup task of §11.3 does that copy; the runbook
   §11 explains the confirmation). With it, each run copies its archive there, checks the copy, and goes on by itself
   with the pre-purge backup and the purge. The folder must be on **another device**, mounted on the NAS, writable by
   `root`:
   - a USB disk: DSM mounts it under `/volumeUSB1/usbshare`; create a folder on it, for example
     `mkdir -p /volumeUSB1/usbshare/partflow-archive-copy`;
   - or a shared folder of another server: in **File Station → Tools → Mount Remote Folder → CIFS Shared Folder**,
     mount it on an empty folder of the NAS, for example `/volume1/docker/partflow/archive-copy`, with **Mount
     automatically on startup** selected.

   Then set the folder in the configuration file:

   ```sh
   sed -i '/^PARTFLOW_ARCHIVE_COPY_DIR=/d' .env.production
   echo 'PARTFLOW_ARCHIVE_COPY_DIR=/volumeUSB1/usbshare/partflow-archive-copy' >> .env.production
   ```

   The job uses the copy folder only while it exists and lies on a file system that is neither the archive folder's
   nor the NAS's system partition `/`, and it checks this again before every step. When the disk or the share is not
   mounted, its path is a folder on the system partition: the job copies nothing there, the run waits, and `check.sh`
   reports `copy_dir_unusable` with the reason. Never create the copy folder by hand while the disk or share is out.

4. **Create the task** as in §11.2 (User `root`, email only when the script terminates abnormally):
   - **General:** Task `PartFlow history archive`.
   - **Schedule:** **Run on the following days: Daily**, first run time `00:00`, **Frequency** every 5 minutes, last
     run time `23:55`.
   - **Run command:**

     ```sh
     cd /volume1/docker/partflow/repo && HOME=/root deploy/production/archive.sh --scheduled
     ```

   Every start first checks, in a few seconds, whether a permanent deletion request is open, and carries it out at
   once: the pre-purge backup in the backup folder, the deletion, and the reconciliation after it. Most starts then
   end. Once an hour, at the first start at or after the **Start minute** set in Administration → History archival &
   purge (20 initially), the task does the hourly work: it first evaluates the automatic runs (it creates one when they
   are on and due), then exports, verifies and copies an approved run, takes its pre-purge backup in the backup folder,
   purges the archived Movement history from the database and runs the reconciliation after it. A start that carried
   out a deletion leaves the hourly work to the next start. The very first start only records the current hour, so
   the first hourly work comes at the next Start minute. A refusal or a failure sends one email for its hour, except
   that while a deletion request is open, every start that cannot carry it out sends one; exit code 4 is an incident
   (the reconciliation after a purge or a deletion did not end clean: the runbook §8), and until it is closed every
   hourly start refuses with exit code 1, creates no automatic run, works on no run and carries out no deletion
   request. Keep the Start minute away from minute 0: a pre-purge backup waits for the hourly backup while
   that one runs (up to 60 minutes), and a release is refused while the task works.

5. **Test it.** Select the task and click **Run**. Then check that the job can reach its folder and the database,
   which prints the work's lines and ends with a line that starts with `EVALUATE` when it worked on no run
   (`EVALUATE off - -` while automatic runs are off and no run is open: then nothing is exported or deleted), with
   `ARCHIVE` when it worked on a run, or with `DELETION` when it carried out a deletion request:

   ```sh
   deploy/production/pf archive
   deploy/production/check.sh --url https://<HOSTNAME> --only disk_archive --only archival_proposal --no-state
   ```

   Both check lines start with `PASS`. Add the options of your `PartFlow check` task (`--cacert`, `--resolve-to`).

6. Add the archive folder to the Hyper Backup task (§11.3, step 3), if you have not done so yet.

**Before any restore of the database** (a rollback, a restore after an incident, a move to a new NAS: the runbook §6
and §8), untick **Enabled** for this task, and tick it again only after you have checked the runs and the permanent
deletion requests in Administration → History archival & purge. A restored backup that holds a run in `PURGING` (for
example the hourly backup taken while a purge ran) resumes that purge at the task's next start, unless the task is
disabled or the incident record of the runbook §8 exists. A restored backup that holds a deletion request in
`APPROVED` (taken between the request and its deletion) has it carried out at the task's next start: cancel it in
PartFlow first unless the deletion is still wanted. To undo a purge or a deletion, restore its pre-purge backup: the
run or the request is `RUNNING` in it, the next start fails it, and nothing is purged or deleted again.

## 12. Installing a new release

Every later release is installed with `deploy/production/release.sh`. It takes a verified backup, builds the new
images without touching the running ones, migrates the database, switches PartFlow to the new release and checks it.
Choose a time when no Work Order import runs, a few minutes after the full hour, once the hourly backup has finished
and well before the Start minute of the history archive task: a release is refused while that task does its hourly
work or carries out a permanent deletion request (start it again once the work has finished; a deletion takes a few
minutes for its pre-purge backup and reconciliation), and the task refuses while a release runs. A run's purge, and a
first large run, can keep the task busy for long.

```sh
cd /volume1/docker/partflow/repo
git fetch --tags
git checkout <NEW_TAG>
deploy/production/release.sh --release <NEW_TAG> --operator "<your name>" --approver "<your name>"
```

`deploy/production/pf update <NEW_TAG>` runs the same three commands.

- Exit code 0 means the new release runs and its automatic checks passed. Then check by hand with test data: sign in,
  scan at a Scan Station, and save a change and read it back. Open pages show an update notice and reload themselves:
  a Scan Station or Production Board after a 10-second countdown in the notice, every other page once nobody has used
  it for 1 minute and nothing on it is unsaved. Whoever is working on a page can click **Reload page** sooner.
- Any other exit code: read the message and follow the runbook §5 (the exit code table). Nothing reopens by itself
  when `release.sh` has stopped writes.
- `.env.production` is not tracked by git, so it stays in place; `release.sh` writes the new tag into it at the switch.
  The scheduled tasks need no change, because they run in the same folder.
- Keep the previous release's images (never `docker image prune -a`) until you are sure you will not roll back
  (runbook §6). Before a rollback that restores a backup, disable the history archive task (§11.8), check the runs
  and the permanent deletion requests after the restore, and cancel in PartFlow every deletion request found `APPROVED`
  that is no longer wanted before you enable the task again.

## 13. Capacity and resource limits

A general memory or processor figure does not prove that this NAS is large enough. Measure during the first weeks of
use: the processor and memory use of every PartFlow container at rest and at peak (`docker stats --no-stream`), the
growth of the database, the duration and size of the backups, the duration of a restore drill, the response time
from the shop floor, and the behaviour during a NAS restart and a power cut.

Set the limits in `.env.production` from those measurements, then apply them at a quiet time with
`$PF up -d db backend web`, which restarts the containers whose limits changed. Before that, keep the recent backend
errors with `deploy/production/check.sh --url https://<HOSTNAME> --only errors --no-state` (runbook §2). On a NAS
without processor quota support (§3 step 4), the `CPUS` values stay `0`.

| Setting | Default |
| --- | --- |
| `PARTFLOW_DB_MEMORY`, `PARTFLOW_DB_CPUS` | `1g`, `1.0` |
| `PARTFLOW_BACKEND_MEMORY`, `PARTFLOW_BACKEND_CPUS` | `1g`, `2.0` |
| `PARTFLOW_WEB_MEMORY`, `PARTFLOW_WEB_CPUS` | `128m`, `0.5` |
| `PARTFLOW_OPS_MEMORY`, `PARTFLOW_OPS_CPUS` (each one-shot job) | `512m`, `1.0` |

Keep enough free disk space for the database, the images of at least one more release, temporary migration work and
the local backups. A backup needs free space of twice the newest backup plus 1 GiB in the backup folder; a restore
drill needs five times the backup plus 1 GiB on the Docker storage. The `check.sh` disk checks fail below 15 % free
space: `disk_data` for the database volume, `disk_backup` for the backup folder and `disk_docker` for the Docker
storage (images and container logs) and `disk_archive` for the archive folder (§11.8). The archive folder grows with
every archival run; nothing removes archives automatically. The backup folder also holds the pre-purge backups that
the history archive task takes before each purge and each permanent deletion (the newest 3 by default, one dump each,
both kinds together: a series of deletions can rotate out the pre-purge backup of an earlier purge). A purge frees
space inside the database for new Movements, so the database stops growing, but its files get smaller only with
`VACUUM FULL` (the runbook §11). `/root/partflow-monitoring/growth.tsv` records the database size once a day.

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-18 | uncommitted | §11.8: the history archive task also creates the automatic runs once they are switched on (none without the task), its hourly work evaluates them first, and its test ends with the `EVALUATE` line when it worked on no run. |
| 2026-11-18 | `1874636` | §11.8: the history archive task also carries out permanent deletion requests within 5 minutes, after a pre-purge backup, and a deletion needs it; §7, §11.8 and §12: after a restore, check the deletion requests too and cancel the `APPROVED` requests that are no longer wanted, because a restored `APPROVED` request is carried out; §8: `check.sh` reports a deletion request that waits, also while archival is not set up; §11, §11.2, §12 and §13: the pre-purge backups of deletions, which share Pre-purge backups kept with purges, and releases refused during a deletion. |
| 2026-11-18 | `d094f5e` | §7, §11.8 and §12: disable the history archive task before any restore and check the runs before enabling it, because a restored `PURGING` run resumes its purge. |
| 2026-11-18 | `d094f5e` | §11.8: a run purges its archived Movement history after the off-host copy and a pre-purge backup, then reconciles, and an incident stops the task's work until it is closed; the Start minute away from the hourly backup; §11.2, §11.3, §12 and §13: the pre-purge backups in the backup folder and in Hyper Backup, the archive as the only copy after a purge, a long purge before a release, and `VACUUM FULL`. |
| 2026-11-17 | `2f12d33` | §11.8: the archive folder, the optional copy folder and the 5-minute history archive task, which exports and copies archives and never deletes anything; §4, §11.3, §11.5, §12 and §13 for the archive folder. |
| 2026-11-16 | `5708664` | §7, §12: `deploy/production/pf install` and `pf update` run these steps. |
| 2026-11-16 | `c1f042b` | §1, §11.2: the backup task runs every hour with `--kind scheduled`, and Administration → Backups sets the interval and how many backups are kept; §11.3, §11.5, §12: times around the hourly backup. |
| 2026-11-12 | `de4d99f` | §12: a Scan Station or Production Board reloads after a 10-second countdown in the notice instead of after a minute, and every other page once idle for 1 minute instead of 2. |
| 2026-11-12 | `77a2dfb` | §12: pages open during a release now reload themselves (a Scan Station or Production Board after about a minute, every other page once idle for 2 minutes with nothing unsaved). |
| 2026-11-10 | `e0e5056` | Rewritten as one linear installation from an empty NAS to people working in PartFlow, with every DSM path and command in order; the HTTPS step offers the LAN option or the Cloudflare guide; added the check for kernels without processor quota support and the firewall check of the container networks; the optional NAS staging and the move procedure were removed (the move is now in the runbook §6). |
| 2026-11-09 | `398c617` | Restored small items the rewrite had dropped: the health check in the staging smoke test, the off-NAS copy and restore test of staging dumps, the staging reconciliation, the full name of reconcile check (h) and the full `revision` command; step 1 now covers a NAS where staging was never installed. |
| 2026-11-09 | `b405c6e` | Rewritten as current content for new readers; pilot and release gates removed by owner decision. |
| 2026-10-09 | `03bd90c` | Added the monitoring tasks and the disk thresholds of the host checks. |
| 2026-10-08 | `a330383` | Added the production backup tasks, the off-NAS encrypted copy and the restore drill. |
| 2026-10-08 | `1116b07` | Added the production database roles to the installation steps. |
| 2026-10-08 | `b820e7a` | Added the release flow with `release.sh` for later releases. |
| 2026-10-08 | `d7e86a9` | Added the production stack, the DSM reverse proxy settings and the certificate options. |
| 2026-10-06 | `702ab1c` | Added first-run Administrator setup and Scan Station device enrollment. |
| 2026-09-02 | `d2f68de` | First version of the Synology NAS guide with its Vietnamese mirror. |
