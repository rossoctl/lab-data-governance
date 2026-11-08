"""Label lineage: which classified units are still present in each payload of a
trace, and how much of each — a second derivation beside data lineage, over the
same legs, the same inbound routing and the same classifier output.

Pure modules, one concern each: :mod:`.value` reads a finding's value,
:mod:`.units` holds the nouns and the units of one payload, :mod:`.find` is the
matcher registry, :mod:`.fold` walks a trace, :mod:`.summary` says what a leg
inherited. The database side is ``processors/label_lineage``.
"""
