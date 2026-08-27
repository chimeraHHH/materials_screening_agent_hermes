# Materials Research Agent — full report

**Title:** Materials Research Agent: A Hermes-Based Agentic System for
Evidence-Grounded Materials Hypothesis Generation and Structure Exploration

- **Author:** Yiming Hua
- **Affiliation:** Wuhan University
- **Email:** cnhym@foxmail.com

## Read the report

- [Compiled PDF](materials-research-agent-full-report.pdf)
- [Compile-ready LaTeX source](source/main.tex)
- [Bibliography](source/references.bib)
- [Final figure assets](source/figures)
- [Conceptual-figure prompts](source/prompts)

The report presents the Hermes-based research-agent architecture, evidence and
constraint layers, executable soft-chemistry transformations, candidate-linked
workflow evolution, three materials case studies, and a layered benchmark
protocol. The quantitative and band-structure panels use archived scientific
artifacts; generated imagery is limited to explicitly identified conceptual
system and workflow diagrams.

## Build

From the `source/` directory, run:

```bash
pdflatex -interaction=nonstopmode -halt-on-error main.tex
bibtex main
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

The checked-in PDF was built and visually verified from this source package on
2026-08-27.
