"""semantic_edges, semantic_edge_evidence; KnowledgeLink rows carried over

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-27

Typed paper → paper relations on the six-term CiTO subset (D-N7), owned by the
project rather than any nucleus (D-N20), proposed by the archivist only on
request and always with a verbatim quote from the citing paper (D-N19).

What the database guarantees rather than application code:

- the relation (and the archivist's original proposal) is one of the six;
- provenance is one of the four (D-N12), and agrees with the proposal:
  unreviewed and accepted relations are exactly what was proposed, a
  corrected one differs from it, and a user-set one has no proposal at all.
  The comparison is IS NOT DISTINCT FROM, not `=`: with `=`, an AI relation
  missing its proposal compares as NULL, and a CHECK passes on NULL;
- both papers are in the edge's project, via composite foreign keys onto
  corpus_nodes (id, project_id), for which that pair gains a unique constraint;
- no paper relates to itself, and a rejection reason implies a rejection.

KnowledgeLink carry-over (NUCLEATE_PLAN.md "Legacy"): paper → paper links
whose rel maps onto the six (supports, contradicts → disagrees_with, extends)
become user_set edges, keeping their author and date. `cites` and `related`
are catch-alls with no equivalent and are counted, not guessed at; links
touching a synthesis have no paper on one end and are counted too. Papers get
a corpus node if they lack one. knowledge_links itself is left untouched.

The relation list below is a frozen copy of domain/relations.py's six terms,
per this project's convention that a migration gives the same result forever.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_UUID = postgresql.UUID(as_uuid=True)

_RELATIONS = (
    "uses_method_of", "uses_data_from", "extends",
    "supports", "disagrees_with", "obtains_background_from",
)
_RELATION_SQL = ", ".join(f"'{r}'" for r in _RELATIONS)
_LEGACY_MAP = {"supports": "supports", "contradicts": "disagrees_with", "extends": "extends"}


def upgrade() -> None:
    bind = op.get_bind()

    op.create_unique_constraint(
        "corpus_nodes_id_project_uq", "corpus_nodes", ["id", "project_id"]
    )

    op.create_table(
        "semantic_edges",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("from_node_id", _UUID, nullable=False),
        sa.Column("to_node_id", _UUID, nullable=False),
        sa.Column("relation", sa.String(30), nullable=False),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("proposed_relation", sa.String(30)),
        sa.Column("rationale", sa.Text),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.ForeignKeyConstraint(
            ["from_node_id", "project_id"], ["corpus_nodes.id", "corpus_nodes.project_id"],
            ondelete="CASCADE", name="semantic_edges_from_node_fk",
        ),
        sa.ForeignKeyConstraint(
            ["to_node_id", "project_id"], ["corpus_nodes.id", "corpus_nodes.project_id"],
            ondelete="CASCADE", name="semantic_edges_to_node_fk",
        ),
        sa.UniqueConstraint("from_node_id", "to_node_id", "relation",
                            name="semantic_edges_pair_relation_uq"),
        sa.CheckConstraint("from_node_id <> to_node_id", name="semantic_edges_not_self_chk"),
        sa.CheckConstraint(f"relation IN ({_RELATION_SQL})", name="semantic_edges_relation_chk"),
        sa.CheckConstraint(
            f"proposed_relation IS NULL OR proposed_relation IN ({_RELATION_SQL})",
            name="semantic_edges_proposed_relation_chk",
        ),
        sa.CheckConstraint(
            "provenance IN ('ai_proposed', 'ai_proposed_accepted', "
            "'ai_proposed_corrected', 'user_set')",
            name="semantic_edges_provenance_chk",
        ),
        sa.CheckConstraint(
            "(provenance = 'user_set' AND proposed_relation IS NULL) OR "
            "(provenance IN ('ai_proposed', 'ai_proposed_accepted') "
            " AND relation IS NOT DISTINCT FROM proposed_relation) OR "
            "(provenance = 'ai_proposed_corrected' AND proposed_relation IS NOT NULL "
            " AND relation <> proposed_relation)",
            name="semantic_edges_provenance_agrees_chk",
        ),
        sa.CheckConstraint(
            "rejection_reason IS NULL OR rejected_at IS NOT NULL",
            name="semantic_edges_rejection_reason_chk",
        ),
    )
    op.create_index("semantic_edges_project_idx", "semantic_edges", ["project_id"])
    op.create_index("semantic_edges_to_node_idx", "semantic_edges", ["to_node_id"])

    op.create_table(
        "semantic_edge_evidence",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("edge_id", _UUID, sa.ForeignKey("semantic_edges.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("verbatim_text", sa.Text, nullable=False),
        sa.Column("text_quote_selector", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("edge_id", "ord", name="semantic_edge_evidence_edge_ord_uq"),
        sa.CheckConstraint("source IN ('full_text', 'abstract')",
                           name="semantic_edge_evidence_source_chk"),
        sa.CheckConstraint(
            "jsonb_typeof(text_quote_selector) = 'object' "
            "AND text_quote_selector->>'type' IS NOT DISTINCT FROM 'TextQuoteSelector' "
            "AND text_quote_selector ? 'exact'",
            name="semantic_edge_evidence_text_quote_selector_chk",
        ),
    )

    _carry_over_knowledge_links(bind)


def _carry_over_knowledge_links(bind: sa.engine.Connection) -> None:
    total = bind.execute(sa.text("SELECT count(*) FROM knowledge_links")).scalar_one()
    touching_synthesis = bind.execute(sa.text(
        "SELECT count(*) FROM knowledge_links "
        "WHERE from_type <> 'paper' OR to_type <> 'paper'"
    )).scalar_one()
    unmapped = bind.execute(sa.text(
        "SELECT rel, count(*) FROM knowledge_links "
        "WHERE from_type = 'paper' AND to_type = 'paper' "
        "AND rel NOT IN ('supports', 'contradicts', 'extends') GROUP BY rel"
    )).all()

    mapping = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in _LEGACY_MAP.items())
    carried = "SELECT * FROM knowledge_links WHERE from_type = 'paper' AND to_type = 'paper' " \
              "AND rel IN ('supports', 'contradicts', 'extends') AND from_id <> to_id"

    nodes = bind.execute(sa.text(f"""
        INSERT INTO corpus_nodes (id, project_id, paper_id)
        SELECT gen_random_uuid(), project_id, paper_id FROM (
            SELECT project_id, from_id AS paper_id FROM ({carried}) l
            UNION SELECT project_id, to_id FROM ({carried}) l
        ) p
        WHERE EXISTS (SELECT 1 FROM papers WHERE papers.id = p.paper_id)
        ON CONFLICT (project_id, paper_id) DO NOTHING
    """)).rowcount

    edges = bind.execute(sa.text(f"""
        INSERT INTO semantic_edges
            (id, project_id, from_node_id, to_node_id, relation, provenance,
             created_by, created_at, updated_at)
        SELECT gen_random_uuid(), l.project_id, a.id, b.id,
               CASE l.rel {mapping} END, 'user_set', l.created_by, l.created_at, l.created_at
        FROM ({carried}) l
        JOIN corpus_nodes a ON a.project_id = l.project_id AND a.paper_id = l.from_id
        JOIN corpus_nodes b ON b.project_id = l.project_id AND b.paper_id = l.to_id
        ON CONFLICT (from_node_id, to_node_id, relation) DO NOTHING
    """)).rowcount

    log.info("0035: %d knowledge links: %d carried over as user_set semantic edges "
             "(%d corpus nodes created for their papers); %d touch a synthesis, and "
             "unmapped paper links by rel: %s — all left in knowledge_links",
             total, edges, nodes, touching_synthesis, dict(unmapped) or "none")


def downgrade() -> None:
    """Drop both tables, and the corpus nodes this migration created.

    Nodes created here have no lineage edge (every other node is created with
    one, by place_node), so "no lineage edge points at it" finds them. It also
    finds any node whose every nucleus has since been deleted; such a node is
    shown nowhere, so removing it loses nothing.
    """
    bind = op.get_bind()
    edges = bind.execute(sa.text("SELECT count(*) FROM semantic_edges")).scalar_one()
    evidence = bind.execute(sa.text("SELECT count(*) FROM semantic_edge_evidence")).scalar_one()
    log.info("0035 downgrade: discarding %d semantic edges and %d evidence quotes",
             edges, evidence)
    op.drop_table("semantic_edge_evidence")
    op.drop_table("semantic_edges")
    orphans = bind.execute(sa.text(
        "DELETE FROM corpus_nodes n WHERE NOT EXISTS "
        "(SELECT 1 FROM lineage_edges e WHERE e.to_node_id = n.id)"
    )).rowcount
    log.info("0035 downgrade: removed %d corpus nodes with no lineage", orphans)
    op.drop_constraint("corpus_nodes_id_project_uq", "corpus_nodes", type_="unique")
