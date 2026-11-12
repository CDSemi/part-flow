# Reaching PartFlow from the Internet through Cloudflare

> **Language:** English is the source of truth. [Tiếng Việt](./SYNOLOGY_PUBLIC_HTTPS.vi.md).

This optional guide makes PartFlow on the company Synology NAS reachable from anywhere at an address such as
`https://partflow.yourdomain.com`, through Cloudflare. Follow it at the HTTPS step of the installation
([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §9, option B), once PartFlow answers on the NAS (`SYNOLOGY_NAS.md` §8). It is
written for a reader who is new to DNS, Cloudflare, port forwarding and certificates, so every step gives the menu
path, the values and a check.

**What you need:**

- PartFlow installed and answering on the NAS: on the NAS, `curl -fsS http://127.0.0.1:18080/web-health` prints `live`.
- A domain whose DNS is managed by Cloudflare (in this guide the zone `yourdomain.com`) and a Cloudflare account that
  can change it.
- A router that can forward a port and keep a Cloudflare DNS record up to date when the company's public address
  changes (dynamic DNS, DDNS). This guide uses a UniFi gateway; other routers offer the same settings under other
  names.
- A public IPv4 address on the router's Internet (WAN) connection (§2).

**What changes for security.** Anyone on the Internet can open the PartFlow sign-in page. PartFlow protects itself
with HTTPS, sign-in, rate limits and the per-account lockout. This guide opens one port on the router and never
forwards the DSM administration ports. That one port leads to the NAS's web server on port 443, which anyone who knows
the company's public address could reach without going through Cloudflare; §12 shows how to admit only Cloudflare.

## 1. How the connection works

The browser always connects to Cloudflare on the normal HTTPS port. Cloudflare connects to the company's public
address, the router forwards that connection to the NAS, and the DSM reverse proxy passes it to PartFlow. Which
public port Cloudflare uses depends on whether port 443 of the router is still free:

```text
Case A: router port 443 is free             Case B: router port 443 is used by another service

Browser                                      Browser
  │ HTTPS 443                                  │ HTTPS 443
Cloudflare                                   Cloudflare, Origin Rule: connect on port 8443
  │ HTTPS 443                                  │ HTTPS 8443
Router: public 443 → NAS 443                 Router: public 8443 → NAS 443
  │                                            │
DSM reverse proxy on port 443                DSM reverse proxy on port 443
  │ HTTP                                       │ HTTP
127.0.0.1:18080 → PartFlow web               127.0.0.1:18080 → PartFlow web
```

In both cases people type only `https://partflow.yourdomain.com`, without a port. The example values below are used
throughout; replace them with your own:

| Purpose | Example |
| --- | --- |
| Cloudflare zone (your domain) | `yourdomain.com` |
| DDNS host name (follows the public address) | `ddns.yourdomain.com` |
| PartFlow host name | `partflow.yourdomain.com` |
| NAS LAN address | `192.168.0.11` |
| PartFlow loopback port (`PARTFLOW_HTTP_PORT`) | `18080` |
| Public port in case B | `8443` |

A dedicated name such as `partflow.yourdomain.com`, rather than the bare domain, is easier to route, test and protect
on its own.

## 2. Check that the router has a public IPv4 address

1. Open **UniFi Network → Settings → Internet** and open the active WAN connection.
2. Note its IPv4 address.
3. On a computer of the company network, open a "what is my IP" web page and compare: both addresses must be equal.

The WAN address must not be in one of these private or carrier-grade NAT ranges: `10.0.0.0/8`, `172.16.0.0/12`,
`192.168.0.0/16`, `100.64.0.0/10`. If it is, the router sits behind another router or the provider's address
sharing; solve that with the Internet provider first, because Cloudflare cannot reach the NAS otherwise.

## 3. Cloudflare: create the DDNS record

This record always holds the company's current public address. Starting it at `0.0.0.0` makes it obvious later that
the router updates it.

1. Sign in to the **Cloudflare dashboard** and select the zone `yourdomain.com`.
2. Open **DNS → Records** and click **Add record**.
3. Enter **Type** `A`, **Name** `ddns`, **IPv4 address** `0.0.0.0`, **TTL** `Auto`.
4. Leave **Proxy status** at **DNS only**. Cloudflare may not even offer proxying for `0.0.0.0`; never proxy this
   record.
5. Click **Save**. Do not type the real public address yourself: §5 step 4 checks that the router writes it.

## 4. Cloudflare: create an API token for the router

The router needs a token that can change DNS records of this one zone and nothing else.

1. In the Cloudflare dashboard, open **Manage account → Account API tokens** and click **Create Token**.
2. Enter a token name such as `UniFi DDNS`, and in **Permission policies** click the template **Edit zone DNS**. Do not
   choose a template with full account access.
3. On the policy card that the template created, click the pencil icon (**Edit policy**):
   - change the resource selector from all zones to **Specified Domains** and choose `yourdomain.com`;
   - open **DNS & Zones** and select **Edit** on the **DNS** row and **Read** on the **Zone** row;
   - leave every other permission empty. The section then shows about `2/12`.
4. **Token expiration:** **No expiration**, unless company policy requires rotating tokens.
5. **Client IP address filtering:** leave it empty. The token must keep working after the provider changes the
   company's public address.
6. Click **Review token**, check that only `yourdomain.com`, **DNS Edit** and **Zone Read** are listed, and click
   **Create Token**.
7. Copy the token at once and keep it in the company password vault. Treat it like a password: never put it in git, a
   screenshot or a document.

## 5. Router: keep the DDNS record up to date

1. Open **UniFi Network → Settings → Internet**, select the WAN connection with the public address, find **Dynamic
   DNS** and click **Create New Dynamic DNS**.
2. Choose **Service** `Cloudflare` and enter **Hostname** `ddns.yourdomain.com`, **Zone Name** `yourdomain.com` and
   **API Token** (the token of §4). If your UniFi version shows an older generic form instead, use the Cloudflare
   values that UniFi documents for it; never use the account-wide Global API Key.
3. Click **Save**.
4. Check the result: in Cloudflare **DNS → Records**, the record `ddns.yourdomain.com` now shows the public address of
   §2 instead of `0.0.0.0`. On a workstation, this command must answer with the same address:

   ```powershell
   nslookup ddns.yourdomain.com 1.1.1.1
   ```

The record stays **DNS only**.

## 6. Cloudflare: create the PartFlow host name

1. In Cloudflare **DNS → Records**, click **Add record**.
2. Enter **Type** `CNAME`, **Name** `partflow`, **Target** `ddns.yourdomain.com`, **TTL** `Auto`.
3. Set **Proxy status** to **Proxied** (orange cloud). This is required: browsers then connect to Cloudflare, not to
   the company, and in case B Cloudflare can change the port. The company's public address stays visible through the
   DDNS record of §3, which is why §12 limits who may connect to it.
4. Click **Save**.

`nslookup partflow.yourdomain.com 1.1.1.1` now answers with Cloudflare addresses, not the company's address. That is
expected.

## 7. Cloudflare: origin certificate and encryption mode

Cloudflare checks the certificate of the NAS. This guide uses a Cloudflare Origin CA certificate, which Cloudflare
trusts and which needs no open port 80, unlike DSM's Let's Encrypt function.

1. In Cloudflare, open **SSL/TLS → Origin Server** and click **Create Certificate**.
2. Choose **Generate private key and CSR with Cloudflare** and a key type (RSA is the simplest choice; ECC also works on
   current DSM versions).
3. In **Hostnames**, keep only `partflow.yourdomain.com`; remove the wildcard and the bare domain if you do not need
   them.
4. Choose the validity (the default is 15 years) and click **Create**.
5. Cloudflare shows two text blocks. Save the **Origin Certificate** as `partflow-origin.pem` and the **Private Key**
   as `partflow-origin.key`, each exactly as shown, including the `-----BEGIN …-----` and `-----END …-----` lines and
   every line break. Cloudflare may show the private key only once; keep both files in the password vault.
6. Note the certificate's expiry date in your calendar: the monitoring of PartFlow cannot see it (§13).
7. Open **SSL/TLS → Overview** and set the encryption mode to **Full (strict)**. Never use **Flexible**, which would
   send the connection from Cloudflare to the NAS unencrypted. This mode applies to every proxied name of the zone,
   so other proxied services of the zone also need valid certificates.
8. Optional: to send people who type `http://` to `https://`, turn on **SSL/TLS → Edge Certificates → Always Use
   HTTPS**. It also applies to every proxied name of the zone.

## 8. DSM: import the certificate and create the reverse proxy rule

1. In DSM, open **Control Panel → Security → Certificate → Add → Add a new certificate**, choose **Import
   certificate** and enter a description such as `PartFlow - partflow.yourdomain.com`.
2. Select `partflow-origin.key` as **Private key** and `partflow-origin.pem` as **Certificate**. Leave **Intermediate
   certificate** empty; only if DSM refuses without it, use the Cloudflare Origin CA root certificate that matches the
   key type, from the Cloudflare Origin CA documentation. Finish the import; the certificate appears in the list.
3. Create the reverse proxy rule exactly as [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §9.2 describes, with the host name
   `partflow.yourdomain.com`. In its last step, assign the certificate `PartFlow - partflow.yourdomain.com` to that
   host name.

## 9. Router: forward the HTTPS port to the NAS

The NAS must keep its LAN address, because the forwarding rule points to it: check that the address reservation of
`SYNOLOGY_NAS.md` §2 step 2 is in place.

Open the port forwarding page of UniFi Network. Depending on its version, it is **Settings → Policy Engine → Port
Forwarding**, or **Settings → Policy Table → Create New Policy → Port Forwarding**. Create one rule:

| Field | Case A (router port 443 free) | Case B (router port 443 used) |
| --- | --- | --- |
| Name | `PartFlow HTTPS` | `PartFlow HTTPS origin` |
| WAN interface | the WAN connection used by DDNS | the WAN connection used by DDNS |
| Incoming port | `443` | `8443` |
| From | Any | Any |
| Forward IP | `192.168.0.11` | `192.168.0.11` |
| Forward port | `443` | `443` |
| Protocol | TCP | TCP |

In case B, leave the existing rule for port 443 unchanged: one public port can lead to only one LAN address. DSM still
listens on port 443; `8443` exists only on the public side. **From: Any** is the simplest setting to start with; §12
restricts it to Cloudflare once everything works.

Never forward a public port to `5173`, `18080`, `8000`, `5432`, `5000` or `5001`.

## 10. Cloudflare: Origin Rule (case B only)

Skip this section in case A. In case B, Cloudflare must connect to the public port `8443` instead of `443`; this
works only because the PartFlow host name is **Proxied** (§6).

1. In the Cloudflare dashboard, select `yourdomain.com` and open **Rules → Origin Rules**. Click **Create rule** and
   name it `PartFlow origin port`.
2. Under the matching condition, enter **Field** `Hostname`, **Operator** `equals`, **Value**
   `partflow.yourdomain.com`.
3. Under the origin settings, enable **Destination Port** and set **Rewrite to** `8443`.
4. Click **Deploy**.

## 11. Test from outside the company network

Test from a device that is really outside, not from a computer of the company network:

1. On a phone, turn off Wi-Fi so that it uses mobile data, and open `https://partflow.yourdomain.com`.
2. Check that PartFlow opens, the browser shows a secure connection, and the address has no port (`:8443`, `:18080` or
   `:5173`).
3. If first-run setup is already done, sign in and open a few screens and `/scan-station`; otherwise the **Set up
   PartFlow** button appears.

The browser shows Cloudflare's own public certificate, not the Origin certificate installed on DSM: Cloudflare checks
that one. When everything works, return to [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §10.

## 12. Firewall and security

- Only the forwarded port of your case is open on the router: public `443` (case A) or `8443` (case B) to NAS `443`.
- These ports stay private and are never forwarded: `5173` (development server), `18080` (PartFlow loopback), `8000`
  (backend), `5432` (PostgreSQL), `5000` and `5001` (DSM administration). DSM administration is never reachable
  through `partflow.yourdomain.com`.
- **Admit only Cloudflare (recommended).** The DDNS record publishes the company's address, so anyone can connect to
  the forwarded port directly, without Cloudflare, and reach everything that the NAS's web server answers on port
  443: the PartFlow rule, every other reverse proxy rule, and any other web service of the NAS. Enable the DSM
  firewall ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §11.7) and allow port 443 only from Cloudflare's published IPv4
  ranges (`https://www.cloudflare.com/ips/`), plus your company subnets for option A, instead of from everyone.
  Review that list twice a year: a stale list can block PartFlow.
- Know what else answers on NAS port 443: in **Control Panel → Login Portal → Advanced → Reverse Proxy**, every
  other rule is reachable the same way; remove the ones you do not need.
- Keep the PartFlow host name **Proxied**, the encryption mode **Full (strict)**, the API token limited to the zone,
  the NAS on its fixed address, and port `18080` bound to `127.0.0.1`.
- Turn off UPnP on the router unless another documented need requires it, and review the router's firewall rules.
- Turn on HSTS (on the DSM rule or in Cloudflare) only after HTTPS has worked reliably for a while: browsers then
  refuse plain HTTP for that name for a long time, even if you later need it.

## 13. Known limitations

- **Everyone depends on the Internet connection.** People inside the company also reach PartFlow through Cloudflare.
  When the company's Internet connection is down, nobody can open PartFlow, including the shop floor. If the factory
  must keep working without the Internet, also set up option A of `SYNOLOGY_NAS.md` §9 with a second, internal host
  name and its own certificate, as a second reverse proxy rule to the same `127.0.0.1:18080`; people inside then use
  that address. A browser keeps its Scan Station enrollment per address, so a station enrolled at the public address
  must be enrolled again to work at the internal one.
- **Cloudflare waits at most 100 seconds for an answer.** On Cloudflare's standard plans, a request that gets no
  answer within 100 seconds ends with Cloudflare error 524, whatever the DSM timeouts say. A Work Order import
  normally takes well under a minute (the largest allowed import about half a minute), so this matters only on a slow
  or busy server. After a 524 the outcome is unknown: check in PartFlow whether the import was saved before repeating
  it.
- **Sign-in rate limits are shared.** `web` takes the last address of the `X-Forwarded-For` header as the user's
  address. DSM appends the address it received the connection from, and behind Cloudflare that is a Cloudflare
  server. The sign-in limit (10 per minute, burst 5) then applies per Cloudflare server address, and the logs show
  Cloudflare addresses. When several people sign in at the same moment, some may see "Too many attempts from this
  computer"; they wait a minute and try again. The per-account lockout is not affected. Never make DSM trust a
  client-supplied header such as `CF-Connecting-IP` instead, unless the NAS admits only Cloudflare's addresses (§12):
  anyone could otherwise fake an address and bypass the limits.
- **The Origin certificate is not monitored.** `check.sh` sees Cloudflare's public certificate, which Cloudflare
  renews. Renew the Origin certificate yourself before the date you noted in §7. For the same reason, never add
  `--resolve-to 127.0.0.1` to `check.sh` in this design: only Cloudflare trusts the Origin certificate.

## 14. Troubleshooting

| Symptom | Check, in this order |
| --- | --- |
| Cloudflare error 521 (web server is down) or 522 (connection timed out) | `web` is healthy in `$PF ps`; `curl -fsS http://127.0.0.1:18080/web-health` prints `live` on the NAS; the DSM rule exists with source HTTPS 443; the DSM firewall admits the connection; the router rule uses the right public port and forwards to `NAS_IP:443`; in case B, the Origin Rule uses the same public port as the router rule. |
| Cloudflare error 525 (SSL handshake failed) | The DSM rule's source protocol is HTTPS on port 443; the Origin certificate was imported with its matching private key; the router forwards to NAS port 443; in case B, the Origin Rule port. |
| Cloudflare error 526 (invalid SSL certificate) | The Origin certificate has not expired, names `partflow.yourdomain.com`, and is assigned to that host name in DSM (`SYNOLOGY_NAS.md` §9.2 step 6). |
| PartFlow opens, but sign-in does not stay or the session is lost | The DSM rule's custom headers are exactly `X-Forwarded-For` = `$proxy_add_x_forwarded_for` and `X-Forwarded-Proto` = `$scheme`, and the address is `https://partflow.yourdomain.com`. PartFlow's sign-in cookie needs to know that the browser used HTTPS. |
| Cloudflare error 524 (a timeout occurred) | The server took more than 100 seconds to answer (§13). Check whether the change was saved before repeating it; if it happens often, check the load of the NAS (`docker stats --no-stream`). |
| Cloudflare error 523 (origin is unreachable), or the DDNS record stays `0.0.0.0` or keeps an old address | The router has a public IPv4 (§2); the token is active, limited to the right zone, with **DNS Edit** and **Zone Read** and no IP filtering; the host name and zone name in the router are right; the record is **DNS only**. To retest, set the record back to `0.0.0.0`, save the router's DDNS settings again and watch the record change. |
| "Blocked request" (HTTP 403), or a 404, at port `5173` | That is the development server, not production. Production never uses port `5173`; use the reverse proxy path of this guide. |
| DSM's Let's Encrypt fails | Not needed: this design uses the Cloudflare Origin certificate, and port 80 stays closed. |
| Only one browser reports "Not secure" while others work | That browser remembers an old exception from troubleshooting. Try a private window or reset the site's permissions before changing the server. |
| "Too many attempts from this computer" when several people sign in together | The shared rate limit of §13; wait a minute. |

## Change history

| Date | Commit | Change |
| --- | --- | --- |
| 2026-11-10 | uncommitted | Restructured as one numbered path from the public address check to the outside test; the DSM reverse proxy rule now comes from the NAS guide instead of being repeated; the per-vendor checklists were folded into the steps; added the known limitations (Internet dependency, Cloudflare's 100-second limit, shared rate limits, unmonitored Origin certificate), the exposure of the forwarded port with the recommendation to admit only Cloudflare, the Cloudflare errors 522 to 524, and the optional HTTPS redirect. |
| 2026-11-10 | `8a7e2b5` | First version: Cloudflare DDNS with a scoped API token, the Origin CA certificate and Full (strict), UniFi port forwarding for a free or occupied WAN port 443, the DSM reverse proxy, troubleshooting and end-to-end checks. |
