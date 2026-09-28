"""JSONB columns: store "absent" as SQL NULL, and make selector CHECKs reject JSON null

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-27

Corrects a pitfall in columns added by 0029-0032. SQLAlchemy's JSON types, by
default, persist Python None as the JSON value `null` — not SQL NULL. The two
are different: `'null'::jsonb IS NULL` is false. So:

- A row meant to say "absent" did not match `IS NULL`. The practical damage was
  in two places. A Zotero highlight imported without a position (0029) was
  meant to be findable by a later backfill through
  `zotero_position_json IS NULL`, and would have been skipped. A passage whose
  selector check_passage cleared (0031) was likewise invisible to any "not yet
  anchored" query.
- The selector CHECKs could be bypassed. `'null'::jsonb ->> 'type'` is NULL, a
  CHECK treats NULL as passing, and node_evidence's NOT NULL does not catch it
  either — so an unanchored quote could be stored as evidence. A test written
  to prove that impossible found it possible.

The models now declare these columns JSONB(none_as_null=True), so new writes
store SQL NULL. This migration converts any JSON null already stored, then
replaces the selector CHECKs with ones that require an object, the right type,
and the one field W3C makes mandatory, `exact`. The conversion runs first,
because the stricter CHECK would otherwise reject those rows.

Converting JSON null to SQL NULL loses nothing: both meant "absent".

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

# Every JSONB column added in 0029-0032, where NULL means "absent".
_COLUMNS = (
    ("passages", "text_quote_selector"),
    ("highlights", "text_quote_selector"),
    ("highlights", "zotero_position_json"),
    ("audit_draft_citations", "candidates_json"),
    ("audit_draft_citations", "matched_authors_json"),
    ("nucleus_expansion_events", "counts"),
)

_STRICT = (
    "jsonb_typeof(text_quote_selector) = 'object' "
    "AND text_quote_selector->>'type' IS NOT DISTINCT FROM 'TextQuoteSelector' "
    "AND text_quote_selector ? 'exact'"
)
_LOOSE = "text_quote_selector->>'type' = 'TextQuoteSelector'"

_SELECTOR_CHECKS = (
    # (table, constraint, strict condition, loose condition it replaces)
    ("passages", "passages_text_quote_selector_chk",
     f"text_quote_selector IS NULL OR ({_STRICT})",
     f"text_quote_selector IS NULL OR {_LOOSE}"),
    ("highlights", "highlights_text_quote_selector_chk",
     f"text_quote_selector IS NULL OR ({_STRICT})",
     f"text_quote_selector IS NULL OR {_LOOSE}"),
    ("node_evidence", "node_evidence_text_quote_selector_chk", _STRICT, _LOOSE),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in _COLUMNS:
        converted = bind.execute(
            sa.text(f"UPDATE {table} SET {column} = NULL WHERE {column} = 'null'::jsonb")
        ).rowcount
        log.info("0033: %s.%s — %d JSON null values converted to SQL NULL",
                 table, column, converted)

    # node_evidence is NOT NULL, so it cannot hold SQL NULL. Any JSON null there
    # is an unanchored quote that should never have been stored; report it
    # rather than guess, and the stricter CHECK below will then refuse it.
    unanchored = bind.execute(
        sa.text("SELECT count(*) FROM node_evidence WHERE text_quote_selector = 'null'::jsonb")
    ).scalar_one()
    if unanchored:
        raise RuntimeError(
            f"0033: {unanchored} node_evidence rows hold a JSON null selector — unanchored "
            "evidence that should not exist. Resolve them before upgrading."
        )

    for table, name, strict, _loose in _SELECTOR_CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, strict)
    log.info("0033: 3 selector CHECKs now reject JSON null, non-objects, and a missing 'exact'")


def downgrade() -> None:
    """Restore the looser CHECKs. The NULLs stay NULL: they already meant
    "absent", and turning them back into JSON null would only reintroduce the
    ambiguity this migration removed."""
    for table, name, _strict, loose in _SELECTOR_CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, loose)
    log.info("0033 downgrade: selector CHECKs loosened; converted NULLs left as SQL NULL")
