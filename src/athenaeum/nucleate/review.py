"""What a person's action on a node review does to it.

Pure, so the rules can be tested without a database. The rules that matter:

- Accepting or pinning a node accepts its statement as written: an
  ai_proposed statement becomes ai_proposed_accepted. That is the moment its
  styling stops being AI text (handoff Part IV).
- Editing a statement records who shaped it: ai_proposed_corrected if the
  archivist wrote the original, user_set if a person did (or there was none).
- A rejection reason belongs to a rejection. Moving off rejected clears it.
- Rejecting leaves the statement's provenance alone; rejecting a paper is not
  a judgement on how it was described.
"""
from __future__ import annotations

from athenaeum.domain import nucleate as v
from athenaeum.storage.models import NodeReview

_AI_WRITTEN = (v.AI_PROPOSED, v.AI_PROPOSED_ACCEPTED, v.AI_PROPOSED_CORRECTED)


class InvalidReviewUpdate(ValueError):
    pass


def apply_review_update(
    review: NodeReview,
    *,
    status: str | None = None,
    rejection_reason: str | None = None,
    relevance_text: str | None = None,
) -> None:
    """Apply one update from the drawer to a review, in place."""
    if status is not None and status not in v.REVIEW_STATUSES:
        raise InvalidReviewUpdate(f"unknown status {status!r}")
    if rejection_reason is not None and (status or review.status) != v.REJECTED:
        raise InvalidReviewUpdate("a rejection reason can only accompany a rejection")

    if relevance_text is not None:
        text = relevance_text.strip()
        if not text:
            review.relevance_text = None
            review.relevance_provenance = None
        elif text != (review.relevance_text or ""):
            review.relevance_provenance = (
                v.AI_PROPOSED_CORRECTED if review.relevance_provenance in _AI_WRITTEN
                else v.USER_SET
            )
            review.relevance_text = text

    if status is not None:
        review.status = status
        if status != v.REJECTED:
            review.rejection_reason = None
        if status in (v.ACCEPTED, v.PINNED) and review.relevance_provenance == v.AI_PROPOSED:
            review.relevance_provenance = v.AI_PROPOSED_ACCEPTED

    if rejection_reason is not None:
        review.rejection_reason = rejection_reason.strip() or None
