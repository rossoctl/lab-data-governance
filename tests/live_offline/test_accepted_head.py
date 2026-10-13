"""Offline guard for the ``E2E_ACCEPT_ALEMBIC_HEAD`` rule in tests/live/conftest.py:
the override may name only a revision this checkout does not have (another
branch's later migration); naming the checkout's own head or an older revision
is refused, so a run can never proceed on an older schema by override."""

from tests.live.conftest import accepted_alembic_head

KNOWN = {"0018_a", "0019_b", "0020_c"}


def test_unset_is_strict() -> None:
    assert accepted_alembic_head("0020_c", None, KNOWN) == ("0020_c", None)
    assert accepted_alembic_head("0020_c", "", KNOWN) == ("0020_c", None)


def test_a_foreign_later_revision_is_accepted_and_recorded() -> None:
    head, refused = accepted_alembic_head("0020_c", "0021_other_branch", KNOWN)
    assert (head, refused) == ("0021_other_branch", None)


def test_the_checkouts_own_head_is_refused() -> None:
    head, refused = accepted_alembic_head("0020_c", "0020_c", KNOWN)
    assert head == "0020_c" and refused and "is a revision of this checkout" in refused


def test_an_older_revision_is_refused() -> None:
    head, refused = accepted_alembic_head("0020_c", "0019_b", KNOWN)
    assert head == "0020_c" and refused and "0019_b" in refused
