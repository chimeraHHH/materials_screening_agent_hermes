# Local semantic embedding runtime

The Inspiration semantic path uses a real local Sentence Transformers model;
it never treats signed hashing or DeepSeek chat output as an embedding. The
runtime is optional and isolated from the default material-agent environment.

Prepare the pinned engineering bundle:

```bash
uv venv .venv-semantic --python 3.11
uv pip install --python .venv-semantic/bin/python \
  'sentence-transformers==5.6.1' 'socksio==1.0.0'

.venv-semantic/bin/python \
  integrations/semantic/bootstrap_local_bundle.py \
  --output /absolute/path/to/models/local/all-MiniLM-L6-v2-1110a243
```

The bootstrap command is the only network-enabled step. It fetches exactly
`sentence-transformers/all-MiniLM-L6-v2` at commit
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, saves a local-only model, preserves
the pinned model card carrying its `apache-2.0` declaration, and writes a
manifest containing every file SHA-256 and the dependency versions used to
build the bundle. The model card is license metadata, not an independent legal
or training-data audit.

Runtime use is offline and explicit:

```bash
export MATERIALS_SEMANTIC_MODEL_BUNDLE=/absolute/path/to/the/bundle
export MATERIALS_SEMANTIC_MODEL_DEVICE=cpu
```

`build_sentence_transformers_adapter_from_environment()` verifies the complete
file set, dependency version, tokenizer/config/license hashes, model dimension,
and L2-normalized float output before accepting a vector. The generic MiniLM
bundle is an engineering integration baseline, not a materials-domain accuracy
claim; scientific release still needs an expert-adjudicated retrieval/ranking
benchmark and an approved model/license review.
