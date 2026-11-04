# WorldLawHarvester Test Infrastructure Specification (TEST_INFRA)

## 1. Overview and Core Philosophy

WorldLawHarvester (`wlh`) adheres to a strict **opaque-box (black-box)** testing architecture governed by the Universal Harness (`AGENTS.md`). The infrastructure verifies end-to-end functionality across legal document acquisition, parsing, cleaning, storage, and quantitative benchmarking.

### Core Testing Tenets
1. **Zero Internal Mocking**: All tests exercise genuine subsystem contracts, real file I/O, live local SQLite/PostgreSQL/MinIO instances, real local HTTP servers, and native binaries (`PyMuPDF`, `LibreOffice`, `Tesseract`). No mocking of internal business logic or parser internals is permitted.
2. **Deterministic Hermetic Isolation**: Every test run executes independently. Database tables and S3 buckets are strictly managed with whitelists (`ALLOWED_POSTGRES_DATABASES`, `ALLOWED_S3_BUCKETS`) and cleaned before/after test suites.
3. **Strict AGENTS.md Compliance**:
   - Test files are organized by domain capability (`tests/test_<domain>.py`).
   - Test classes follow `class Test<Subject>:`.
   - Test methods follow `def test_<scenario_or_input_condition>_<expected_behavior>()`.
   - Implementation library names (`pytest`, `subprocess`, `docx`, `procrastinate`) are forbidden in test names.
4. **Progressive Testability**: Verification tiers allow fast static and contract checking before executing long-running integration and benchmark pipelines.

---

## 2. Four Tiers of Test Verification

The test harness is organized into four complementary tiers, creating a comprehensive verification pyramid:

```
                  ▲
                 / \
                /   \     Tier 4: E2E Acceptance & Benchmark
               / T4  \    (Full acceptance, quantitative metric benchmarks)
              /-------\
             /   T3    \   Tier 3: Pipeline & Source Adapters
            /           \  (Replay fixtures, concurrency, DB/CAS storage)
           /-------------\
          /      T2       \ Tier 2: Extraction & Engine Core
         /                 \ (HTML fallback, DOM tree, cleaners, Office/PDF)
        /-------------------\
       /         T1          \ Tier 1: Static & Architecture Contracts
      /                       \ (harness doctor, AST guards, source config rules)
     /-------------------------\
```

### Tier 1: Static and Contract Verification (`marker: contract`)
- **Scope**: Structural validation, dead-link scanning, formatting, strict typing, and AST architectural boundaries.
- **Key Modules & Tools**:
  - `harness doctor`: Validates all documentation links (LNK001), lifecycle states, and repository structure.
  - `ruff check .` / `ruff format --check .`: Enforces PEP 8 and linting rules.
  - `pyright`: Strict static typing across all modules.
  - `tests/test_architecture_contract.py`: Uses Python AST parsing to ensure zero site-specific selectors or domain tokens leak into core engine modules (`html_to_md.py`, `dom_cleanup.py`, `text_cleaner.py`).
  - `tests/test_storage_and_config.py`: Validates all 24 `sources/dd-*.yaml` declarations against unified schema contracts.

### Tier 2: Extraction & Engine Verification (`marker: extractor`)
- **Scope**: Deterministic content extraction, document building, heuristic DOM recovery, and text normalization.
- **Key Modules**:
  - `tests/test_html_extraction.py`: Scoped vs unscoped extraction, SSR DOM recovery, LaTeX formula retention, currency dollar escaping, and noise filtering.
  - `tests/test_document_extraction.py`: Multi-format document parser contracts (PDF, HTML, XML, plaintext).
  - `tests/test_office_extraction.py`: Office document extraction handling large ZIP archives (>50 members) and truncated stream recovery.
  - `tests/test_delivery_quality.py`: Output quality invariants and content completeness.

### Tier 3: Source Adapters & Integration Verification (`markers: pipeline`, `replay`)
- **Scope**: Source replay against recorded payloads, crawler lifecycle, transactional storage, and concurrent task queues.
- **Key Modules**:
  - `tests/test_japan_sources.py` & `tests/test_taiwan_source_delivery.py`: Source replay pipelines for specialized judicial, patent, and tax portals.
  - `tests/test_archive_storage.py`: Content-Addressable Storage (CAS) on S3/MinIO, cryptographic SHA256 integrity, and PostgreSQL state persistence.
  - `tests/test_collection_concurrency.py`: Worker pool concurrency, rate limiting, and failure containment.
  - `tests/test_session_replay.py`: Deterministic HTTP session replays.

### Tier 4: End-to-End Acceptance & Benchmark Evaluation
- **Scope**: Complete end-to-end acceptance, repository compliance verification, and quantitative evaluation of extraction accuracy and cleanliness.
- **Key Modules**:
  - `tests/test_e2e_worktrees_acceptance.py`: Validates repository-wide AGENTS.md conventions, flawless `harness doctor` execution, and benchmark CLI execution.
  - `tests/test_benchmark.py`: Metric scoring rules for cleanliness (markdown syntax, encoding, boilerplate removal) and field accuracy (title, doc_number, published_at, effective_at, issuer).
  - `tests/test_benchmark_cli.py`: Subprocess invocation of `wlh.benchmark` CLI commands (`sample` and `run`).
  - `tests/test_benchmark_completion.py` & `tests/test_benchmark_processor_suite.py`: Dual-model tokenizer pre-selection, completion integrity gates, and 5-pillar processor evaluation.
  - Full Benchmark Regression Evaluation: Runs offline evaluation across 417 golden-set samples without metric degradation.

---

## 3. Test Runner Invocations

All tests are executed within the hermetic project virtual environment using `uv run`.

### Quick Smoke & Static Verification
```bash
# 1. Harness repository structure and link doctor
harness doctor

# 2. Static linting and format check
uv run ruff check .
uv run ruff format --check .

# 3. Static type check
uv run pyright
```

### Pyramid Tier Execution
```bash
# Run Tier 1: Architecture & Config Contracts
uv run pytest -m contract

# Run Tier 2: Extraction Engines
uv run pytest -m extractor

# Run Tier 3: Pipeline & Storage Integration
uv run pytest -m pipeline

# Run Tier 3 (Replay): Source Replay Suites
uv run pytest -m replay
```

### End-to-End Acceptance Testing
```bash
# Execute the comprehensive worktrees merge acceptance suite
uv run pytest tests/test_e2e_worktrees_acceptance.py -v
```

### Quantitative Benchmark Evaluation
```bash
# Fast 2-sample benchmark validation (fixture-based)
uv run python -m wlh.benchmark run \
  --input tests/fixtures/benchmark/candidates_sample.jsonl \
  --output-dir work/benchmark_sample_run

# Full regression benchmark evaluation (417 golden-set samples)
uv run python -m wlh.benchmark run \
  --input data/benchmark/ground_truth_benchmark.jsonl \
  --output-dir artifacts/benchmark_full_run
```

### Complete Test Suite Execution
```bash
# Run the entire test suite across all modules
uv run pytest
```

---

## 4. Pass / Fail Semantics and Invariants

| Category | Pass Condition | Failure Condition |
|----------|---------------|-------------------|
| **Exit Code** | Process exits with return code `0`. | Non-zero exit code (`1`, `2`, or uncaught exception). |
| **Harness Doctor** | `0` issues reported across all specifications, docs, and links. | Any missing target link (`LNK001`), orphan spec, or structure violation. |
| **Architecture Contract** | Zero prohibited site tokens in `wlh/processor` or `wlh/cleaners`. | Any hardcoded domain/selector detected by AST static analysis. |
| **Test Naming** | Every test matches `class Test<Subject>` and `test_<condition>_<behavior>` without library names. | Mismatched naming convention or inclusion of library names (`pytest`, etc.). |
| **Hermetic State** | All temporary DB connections, proxy ports, and tmpdirs terminated and released. | Connection leaks, unclosed sockets, or stray test databases. |
| **Benchmark Sample Quality** | Overall Score >= 50.0, Cleanliness Score >= 80.0, Accuracy Score >= 40.0. | Metric degradation below established quality thresholds. |
| **Benchmark Regression** | Zero negative metric regression across candidate dataset compared to baseline. | Statistically significant regression in extraction recall or precision. |

---

## 5. Test Artifacts and Temporary Directories

- **`work/`**: Used for temporary runtime outputs during local development and test runs. Automatically cleaned and ignored from version control.
- **`artifacts/`**: Destination for durable evaluation reports (`benchmark_report.json`, `benchmark_report.md`).
- **`tests/fixtures/`**: Minimal tracked deterministic fixtures required for fast offline testing (e.g. `tests/fixtures/benchmark/candidates_sample.jsonl`). Large raw binary assets remain untracked.
