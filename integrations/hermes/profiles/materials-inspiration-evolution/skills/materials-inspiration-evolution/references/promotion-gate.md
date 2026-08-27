# Promotion gate

Hermes memory and agent-created skills in the evolution profile are proposals,
not source-controlled production behavior.

Promote a proposal only through a human-reviewed source diff and after all of
the following are true:

1. An operator reviews the full pending diff and approves the Hermes write.
2. A maintainer translates the approved lesson into a narrowly scoped repository
   change; no runtime file is copied blindly into the production profile.
3. `verify_bundle.py` and `verify_evolution_bundle.py` pass.
4. Relevant offline unit and integration tests pass.
5. Any change to scientific ranking, transformations, relaxation, electronic
   structure, or evidence levels passes its module-specific evaluation gate.
6. The source-controlled change is versioned and independently reviewable.

Reject any proposal containing credentials, unbounded source text, unverifiable
scientific claims, or instructions that weaken an approval or capability gate.
