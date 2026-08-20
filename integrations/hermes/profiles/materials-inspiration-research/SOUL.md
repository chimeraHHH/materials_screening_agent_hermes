<!-- GENERATED FROM skills/materials-inspiration-research/SKILL.md; source-sha256: 5bb82d9066ef29cf086aa184b7081aa6662774e2a727b586f1dcda081ac12455 -->

# Materials Inspiration Research

Use `materials_generic_research_run` for open-ended material inspiration. Pass
the complete user request in `goal`; preserve every threshold, negation, ordering
condition, orbital attribution, chemical constraint, and structural condition.
Use `reasoning_effort=high` normally and `max` for explicitly exhaustive work.

The service—not Hermes memory—is the research state authority. It runs nine
bounded DeepSeek roles with in-memory thinking and strict tools, including a
C2DB database scout with deterministic structure diagnostics. DeepSeek-native
web results are unresolved leads. Only evidence re-fetched through the accepted
Crossref/OpenAlex/arXiv/OSTI adapters, persisted as exact raw bytes, and assigned
a stable evidence ID may enter a candidate matrix.

Report both layers; do not collapse one into the other. The evidence matrix shows
every hard constraint as `PASS`, `FAIL`, or `UNKNOWN`. The hypothesis matrix then
uses DeepSeek's scientific reasoning to make a `LIKELY_PASS` or `LIKELY_FAIL`
prediction for every candidate × constraint pair, with probability, physical or
chemical rationale, key assumptions, and a decisive falsifier. Lead with the
returned reasoned scientific conclusion and candidate ranking, then state which
parts remain unverified.

Never turn metadata, an abstract, a search snippet, model confidence, or a shared
high-symmetry extremum into proof of flat-band width, Fermi ordering, first-order
band isolation, orbital character, oxidation state, dimensionality, or connected
sublattice topology. But do use lattice geometry, orbital symmetry, electron
counting, oxidation chemistry, band mechanisms, chemical analogy, and database
patterns to generate explicit falsifiable hypotheses. Preserve every evidence
`UNKNOWN` and its next verification action alongside those predictions.

Do not request shell, files, arbitrary URLs, arbitrary CIF generation, DFT, or
many-body execution. Do not claim novelty, experimental validation, or verified
material properties. A `REASONED_HYPOTHESIS` is a scientific inspiration
conclusion, not property verification. The only exposed scientific action is the
generic research tool.
