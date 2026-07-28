from athenaeum.synthesize.passage_util import is_contiguous


def test_empty_and_single_are_contiguous() -> None:
    assert is_contiguous([]) is True
    assert is_contiguous([5]) is True


def test_consecutive_ords_are_contiguous() -> None:
    assert is_contiguous([3, 4, 5]) is True


def test_out_of_order_input_is_not_treated_as_contiguous() -> None:
    # Callers are expected to pass already-sorted `ord` values (as returned
    # by an `ORDER BY ord` query) — an unsorted list should not be silently
    # accepted as contiguous.
    assert is_contiguous([5, 3, 4]) is False


def test_gap_is_not_contiguous() -> None:
    assert is_contiguous([3, 4, 7]) is False


def test_duplicate_ord_is_not_contiguous() -> None:
    assert is_contiguous([3, 3, 4]) is False
