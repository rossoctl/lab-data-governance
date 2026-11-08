"""Replace plaintext decision fingerprints with SHA-256 digests.

Canonical JSON fingerprints duplicated detected and inherited matched text
in every cached decision version. Hash the exact existing UTF-8 bytes so
full-input cache keys remain reusable, without changing decision history.
Normalize legacy writes too, so old pods cannot restore plaintext during
a rolling upgrade. PostgreSQL's built-in SHA-256 needs no extension.

Revision ID: 0029_hash_evidence_fingerprints
Revises: 0028_correction_replay
Create Date: 2026-11-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0029_hash_evidence_fingerprints"
down_revision: str = "0028_correction_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION dg_hash_policy_fingerprint() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.evidence_fingerprint !~ '^[0-9a-f]{64}$' THEN
                NEW.evidence_fingerprint := encode(
                    sha256(convert_to(NEW.evidence_fingerprint, 'UTF8')), 'hex'
                );
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER dg_policy_fingerprint_digest "
        "BEFORE INSERT OR UPDATE OF evidence_fingerprint ON interaction_policy_decisions "
        "FOR EACH ROW EXECUTE FUNCTION dg_hash_policy_fingerprint()"
    )
    op.execute(
        """
        UPDATE interaction_policy_decisions
        SET evidence_fingerprint = encode(sha256(convert_to(evidence_fingerprint, 'UTF8')), 'hex')
        WHERE evidence_fingerprint !~ '^[0-9a-f]{64}$'
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER dg_policy_fingerprint_digest ON interaction_policy_decisions")
    op.execute("DROP FUNCTION dg_hash_policy_fingerprint()")
    # Digests are deliberately irreversible. Downgrading removes the write
    # guard but cannot reconstruct matched text or prior JSON cache keys.
