<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
  <img alt="whitehat-boss — Scout → Auditor → Gate → Critic → PoV → Oracle" src="docs/assets/banner-light.svg" width="100%">
</picture>

### 역할 하나씩 맡은 소형 LLM들이 C/C++ 메모리 버그를 찾고 PoC를 만든다

<a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-2ea44f?style=for-the-badge"></a> <img alt="python" src="https://img.shields.io/badge/python-3.11.4%2B-3776AB?style=for-the-badge&logo=python&logoColor=white"> <a href="https://github.com/sunblaze-ucb/cybergym"><img alt="benchmark" src="https://img.shields.io/badge/benchmark-CyberGym%20L1-8A2BE2?style=for-the-badge"></a> <img alt="status" src="https://img.shields.io/badge/status-research-f0883e?style=for-the-badge">

[English](README.md) · **한국어**

[빠른 시작](#빠른-시작-gpu-없이) · [파이프라인](#파이프라인) · [실제 과제 돌리기](#실제-과제-돌리기) · [문서](#문서) · [기여](CONTRIBUTING.ko.md)

</div>

---

## 빠른 시작: GPU 없이

```bash
pip install -r requirements.txt
python -m src.cli doctor --profile dev_fake
python tools/cybergym_task.py arvo:1065 --out eval/datasets/arvo-1065
python -m src.cli run --profile dev_fake \
  --task-dir eval/datasets/arvo-1065 --task-work-dir path/to/empty-dir
```

1. `doctor`가 환경을 점검한다.
2. `cybergym_task.py`가 Hugging Face에서 과제 하나를 받는다. 이 저장소에는 CyberGym 데이터가 없다.
3. `run`이 그 과제를 0~9단계로 돌린다. `--task-work-dir`는 빈 디렉터리여야 한다.

`dev_fake`는 모든 모델을 Fake로 바꾼다. 그래서 이 run은 배관만 확인하고, 탐지 성능은 재지 않는다.

테스트: `python -m pytest -q`

## 무엇을 하나

C/C++ 소스를 읽고 메모리 버그를 찾는다. 근거를 확인한 뒤 크래시 입력(PoC)을 만들어
[CyberGym](https://github.com/sunblaze-ucb/cybergym) 채점기에 낸다. 단계마다 다른 소형 LLM이 돌고, 모델이 읽을
코드는 모델을 쓰지 않는 Joern 슬라이서가 미리 만든다.

> [!NOTE]
> README는 영어·한국어 둘이다. 그 밖의 문서(`docs/`), 코드 주석, CLI 출력은 한국어다.

## 한눈에 보기

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/pipeline-ko-dark.svg">
  <img alt="파이프라인: Scout → Auditor → 게이트 → Critic → PoV → Oracle" src="docs/assets/pipeline-ko-light.svg" width="100%">
</picture>

| 역할 | 하는 일 |
| --- | --- |
| 🔭 **Scout** | 소스에서 취약해 보이는 지점을 고른다 |
| 🔍 **Auditor** | 후보 코드를 읽고 근거를 붙여 Finding을 쓴다 |
| ⚖️ **Critic** | 다른 계열 모델이 Finding을 반박한다. 그 판정은 PoV로 넘어갈 순서를 정하는 기준 중 하나다 |
| 💥 **PoV** | 그 순서대로 Finding의 크래시 입력을 만들어 채점기에 낸다 |
| 🔪 **슬라이서** | 하네스보다 먼저 돌아 분석할 코드 조각 `slices.json`을 쓴다 — [규칙](docs/slicer-README.md) |

정본은 코드와 `config/`이고, 문서는 그 요약이다. 프로젝트 규칙: [CLAUDE.md](CLAUDE.md).

## 파이프라인

입력은 소스 디렉터리(`--target`)나 CyberGym 과제 번들(`--task-dir`)이다. 펌웨어 이미지는 아직 지원하지
않는다([architecture.md](docs/architecture.md) §3).

| 단계 | 이름 | 하는 일 | 산출물 |
| --- | --- | --- | --- |
| 0 | 전처리 | 파일마다 종류를 가르고 해시를 붙인다 | Artifact 목록 |
| 1 | 인덱싱 | tree-sitter로 심볼과 호출 그래프를 뽑는다. 오프라인 Joern 슬라이서가 후보 조각을 쓴다 | 코드 인덱스, `slices.json` |
| 2 | 신호 수집 | Semgrep, clang-tidy, 빌드 프로브, 새니타이저 로그에서 정적 신호를 모은다 | Evidence 목록 |
| 3 | 검색 | 맥락을 모은 뒤 임베딩과 리랭커로 다시 고른다. 실서빙에서는 기본으로 꺼져 있다 | ContextCapsule |
| 4 | Scout | 후보를 고르고 위험 카테고리를 붙인다 | Candidate 목록 |
| 5 | Auditor | source→sink 흐름, 메모리 수명, 경계 조건을 보고 근거 초안을 쓴다 | Finding 초안 |
| 6 | 근거 게이트 | 정해진 규칙으로 근거 등급을 매긴다. 모델은 부르지 않는다 | 승격, 보류, 폐기 |
| 7 | Critic | reachability, aliasing, guard를 들어 Finding을 반박한다 | 반박 또는 지지 |
| 8 | 도구 검증 | Docker 안에서 빌드하고 새니타이저와 PoC를 돌려 채점기에 낸다 | 실행 로그 |
| 9 | 리포트 | 규칙으로 중복을 지우고 리포트를 쓴다 | 리포트 |

### 근거 등급

Finding은 근거로만 올라간다. 모델이 매긴 점수로는 오르지 않는다.

| 등급 | 뜻 | 상태 |
| --- | --- | --- |
| E0 | 근거가 부족하다 | `queued` |
| E1 | 정적 신호나 위치가 맞는다 | `hypothesized` |
| E2 | source→sink 경로와 그래프 근거가 있고, 막는 것이 없다 | Critic으로 간다 |
| E3 | E2를 만족하고, Critic을 버텼고, 재현 명세가 있다 | `validated` |
| E4 | 도구가 돌았고 종료 코드가 0이 아니다. 대용 지표이지 재현 확정이 아니다 | `proven` — 판정은 채점기가 한다 |

## 핵심 설계

<table>
<tr>
<td width="33%" valign="top">

<b>🔁 모델은 한 번에 하나</b><br>
단계가 바뀌면 앞 모델의 서버를 끄고 다음 모델을 켠다. 모든 모델을 GPU 하나에 동시에 올리지 않는다.

</td>
<td width="33%" valign="top">

<b>🧾 근거가 올린다</b><br>
Finding을 올리는 것은 게이트, Critic, 도구 결과다. 모델 점수가 아니다.

</td>
<td width="33%" valign="top">

<b>📏 실측 ≠ 추정</b><br>
<code>benchmark: 0</code>과 <code>checksum: 0</code>은 빈 값이 아니라 「아직 안 쟀다」는 표시다.

</td>
</tr>
<tr>
<td width="33%" valign="top">

<b>🔪 슬라이싱에는 모델이 없다</b><br>
슬라이싱에는 모델도 GPU도 쓰지 않는다. 다만 Joern은 다시 돌리면 다른 데이터플로 경로를 고를 수 있다(<a href="docs/slicer-README.md">slicer-README</a>).

</td>
<td width="33%" valign="top">

<b>🧪 프로필로 Fake·실서빙 전환</b><br>
<code>dev_fake</code>는 모든 역할을 Fake로 바꿔 GPU 없이 파이프라인 전체를 돌린다.

</td>
<td width="33%" valign="top">

<b>🗂️ 호출을 따로 기록</b><br>
모델 호출 <code>model_call</code>과 검색 호출 <code>retrieval_call</code>을 trace에 따로 남긴다.

</td>
</tr>
</table>

## 저장소 구조

| 경로 | 내용 |
| --- | --- |
| `src/` | 오케스트레이터, CLI, 어댑터, 상태기계 |
| `config/` | 모델 레지스트리, 프로필, 예산, 장비별 서빙 설정 |
| `tools/slicer/` | 오프라인 Joern 슬라이서. 표준 라이브러리만 쓴다 |
| `prompts/` | 역할별 프롬프트 |
| `eval/` | 데이터 누출 검사와 CyberGym 과제 받는 법. 데이터셋은 없다 |
| `docs/` | 아키텍처, 결정(ADR), 결함(D)과 리스크(R), run 기록, 조사 |

## 요구 사항

| 무엇 | 언제 |
| --- | --- |
| **Python 3.11.4 이상** | 항상 |
| **Docker** | 도구 실행과 채점 |
| **Joern 4.0.x · JDK 21** | 슬라이싱 |
| **GPU · vLLM** | 실모델 서빙 |

## 실제 과제 돌리기

<details>
<summary><b>1) 도구 이미지 빌드</b></summary>

2·8단계 도구(semgrep, clang-tidy, 빌드, gdb)가 이 이미지 안에서 돈다.

```bash
docker build -t harness-tools:v3 tools/harness-tools/
```

룰셋 고정과 Slurm 클러스터용 Singularity 설정: [tools/README.md](tools/README.md).

</details>

<details>
<summary><b>2) 슬라이싱</b></summary>

`--repo`에는 번들을 푼 디렉터리(`src-vul/`의 부모)를 준다. run이 슬라이스 경로를 그 디렉터리 기준으로 찾는다.

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
<summary><b>3) 모델 서빙</b></summary>

역할 모델을 vLLM으로 띄운다. 모델과 기동 인자: `config/models.yaml`, `config/serving/<device>.yaml`.
`--device`에는 그 파일 이름(`atom` · `vastai` · `vastai-remote`)을 준다.

</details>

<details>
<summary><b>4) 채점 준비</b></summary>

채점기(CyberGym 저자 서버)는 직접 띄운다. 그다음 카나리를 돌린다. 이 장비에서 새니타이저가 제대로 도는지 재는
것이고, 통과한 보고서가 없으면 run이 제출을 거부한다. 채점기 왕복:
[2026-10-02 보고서](docs/experiments/harness/2026-10-02-cybergym-oracle-roundtrip-win11wsl.md).

```bash
python tools/oracle_canary.py arvo:1065 --out canary.json
export CYBERGYM_API_KEY=<채점 서버 키>
```

</details>

<details open>
<summary><b>5) 실행</b></summary>

실서빙 프로필은 `--condition` 없이는 시작하지 않는다.

```bash
python -m src.cli run --profile harness_pool --device vastai-remote \
  --condition <조건 라벨> \
  --task-dir eval/datasets/arvo-1065 --task-work-dir /work/run-empty \
  --slices slices.json \
  --oracle-submit --oracle-server http://127.0.0.1:8666 --oracle-canary canary.json
```

</details>

서브커맨드: `doctor`, `preflight`, `estimate`, `run`, `bench`, `gc`. 플래그: `python -m src.cli <명령> -h`.

> [!CAUTION]
> **이 도구는 크래시 입력을 만든다.** 테스트를 허락받은 대상에만 쓴다. [SECURITY.ko.md](SECURITY.ko.md)를 본다.

## 문서

| 주제 | 문서 |
| --- | --- |
| 전체 구조, 단계별 책임, 근거 등급 | [docs/architecture.md](docs/architecture.md) |
| 역할별 실행 계약과 프롬프트 규칙 | [docs/agent-contracts.md](docs/agent-contracts.md) |
| 지표와 비교 규칙 | [docs/evaluation.md](docs/evaluation.md) |
| 슬라이서 규칙(Case A, Case B) | [docs/slicer-README.md](docs/slicer-README.md) |
| 결정(ADR) | [docs/decisions/](docs/decisions/) |
| 미해결 결함과 리스크 | [docs/open-questions.md](docs/open-questions.md) |
| run 기록 | [docs/experiments/](docs/experiments/README.md) |
| 기여 방법 | [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md) |

## 팀

[화이트햇스쿨](https://www.whsedu.kr/) 4기 **Team 화햇대장**. 팀원: [팀 페이지](https://github.com/whitehatboss4/whitehatboss4).

## 라이선스

[MIT](LICENSE). CyberGym 과제와 정답은 이 저장소에 없다. 과제는 Hugging Face에서 받는다:
[eval/cybergym/README.md](eval/cybergym/README.md).
