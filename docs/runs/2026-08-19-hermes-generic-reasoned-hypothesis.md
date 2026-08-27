# Hermes generic reasoned-hypothesis release run

Date: 2026-08-19
Prompt: original Chinese transition-metal 2D flat-band request
Provider/model: DeepSeek `deepseek-v4-pro`, thinking enabled
Result run: `generic-7a2ed01d47b5c71fba2db374`

## Why this supersedes the first generic release

The first implementation used one conservative evidence matrix. Because public
metadata and C2DB structures do not directly establish target bands, PDOS,
Fermi ordering, or oxidation states, all 50 assessments correctly became
`UNKNOWN`. That was an evidence audit, but it was not a useful inspiration
product.

The v2 graph separates two questions:

1. **Evidence audit:** what is directly established? Values may be
   `PASS`, `FAIL`, or `UNKNOWN`.
2. **Scientific inference:** what is most likely, given lattice geometry,
   orbital symmetry, electron counting, oxidation chemistry, band mechanisms,
   analogies, and database patterns? Every pair must be `LIKELY_PASS` or
   `LIKELY_FAIL`, with `probability_pass` and a concise public rationale.

Candidate-level output additionally contains the scientific hypothesis,
mechanistic argument, inference bases, assumptions, decisive falsifiers, and
highest-information-gain calculation. Synthesis returns a non-empty
`REASONED_HYPOTHESIS`; it separately retains
`property_verification_complete=false`.

## Real-run iteration

The first inference schema repeated long rationale, assumptions, references,
and falsifiers in every candidate × constraint cell. A real run completed the
first seven roles but remained inside the hypothesis role; it was intentionally
interrupted after 25 minutes 19 seconds. The completed role checkpoints were
preserved.

The corrected schema moved shared mechanism, assumptions, and falsifiers to the
candidate level and reduced each constraint cell to verdict, probability, and a
short rationale. The hypothesis role also reads a filtered inference context
instead of the complete retrieval state. Resuming from the seven validated
checkpoints completed the live pytest Gate:

```text
1 passed in 162.61s
```

The final MCP stdio replay returned `isError=false` and the complete v2 result.

## Result audit

| Field | Result |
|---|---:|
| DeepSeek roles | 9 |
| Compiled constraints | 10 |
| Candidates | 2 |
| Evidence assessments | 20 |
| Evidence `UNKNOWN` | 20 |
| Inference predictions | 20 |
| `LIKELY_PASS` | 7 |
| `LIKELY_FAIL` | 13 |
| Probability range | 0.06–0.98 |
| Distinct probabilities | 13 |
| Conclusion status | `REASONED_HYPOTHESIS` |
| Property verification | `false` |

The reasoned ranking is:

1. `NbCl2O` (`candidate-nbcl2o-2d-flatband`), promise score 0.22.
2. `TaCl2O` (`candidate-tacl2o-2d-flatband`), promise score 0.15.

DeepSeek's hypothesis is that destructive interference on a connected Nb 4d or
Ta 5d sublattice, with O/Cl-mediated hopping, could produce a metal/ligand
hybridized narrow band. It ranks NbCl2O first because Nb(IV) is more accessible
than Ta(IV) and Nb 4d orbitals are less extended than Ta 5d orbitals.

The reasoning is not blindly positive. Both C2DB records have transition-metal
connectivity proxy 0.0, so the model assigns only 0.08 and 0.06 probability to
the interconnected-sublattice constraint. It also predicts that W <= 50 meV,
Fermi ordering, band isolation, and absence of dispersive Fermi crossings are
more likely to fail than pass. The decisive next step is candidate-specific
band structure plus PDOS, Bader oxidation analysis, and refined bonding topology.

## Verification

```text
targeted generic/Hermes tests: 35 passed
non-flatband repository Gate: 1145 passed, 20 skipped, 377 warnings
Hermes MCP: isError=false, 20 evidence assessments, 20 inference predictions
```

No API key or provider `reasoning_content` is part of the result contract. The
persisted rationale fields are concise scientific explanations explicitly
requested for review, not hidden chain-of-thought.
