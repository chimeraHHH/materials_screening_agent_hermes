# Hermes federated database layer release

Date: 2026-08-19
Generic result schema: `materials-generic-research-run-v3`
Research graph schema: `materials-inspiration-research-graph-v3`
Implementation revision: `generic-research-20260819-r4`

## Scope

The generic database scout no longer exposes a C2DB-only tool. Its one callable
tool is `search_federated_materials_candidates`, and every call is fanned out by
deterministic service code to all enabled sources:

- C2DB (public, 2D-specialized);
- Materials Cloud MC3D PBE-v1 (public OPTIMADE structures, useful for
  layered bulk parents);
- NOMAD public Archive;
- Materials Project when a credential can be resolved without persisting it.

The model cannot select a single source. Each source has an independent bounded
query plan and failure boundary. A source may report `SUCCEEDED`, `EMPTY`,
`FAILED`, or `UNAVAILABLE_CREDENTIAL`; the other sources continue.

Every accepted source record retains its source material ID, database version,
query fingerprint, band-gap scalar when available, license marker, raw-response
artifact, canonical CIF, and both hashes. Candidate identity is normalized from
the structure. Exact canonical IDs are merged first; a strict, non-scaling
StructureMatcher then merges equivalent representations. All source records are
kept on the federated candidate, so duplicate database rows are not counted as
independent scientific evidence.

Local hard filters re-check required/excluded elements, exact composition, site
count, transition-metal presence, and resolved two-dimensionality. Dimensionality
and periodic transition-metal connectivity remain conservative local diagnostics.
The layer does not turn database scalars into verified flat-band, Fermi-ordering,
orbital, or oxidation conclusions.

## Verification

The fixture test returned the same TiS2 structure from C2DB, MC3D, and NOMAD and
forced Materials Project credential failure. The result contained one federated
candidate with three source records and the source statuses:

```text
C2DB              SUCCEEDED
MC3D              SUCCEEDED
NOMAD              SUCCEEDED
Materials Project  UNAVAILABLE_CREDENTIAL
source records     3
federated records  1
merge count        2
```

Checkpoint serialize/restore reproduced both candidate and federation audit
exactly.

A live public smoke query for Ti fanned out concurrently to C2DB, MC3D, and
NOMAD:

```text
C2DB   web:undated-live-web             raw=2 accepted=2
MC3D   pbe-v1;optimade:1.2.0            raw=2 accepted=0
NOMAD  api:v1, NOMAD 1.4.3.post1        raw=2 accepted=0
```

MC3D's and NOMAD's two records each were explicitly retained in their query
receipts as rejected by local structure filters; they were not silently treated
as no search. The final two candidates in that smoke happened to come from
C2DB. This validates federated fan-out and source-level accounting, not a claim
that every chemical query must produce an accepted candidate from every source.

Focused verification after the change:

```text
generic research unit tests: 22 passed
federated tool/graph tests:    8 passed
non-flatband repository Gate:  1132 passed, 20 skipped
Hermes/contracts subset:       14 passed, 1 skipped
ruff on changed Python files:  all checks passed
```

The literal whole-repository command additionally passed 327 tests before it
was stopped inside an unrelated pre-existing flat-band recursive JSON/Pydantic
stress test after 11 minutes 51 seconds. The release Gate above excludes
flat-band-named test files, matching the generic/Hermes change boundary.

## Original-prompt four-source DeepSeek run

After installing a user-supplied Materials Project credential in the macOS
Keychain service `material-screening-agent-mp-api`, the adapter resolved it
without an environment file and successfully read Materials Project database
version `2026.04.13` with 69 summary fields. No credential bytes entered the
repository, Artifact store, test output, or result JSON.

The original Chinese flat-band prompt was then rerun through the complete
nine-role r4 graph. Four database queries each fanned out to all four enabled
sources. Materials Project returned `SUCCEEDED` in all four receipts and
contributed an accepted NbI2O source record. That structure was merged with the
equivalent MC3D record while retaining both provenances. One C2DB query failed
with a proxy error; MC3D, NOMAD, and Materials Project continued successfully.

The larger federated pool expanded the hypothesis matrix to seven candidates by
eight constraints. DeepSeek repeatedly truncated or repaired the 56-assessment
JSON under the former 8,192-token per-round ceiling and exhausted ten rounds.
The default `max_completion_tokens_per_round` was therefore raised to 32,768,
with a unit assertion on the outbound provider payload. Resuming the same seven
validated role checkpoints then completed hypothesis reasoning and synthesis:

```text
live original-prompt Gate: 1 passed in 200.98 s
database candidates:        7
source records:             8
cross-source merges:        1
evidence assessments:       56
inference assessments:      56
LIKELY_PASS / LIKELY_FAIL:  35 / 21
probability range:          0.08-0.99
property verification:      false
```

The reasoned ranking was CrPS4 (MC3D), two P2Pd3S8 structural records, two
NbCl2O records, and two NbI2O records. DeepSeek proposed connected Cr-S or Pd-S
networks with transition-metal d / sulfur p hybridization as the most plausible
mechanism, while explicitly predicting that in-plane covalency will probably
broaden the target band beyond 50 meV. The decisive next test remains
spin-polarized band structure, atom-resolved PDOS, oxidation analysis, and
bonding-topology verification; the result is a `REASONED_HYPOTHESIS`, not a
verified flat-band claim.

## Markdown figure report

The same seven-candidate result was rendered into
`materials-generic-research-report-v2`. The report contains seven CIF
crystallographic three-view panels and one eight-source-record scalar overview.
Formation energy and hull values were taken only from C2DB table fields or the
Materials Project summary record; MC3D missing values were left unfilled.

C2DB's official material pages exposed numerical GPAW/PBE Plotly band traces
for the three C2DB records. The reporter extracted and archived those exact
arrays, then rendered band figures for NbI2O, P2Pd3S8, and NbCl2O. The sole
Materials Project mapping (`mp-aaabfhfc`) returned `OSError` for the line-band
object, so its report entry remains `NOT_AVAILABLE` rather than displaying a
fabricated plot. Visual inspection covered the scalar overview, the leading
CrPS4 three-view, and the P2Pd3S8 band image.

```text
CIF three-view figures:       7
source-resolved scalar rows:  8
real band figures:            3
fabricated/imputed figures:   0
focused report tests:         33 passed
```
