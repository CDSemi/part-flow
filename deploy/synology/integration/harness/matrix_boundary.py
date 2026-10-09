"""Effect-boundary rows (SPEC 8.3.2): an I3 SIGKILL just before (the effect's intent, state ``unknown``) or just
after (state ``complete``) one irreversible effect whose before/after the R-rows do not reach. Named B01..B33; each
runs through the same engine, recovery procedure and oracles as the R-rows."""
from . import matrix
from . import matrix_custom
from . import matrix_rows


def rows(h, inst):
    table = []

    def add(row_id, kind, phase, effect_type, when, *, target_prefix=None, prepare, command, subject_gate=False,
            **extra):
        states = ("unknown",) if when == "before" else ("complete",)
        value = {"id": row_id, "kind": kind, "phase": phase, "effect_type": effect_type, "when": when, "grid": [],
                 "injection": f"I3 SIGKILL at {effect_type}"
                              f"{' ' + target_prefix if target_prefix else ''} {'started' if when == 'before' else 'complete'}",
                 "spec": {"primitive": "I3", "signal": "SIGKILL",
                          "gate": matrix.effect_trigger(h, inst.slug, effect_type=effect_type,
                                                        target_prefix=target_prefix, states=states,
                                                        effect_phase=phase)},
                 "prepare": prepare, "command": command}
        if subject_gate:
            value["gate_effect"] = {"effect_type": effect_type, "target_prefix": target_prefix, "states": states,
                                    "effect_phase": phase}
        value.update(extra)
        table.append(value)

    # the stop executor (Controller.pause, role "stop")
    add("B01", "update", "preserving", "service-change", "before", target_prefix="services:stop",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    add("B02", "update", "preserving", "service-change", "after", target_prefix="services:stop",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    # the candidate drop of act_database (the rehearsal database pf_migrate_*)
    add("B03", "update", "migrating", "database-drop", "before", target_prefix="database:pf_migrate_",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    add("B04", "update", "migrating", "database-drop", "after", target_prefix="database:pf_migrate_",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    # activate_backend (role "backend") and act_frontend (role "frontend")
    add("B05", "update", "activating", "service-change", "after", target_prefix="service:backend",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    add("B08", "update", "activating", "service-change", "before", target_prefix="service:frontend",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    add("B09", "update", "activating", "service-change", "after", target_prefix="service:frontend",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    # the workspace generation switch
    add("B07", "update", "syncing-workspace", "source-switch", "after",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    # act_image_load (restore-instance)
    add("B19", "restore-instance", "preparing-target", "image-load", "before",
        prepare=matrix_rows.prepare_purged, command=matrix_rows.cmd_restore, post=matrix_rows.bring_back,
        data_relation="restored")
    # the isolated topology start (topology_action, restore-side-by-side)
    add("B27", "restore-side-by-side", "activating", "service-change", "before",
        prepare=matrix_custom.prepare_side_by_side, command=matrix_custom.cmd_side_by_side,
        post=matrix_custom.remove_targets)
    add("B28", "restore-side-by-side", "activating", "service-change", "after",
        prepare=matrix_custom.prepare_side_by_side, command=matrix_custom.cmd_side_by_side,
        post=matrix_custom.remove_targets)
    # act_db_start (role "db-start", a first deployment's database service)
    add("B29", "deploy", "initializing", "service-change", "before", prepare=matrix_rows.prepare_fresh,
        command=matrix_rows.cmd_deploy, subject_gate=True)
    add("B30", "deploy", "initializing", "service-change", "after", prepare=matrix_rows.prepare_fresh,
        command=matrix_rows.cmd_deploy, subject_gate=True)
    # cleanup_action resource-delete (pf cleanup --apply)
    add("B33", "cleanup", "deleting", "resource-delete", "before",
        prepare=matrix_custom.prepare_cleanup_target, command=matrix_custom.cmd_cleanup_target,
        post=matrix_custom.remove_targets)
    # act_seal and act_pointer (the deployment record seal and deployed.json)
    add("B10", "update", "activating", "artifact-seal", "before",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    add("B11", "update", "activating", "file-write", "before", target_prefix="pointer:",
        prepare=matrix.prepare_update, command=matrix.cmd_update)
    # act_database: the clean candidate's create (reset-db) and the restore's drop/alter (restore-instance)
    add("B12", "reset-db", "initializing", "database-create", "after",
        prepare=matrix_rows.prepare_running, command=matrix.cmd_reset, data_relation="restored",
        post=matrix.restore_data_after_reset)
    add("B13", "restore-instance", "restoring-data", "database-drop", "before",
        prepare=matrix_rows.prepare_purged, command=matrix_rows.cmd_restore, post=matrix_rows.bring_back,
        data_relation="restored")
    add("B14", "restore-instance", "restoring-data", "database-alter", "before",
        prepare=matrix_rows.prepare_purged, command=matrix_rows.cmd_restore, post=matrix_rows.bring_back,
        data_relation="restored")
    # the side-by-side verification (act_side_by_side_verification)
    add("B15", "restore-side-by-side", "verifying", "verification", "before",
        prepare=matrix_custom.prepare_side_by_side, command=matrix_custom.cmd_side_by_side,
        post=matrix_custom.remove_targets)
    # the abort-deploy deletion (act_delete of a first deployment's resources) and cleanup's candidate drop
    add("B31", "abort-deploy", "deleting", "resource-delete", "before", prepare=matrix_custom.prepare_opened_deploy,
        command=matrix_custom.cmd_abort, subject_gate=True)
    add("B32", "abort-deploy", "deleting", "resource-delete", "after", prepare=matrix_custom.prepare_opened_deploy,
        command=matrix_custom.cmd_abort, subject_gate=True)
    add("B34", "cleanup", "deleting", "database-drop", "after", prepare=matrix_custom.prepare_cleanup_candidate,
        command=matrix_custom.cmd_cleanup_default)
    return table


ORDER = ("B01", "B02", "B03", "B04", "B05", "B08", "B09", "B07", "B10", "B11", "B12", "B19", "B13", "B14", "B27",
         "B28", "B15", "B29", "B30", "B31", "B32", "B33", "B34")
