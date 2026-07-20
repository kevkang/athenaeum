import importlib.util
from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0011_statements.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_0011_statements", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


def _find_call_index(calls, name: str, *expected_args: object) -> int:
    for idx, (call_name, args, _kwargs) in enumerate(calls):
        if call_name == name and args[: len(expected_args)] == expected_args:
            return idx
    raise AssertionError(f"Call not found: {name}{expected_args!r}")


def test_upgrade_drops_old_constraints_before_retargeting_rows() -> None:
    migration = _load_migration_module()
    recorder = _Recorder()
    migration.op = recorder

    migration.upgrade()

    drop_statement_evidence = _find_call_index(
        recorder.calls, "drop_constraint", "brick_rests_on_target_kind_chk", "statement_evidence"
    )
    update_statement_evidence = _find_call_index(
        recorder.calls,
        "execute",
        "UPDATE statement_evidence SET target_kind = 'statement' WHERE target_kind = 'brick'",
    )
    drop_annotations = _find_call_index(
        recorder.calls, "drop_constraint", "annotations_target_kind_chk", "annotations"
    )
    update_annotations = _find_call_index(
        recorder.calls,
        "execute",
        "UPDATE annotations SET target_kind = 'statement' WHERE target_kind = 'brick'",
    )

    assert drop_statement_evidence < update_statement_evidence
    assert drop_annotations < update_annotations


def test_downgrade_drops_new_constraints_before_restoring_brick_rows() -> None:
    migration = _load_migration_module()
    recorder = _Recorder()
    migration.op = recorder

    migration.downgrade()

    drop_statement_evidence = _find_call_index(
        recorder.calls,
        "drop_constraint",
        "statement_evidence_target_kind_chk",
        "statement_evidence",
    )
    update_statement_evidence = _find_call_index(
        recorder.calls,
        "execute",
        "UPDATE statement_evidence SET target_kind = 'brick' WHERE target_kind = 'statement'",
    )
    drop_annotations = _find_call_index(
        recorder.calls, "drop_constraint", "annotations_target_kind_chk", "annotations"
    )
    update_annotations = _find_call_index(
        recorder.calls,
        "execute",
        "UPDATE annotations SET target_kind = 'brick' WHERE target_kind = 'statement'",
    )

    assert drop_statement_evidence < update_statement_evidence
    assert drop_annotations < update_annotations
