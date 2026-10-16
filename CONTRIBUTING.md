# Contributing to whitehat-boss

**English** · [한국어](CONTRIBUTING.ko.md)

Thanks for your interest. This repository builds the **harness** — its code, config, prompts, the tools that
produce its inputs (such as the slicer), and its decisions (ADRs) and defects (D/R). Choosing models,
designing experimental conditions, and building benchmarks are out of scope here
([ADR 0012](docs/decisions/0012-harness-canon-v2.md), decision 5).

- **Language.** Issues, PRs, and review comments may be in Korean or English. Everything under `docs/` is
  written in Korean; keep new documents there in Korean too.
- **License.** Contributions are accepted under the [MIT License](LICENSE). No CLA or DCO sign-off is required.
- **Security.** Do not open public issues for vulnerabilities in the harness — see [SECURITY.md](SECURITY.md).

## Before you start

1. **Search open issues and PRs.** Someone may already be fixing the same defect.
2. **Open an issue** with the matching template:
   - **Bug** — a defect with no design decision attached.
   - **D-series** — a defect or decision that needs a design call. Its body lives in
     `docs/open-questions/D/D<n>.md`, and the issue mirrors it
     ([ADR 0008](docs/decisions/0008-defect-source-of-truth.md)).
3. Include the **observed fact** (file and line), **how you found it** (run ID or code location), and
   **more than one option** for fixing it.

## Development setup

```bash
pip install -r requirements.txt          # Python 3.11.4+
python -m src.cli doctor --profile dev_fake
```

`dev_fake` replaces every model with a Fake, so the whole pipeline runs without a GPU. See the
[README](README.md#quick-start-no-gpu) for an end-to-end run.

## Pull requests

- **One topic per PR**, based on `main`. Commit messages follow
  [Conventional Commits](https://www.conventionalcommits.org/) (`fix:`, `docs:`, `feat:` …).
- **Run both checks and read the results before you push:**

  ```bash
  python -m pytest -q
  python -m ruff check .
  ```

  Do not chain them into a push (`pytest | tail -1 && git push`): the pipe hides a failing exit code.
- **Link issues correctly.** Use `Closes #N` only when the PR fully resolves the issue; use `Refs #N` when it
  covers part of it. Write them as plain text, not inside backticks.
- **Defect status changes in `D<n>.md`,** not on the issue. The issue is closed by the PR that marks the D
  file closed.

## Writing rules

These come from [CLAUDE.md](CLAUDE.md). CI checks some of them; reviewers check the rest.

- **Never mix measured and estimated values.** A value you did not measure stays `0` (or `미기재`) with a
  reason. When you cite a number, name its source in the same sentence.
- **When a document describes code, open the code and check it.** Do not write from memory or from another
  summary.
- **A summary must not claim more than its source.**
- **When you delete or change something, find everything that points at it** (`git grep`) and update it.

## Contributing GPU / real-serving runs

If a PR's claim rests on a run with real models (anything other than `dev_*` Fake profiles), the PR must
include a run report:

1. Copy [`docs/experiments/TEMPLATE.md`](docs/experiments/TEMPLATE.md) to
   `docs/experiments/harness/YYYY-MM-DD-<topic>-<device>.md` and fill it in.
   - Required fields include the `run_id`, `repo_commit`, profile, `--device`, the served model and its exact
     revision, and the tool image digest.
   - Write `미기재` for anything you could not record. Do not fill gaps with plausible values.
2. Add a row for the report to the index in [`docs/experiments/README.md`](docs/experiments/README.md).
3. A run whose serving model has `checksum: 0` in `config/models.yaml` is reported but **left out of
   comparison tables**. Say so in the report.
4. Do not commit run artifacts, datasets, or PoC bytes. `artifacts/` and `eval/datasets/` are git-ignored.
5. Do not put hostnames, IP addresses, account names, or personal paths in reports. Use placeholders such as
   `<host>`, `<account>`, and `/home/user`.

Small observations that do not back a decision can go in an issue instead of a report.
