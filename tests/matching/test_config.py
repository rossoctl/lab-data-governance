"""Which matcher is active is chosen by configuration (issue #116).

ADR-0028 makes matching pluggable: lineage calls ``get_matcher()`` and never names
an implementation, so shipping a real matcher (value-based, confidential-aware) is
a ``SEMANTIC_MATCHER`` change plus a registry entry — never an edit at a call site.
The default is the trivial ``simple_match``, which is what makes lineage computable
today (complete but full of maybes).

Selection follows the repo's env-var convention (``INTERACTIONS_ALGORITHM`` in
``processors/interactions/__main__.py``: a name → callable registry, an explicit
default, a hard failure on an unknown name).

Pure: no database, no network, no lineage import.
"""

from __future__ import annotations

import pytest

from data_governance.matching import (
    MATCHER_ENV_VAR,
    MatchResult,
    UnknownMatcher,
    get_matcher,
    simple_match,
)


def test_the_default_matcher_is_the_trivial_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset configuration means the trivial default — the "no need to change in
    run time" baseline of the spec, so lineage has a matcher from day one."""
    monkeypatch.delenv(MATCHER_ENV_VAR, raising=False)

    assert get_matcher() is simple_match


def test_an_empty_setting_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty/whitespace value is "unset", not an unknown matcher — a blank env
    var in a manifest must not crash the processor."""
    monkeypatch.setenv(MATCHER_ENV_VAR, "  ")

    assert get_matcher() is simple_match


def test_the_configured_matcher_is_selected_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming a registered matcher selects it. Registering a stub here is exactly
    how a real matcher lands later: one registry entry plus configuration, no
    change where ``get_matcher`` is called."""

    def stub_matcher(payload_a: object, payload_b: object) -> MatchResult:
        return MatchResult(matched=False)

    monkeypatch.setitem(_registry(), "stub", stub_matcher)
    monkeypatch.setenv(MATCHER_ENV_VAR, "stub")

    matcher = get_matcher()
    assert matcher is stub_matcher
    assert matcher("in", "out").matched is False


def test_the_trivial_matcher_can_be_named_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default is a real registry entry, so a deployment can pin it explicitly
    rather than relying on the absent-value fallback."""
    monkeypatch.setenv(MATCHER_ENV_VAR, "simple")

    assert get_matcher() is simple_match


def test_an_unknown_matcher_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd matcher name must not silently degrade to the trivial matcher —
    that would produce maybe-everywhere lineage while the operator believes a real
    matcher is running. Fail with the valid names, mirroring
    ``INTERACTIONS_ALGORITHM``."""
    monkeypatch.setenv(MATCHER_ENV_VAR, "definitely-not-a-matcher")

    with pytest.raises(UnknownMatcher) as excinfo:
        get_matcher()

    message = str(excinfo.value)
    assert "definitely-not-a-matcher" in message
    assert "simple" in message


def test_an_explicit_name_overrides_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller (a test, a CLI) may pass the name directly; the environment is only
    the default source of that name."""
    monkeypatch.setenv(MATCHER_ENV_VAR, "definitely-not-a-matcher")

    assert get_matcher("simple") is simple_match


def _registry() -> dict:
    """The name → matcher registry, reached through the package's private module.

    Tests register a stub matcher to prove config selection works for a
    non-default implementation. Production code must go through ``get_matcher``;
    only this test peeks at the registry, and it does so via ``monkeypatch.setitem``
    so the mutation is undone.
    """
    from data_governance.matching import config as _config

    return _config._MATCHERS
