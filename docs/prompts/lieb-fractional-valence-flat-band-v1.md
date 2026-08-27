# Hermes task contract: fractional-valence transition-metal Lieb lattice

## Shared scientific goal

Search federated materials databases and the scientific literature, then generate
falsifiable materials hypotheses for a periodic crystalline material satisfying
all of the following. This is an ML/database/literature-only campaign: do not run
or request DFT, and do not use a DFT fallback.

1. The transition-metal sites that dominate the target electronic manifold must
   form an extended periodic Lieb sublattice. Establish this from the periodic
   coordination graph and site equivalence, not from visual resemblance or an
   isolated molecular/cluster motif. Report the primitive Lieb motif, coordination
   pattern, dimensional connectivity and any distortion from the ideal graph.
2. The Lieb-sublattice transition metal must have a fractional average formal
   valence arising from chemically plausible mixed common oxidation states or a
   symmetry/charge-order pattern. Do not call a non-integer Bader, Mulliken or
   model charge a formal mixed valence without a separate oxidation-state argument.
   Report charge-neutrality equations, plausible integer-valence components,
   their fractions, and competing assignments.
3. Identify a three-band Lieb-derived manifold: one flat/narrow band and its two
   Lieb partner bands. Use `W <= 50 meV` as the speed-first operational flat-band
   threshold. The flat band must intersect the Fermi level or have its closest
   edge within 50 meV of it; also report sensitivity under a 100 meV near-Fermi
   window rather than silently changing the acceptance threshold.
4. Apart from this three-band Lieb-derived manifold, no other band may cross the
   Fermi level anywhere in the sampled Brillouin zone. Symmetry-enforced touching
   among the three Lieb bands is allowed, but every touching with an external band
   is a failure unless the evidence shows that the external band is actually part
   of the same three-band manifold.
5. The orbital/site character of all three bands must be dominated by the connected
   transition-metal Lieb sublattice or by its transition-metal–ligand hybridized
   states. Reject flat bands attributable to isolated atoms, molecular clusters,
   vacancy-localized states or trivial supercell folding.
6. Treat spin channels and SOC explicitly. “Three Lieb bands” means the underlying
   orbital manifold before spin duplication; report whether the condition holds
   without SOC, with SOC, and per spin channel when magnetism is relevant. Never
   merge incompatible calculation settings into one pass claim.

## Required workflow

- Let DeepSeek derive the useful material families, queries, chemical mechanisms,
  candidate ranking and minimal structure operations from the goal and accumulated
  evidence. Do not restrict it to a pre-registered element family.
- Search C2DB, MC3D, NOMAD and Materials Project through the federated database
  layer when available, and use literature discovery/resolution for candidates
  and mechanisms. Preserve source database IDs, structure/CIF hashes, calculation
  settings, DOI/arXiv identifiers and exact evidence IDs.
- Keep verified evidence and DeepSeek inference separate. For every candidate and
  every hard constraint, return `PASS`, `FAIL` or `UNKNOWN`; separately return a
  DeepSeek `LIKELY_PASS`/`LIKELY_FAIL` hypothesis with probability, assumptions,
  physical/chemical rationale and a decisive falsifier.
- After retrieved parents are eliminated, continue with DeepSeek-proposed minimal
  structure operations. Compile and validate each operator through the current
  operator registry, generate a hash-pinned child CIF when supported, run only
  registered local/ML checks, write evidence back to research memory, and continue
  until at least one candidate appears or the explicit candidate/cost/iteration
  budget is exhausted.
- Deterministic policy may validate schema, artifact hashes, operator availability,
  geometry, chemical plausibility, model applicability, weights, evidence ceiling,
  deduplication and budgets. It must not invent candidate families, substitutions,
  scientific parameters or conclusions for DeepSeek.

## Acceptance and output

A candidate is accepted only when all six hard constraints have non-contradictory
real database or real ML evidence at the available evidence level. If the available
workflow cannot verify a constraint for a new CIF, preserve `UNKNOWN`, name the
missing observable/artifact/model capability, and keep the item as a reasoned
hypothesis rather than a verified material.

Return:

1. ranked verified candidates, if any;
2. ranked reasoned hypotheses, clearly separated;
3. candidate-by-constraint evidence and inference matrices;
4. oxidation-state ledgers and periodic Lieb-graph diagnostics;
5. three-band-manifold, Fermi-crossing, orbital, spin and SOC evidence;
6. generated-structure lineage and operator/compiler receipts;
7. eliminated candidates with exact failure reasons;
8. the smallest next local/ML verification task for every remaining `UNKNOWN`;
9. complete run IDs, artifact URIs/hashes, budgets and stop reason.

## Parallel route overlays

Each independent Hermes run must receive the complete shared goal above plus one
and only one overlay:

- `FEDERATED_DATABASE`: maximize database candidate breadth and downloadable CIF/
  band/projection evidence; prioritize exact periodic graph and band-manifold
  diagnostics.
- `LITERATURE_MECHANISM`: maximize resolved literature evidence and mechanism-led
  hypotheses for mixed-valence transition-metal Lieb systems; map every lead back
  to a retrievable structure or clearly state that no structure is available.
- `STRUCTURE_GENERATION`: start from the strongest retrieved parents and ask
  DeepSeek for minimal, chemically plausible operator routes that can create or
  tune a Lieb-derived three-band manifold; execute only supported hash-pinned
  structure operations and local/ML checks.

The overlays change search emphasis only. They do not relax the shared acceptance
criteria or authorize the supervising mentor to supply scientific conclusions.
