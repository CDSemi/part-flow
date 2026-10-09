"""Synthetic data through the deployed app's HTTP API only (SPEC 3.4, OD-A34-05): never SQL writes to business
tables. Every request and its answer status is recorded in ``seed.jsonl``."""
import json
import re
import secrets
import time
import urllib.error
import urllib.request
import uuid

from . import util

CSRF = {"X-PartFlow-CSRF": "1"}
STATION_HEADER = "X-PartFlow-Station-Device"
TOKEN_RE = re.compile(r"Setup token: ([A-Z2-7]{4}(?:-[A-Z2-7]{4})+)")
SESSION_COOKIE = "partflow_session"
# Permission keys at OLD (backend/app/domain/enums.py Permission) the synthetic administrator needs; correction keys
# are deliberately not granted (the seed records no correction).
ADMIN_KEYS = ("MANAGE_DEPARTMENTS", "MANAGE_AREAS", "MANAGE_OPERATIONS", "MANAGE_WORKERS", "MANAGE_SCAN_STATIONS",
              "MANAGE_ROUTE_TEMPLATES", "MANAGE_PART_NUMBER_MASTER", "VIEW_PRODUCTION_DATA", "MANAGE_WORK_ORDERS",
              "EDIT_WORK_ORDER_DEMAND", "ASSIGN_ROUTES")
STATION_KEYS = ("SCAN_PN_BARCODES", "RECEIVE_QUANTITY", "CONFIRM_QUANTITY")


class ApiError(util.HarnessError):
    pass


class Client:
    def __init__(self, harness, slug, port, *, label=None):
        self.harness = harness
        self.slug = slug
        self.base = f"http://127.0.0.1:{port}"
        self.cookie = None
        self.label = label or slug
        self.credentials = None

    def request(self, method, path, body=None, *, headers=None, station_token=None, expect=None, record=True,
                timeout=60, retry_auth=True):
        data = None if body is None else json.dumps(body).encode("utf-8")
        values = {"Accept": "application/json"}
        if body is not None:
            values["Content-Type"] = "application/json"
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            values.update(CSRF)
        if self.cookie:
            values["Cookie"] = f"{SESSION_COOKIE}={self.cookie}"
        if station_token:
            values[STATION_HEADER] = station_token
        values.update(headers or {})
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=values)
        started = util.utc()
        status, payload, set_cookie = None, None, None
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                raw = response.read()
                set_cookie = response.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            set_cookie = exc.headers.get_all("Set-Cookie") or [] if exc.headers else []
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            if record:
                self._record(method, path, None, started, error=type(exc).__name__ + ": " + str(exc)[:200])
            raise ApiError(f"{self.label}: {method} {path} failed: {exc}") from exc
        for item in set_cookie:
            match = re.match(SESSION_COOKIE + r"=([^;]*)", item)
            if match:
                self.cookie = match.group(1) or None
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            payload = {"text": raw.decode("utf-8", "replace")[:400]}
        if record:
            self._record(method, path, status, started)
        if status == 401 and retry_auth and self.credentials and path != "/api/session":
            self.sign_in()
            return self.request(method, path, body, headers=headers, station_token=station_token, expect=expect,
                                record=record, timeout=timeout, retry_auth=False)
        if expect is not None and status not in (expect if isinstance(expect, tuple) else (expect,)):
            raise ApiError(f"{self.label}: {method} {path} answered {status} (expected {expect}): "
                           f"{json.dumps(payload)[:600]}")
        return status, payload

    def _record(self, method, path, status, started, error=None):
        self.harness.evidence.append_jsonl("seed.jsonl", {"instance": self.slug, "method": method, "path": path,
                                                          "status": status, "at": started, "error": error,
                                                          "scenario": self.harness.current_scenario})

    def sign_in(self):
        login, password = self.credentials
        self.cookie = None
        self.request("POST", "/api/session", {"login_name": login, "password": password}, expect=(200, 201),
                     retry_auth=False)

    def health(self):
        try:
            status, payload = self.request("GET", "/api/health", record=False, timeout=10)
        except ApiError:
            return None
        return status


def new_event_id():
    return str(uuid.uuid4())


def setup_token(harness, project):
    """The setup token from the backend log (as deploy/production/tests/stack_smoke.py SM-6 reads it)."""
    container = util.docker_checked(["ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
                                     "--filter", "label=com.docker.compose.service=backend"]).split()
    if not container:
        raise util.HarnessError(f"no running backend container for project {project}")
    logs = util.docker(["logs", container[0]], timeout=60)
    tokens = TOKEN_RE.findall(logs.stdout + logs.stderr)
    return tokens[-1] if tokens else None


def wait_healthy(client, timeout=300):
    return util.wait_until(lambda: client.health() == 200, timeout=timeout, interval=2,
                           what=f"{client.label} /api/health 200")


class Seeder:
    """D0/D0b/D0c and the W<n> markers (SPEC 3.4), with deterministic PFA34-… names."""

    def __init__(self, harness, slug, port, project, prefix="PFA34"):
        self.harness = harness
        self.client = Client(harness, slug, port)
        self.project = project
        self.prefix = prefix
        self.ids = harness.instances.setdefault(slug, {}).setdefault("seed", {})
        self.secret = harness.secrets.setdefault(slug, {})
        if self.secret.get("admin_password"):
            self.client.credentials = (self.ids["admin_login"], self.secret["admin_password"])

    def first_run(self):
        client = self.client
        wait_healthy(client)
        status, setup = client.request("GET", "/api/setup", expect=200)
        if not setup.get("open"):
            raise util.HarnessError("setup is not open on a fresh deployment")
        token = None
        for _ in range(30):
            token = setup_token(self.harness, self.project)
            if token:
                break
            time.sleep(1)
        if not token:
            raise util.HarnessError("no setup token announced in the backend log")
        role = setup["eligible_roles"][0]
        password = "Pfa34-" + secrets.token_hex(12)
        login = (self.prefix + "-admin").lower()
        self.harness.evidence.add_secret(password)
        client.request("POST", "/api/setup/administrator", {
            "setup_token": token, "login_name": login, "display_name": "PF-A3.4 Administrator",
            "role_id": role["id"], "password": password}, expect=201)
        client.credentials = (login, password)
        self.ids.update({"admin_login": login, "admin_role": role})
        self.secret["admin_password"] = password
        self.harness.save()
        self.grant_permissions(role["id"])

    def grant_permissions(self, admin_role_id):
        """The first-run role holds only the two setup keys: grant it every key the seed needs, and the role applied
        at Scan Stations the station keys (through the Roles API, as an administrator would)."""
        client = self.client
        _, roles = client.request("GET", "/api/roles", expect=200)
        self.ids["roles_before"] = [{"id": item["id"], "name": item["name"], "permissions": item["permissions"],
                                     "applies_at_scan_stations": item.get("applies_at_scan_stations")}
                                    for item in roles]
        admin = next(item for item in roles if item["id"] == admin_role_id)
        missing = [key for key in ADMIN_KEYS if key not in admin["permissions"]]
        if missing:
            client.request("PATCH", f"/api/roles/{admin_role_id}", {"grant_permissions": missing}, expect=200)
        for item in roles:
            if item.get("applies_at_scan_stations"):
                missing = [key for key in STATION_KEYS if key not in item["permissions"]]
                if missing:
                    client.request("PATCH", f"/api/roles/{item['id']}", {"grant_permissions": missing}, expect=200)

    def token(self, station_id):
        return self.secret["device_tokens"][station_id]

    def environment(self, *, areas=2, part_numbers=5):
        client, p = self.client, self.prefix
        _, department = client.request("POST", "/api/departments", {"name": f"{p}-Department"}, expect=201)
        area_ids, operation_ids = [], []
        for index in range(areas):
            letter = "ABCDEFGH"[index]
            _, area = client.request("POST", "/api/areas", {
                "department_id": department["id"], "name": f"{p}-Area-{letter}",
                "worker_identification_mode": "DISABLED"}, expect=201)
            _, operation = client.request("POST", "/api/operations", {
                "area_id": area["id"], "code": f"{p}-OP-{letter}", "name": f"{p} operation {letter}"}, expect=201)
            area_ids.append(area["id"])
            operation_ids.append(operation["id"])
        _, template = client.request("POST", "/api/route-templates", {
            "name": f"{p}-Route", "steps": [{"area_id": area_ids[i], "operation_id": operation_ids[i]}
                                            for i in range(areas)]}, expect=201)
        stations = {}
        for index, area_id in enumerate(area_ids):
            station_id = f"{p}-ST-{'ABCDEFGH'[index]}"
            client.request("POST", "/api/scan-stations", {"station_id": station_id, "area_id": area_id}, expect=201)
            _, issued = client.request("POST", f"/api/scan-stations/{station_id}/device-enrollments",
                                       {"label": f"{p} device {index + 1}"}, expect=201)
            _, activated = client.request("POST", f"/api/scan-stations/{station_id}/device-activations",
                                          {"enrollment_code": issued["enrollment_code"]}, expect=201)
            self.harness.evidence.add_secret(activated["device_token"])
            stations[station_id] = {"area_id": area_id, "device_id": activated["device"]["id"]}
            self.secret.setdefault("device_tokens", {})[station_id] = activated["device_token"]
        numbers = []
        for index in range(part_numbers):
            number = f"{p}-PN-{index + 1}"
            client.request("POST", "/api/part-numbers", {"part_number": number, "name": f"{p} part {index + 1}"},
                           expect=201)
            numbers.append(number)
        self.ids.update({"department_id": department["id"], "area_ids": area_ids, "operation_ids": operation_ids,
                         "route_template_id": template["id"], "stations": stations, "part_numbers": numbers})

    def work_order(self, number, part_number, quantity):
        _, order = self.client.request("POST", "/api/work-orders", {
            "work_order_number": number, "lines": [{"part_number": part_number, "requested_quantity": quantity}]},
            expect=201)
        return order

    def release(self, order, quantity, *, planned=True):
        demand = order["demands"][0]
        body = {"part_number": demand["part_number"], "quantity": quantity,
                "route_mode": "PLANNED" if planned else "FLOATING",
                "starting_area_id": self.ids["area_ids"][0], "operation_id": self.ids["operation_ids"][0],
                "device_event_id": new_event_id()}
        if planned:
            body["route_template_id"] = self.ids["route_template_id"]
        path = f"/api/work-orders/{order['id']}/demands/{demand['id']}/release"
        status, result = self.client.request("POST", path, body, expect=(200, 201, 409))
        if status == 409 and (result or {}).get("confirmation_required"):
            # The PN already has active quantity: the operator confirms a separate Quantity Flow (same event id).
            body["confirm_active_quantity"] = True
            status, result = self.client.request("POST", path, body, expect=(200, 201))
        elif status == 409:
            raise ApiError(f"{self.client.label}: release answered 409: {json.dumps(result)[:400]}")
        return result

    def station(self, index):
        station_id = sorted(self.ids["stations"])[index]
        return station_id, self.ids["stations"][station_id]

    def transfer(self, flow, quantity, *, to_index=1, event_id=None):
        station_id, station = self.station(to_index)
        body = {"part_number": flow["part_number"], "quantity_flow_id": flow["quantity_flow_id"],
                "source_area_id": self.ids["area_ids"][0], "target_area_id": station["area_id"],
                "quantity": quantity, "operation_id": self.ids["operation_ids"][to_index],
                "device_event_id": event_id or new_event_id()}
        return self.client.request("POST", f"/api/scan-stations/{station_id}/transfers", body,
                                   station_token=self.token(station_id), expect=(200, 201))[1]

    def complete(self, movement, quantity, *, at_index=1, event_id=None):
        station_id, station = self.station(at_index)
        body = {"part_number": movement["part_number"], "quantity_flow_id": movement["quantity_flow_id"],
                "quantity": quantity, "device_event_id": event_id or new_event_id()}
        return self.client.request("POST", f"/api/scan-stations/{station_id}/area-completions", body,
                                   station_token=self.token(station_id), expect=(200, 201))[1]

    def d0(self, *, work_orders=3, small=False):
        """The D0 set (or the smaller D0b/D0c shape when ``small``)."""
        self.first_run()
        self.environment(part_numbers=2 if small else 5)
        numbers = self.ids["part_numbers"]
        orders = []
        for index in range(1 if small else work_orders):
            orders.append(self.work_order(f"{self.prefix}-WO-{index + 1}", numbers[index % len(numbers)], 10))
        release = self.release(orders[0], 4, planned=True)
        moved = self.transfer(release, 4)
        done = self.complete(moved, 4)
        flows = [{"release": release, "transfer": moved, "complete": done}]
        if not small:
            release2 = self.release(orders[1], 3, planned=False)
            flows.append({"release": release2})
        self.ids["d0_orders"] = [order["id"] for order in orders]
        self.ids["d0_flows"] = flows
        self.harness.save()
        return flows

    def marker(self, name, *, movement=False):
        """``PFA34-W<n>``: one new Work Order; with ``movement`` also one release and one station Movement."""
        numbers = self.ids["part_numbers"]
        order = self.work_order(f"{self.prefix}-{name}", numbers[0], 2)
        value = {"name": name, "work_order_id": order["id"]}
        if movement:
            release = self.release(order, 1, planned=False)
            moved = self.transfer(release, 1)
            value.update({"quantity_flow_id": release["quantity_flow_id"], "movement_id": moved["movement_id"]})
        self.ids.setdefault("markers", {})[name] = value
        self.harness.save()
        return value
