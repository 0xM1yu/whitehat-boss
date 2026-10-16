# whitehat-boss

> 소형 LLM 여러 개를 역할별로 조합해 C/C++ 취약점을 찾아내는 Multi-Agent 하네스

![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

C/C++ 소스에서 메모리 취약점을 찾고, 근거를 검증하고, 실제로 크래시를 내는 PoC까지 만들어
채점기에 제출하는 자동화 하네스다. 큰 단일 모델 하나에 맡기지 않고, 역할이 다른 소형 LLM을
파이프라인 단계마다 바꿔 세운다. 분석에 넣을 코드 조각은 모델을 쓰지 않는 Joern 슬라이서가
미리 만든다. 평가는 CyberGym 벤치마크로 한다. CyberGym은 실제 오픈소스 프로젝트의 알려진
취약점을 재현하는 과제 모음이다.

화이트햇스쿨 4기 2차 프로젝트.

## 한눈에 보기

```
소스 코드  ─┐
           ├─▶ Scout ─▶ Auditor ─▶ Critic ─▶ PoV ─▶ 채점기 oracle
슬라이스   ─┘   후보      근거       반박      PoC      정답 판정
```

- **Scout** 가 소스에서 취약해 보이는 지점을 후보로 추린다.
- **Auditor** 가 후보 코드를 읽고 취약점인지 근거를 모아 Finding을 낸다.
- **Critic** 이 다른 계열 모델로 그 Finding을 반박한다. 반박을 버텨야 등급이 오른다.
- **PoV** 가 살아남은 Finding에서 크래시 입력 PoC를 만들어 채점기에 제출한다.
- **슬라이서** 는 하네스 밖에서 미리 돌아 분석할 코드 조각 `slices.json`을 만든다. 규칙은
  [docs/slicer-README.md](docs/slicer-README.md)에 있다.

실측과 설계의 단일 출처는 코드와 `config/`다. 문서는 그 요약이다. 규약은 [CLAUDE.md](CLAUDE.md)를 본다.

## 파이프라인

입력은 소스 repo, 펌웨어 이미지, 또는 크래시 번들이다. 0단계부터 9단계까지 순서대로 돈다.
각 단계는 순수 함수와 스키마로 되어 있고, 상태기계가 실행 순서와 체크포인트를 관리한다.

| 단계 | 이름 | 하는 일 | 산출물 |
| --- | --- | --- | --- |
| 0 | 전처리 | 파일 타입을 분류하고 파일마다 해시를 부여한다 | Artifact 목록 |
| 1 | 인덱싱 | tree-sitter로 심볼과 호출 그래프를 뽑는다. 오프라인 Joern 슬라이서가 후보 코드 조각을 만든다 | 코드 인덱스, `slices.json` |
| 2 | 신호 수집 | Semgrep, clang-tidy, 빌드 프로브, 새니타이저 로그로 정적 신호를 모은다 | Evidence 목록 |
| 3 | 검색 | 넓게 수집한 뒤 임베딩과 리랭커로 재선별한다. 실서빙 기본값은 꺼짐이다 | ContextCapsule |
| 4 | Scout | 취약 후보를 추리고 위험 카테고리를 매긴다 | Candidate 목록 |
| 5 | Auditor | source에서 sink까지의 흐름, 메모리 수명, 경계 조건을 분석하고 근거 초안을 쓴다 | Finding 초안 |
| 6 | 근거 게이트 | 결정론 규칙으로 근거 등급을 매긴다. 애매할 때만 모델을 부른다 | 승격, 보류, 폐기 |
| 7 | Critic | 다른 계열 모델이 reachability, aliasing, guard를 들어 반박한다 | 반박 또는 지지 |
| 8 | 도구 검증 | Docker 안에서 빌드, 새니타이저, PoC를 실행하고 채점기에 제출한다 | 실행 로그 |
| 9 | 리포트 | 규칙 기반으로 중복을 제거하고 리포트를 만든다 | 리포트 |

## 근거 등급

Finding은 모델이 매긴 점수가 아니라 쌓인 근거로만 올라간다. 등급 E0부터 E4까지 있고 그에 따라
상태가 바뀐다.

| 등급 | 뜻 | 상태 |
| --- | --- | --- |
| E0 | 근거가 부족하다 | queued |
| E1 | 정적 신호나 위치 근거가 있다 | hypothesized |
| E2 | source와 sink 경로가 있고 그래프 근거가 있으며 막는 미해결 근거가 없다 | Critic 대상 |
| E3 | E2를 만족하고 Critic 반박을 버티며 재현 명세가 있다 | validated |
| E4 | 도구가 비정상 종료로 재현했다 | proven. 최종 판정은 채점기가 한다 |

## 핵심 설계

- **단계 단위 교대 서빙.** 역할별 소형 모델을 GPU 하나에 다 올리지 않고, 단계가 바뀔 때
  서버를 끄고 다음 모델을 켠다.
- **근거로만 승격한다.** 모델 점수가 아니라 게이트와 Critic과 도구 결과가 Finding을 올린다.
- **실측과 추정을 섞지 않는다.** `benchmark: 0`과 `checksum: 0`은 빈 값이 아니라 아직 안 쟀다는 표시다.
- **모델 없는 슬라이서가 입력을 미리 만든다.** 그래서 같은 코드면 같은 결과가 나온다.
- **프로필로 Fake와 실서빙을 바꾼다.** `dev_fake`는 모든 역할이 Fake라 GPU 없이 끝까지 돈다.
- **호출을 분리해 기록한다.** 모델 호출 `model_call`과 검색 호출 `retrieval_call`을 trace에 따로 남긴다.

## 저장소 구조

| 경로 | 내용 |
| --- | --- |
| `src/` | 오케스트레이터, CLI, 어댑터, 상태기계 |
| `config/` | 모델 레지스트리, 프로필, 예산, 장비별 서빙 설정 |
| `tools/slicer/` | 오프라인 Joern 슬라이서. 표준 라이브러리만 쓴다 |
| `prompts/` | 역할별 프롬프트 |
| `eval/` | CyberGym 연동과 데이터 누출 검사 유틸. 데이터셋 자체는 들어 있지 않다 |
| `docs/` | 아키텍처, 결정 ADR, 결함 D와 R, 실험 기록, 조사 |

## 요구 사항

- **Python 3.11.4 이상.** `pip install -r requirements.txt`
- 슬라이싱을 할 때만 **Joern 4.0.x 와 JDK 21**
- 도구 실행과 채점에 **Docker**
- 역할 모델을 실제로 서빙할 때만 **GPU 와 vLLM**

## 빠른 시작: GPU 없이

`dev_fake` 프로필은 모든 역할을 Fake로 두어 GPU 없이 0단계부터 9단계까지 배관을 끝까지
돌려 본다.

```bash
pip install -r requirements.txt

# 환경 점검
python -m src.cli doctor --profile dev_fake

# 과제 번들로 한 번 완주시킨다. 번들은 repo-vul.tar.gz 와 description.txt 를 담는다
python -m src.cli run --profile dev_fake \
  --task-dir path/to/task --unpack-dir path/to/empty-dir
```

테스트:

```bash
python -m pytest -q
```

## 실제 과제 돌리기

슬라이싱, 모델 서빙, 실행 세 단계다.

```bash
# 1) 슬라이싱. Joern 서버를 띄우고 오프라인 슬라이서를 돌린다
./joern-cli/joern -J-Xmx16g --server --server-host 127.0.0.1 --server-port 16242 &
python tools/slicer/slicer.py --repo path/to/repo-vul \
  --joern-endpoint http://127.0.0.1:16242 \
  --joern-version-command ./joern-cli/joern \
  --out slices.json

# 2) 역할 모델을 vLLM으로 서빙한다. 모델과 기동 인자는 config/models.yaml 와
#    config/serving/<device>.yaml 에 있다

# 3) 실서빙 프로필로 채점기까지 돌린다
python -m src.cli run --profile harness_pool --device <device-label> \
  --task-dir path/to/task --unpack-dir path/to/empty-dir \
  --slices slices.json \
  --oracle-submit --oracle-server http://127.0.0.1:8666 --canary canary.json
```

서브커맨드는 `doctor`, `preflight`, `estimate`, `run`, `bench`, `gc` 다. 각 명령의 플래그는
`python -m src.cli <명령> -h` 로 본다.

## 문서

| 알고 싶은 것 | 문서 |
| --- | --- |
| 전체 구조, 단계별 책임, 근거 등급 | [docs/architecture.md](docs/architecture.md) |
| 역할별 실행 계약과 프롬프트 규약 | [docs/agent-contracts.md](docs/agent-contracts.md) |
| 지표 정의와 조건 간 비교 규칙 | [docs/evaluation.md](docs/evaluation.md) |
| 슬라이서 분석 규칙 Case A와 Case B | [docs/slicer-README.md](docs/slicer-README.md) |
| 되돌리기 어려운 결정 ADR | [docs/decisions/](docs/decisions/) |
| 미해결 결함과 리스크 | [docs/open-questions.md](docs/open-questions.md) |
| 하네스를 돌린 run 기록 | [docs/experiments/](docs/experiments/README.md) |

## 라이선스

[MIT](LICENSE). 평가 데이터셋인 CyberGym 과제와 정답은 라이선스와 대회 규정에 따라 이 저장소에
넣지 않는다.
