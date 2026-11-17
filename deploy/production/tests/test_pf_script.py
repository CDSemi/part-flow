"""The minimal pf (owner decision of 2026-11-13): deploy/production/pf against the fake executables of
test_backup_scripts.py (docker, df, ps, uname, id, curl, sleep) plus fake git and sha256sum. pf runs from a copy in
the fake release root, beside stub backup.sh, release.sh, check.sh and smoke.sh that only record their arguments.
Nothing touches a Docker daemon, a database, a git repository or the network.

Run with the other production tests:
  python -B -m unittest discover -s deploy/production/tests -p 'test*.py'
"""
import json
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_backup_scripts import (  # noqa: E402
    BACKUP_NAME, RELEASE, REPO, ScriptHarness, drill_reconcile_doc, report, verify_doc,
)
from test_release_scripts import find_sh, health_body, out, rule  # noqa: E402

PF = REPO / "deploy" / "production" / "pf"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
NEW_TAG = "v1.1.0"
STUBS = ("backup.sh", "release.sh", "check.sh", "smoke.sh")
PREFIX = "compose -f compose.production.yaml --env-file .env.production "
BUILD_PREFIX = "compose -f compose.production.yaml -f compose.production.build.yaml --env-file .env.production "
PSQL_COUNT = r" exec -T db sh -c psql .*pg_class"
DB_DF = r" exec -T db df -Pk /var/lib/postgresql/data$"


def df_text(available_kib):
    return out(f"Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/fake 99999999 1 {available_kib} 1% /x\n")


class PfHarness(ScriptHarness):
    tools = ScriptHarness.tools + ("git", "sha256sum")

    def setUp(self):
        super().setUp()
        scripts = self.root / "deploy" / "production"
        scripts.mkdir(parents=True)
        shutil.copyfile(PF, scripts / "pf")
        self.pf_path = scripts / "pf"
        for name in STUBS:
            self.wrapper(scripts / name,
                         f'exec "{self.python}" "{self.fake.as_posix()}" "{self.state.as_posix()}" {name} "$@"')
            self.rules[name] = [rule(r".*")]
        self.rules["git"] = [
            rule(r"^rev-parse -q --verify refs/tags/v1\.0\.0\^\{commit\}$", out(COMMIT + "\n")),
            rule(r"^rev-parse HEAD$", out(COMMIT + "\n")),
            rule(r"^status --porcelain --untracked-files=no$"),
            rule(r"^fetch --tags$"),
            rule(r"^rev-parse -q --verify refs/tags/v1\.1\.0\^\{commit\}$", out("f" * 40 + "\n")),
            rule(r"^checkout -q v1\.1\.0$"),
        ]
        self.rules["sha256sum"] = [rule(r"^-c SHA256SUMS$", out("partflow.dump: OK\n"))]
        self.rules["id"].insert(0, rule(r"^-un$", out("ops\n")))
        self.rules["curl"] = [rule(r"^GET /api/health$", {"status": 200, "body": health_body(release=RELEASE),
                                                           "headers": {}})]
        self.rules["docker"] = [
            rule(r" build$"),
            rule(r" up -d db$"),
            rule(r" ps -q db$", out("cid-db\n")),
            rule(r"^inspect --format \{\{\.State\.Health\.Status\}\} cid-db$", out("healthy\n")),
            rule(PSQL_COUNT, out("0\n")),
            rule(DB_DF, df_text(50 * 1048576)),
            rule(r"backup-tools backup-verify ", out(verify_doc())),
            rule(r" exec -T db sh -c pg_restore ", {"read_stdin": True}),
            rule(r" --profile ops run --rm -T db-roles$", out(report("provision-roles", "provisioned", 0))),
            rule(r" --profile ops run --rm -T db-roles apply-grants$", out(report("apply-grants", "applied", 0))),
            rule(r" --profile ops run --rm -T migrate ", out(report("migrate", "upgraded", 0))),
            rule(r" run --rm --no-deps -T backend python -m app\.cli revision$",
                 out(report("revision", "current", 0, state="current"))),
            rule(r" run --rm --no-deps -T backend python -m app\.cli reconcile --max-findings 10000$",
                 out(drill_reconcile_doc())),
            rule(r" up -d backend web$"),
            rule(r" ps$", out("NAME STATUS\npartflow-production-db-1 Up (healthy)\n")),
            rule(r" --profile ops run --rm --no-deps -T --user 1000:1000 status$",
                 out(report("status", "ok", 0), stderr="status: ok (database partflow, schema current)\n")),
        ]

    def pf(self, *arguments):
        return self.run_script(self.pf_path, *arguments)

    def labels(self):
        result = []
        for entry in self.calls("docker"):
            text = " ".join(entry["argv"])
            for prefix, short in ((BUILD_PREFIX, "BUILD "), (PREFIX, "")):
                if text.startswith(prefix):
                    text = short + text[len(prefix):]
                    break
            result.append(text)
        return result

    def stub_calls(self, name):
        return [entry["argv"] for entry in self.calls(name)]


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class Usage(PfHarness):
    def test_help_and_usage_errors(self):
        result = self.pf("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: deploy/production/pf <command> [options]", result.stdout)
        for arguments, message in (
            ((), "a command is required."),
            (("deploy",), "unknown command deploy."),
            (("update",), "pf update needs the release TAG."),
            (("update", "bad tag!"), "TAG must be a release tag"),
            (("restore", BACKUP_NAME), "pf restore needs --approved-by NAME"),
            (("restore", "../x", "--approved-by", "Owner"), "NAME must name a backup folder"),
            (("backup", "--url", "https://x"), "--url is not an option of pf backup."),
            (("status", "--url", "http://x"), "--url must be https://HOST[:PORT]"),
            (("status", "extra"), "pf status takes no argument extra."),
            (("backup", "--reason", 'a "quote"'), '--reason must not contain " or \\.'),
            (("backup", "--reason", "x", "--reason", "y"), "--reason is given twice."),
        ):
            with self.subTest(arguments=arguments):
                self.pf(*arguments)
                self.assertExit(2)
                self.assertIn(message, self.result.stderr)
                self.assertEqual(self.calls(), [])

    def test_not_a_release_checkout(self):
        (self.root / "compose.production.yaml").unlink()
        self.pf("status")
        self.assertExit(2)
        self.assertIn("compose.production.yaml not found", self.result.stderr)
        self.assertEqual(self.calls("docker"), [])

    def test_posix_sh(self):
        text = PF.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn("\nset -eu\n", text)
        self.assertNotIn("\r", text)
        self.assertIsNotNone(find_sh())


# ---------------------------------------------------------------------------
# backup and update run the documented scripts
# ---------------------------------------------------------------------------


class Delegation(PfHarness):
    def test_backup_runs_a_manual_backup(self):
        self.pf("backup", "--reason", "before the NAS move", "--operator", "Ops Person")
        self.assertExit(0)
        self.assertEqual(self.stub_calls("backup.sh"), [["--kind", "manual", "--operator", "Ops Person", "--reason",
                                                        "before the NAS move", "--env-file", ".env.production"]])
        self.reset()
        self.prepend("backup.sh", rule(r".*", out("", rc=4)))
        self.pf("backup")
        self.assertExit(4)
        self.assertEqual(self.stub_calls("backup.sh"), [["--kind", "manual", "--operator", "ops", "--reason",
                                                        "manual backup (pf backup)", "--env-file", ".env.production"]])

    def test_update_checks_out_the_tag_and_runs_release_sh(self):
        self.pf("update", NEW_TAG)
        self.assertExit(0)
        self.assertEqual([" ".join(argv) for argv in self.stub_calls("git")], [
            "status --porcelain --untracked-files=no", "fetch --tags",
            f"rev-parse -q --verify refs/tags/{NEW_TAG}^{{commit}}", f"checkout -q {NEW_TAG}"])
        self.assertEqual(self.stub_calls("release.sh"), [["--release", NEW_TAG, "--operator", "ops", "--approver", "ops",
                                                         "--env-file", ".env.production"]])
        self.assertEqual(self.calls("docker"), [])

    def test_update_refusals_change_nothing(self):
        for name, extra in (
            ("local changes", rule(r"^status --porcelain", out(" M backend/app/main.py\n"))),
            ("no such tag", rule(r"^rev-parse -q --verify refs/tags/v1\.1\.0", out("", rc=1))),
        ):
            with self.subTest(case=name):
                self.reset()
                self.prepend("git", extra)
                self.pf("update", NEW_TAG, "--approver", "Owner")
                self.assertExit(1)
                self.assertNotIn(f"checkout -q {NEW_TAG}", [" ".join(argv) for argv in self.stub_calls("git")])
                self.assertEqual(self.stub_calls("release.sh"), [])
        with self.subTest(case="release.sh exit status"):
            self.reset()
            self.prepend("release.sh", rule(r".*", out("", rc=3)))
            self.pf("update", NEW_TAG, "--approver", "Owner")
            self.assertExit(3)
            self.assertEqual(self.stub_calls("release.sh")[0][5], "Owner")


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


class Install(PfHarness):
    def test_install_on_an_empty_database(self):
        self.pf("install")
        self.assertExit(0)
        self.assertEqual(self.labels(), [
            "BUILD build",
            "up -d db",
            "ps -q db",
            "inspect --format {{.State.Health.Status}} cid-db",
            'exec -T db sh -c psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_class c JOIN'
            " pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'\"",
            "--profile ops run --rm -T db-roles",
            "--profile ops run --rm -T migrate --no-backup-reason first install: empty database",
            "up -d backend web",
        ])
        entries = self.calls("docker")
        self.assertEqual(entries[0]["env"].get("PARTFLOW_COMMIT"), COMMIT)
        self.assertEqual(entries[-1]["env"].get("PARTFLOW_BACKEND_WORKERS"), "1")
        self.assertIn(f"PartFlow {RELEASE} is installed", self.result.stdout)

    def test_install_refusals(self):
        with self.subTest(case="checkout not at the release tag"):
            self.prepend("git", rule(r"^rev-parse HEAD$", out("e" * 40 + "\n")))
            self.pf("install")
            self.assertExit(1)
            self.assertIn(f"run `git checkout {RELEASE}` first", self.result.stderr)
            self.assertEqual(self.calls("docker"), [])
        with self.subTest(case="database not empty"):
            self.reset()
            self.prepend("docker", rule(PSQL_COUNT, out("42\n")))
            self.pf("install")
            self.assertExit(1)
            self.assertIn("the database already holds PartFlow tables", self.result.stderr)
            self.assertFalse([c for c in self.labels() if "db-roles" in c or "migrate" in c or "backend" in c])
        with self.subTest(case="backend never ready"):
            self.reset()
            self.rules["curl"] = [rule(r"^GET /api/health$", {"status": 200, "body": health_body(release="v0.9.0"),
                                                               "headers": {}})]
            self.pf("install")
            self.assertExit(2)
            self.assertIn(f"/api/health does not report release {RELEASE}", self.result.stderr)


# ---------------------------------------------------------------------------
# restore (new-instance restore)
# ---------------------------------------------------------------------------


class Restore(PfHarness):
    def setUp(self):
        super().setUp()
        backup = self.backup_dir / BACKUP_NAME
        backup.mkdir()
        (backup / "partflow.dump").write_bytes(b"PGDMP restore test dump bytes")
        (backup / "SHA256SUMS").write_text("x  partflow.dump\n", encoding="utf-8")

    def restore_records(self):
        return sorted((self.tmp / "partflow-deployments").glob(f"*-restore-{BACKUP_NAME}"))

    def test_restore_into_an_empty_database(self):
        self.pf("restore", BACKUP_NAME, "--approved-by", "Owner Name", "--operator", "Ops Person")
        self.assertExit(0)
        labels = self.labels()
        self.assertEqual(labels[0], f"--profile ops run --rm -T --user 1000:1000 backup-tools backup-verify {BACKUP_NAME}")
        order = ["up -d db", "pg_restore", "db-roles", "db-roles apply-grants", "app.cli revision", "app.cli reconcile",
                 "up -d backend web"]
        positions = [next(i for i, c in enumerate(labels) if step in c) for step in order]
        self.assertEqual(positions, sorted(positions))
        restore = [entry for entry in self.calls("docker") if "pg_restore" in " ".join(entry["argv"])][0]
        self.assertEqual(restore["stdin"]["bytes"], len(b"PGDMP restore test dump bytes"))
        self.assertEqual(self.stub_calls("smoke.sh"), [["--release", RELEASE, "--env-file", ".env.production"]])
        record = self.restore_records()
        self.assertEqual(len(record), 1)
        approval = json.loads((record[0] / "restore.json").read_text(encoding="utf-8"))
        self.assertEqual((approval["backup"], approval["release"], approval["operator"], approval["approved_by"]),
                         (BACKUP_NAME, RELEASE, "Ops Person", "Owner Name"))
        self.assertTrue((record[0] / "reconcile.json").exists())

    def test_restore_refusals_never_start_partflow(self):
        cases = {
            "files do not match": ("sha256sum", rule(r".*", out("partflow.dump: FAILED\n", rc=1)), 1, "do not use this backup"),
            "verification fails": ("docker", rule(r"backup-tools backup-verify ", out(verify_doc(exit_code=1), rc=1)), 1,
                                   "failed verification"),
            "another release": ("docker", rule(r"backup-tools backup-verify ", out(verify_doc(release="v0.9.0"))), 1,
                                "is a backup of release v0.9.0"),
            "database not empty": ("docker", rule(PSQL_COUNT, out("7\n")), 1, "the database is not empty"),
            "not enough space": ("docker", rule(DB_DF, df_text(1024)), 1, "MiB needed"),
            "restore fails": ("docker", rule(r" exec -T db sh -c pg_restore ", {"read_stdin": True, "rc": 1}), 1,
                              "pg_restore failed and rolled back"),
            "reconcile findings": ("docker", rule(r"app\.cli reconcile ", out(drill_reconcile_doc(exit_code=1), rc=1)), 1,
                                   "review"),
        }
        for name, (tool, extra, code, message) in cases.items():
            with self.subTest(case=name):
                self.reset()
                self.prepend(tool, extra)
                self.pf("restore", BACKUP_NAME, "--approved-by", "Owner")
                self.assertExit(code)
                self.assertIn(message, self.result.stderr)
                self.assertNotIn("up -d backend web", self.labels())
                self.assertEqual(self.stub_calls("smoke.sh"), [])
        with self.subTest(case="no backup folder"):
            self.reset()
            self.pf("restore", "20261008T030000Z-manual", "--approved-by", "Owner")
            self.assertExit(1)
            self.assertIn("there is no backup folder", self.result.stderr)
            self.assertEqual(self.calls("docker"), [])


# ---------------------------------------------------------------------------
# delete-permanently (OPERATIONS_RUNBOOK §10)
# ---------------------------------------------------------------------------

DELETE_RUN = ["--profile", "ops", "run", "--rm", "--no-deps", "-T", "db-roles", "delete-permanently"]
DELETE_RULE = r" --profile ops run --rm --no-deps -T db-roles delete-permanently "
MANUAL_BACKUP = "20261117T010203Z-manual"


def deletion_doc(result, exit_code):
    return out(json.dumps({"report_version": 1, "command": "delete-permanently", "result": result,
                           "exit_code": exit_code}, indent=2) + "\n", rc=exit_code)


class DeletePermanently(PfHarness):
    TARGETS = ["--work-order", " WO 'quoted' \"x\" $HOME `id` \\ * ", "--work-order", "WO-2", "--part-number",
               "pn-1"]

    def setUp(self):
        super().setUp()
        self.prepend("docker", rule(DELETE_RULE + r".*--confirm", deletion_doc("deleted", 0)),
                     rule(DELETE_RULE, deletion_doc("preview", 0)))
        self.prepend("backup.sh", rule(r".*", out(f"BACKUP {MANUAL_BACKUP} /backups/{MANUAL_BACKUP}\n")))

    def deletions(self):
        calls = []
        for entry in self.calls("docker"):
            argv = entry["argv"]
            if "db-roles" in argv:
                start = argv.index("--profile")
                self.assertEqual(argv[start:start + len(DELETE_RUN)], DELETE_RUN)
                calls.append(argv[start + len(DELETE_RUN):])
        return calls

    def test_preview_passes_every_value_verbatim_and_changes_nothing(self):
        self.pf("delete-permanently", *self.TARGETS, "--reason", "typo test data")
        self.assertExit(0)
        self.assertEqual(self.deletions(), [[*self.TARGETS, "--operator", "ops", "--reason", "typo test data"]])
        self.assertIn('"result": "preview"', self.result.stdout)
        self.assertEqual(self.stub_calls("backup.sh"), [])

    def test_confirm_takes_a_backup_then_deletes(self):
        self.pf("delete-permanently", *self.TARGETS, "--reason", "typo", "--operator", "Ops Person", "--confirm")
        self.assertExit(0)
        common = [*self.TARGETS, "--operator", "Ops Person", "--reason", "typo"]
        self.assertEqual(self.deletions(), [common, [*common, "--confirm", "--backup-name", MANUAL_BACKUP]])
        self.assertEqual(self.stub_calls("backup.sh"), [["--kind", "manual", "--operator", "Ops Person", "--reason",
                                                        "before pf delete-permanently", "--env-file",
                                                        ".env.production"]])
        order = [entry["tool"] for entry in self.calls() if entry["tool"] in ("docker", "backup.sh")]
        self.assertEqual(order, ["docker", "backup.sh", "docker"])
        self.assertIn('"result": "deleted"', self.result.stdout)
        self.assertNotIn('"result": "preview"', self.result.stdout)

    def test_a_refused_preview_takes_no_backup(self):
        self.prepend("docker", rule(DELETE_RULE, deletion_doc("refused", 1)))
        self.pf("delete-permanently", "--work-order", "WO-1", "--reason", "typo", "--confirm")
        self.assertExit(1)
        self.assertIn('"result": "refused"', self.result.stdout)
        self.assertIn("No backup was taken and nothing was deleted", self.result.stderr)
        self.assertEqual(len(self.deletions()), 1)
        self.assertEqual(self.stub_calls("backup.sh"), [])

    def test_a_failed_backup_deletes_nothing(self):
        for name, answer in (("backup fails", out("", rc=3)), ("no backup named", out("", rc=0))):
            with self.subTest(case=name):
                self.reset()
                self.prepend("backup.sh", rule(r".*", answer))
                self.pf("delete-permanently", "--part-number", "PN-1", "--reason", "typo", "--confirm")
                self.assertExit(2)
                self.assertIn("Nothing was deleted", self.result.stderr)
                self.assertEqual([call[-1] for call in self.deletions()], ["typo"])

    def test_the_deletion_exit_status_is_pf_exit_status(self):
        self.prepend("docker", rule(DELETE_RULE + r".*--confirm", deletion_doc("failed", 2)))
        self.pf("delete-permanently", "--work-order", "WO-1", "--reason", "typo", "--confirm")
        self.assertExit(2)
        self.assertEqual(len(self.deletions()), 2)

    def test_usage_errors(self):
        for arguments, message in (
            (("delete-permanently", "--reason", "x"), "needs at least one --work-order NUMBER or --part-number PN"),
            (("delete-permanently", "--work-order", "W"), "needs --reason TEXT"),
            (("delete-permanently", "--work-order", "  ", "--reason", "x"), "--work-order must not be empty."),
            (("delete-permanently", "--part-number", "a\tb", "--reason", "x"),
             "--part-number must not contain control characters."),
            (("delete-permanently", "W", "--reason", "x"), "takes no argument W"),
            (("delete-permanently", "--work-order", "W", "--reason", "x", "--confirm", "--confirm"),
             "--confirm is given twice."),
            (("backup", "--confirm"), "--confirm is not an option of pf backup."),
            (("backup", "--work-order", "W"), "--work-order is not an option of pf backup."),
        ):
            with self.subTest(arguments=arguments):
                self.pf(*arguments)
                self.assertExit(2)
                self.assertIn(message, self.result.stderr)
                self.assertEqual(self.calls(), [])


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class Status(PfHarness):
    def test_status_reports_containers_health_and_the_status_summary(self):
        self.pf("status")
        self.assertExit(0)
        self.assertIn("partflow-production-db-1 Up (healthy)", self.result.stdout)
        self.assertIn(f'health: {health_body(release=RELEASE)}', self.result.stdout)
        self.assertIn("status: ok (database partflow, schema current)", self.result.stdout)
        self.assertEqual(self.labels(), ["ps", "--profile ops run --rm --no-deps -T --user 1000:1000 status"])

    def test_status_fails_when_something_does(self):
        self.prepend("docker", rule(r" status$", out(report("status", "attention", 1), rc=1,
                                                       stderr="status: ATTENTION — backup_stale\n")))
        self.pf("status")
        self.assertExit(1)
        self.assertIn("status: ATTENTION — backup_stale", self.result.stdout)
        self.reset()
        self.rules["curl"] = []
        self.pf("status")
        self.assertExit(1)
        self.assertIn("health: no answer on http://127.0.0.1:18080/api/health", self.result.stdout)

    def test_status_with_url_runs_check_sh(self):
        self.pf("status", "--url", "https://partflow.example")
        self.assertExit(0)
        self.assertEqual(self.stub_calls("check.sh"), [["--url", "https://partflow.example", "--env-file",
                                                       ".env.production", "--no-state"]])
        self.assertEqual(self.calls("docker"), [])


if __name__ == "__main__":
    unittest.main()
