<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
  <img alt="whitehat-boss — Scout → Auditor → Gate → Critic → PoV → Oracle" src="docs/assets/banner-light.svg" width="100%">
</picture>

### Small LLMs, one role each, that find C/C++ memory bugs and build PoCs for them

<a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-2ea44f?style=for-the-badge"></a> <img alt="python" src="https://img.shields.io/badge/python-3.11.4%2B-3776AB?style=for-the-badge&logo=python&logoColor=white"> <a href="https://github.com/sunblaze-ucb/cybergym"><img alt="benchmark" src="https://img.shields.io/badge/benchmark-CyberGym%20L1-8A2BE2?style=for-the-badge"></a> <img alt="status" src="https://img.shields.io/badge/status-research-f0883e?style=for-the-badge">

**English** · [한국어](README.ko.md)

[Quick start](#quick-start-no-gpu) · [Pipeline](#pipeline) · [Running a real task](#running-a-real-task) · [Docs](#docs) · [Contributing](CONTRIBUTING.md)

</div>

---

## Quick start (no GPU)

```bash
pip install -r requirements.txt
python -m src.cli doctor --profile dev_fake
python tools/cybergym_task.py arvo:1065 --out eval/datasets/arvo-1065
python -m src.cli run --profile dev_fake \
  --task-dir eval/datasets/arvo-1065 --task-work-dir path/to/empty-dir
```

1. `doctor` checks the environment.
2. `cybergym_task.py` downloads one task from Hugging Face. This repository ships no CyberGym data.
3. `run` takes the task through stages 0 to 9. `--task-work-dir` must be an empty directory.

`dev_fake` replaces every model with a Fake, so the run tests the plumbing, not detection quality.

Tests: `python -m pytest -q`

## What it does

The harness reads C/C++ source, looks for memory bugs, checks the evidence, builds a crashing input (PoC),
and submits it to the [CyberGym](https://github.com/sunblaze-ucb/cybergym) grader. Each stage runs its own
small LLM. A Joern slicer, which uses no model, prepares the code the models read.

> [!NOTE]
> The README is in English and Korean. Everything else (`docs/`, code comments, CLI output) is in Korean.

## At a glance

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/pipeline-en-dark.svg">
  <img alt="Pipeline: Scout → Auditor → Gate → Critic → PoV → Oracle" src="docs/assets/pipeline-en-light.svg" width="100%">
</picture>

| Role | Job |
| --- | --- |
| 🔭 **Scout** | Picks the spots in the source that look vulnerable |
| 🔍 **Auditor** | Reads a candidate and writes a Finding with evidence |
| ⚖️ **Critic** | A model from another family argues against the Finding. Its verdict is one of the keys that order Findings for PoV |
| 💥 **PoV** | Builds crashing inputs for Findings in that order and submits them to the grader |
| 🔪 **Slicer** | Runs before the harness and writes `slices.json`, the code fragments to analyze — [rules](docs/slicer-README.md) |

Code and `config/` are the source of truth; the docs summarize them. Project rules: [CLAUDE.md](CLAUDE.md).

## Pipeline

Input is a source directory (`--target`) or a CyberGym task bundle (`--task-dir`). Firmware images are not
supported yet ([architecture.md](docs/architecture.md) §3).

| Stage | Name | Job | Output |
| --- | --- | --- | --- |
| 0 | Preprocess | Classifies and hashes every file | Artifact list |
| 1 | Index | Extracts symbols and the call graph with tree-sitter. The offline Joern slicer writes candidate fragments | Code index, `slices.json` |
| 2 | Signals | Collects static signals: Semgrep, clang-tidy, a build probe, sanitizer logs | Evidence list |
| 3 | Retrieval | Collects context, then re-ranks it with an embedding model and a reranker. Off by default in real serving | ContextCapsule |
| 4 | Scout | Picks candidates and assigns a risk category | Candidate list |
| 5 | Auditor | Traces source-to-sink flow, memory lifetime, and bounds, and drafts the evidence | Finding draft |
| 6 | Evidence gate | Grades the evidence by fixed rules. Never calls a model | Promote, hold, or drop |
| 7 | Critic | Argues against the Finding on reachability, aliasing, and guards | Rebuttal or support |
| 8 | Tool verification | Builds, runs sanitizers and the PoC in Docker, submits to the grader | Execution logs |
| 9 | Report | Removes duplicates by rule and writes the report | Report |

### Evidence grades

A Finding moves up on evidence only, never on a model's score.

| Grade | Meaning | State |
| --- | --- | --- |
| E0 | Not enough evidence | `queued` |
| E1 | A static signal or a location matches | `hypothesized` |
| E2 | Source-to-sink path, graph evidence, nothing blocking | Goes to the Critic |
| E3 | E2, survives the Critic, and has a reproduction spec | `validated` |
| E4 | A tool ran and exited non-zero. A proxy, not a confirmed reproduction | `proven` — the grader decides |

## Design principles

<table>
<tr>
<td width="33%" valign="top">

<b>🔁 One model at a time</b><br>
When the stage changes, the server for the old model stops and the next one starts. No GPU holds every model at once.

</td>
<td width="33%" valign="top">

<b>🧾 Evidence promotes</b><br>
The gate, the Critic, and tool results move a Finding up. A model's score does not.

</td>
<td width="33%" valign="top">

<b>📏 Measured ≠ estimated</b><br>
<code>benchmark: 0</code> and <code>checksum: 0</code> mean "not measured yet", not "empty".

</td>
</tr>
<tr>
<td width="33%" valign="top">

<b>🔪 No model in slicing</b><br>
Slicing needs no model and no GPU. Joern can still pick a different data-flow path on a re-run (<a href="docs/slicer-README.md">slicer-README</a>).

</td>
<td width="33%" valign="top">

<b>🧪 Fake or real by profile</b><br>
<code>dev_fake</code> swaps every role for a Fake, so the full pipeline runs without a GPU.

</td>
<td width="33%" valign="top">

<b>🗂️ Separate call logs</b><br>
Model calls (<code>model_call</code>) and retrieval calls (<code>retrieval_call</code>) are logged separately.

</td>
</tr>
</table>

## Repository layout

| Path | Contents |
| --- | --- |
| `src/` | Orchestrator, CLI, adapters, state machine |
| `config/` | Model registry, profiles, budgets, per-device serving settings |
| `tools/slicer/` | Offline Joern slicer, standard library only |
| `prompts/` | Prompts per role |
| `eval/` | Data-leakage checks and how to fetch CyberGym tasks. No dataset included |
| `docs/` | Architecture, decisions (ADR), defects (D) and risks (R), run reports, research |

## Requirements

| What | When |
| --- | --- |
| **Python 3.11.4+** | Always |
| **Docker** | Running tools and grading |
| **Joern 4.0.x · JDK 21** | Slicing |
| **GPU · vLLM** | Serving real models |

## Running a real task

<details>
<summary><b>1) Build the tool image</b></summary>

Stage 2 and stage 8 tools (semgrep, clang-tidy, build, gdb) run inside it.

```bash
docker build -t harness-tools:v3 tools/harness-tools/
```

Rule-set pinning and the Singularity setup for a Slurm cluster: [tools/README.md](tools/README.md).

</details>

<details>
<summary><b>2) Slice the task</b></summary>

Pass the directory you unpacked the bundle into (the parent of `src-vul/`) as `--repo`. The run looks up
slice paths relative to it.

```bash
mkdir -p /work/task && tar -xzf eval/datasets/arvo-1065/repo-vul.tar.gz -C /work/task
./joern-cli/joern -J-Xss64m -J-Xmx16g --server --server-host 127.0.0.1 --server-port 16242 &
python tools/slicer/slicer.py --repo /work/task \
  --joern-endpoint http://127.0.0.1:16242 \
  --joern-version-command ./joern-cli/joern \
  --out slices.json
```

</details>

<details>
<summary><b>3) Serve the models</b></summary>

Start the role models with vLLM. Models and launch arguments: `config/models.yaml` and
`config/serving/<device>.yaml`. `--device` takes that file name: `atom`, `vastai`, or `vastai-remote`.

</details>

<details>
<summary><b>4) Set up grading</b></summary>

Start the grader (the CyberGym authors' server) yourself. Then run the canary: it checks that sanitizers
behave on this machine, and the run refuses to submit without a passing report. Grader round trip:
[2026-10-02 report](docs/experiments/harness/2026-10-02-cybergym-oracle-roundtrip-win11wsl.md).

```bash
python tools/oracle_canary.py arvo:1065 --out canary.json
export CYBERGYM_API_KEY=<grading server key>
```

</details>

<details open>
<summary><b>5) Run</b></summary>

Real-serving profiles refuse to start without `--condition`.

```bash
python -m src.cli run --profile harness_pool --device vastai-remote \
  --condition <condition label> \
  --task-dir eval/datasets/arvo-1065 --task-work-dir /work/run-empty \
  --slices slices.json \
  --oracle-submit --oracle-server http://127.0.0.1:8666 --oracle-canary canary.json
```

</details>

Subcommands: `doctor`, `preflight`, `estimate`, `run`, `bench`, `gc`. Flags: `python -m src.cli <command> -h`.

> [!CAUTION]
> **This tool builds crashing inputs.** Use it only on targets you are allowed to test. See
> [SECURITY.md](SECURITY.md).

## Docs

In Korean.

| Topic | Document |
| --- | --- |
| Structure, stage responsibilities, evidence grades | [docs/architecture.md](docs/architecture.md) |
| Role contracts and prompt rules | [docs/agent-contracts.md](docs/agent-contracts.md) |
| Metrics and comparison rules | [docs/evaluation.md](docs/evaluation.md) |
| Slicer rules (Case A, Case B) | [docs/slicer-README.md](docs/slicer-README.md) |
| Decisions (ADR) | [docs/decisions/](docs/decisions/) |
| Open defects and risks | [docs/open-questions.md](docs/open-questions.md) |
| Run reports | [docs/experiments/](docs/experiments/README.md) |
| How to contribute (English) | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Team

**Team 화햇대장**, 4th cohort of [WhiteHat School](https://www.whsedu.kr/). Members: [team page](https://github.com/whitehatboss4/whitehatboss4).

## License

[MIT](LICENSE). CyberGym tasks and answers are not in this repository. Fetch tasks from Hugging Face:
[eval/cybergym/README.md](eval/cybergym/README.md).
