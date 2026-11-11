# Synology Public HTTPS for PartFlow
## Cloudflare + UniFi Gateway + Synology DSM Reverse Proxy

> **English is the source of truth.**  
> Vietnamese mirror: [`SYNOLOGY_PUBLIC_HTTPS.vi.md`](./SYNOLOGY_PUBLIC_HTTPS.vi.md)

This guide explains, from beginning to end, how to publish a **PartFlow production deployment running in Synology Container Manager** at a clean HTTPS URL such as:

```text
https://partflow.yourdomain.com
```

It is written for a reader who is new to DDNS, Cloudflare proxying, port forwarding, certificates, and DSM reverse proxying. Each section therefore includes the navigation path, buttons, fields, values, and verification steps.

The recommended architecture uses:

- **Cloudflare** for authoritative DNS, proxying, TLS at the edge, Origin CA, and Origin Rules when required.
- **UniFi Gateway / UDM** for Cloudflare DDNS and WAN port forwarding.
- **Synology DSM** for TLS termination at the NAS and reverse proxying to PartFlow.
- **Synology Container Manager** for the PartFlow production containers.

This guide covers both cases:

1. Public WAN TCP 443 is available.
2. Public WAN TCP 443 is already used by another service.

---

# 1. Example values used in this guide

Replace these values with your own.

| Purpose | Example |
| --- | --- |
| Cloudflare zone | `yourdomain.com` |
| DDNS hostname | `ddns.yourdomain.com` |
| PartFlow public hostname | `partflow.yourdomain.com` |
| Synology NAS LAN IP | `192.168.0.11` |
| PartFlow production host port | `18080` |
| Alternate public WAN port when 443 is occupied | `8443` |

Equivalent variables:

```text
ZONE               = yourdomain.com
DDNS_HOST          = ddns.yourdomain.com
APP_HOST           = partflow.yourdomain.com
NAS_IP             = 192.168.0.11
PARTFLOW_HTTP_PORT = 18080
ALT_WAN_PORT       = 8443
```

A dedicated application hostname such as `partflow.yourdomain.com` is recommended instead of placing PartFlow directly at the zone apex because it is easier to route, troubleshoot, replace, and protect independently.

---

# 2. Expected PartFlow production layout

PartFlow production publishes the `web` container only on the NAS loopback interface:

```yaml
ports:
  - "127.0.0.1:${PARTFLOW_HTTP_PORT}:80"
```

The default production value is:

```text
PARTFLOW_HTTP_PORT=18080
```

The intended local path is therefore:

```text
DSM host 127.0.0.1:18080
        ↓
PartFlow web container :80
        ↓
PartFlow backend
        ↓
PostgreSQL
```

Do **not** use port `5173` for production. Port `5173` belongs to the development/staging Vite server.

The expected Docker networks are:

```text
partflow-production_edge
  ├─ web
  └─ backend

partflow-production_internal
  ├─ backend
  └─ db
```

Do not modify this network separation merely to make external access work.

---

# 3. Final traffic paths

## 3.1 Case A — Public WAN TCP 443 is available

```text
Browser
  │ HTTPS :443
  ▼
Cloudflare
  │ HTTPS :443
  ▼
Public WAN IP :443
  │
  ▼
UniFi Gateway
  │ WAN 443 → NAS 443
  ▼
Synology DSM Reverse Proxy
  │ HTTPS partflow.yourdomain.com:443
  ▼
HTTP 127.0.0.1:18080
  │
  ▼
PartFlow
```

## 3.2 Case B — Public WAN TCP 443 is already used

Keep the existing service on WAN 443 unchanged.

Cloudflare still accepts the browser connection on normal HTTPS port 443, but an Origin Rule changes the port used by Cloudflare to reach your public WAN address.

```text
Browser
  │ HTTPS :443
  ▼
Cloudflare
  │ Origin Rule: destination port → 8443
  ▼
Public WAN IP :8443
  │
  ▼
UniFi Gateway
  │ WAN 8443 → NAS 443
  ▼
Synology DSM Reverse Proxy :443
  │
  ▼
HTTP 127.0.0.1:18080
  │
  ▼
PartFlow
```

The user still enters only:

```text
https://partflow.yourdomain.com
```

No `:8443` appears in the browser.

---

# Part A — Cloudflare DDNS

# 4. Confirm that UniFi has a public WAN IPv4 address

Before creating DDNS or port-forward rules:

1. Open **UniFi Network**.
2. Go to **Settings → Internet**.
3. Open the active WAN connection.
4. Note the WAN IPv4 address.
5. From a browser, check your public IPv4 address using a public "what is my IP" service.
6. Confirm that the two addresses match.

The UniFi WAN address should not be in these private/CGNAT ranges:

```text
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
100.64.0.0/10
```

If the WAN address is in one of those ranges, resolve double NAT or CGNAT before continuing.

---

# 5. Create the initial DDNS A record in Cloudflare

Using `0.0.0.0` as the initial value makes DDNS verification very obvious: after UniFi runs successfully, the record changes from `0.0.0.0` to the real public WAN IPv4 address.

1. Sign in to the **Cloudflare Dashboard**.
2. Select the zone:
   ```text
   yourdomain.com
   ```
3. In the left navigation, open:
   ```text
   DNS → Records
   ```
4. Click:
   ```text
   Add record
   ```
5. Enter:
   ```text
   Type:          A
   Name:          ddns
   IPv4 address:  0.0.0.0
   TTL:           Auto
   ```
6. Leave the record **DNS only**. In the current Cloudflare UI, a placeholder such as `0.0.0.0` may make proxying unavailable automatically; do not try to force this DDNS record to Proxied.
7. Click:
   ```text
   Save
   ```

The initial record should now be conceptually:

```text
ddns.yourdomain.com → 0.0.0.0
```

Do not manually replace `0.0.0.0` with your current WAN address. The next steps verify that UniFi performs the update.

---

# 6. Create the Cloudflare API token for UniFi DDNS

Use an **Account API token** with the minimum permissions needed by UniFi/Inadyn.

## 6.1 Open the Account API token page

1. In the Cloudflare Dashboard, expand:
   ```text
   Manage account
   ```
2. Click:
   ```text
   Account API tokens
   ```
3. Click:
   ```text
   Create Token
   ```

## 6.2 Select the built-in `Edit zone DNS` template

On the **Create a token** page:

1. Enter a descriptive token name, for example:
   ```text
   UniFi DDNS
   ```
2. Under **Permission policies**, locate the built-in template:
   ```text
   Edit zone DNS
   ```
3. Click the **Edit zone DNS** template.

The template initially provides DNS write access. Do not choose `Full account and zone access`, `Write all resources`, or another broad template.

## 6.3 Edit the template policy

After selecting the template:

1. In **Permission policies**, locate the policy card created by the template.
2. Click the **pencil icon** on the right side of the card:
   ```text
   Edit policy
   ```
3. At the top of the policy editor, change the resource selector from all domains/zones to:
   ```text
   Specified Domains
   ```
4. In the domain selector next to it, choose:
   ```text
   yourdomain.com
   ```
5. Expand:
   ```text
   DNS & Zones
   ```
6. On the **DNS** row, select:
   ```text
   Edit
   ```
7. On the **Zone** row, select:
   ```text
   Read
   ```
8. Leave the other DNS & Zones permissions unchecked.

The final permission set should be exactly:

```text
DNS  → Edit
Zone → Read
```

The UI should show approximately:

```text
DNS & Zones   2/12
```

## 6.4 Token expiration

In **Token expiration**:

1. Select:
   ```text
   No expiration
   ```
   unless company policy requires periodic token rotation.

## 6.5 Client IP address filtering

In **Client IP address filtering**:

1. Leave the IP/CIDR field empty.
2. Do not restrict the token to the current WAN IP.

A DDNS token must continue to work after the ISP changes the WAN address.

## 6.6 Review and create

1. Click:
   ```text
   Review token
   ```
2. Verify:
   - only `yourdomain.com` is selected;
   - `DNS Edit` is granted;
   - `Zone Read` is granted;
   - no unnecessary permissions are present.
3. Click:
   ```text
   Create Token
   ```
4. Copy the token secret immediately and store it securely.

Treat the token like a password. Do not commit it to Git or include it in screenshots or documentation.

---

# 7. Configure Cloudflare DDNS in UniFi

## 7.1 Open the active WAN settings

1. Open **UniFi Network**.
2. Go to:
   ```text
   Settings → Internet
   ```
3. Select the WAN interface that has the public Internet connection.
4. Find:
   ```text
   Dynamic DNS
   ```
5. Click:
   ```text
   Create New Dynamic DNS
   ```

## 7.2 Enter the Cloudflare DDNS values

Select:

```text
Service: Cloudflare
```

On current Cloudflare-specific UniFi forms, enter:

```text
Hostname:  ddns.yourdomain.com
Zone Name: yourdomain.com
API Token: <token created in Section 6>
```

If a UniFi build displays the older generic Inadyn-style form instead, use the labels shown by that build and follow the Cloudflare/Inadyn mapping provided by UniFi. Do not use a Global API Key when the native API-token option is available.

Click:

```text
Save
```

## 7.3 Verify that DDNS actually changed the record

Return to:

```text
Cloudflare Dashboard
→ yourdomain.com
→ DNS
→ Records
```

Find:

```text
ddns.yourdomain.com
```

A successful DDNS update should change the content from:

```text
0.0.0.0
```

to your real public WAN IPv4 address.

This is the easiest visual confirmation that UniFi performed the update.

Then verify from a workstation:

```powershell
nslookup ddns.yourdomain.com 1.1.1.1
```

The returned IPv4 address should match the public WAN IPv4 address shown by UniFi.

Keep this DDNS record **DNS only**.

---

# Part B — Create the PartFlow public hostname

# 8. Create `partflow.yourdomain.com` in Cloudflare DNS

Do not skip directly to record values; follow the full navigation path.

1. Sign in to the **Cloudflare Dashboard**.
2. Select:
   ```text
   yourdomain.com
   ```
3. In the left navigation, click:
   ```text
   DNS
   ```
4. Click:
   ```text
   Records
   ```
5. Click:
   ```text
   Add record
   ```
6. Enter:
   ```text
   Type:    CNAME
   Name:    partflow
   Target:  ddns.yourdomain.com
   TTL:     Auto
   ```

## 8.1 Choose the correct Proxy status

For the final architecture in this guide, set:

```text
Proxy status: Proxied
```

The cloud icon should be orange.

This is **required** if public WAN TCP 443 is already occupied and you will use a Cloudflare Origin Rule.

If WAN 443 is free, DNS-only can temporarily be useful during low-level troubleshooting, but the recommended final state is still **Proxied**.

7. Click:
   ```text
   Save
   ```

The final DNS relationship is:

```text
ddns.yourdomain.com
    A → current WAN IPv4
    DNS only

partflow.yourdomain.com
    CNAME → ddns.yourdomain.com
    Proxied
```

When `partflow.yourdomain.com` is Proxied, `nslookup` normally returns Cloudflare edge addresses rather than your real WAN address. That is expected.

---

# Part C — Cloudflare TLS certificate for the NAS

# 9. Create a Cloudflare Origin CA certificate

This guide recommends **Cloudflare Origin CA** instead of DSM Let's Encrypt because PartFlow is intentionally accessed through Cloudflare and Origin CA does not require WAN port 80 for HTTP-01 validation.

## 9.1 Open Origin Server

1. In the Cloudflare Dashboard, select:
   ```text
   yourdomain.com
   ```
2. Open:
   ```text
   SSL/TLS
   ```
3. Click:
   ```text
   Origin Server
   ```
4. Click:
   ```text
   Create Certificate
   ```

## 9.2 Configure the certificate

1. Choose:
   ```text
   Generate private key and CSR with Cloudflare
   ```
2. Choose an available key type. RSA is a simple compatibility choice; ECC is also acceptable on modern DSM.
3. In **Hostnames**, specify:
   ```text
   partflow.yourdomain.com
   ```
4. Remove unrelated wildcard or apex hostnames if they are not needed.
5. Choose a validity period that matches your organization's policy.
6. Click:
   ```text
   Create
   ```

## 9.3 Save the certificate and private key

Cloudflare displays two text blocks:

```text
Origin Certificate
Private Key
```

Save them immediately.

For example:

```text
partflow-origin.pem
partflow-origin.key
```

When saving:

- keep the `-----BEGIN ...-----` and `-----END ...-----` lines;
- preserve all line breaks;
- do not add spaces or extra text;
- protect the private key carefully.

Cloudflare may show the private key only once.

---

# 10. Set Cloudflare SSL/TLS to `Full (strict)`

1. In the Cloudflare Dashboard, select:
   ```text
   yourdomain.com
   ```
2. Open:
   ```text
   SSL/TLS → Overview
   ```
3. Find **SSL/TLS encryption mode**.
4. Select:
   ```text
   Full (strict)
   ```

Do not use:

```text
Flexible
```

The intended encryption path is:

```text
Browser ─HTTPS→ Cloudflare ─HTTPS→ Synology DSM
```

---

# Part D — Verify PartFlow production on Synology

# 11. Check the PartFlow web container in Container Manager

1. Sign in to DSM.
2. Open:
   ```text
   Container Manager
   ```
3. Click:
   ```text
   Container
   ```
4. Open:
   ```text
   partflow-production-web-1
   ```
5. Confirm the status is:
   ```text
   Healthy
   ```
6. In the **General** details, locate the **Port** section.
7. Confirm the mapping corresponds to:
   ```text
   Container: 80/tcp
   Host:      18080
   ```

The production Compose configuration additionally binds that host port to:

```text
127.0.0.1
```

so the effective mapping is:

```text
127.0.0.1:18080 → container:80
```

Do not change it to `0.0.0.0` or the NAS LAN address.

---

# 12. Verify PartFlow locally on the NAS

If SSH is enabled:

1. SSH into the NAS.
2. Run:
   ```bash
   docker ps --filter name=partflow-production-web-1
   ```
3. Confirm the `PORTS` output contains:
   ```text
   127.0.0.1:18080->80/tcp
   ```
4. Test the web health endpoint:
   ```bash
   curl -fsS http://127.0.0.1:18080/web-health
   ```
5. Expected output:
   ```text
   live
   ```
6. Test the application root:
   ```bash
   curl -I http://127.0.0.1:18080/
   ```

A successful HTTP response confirms that DSM has a valid local destination for the reverse proxy.

It is normal for this address to be unreachable from another LAN computer:

```text
http://192.168.0.11:18080
```

because PartFlow production intentionally binds only to loopback.

---

# Part E — Import the origin certificate into DSM

# 13. Import the Cloudflare Origin CA certificate

1. In DSM, open:
   ```text
   Control Panel
   ```
2. Go to:
   ```text
   Security
   ```
3. Open the:
   ```text
   Certificate
   ```
   tab.
4. Click:
   ```text
   Add
   ```
5. Choose:
   ```text
   Add a new certificate
   ```
6. Choose:
   ```text
   Import certificate
   ```
7. Enter a description such as:
   ```text
   PartFlow - partflow.yourdomain.com
   ```
8. For **Private key**, upload:
   ```text
   partflow-origin.key
   ```
9. For **Certificate**, upload:
   ```text
   partflow-origin.pem
   ```
10. If DSM requires an intermediate/certificate-chain file, use the Cloudflare Origin CA root that matches the certificate type. If the field is optional and DSM accepts the import without it, leave it empty.
11. Complete the import.

The certificate should now appear in DSM's certificate list.

---

# Part F — DSM Reverse Proxy

# 14. Create the PartFlow Reverse Proxy rule

1. In DSM, open:
   ```text
   Control Panel
   ```
2. Open:
   ```text
   Login Portal
   ```
3. Open the:
   ```text
   Advanced
   ```
   tab.
4. In **Reverse Proxy**, click:
   ```text
   Reverse Proxy
   ```
   or the button that opens the Reverse Proxy Rules list.
5. Click:
   ```text
   Create
   ```

## 14.1 General tab — Source

Enter:

```text
Reverse Proxy Name: PartFlow

Source
Protocol: HTTPS
Hostname: partflow.yourdomain.com
Port:     443
```

Leave HSTS disabled during initial setup. Enable it only after HTTPS is stable.

## 14.2 General tab — Destination

Enter:

```text
Destination
Protocol: HTTP
Hostname: 127.0.0.1
Port:     18080
```

Important:

- use `127.0.0.1`;
- do not use `localhost`;
- do not use the NAS LAN IP;
- do not use a Docker `172.x.x.x` address;
- do not use port `5173`;
- destination protocol is `HTTP`, not `HTTPS`.

The local path is:

```text
DSM Reverse Proxy
      ↓ HTTP
127.0.0.1:18080
      ↓
PartFlow web container :80
```

Do not save yet; configure the Custom Header and Advanced Settings tabs first.

---

# 15. Configure the DSM Custom Header tab

This section follows the DSM UI exactly.

1. While editing the **PartFlow** Reverse Proxy rule, click:
   ```text
   Custom Header
   ```
2. Click the dropdown:
   ```text
   Create ▼
   ```
3. Choose:
   ```text
   Create
   ```
   Do **not** choose `WebSocket` for the normal PartFlow production setup.
4. In the new row, enter:
   ```text
   Header Name: X-Forwarded-For
   Value:       $proxy_add_x_forwarded_for
   ```
5. Click again:
   ```text
   Create ▼
   ```
6. Choose:
   ```text
   Create
   ```
7. In the second row, enter:
   ```text
   Header Name: X-Forwarded-Proto
   Value:       $scheme
   ```

The table should contain exactly these two entries:

```text
X-Forwarded-For    $proxy_add_x_forwarded_for
X-Forwarded-Proto  $scheme
```

If DSM already created an identical row, do not create a duplicate.

`X-Forwarded-Proto` is important because the browser uses HTTPS while DSM connects to PartFlow over local HTTP.

---

# 16. Configure DSM Advanced Settings

1. In the same Reverse Proxy rule, click:
   ```text
   Advanced Settings
   ```
2. Set the proxy **read timeout** to:
   ```text
   300 seconds
   ```
3. Set the proxy **send timeout** to:
   ```text
   300 seconds
   ```
4. If your DSM build exposes a per-rule maximum request-body-size field, set it to at least:
   ```text
   5 MiB
   ```
5. Leave other settings at their defaults unless your deployment documentation specifies a different value.
6. Click:
   ```text
   Save
   ```

---

# 17. Assign the Cloudflare Origin certificate to the Reverse Proxy hostname

Importing a certificate is not enough; it must be assigned to the correct DSM service/hostname.

1. Open:
   ```text
   Control Panel → Security → Certificate
   ```
2. Click:
   ```text
   Settings
   ```
3. Find the service/hostname corresponding to:
   ```text
   partflow.yourdomain.com
   ```
4. In its certificate dropdown, select:
   ```text
   PartFlow - partflow.yourdomain.com
   ```
5. Click:
   ```text
   OK / Apply
   ```

Do not make the PartFlow hostname depend on whichever certificate happens to be DSM's global default.

---

# Part G — UniFi port forwarding

# 18. Give the NAS a fixed LAN IP

Before creating a port-forward rule, make sure the NAS keeps the same LAN address.

Example:

```text
192.168.0.11
```

In UniFi, use the device's **Fixed IP / DHCP reservation** capability, or use a carefully managed static address on the NAS.

Verify that the NAS is still reachable at the selected address before continuing.

---

# 19. Case A — WAN TCP 443 is available

If nothing else uses public WAN TCP 443:

1. Open **UniFi Network**.
2. Go to the Port Forwarding page. Depending on the Network application version, this is typically:
   ```text
   Settings → Policy Engine → Port Forwarding
   ```
   or:
   ```text
   Settings → Policy Table → Create New Policy → Port Forwarding
   ```
3. Click:
   ```text
   Create / New Port Forward
   ```
4. Enter:
   ```text
   Name:         PartFlow HTTPS
   WAN Interface: the Internet WAN used by DDNS
   Incoming Port: 443
   From:          Any
   Forward IP:    192.168.0.11
   Forward Port:  443
   Protocol:      TCP
   ```
5. Save/apply the rule.

Do not forward WAN traffic directly to:

```text
5173
18080
8000
5432
5000
5001
```

No Cloudflare Origin Rule is required in Case A.

---

# 20. Case B — WAN TCP 443 is already used by another service

UniFi cannot send the same public WAN TCP port 443 to two different LAN destinations.

Keep the existing 443 rule unchanged.

Create a separate PartFlow origin port:

1. Open **UniFi Network**.
2. Go to:
   ```text
   Settings → Policy Engine → Port Forwarding
   ```
   or the equivalent **Policy Table → Port Forwarding** page in your Network version.
3. Click:
   ```text
   Create / New Port Forward
   ```
4. Enter:
   ```text
   Name:          PartFlow HTTPS Origin
   WAN Interface: the Internet WAN used by DDNS
   Incoming Port: 8443
   From:          Any
   Forward IP:    192.168.0.11
   Forward Port:  443
   Protocol:      TCP
   ```
5. Save/apply the rule.

The resulting NAT is:

```text
Public WAN :8443 → 192.168.0.11:443
```

DSM still listens on normal HTTPS port 443. Port 8443 exists only on the public-WAN side.

---

# Part H — Cloudflare Origin Rule for occupied WAN 443

# 21. Create the Origin Rule

This section is required only for **Case B**.

Before continuing, confirm that:

```text
partflow.yourdomain.com
```

is **Proxied** in Cloudflare DNS.

Then:

1. Sign in to the **Cloudflare Dashboard**.
2. Select:
   ```text
   yourdomain.com
   ```
3. In the left navigation, open:
   ```text
   Rules
   ```
4. Open:
   ```text
   Origin Rules
   ```
5. Click:
   ```text
   Create rule
   ```
6. Give the rule a name such as:
   ```text
   PartFlow origin port
   ```
7. For the matching condition, configure:
   ```text
   Field:    Hostname
   Operator: equals
   Value:    partflow.yourdomain.com
   ```
8. Under the origin parameter actions, enable:
   ```text
   Destination Port
   ```
9. Set:
   ```text
   Rewrite to: 8443
   ```
10. Save/deploy the rule.

The full connection is now:

```text
Browser :443
    ↓
Cloudflare :443
    ↓ Origin Rule
Public WAN :8443
    ↓ UniFi Port Forward
NAS :443
    ↓ DSM Reverse Proxy
127.0.0.1:18080
    ↓
PartFlow
```

The browser never needs to know about port 8443.

---

# Part I — End-to-end verification

# 22. Verify DDNS

1. In Cloudflare:
   ```text
   DNS → Records
   ```
2. Confirm:
   ```text
   ddns.yourdomain.com
   ```
   contains your current public WAN IPv4 address, not `0.0.0.0`.
3. Run:
   ```powershell
   nslookup ddns.yourdomain.com 1.1.1.1
   ```
4. Confirm the result matches the UniFi WAN IPv4 address.

---

# 23. Verify the public PartFlow DNS record

1. In Cloudflare:
   ```text
   DNS → Records
   ```
2. Confirm:
   ```text
   partflow.yourdomain.com
   ```
   is:
   ```text
   CNAME → ddns.yourdomain.com
   Proxied
   ```
3. Run:
   ```powershell
   nslookup partflow.yourdomain.com 1.1.1.1
   ```

Because the record is Proxied, the returned addresses should normally be Cloudflare addresses. That is correct.

---

# 24. Test from outside the LAN

Do not rely only on a computer inside the company LAN.

1. On a phone, disable Wi-Fi and use cellular data.
2. Open:
   ```text
   https://partflow.yourdomain.com
   ```
3. Confirm:
   - PartFlow loads;
   - the browser reports a valid HTTPS connection;
   - login works;
   - `/scan-station` loads;
   - normal API-backed operations work;
   - the URL does not contain `:8443`, `:18080`, or `:5173`.
4. Repeat from another external computer if practical.

Successful external testing confirms the entire path, not just the local reverse proxy.

---

# 25. Understand the certificates seen by the browser

With Cloudflare Proxy enabled:

```text
Browser
  │ public Cloudflare edge certificate
  ▼
Cloudflare
  │ validates Cloudflare Origin CA certificate
  ▼
DSM
```

The browser normally sees a public Cloudflare certificate, not the Origin CA certificate installed on DSM.

If only one browser reports `Not secure`, but:

- its certificate panel says the certificate is valid and trusted;
- another computer works;
- a phone works;

then the browser may be remembering an old certificate exception or insecure-content permission from troubleshooting.

Test an InPrivate/Incognito window or reset the site's browser permissions before changing server configuration.

---

# Part J — Security rules

# 26. Ports that should remain private

Do not publish these PartFlow/DSM ports directly to the Internet:

```text
5173   development/staging Vite
18080  PartFlow production loopback endpoint
8000   PartFlow backend
5432   PostgreSQL
5000   DSM HTTP management
5001   DSM HTTPS management
```

Only expose the public origin port required by your chosen case:

```text
Case A: WAN 443  → NAS 443
Case B: WAN 8443 → NAS 443
```

PartFlow's public hostname must not expose the DSM administration interface.

---

# 27. Recommended hardening after everything works

After successful end-to-end testing:

1. Keep `partflow.yourdomain.com` **Proxied**.
2. Keep Cloudflare SSL/TLS mode at **Full (strict)**.
3. Keep the DDNS API token limited to the required domain and permissions.
4. Keep the NAS on a fixed LAN IP.
5. Keep PartFlow port `18080` bound only to `127.0.0.1`.
6. Keep backend and PostgreSQL ports unpublished.
7. Review DSM firewall rules.
8. Review UniFi WAN/firewall rules.
9. Disable UPnP unless another documented requirement needs it.
10. Optionally restrict the PartFlow origin port to Cloudflare source IP ranges if your operational process can keep those ranges current.
11. Enable HSTS only after HTTPS is stable and the recovery implications are understood.

---

# Part K — Troubleshooting

# 28. `404 Not Found` when testing `NAS_IP:5173`

Do not troubleshoot PartFlow production through:

```text
https://192.168.0.11:5173
```

Production uses:

```text
127.0.0.1:18080 → web container :80
```

Port 5173 is the development/staging Vite path.

---

# 29. Vite `Blocked request` / HTTP 403

This generally means you reached a development/staging Vite server rather than the production nginx web container.

Do not weaken Vite host restrictions as a production workaround.

Use the production DSM reverse proxy path.

---

# 30. Cloudflare 521 — Web server is down / origin unreachable

Check in this order:

1. PartFlow web container is `Healthy`.
2. `curl http://127.0.0.1:18080/web-health` works on the NAS.
3. DSM Reverse Proxy exists and uses source HTTPS 443.
4. DSM firewall permits the connection.
5. UniFi Port Forward uses the correct public WAN port.
6. The port-forward destination is `NAS_IP:443`.
7. In Case B, Cloudflare Origin Rule rewrites to the same port used by the UniFi rule.

---

# 31. Cloudflare 525 — SSL handshake failed

Check:

1. DSM Reverse Proxy source protocol is `HTTPS`.
2. DSM is listening on port 443.
3. The PartFlow Origin CA certificate was imported successfully.
4. The imported certificate and private key match.
5. UniFi forwards to NAS TCP 443.
6. Cloudflare Origin Rule uses the intended public WAN port.

---

# 32. Cloudflare 526 — Invalid SSL certificate

With `Full (strict)`, check:

1. The Origin CA certificate has not expired.
2. Its hostname includes:
   ```text
   partflow.yourdomain.com
   ```
3. DSM assigned that certificate to the PartFlow reverse proxy hostname.
4. Cloudflare is reaching the intended NAS reverse proxy.

---

# 33. PartFlow loads but login/session behavior is wrong

Check the DSM Custom Header tab.

It should contain:

```text
X-Forwarded-For    $proxy_add_x_forwarded_for
X-Forwarded-Proto  $scheme
```

The public browser URL must be:

```text
https://partflow.yourdomain.com
```

PartFlow production uses secure cookies and must know that the original client request used HTTPS.

---

# 34. DDNS does not update

Check:

1. The UniFi WAN has a public IPv4 address.
2. The Cloudflare API token is still active.
3. The token is scoped to the correct domain.
4. `DNS → Edit` is granted.
5. `Zone → Read` is granted.
6. Client IP filtering is blank.
7. The DDNS hostname is correct.
8. The zone name is correct.
9. The DDNS record remains DNS only.

A useful reset test is to temporarily change the DDNS record content back to:

```text
0.0.0.0
```

then cause UniFi to refresh/re-save the DDNS profile and confirm that the real WAN IP appears again.

---

# 35. DSM Let's Encrypt fails

DSM Let's Encrypt normally relies on public HTTP validation and may require public TCP 80 to reach NAS TCP 80.

This guide intentionally uses Cloudflare Origin CA instead, so port 80 is not required for the PartFlow origin certificate.

Do not expose port 80 merely because an old certificate workflow told you to do so if Cloudflare Origin CA is the selected design.

---

# Part L — Final checklist

# 36. Cloudflare checklist

```text
[ ] Zone is active on Cloudflare
[ ] ddns.yourdomain.com A record exists
[ ] Initial DDNS value was 0.0.0.0
[ ] UniFi changed DDNS value to the real WAN IPv4
[ ] DDNS record is DNS only
[ ] API token uses Edit zone DNS template
[ ] API token scope = Specified Domains → yourdomain.com
[ ] API token permissions = DNS Edit + Zone Read only
[ ] API token Client IP filtering is blank
[ ] partflow.yourdomain.com CNAME → ddns.yourdomain.com
[ ] PartFlow CNAME is Proxied
[ ] Cloudflare Origin CA certificate was created
[ ] SSL/TLS mode = Full (strict)
[ ] Case B only: Origin Rule rewrites destination port to 8443
```

# 37. UniFi checklist

```text
[ ] WAN IPv4 is public
[ ] Cloudflare DDNS profile is saved and working
[ ] NAS has a fixed LAN IP
[ ] Case A: WAN TCP 443  → NAS TCP 443
    OR
[ ] Case B: WAN TCP 8443 → NAS TCP 443
[ ] No WAN forward to 5173
[ ] No WAN forward to 18080
[ ] No WAN forward to backend/database ports
```

# 38. Synology checklist

```text
[ ] partflow-production-web-1 is Healthy
[ ] Production mapping is 127.0.0.1:18080 → container:80
[ ] /web-health returns live
[ ] Cloudflare Origin CA certificate is imported
[ ] Origin certificate is assigned to partflow.yourdomain.com
[ ] Reverse Proxy source = HTTPS partflow.yourdomain.com:443
[ ] Reverse Proxy destination = HTTP 127.0.0.1:18080
[ ] X-Forwarded-For = $proxy_add_x_forwarded_for
[ ] X-Forwarded-Proto = $scheme
[ ] Proxy read timeout = 300 seconds
[ ] Proxy send timeout = 300 seconds
```

# 39. External validation checklist

```text
[ ] https://partflow.yourdomain.com opens from cellular/external Internet
[ ] Browser reports a valid HTTPS certificate
[ ] Login works
[ ] Scan Station route works
[ ] API operations work
[ ] Public URL needs no explicit port
```

---

# 40. Recommended final configuration when WAN 443 is occupied

```text
Cloudflare
  ddns.yourdomain.com
      A → dynamic public WAN IPv4
      DNS only

  partflow.yourdomain.com
      CNAME → ddns.yourdomain.com
      Proxied

  SSL/TLS
      Full (strict)

  Origin CA
      certificate for partflow.yourdomain.com

  Origin Rule
      hostname = partflow.yourdomain.com
      destination port = 8443

UniFi Gateway
  Dynamic DNS
      Cloudflare
      ddns.yourdomain.com

  Port Forward
      WAN TCP 8443
          → 192.168.0.11 TCP 443

Synology DSM
  Certificate
      Cloudflare Origin CA
      assigned to partflow.yourdomain.com

  Reverse Proxy
      Source:
          HTTPS partflow.yourdomain.com:443
      Destination:
          HTTP 127.0.0.1:18080

PartFlow
  web container
      127.0.0.1:18080 → container:80
```

Public URL:

```text
https://partflow.yourdomain.com
```

---

# References

- Ubiquiti — UniFi Gateway Dynamic DNS  
  https://help.ui.com/hc/en-us/articles/9203184738583-UniFi-Gateway-Dynamic-DNS
- Ubiquiti — UniFi Gateway Port Forwarding  
  https://help.ui.com/hc/en-us/articles/235723207-UniFi-Gateway-Port-Forwarding
- Cloudflare — Create API token  
  https://developers.cloudflare.com/fundamentals/api/get-started/create-token/
- Cloudflare — API token templates  
  https://developers.cloudflare.com/fundamentals/api/reference/template/
- Cloudflare — Proxy status  
  https://developers.cloudflare.com/dns/proxy-status/
- Cloudflare — Origin Rules  
  https://developers.cloudflare.com/rules/origin-rules/
- Cloudflare — Origin CA  
  https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/
- Cloudflare — Full (strict)  
  https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/
- Synology — Login Portal / Reverse Proxy  
  https://kb.synology.com/en-in/DSM/help/DSM/AdminCenter/system_login_portal_advanced
- Inadyn — Cloudflare provider / token permissions  
  https://github.com/troglobit/inadyn
