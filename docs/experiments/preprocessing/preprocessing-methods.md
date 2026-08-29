## 전처리 실험 진행 방법
### 0.목차
| 번호 | 내용 |
| --- | --- |
| 1 | 실험 전 확인할 요소 |
| 2 | 사용한 데이터셋 (Juliet) |
| 3 | 실험 방법 |
| 4 | 평가 방법 |

---

### 1. 실험 전 확인할 요소
#### 1.1. 실험 진행 모델

| 모델 | 양자화 |
| --- | --- |
| Qwen3-8B | BF16 |
| Foundation-Sec-1.1-8B-Instruct | BF16 |
| Qwen3-4B | BF16 |
| **[별도]** Qwen3-Coder-30B-A3B-Instruct | NVFP4 |
- Qwen3-8B ↔ Foundation-Sec-1.1-8B-Instruct: 다른 계열일 때의 차이 확인
- Qwen3-8B ↔ Qwen3-4B: 모델 크기에 따른 차이 확인
- 전체 실험 결과 → Qwen3-Coder: Auditor 모델에서의 정확도 확인

#### 1.2. vLLM 서빙 옵션 추가

vLLM 서빙 시 실행하려는 모델과 --served-model-name 옵션을 반드시 아래 표를 기준으로 맞춰주어야 한다.
| 모델 | `vllm serve` 인자 | `--served-model-name` |
| --- | --- | --- |
| Qwen3-8B | `Qwen/Qwen3-8B` | `qwen3-8b` |
| Foundation-Sec | `fdtn-ai/Foundation-Sec-1.1-8B-Instruct` | `foundation-sec` |
| Qwen3-4B | `Qwen/Qwen3-4B` | `qwen3-4b` |
| **[별도]** Qwen3-Coder | `NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4` | `qwen3-coder-30b-nvfp4` |

→ `run.py` 스크립트의 `--model` 값과 맞추기 위함이다.

Ex) Qwen3-8b 실행

```bash
vllm serve Qwen/Qwen3-8B \
  --served-model-name qwen3-8b \
  --host 127.0.0.1 --port 8001 \
  --gpu-memory-utilization 0.25 \
  --max-model-len 32768 \
  --seed 42 \
  --dtype bfloat16 \
  --enable-prefix-caching
```

---

### 2. Juliet 데이터셋

전처리 실험용으로 정상, 취약 데이터 48쌍을 가져와 실험을 진행하였다. (NIST SARD Juliet Test Suite for C/C++)

#### 2.1. 취약점(Primary CWE) 종류

| 클래스 | Primary CWE | 취약점 이름 | 개수 | case |
| --- | --- | --- | --- | --- |
| MEMORY_SAFETY | CWE121 | Stack-based Buffer Overflow | 8 | 001, 002, 013, 014, 025, 026, 037, 038 |
| MEMORY_SAFETY | CWE122 | Heap-based Buffer Overflow | 8 | 003, 004, 015, 016, 027, 028, 039, 040 |
| RESOURCE_LIFECYCLE | CWE401 | Memory Leak | 8 | 009, 010, 021, 022, 033, 034, 045, 046 |
| MEMORY_SAFETY | CWE124 | Buffer Underwrite | 4 | 005, 017, 029, 041 |
| FORMAT_STRING | CWE134 | Uncontrolled Format String | 4 | 006, 018, 030, 042 |
| INTEGER_OVERFLOW | CWE190 | Integer Overflow | 4 | 007, 019, 031, 043 |
| INTEGER_OVERFLOW | CWE191 | Integer Underflow | 4 | 008, 020, 032, 044 |
| NULL_DEREF | CWE476 | NULL Pointer Dereference | 4 | 011, 023, 035, 047 |
| RESOURCE_LIFECYCLE | CWE775 | Missing Release of File Descriptor | 4 | 012, 024, 036, 048 |

#### 2.2. 케이스별 상세 (Clean Version)

| case | Juliet flow variant | 클래스 | Primary CWE | 취약점 이름 | 취약 함수 | Source → Sink |
| --- | --- | --- | --- | --- | --- | --- |
| case_001 | `01` Baseline | MEMORY_SAFETY | CWE121 | Stack-based Buffer Overflow | `func_a` | `recv()` → `atoi()` → `buffer[]` 쓰기 |
| case_002 | `01` Baseline | MEMORY_SAFETY | CWE121 | Stack-based Buffer Overflow | `func_a` | `dataBuffer1` 선택 → `strncat()` |
| case_003 | `01` Baseline | MEMORY_SAFETY | CWE122 | Heap-based Buffer Overflow | `func_a` | `recv()` → `atoi()` → `malloc()` → `buffer[]` 쓰기 |
| case_004 | `01` Baseline | MEMORY_SAFETY | CWE122 | Heap-based Buffer Overflow | `func_a` | `malloc()` → `strncat()` |
| case_005 | `01` Baseline | MEMORY_SAFETY | CWE124 | Buffer Underwrite | `func_a` | `recv()` → `atoi()` → `buffer[]` 쓰기 |
| case_006 | `01` Baseline | FORMAT_STRING | CWE134 | Uncontrolled Format String | `func_a` | `recv()` → `printf()` |
| case_007 | `01` Baseline | INTEGER_OVERFLOW | CWE190 | Integer Overflow | `func_a` | `recv()` → `atoi()` → `*` 연산 |
| case_008 | `01` Baseline | INTEGER_OVERFLOW | CWE191 | Integer Underflow | `func_a` | `recv()` → `atoi()` → `-` 연산 |
| case_009 | `01` Baseline | RESOURCE_LIFECYCLE | CWE401 | Memory Leak | `func_a` | `malloc()` → `free()` 누락 |
| case_010 | `01` Baseline | RESOURCE_LIFECYCLE | CWE401 | Memory Leak | `func_a` | `strdup()` → `free()` 누락 |
| case_011 | `01` Baseline | NULL_DEREF | CWE476 | NULL Pointer Dereference | `func_a` | `NULL` 대입 → `printHexCharLine()` |
| case_012 | `01` Baseline | RESOURCE_LIFECYCLE | CWE775 | Missing Release of File Descriptor | `func_a` | `open()` → `close()` 누락 |
| case_013 | `17` for 루프 | MEMORY_SAFETY | CWE121 | Stack-based Buffer Overflow | `func_a` | `recv()` → `atoi()` → `buffer[]` 쓰기 |
| case_014 | `17` for 루프 | MEMORY_SAFETY | CWE121 | Stack-based Buffer Overflow | `func_a` | `dataBuffer1` 선택 → `strncat()` |
| case_015 | `17` for 루프 | MEMORY_SAFETY | CWE122 | Heap-based Buffer Overflow | `func_a` | `recv()` → `atoi()` → `malloc()` → `buffer[]` 쓰기 |
| case_016 | `17` for 루프 | MEMORY_SAFETY | CWE122 | Heap-based Buffer Overflow | `func_a` | `malloc()` → `strncat()` |
| case_017 | `17` for 루프 | MEMORY_SAFETY | CWE124 | Buffer Underwrite | `func_a` | `recv()` → `atoi()` → `buffer[]` 쓰기 |
| case_018 | `17` for 루프 | FORMAT_STRING | CWE134 | Uncontrolled Format String | `func_a` | `recv()` → `printf()` |
| case_019 | `17` for 루프 | INTEGER_OVERFLOW | CWE190 | Integer Overflow | `func_a` | `recv()` → `atoi()` → `*` 연산 |
| case_020 | `17` for 루프 | INTEGER_OVERFLOW | CWE191 | Integer Underflow | `func_a` | `recv()` → `atoi()` → `-` 연산 |
| case_021 | `17` for 루프 | RESOURCE_LIFECYCLE | CWE401 | Memory Leak | `func_a` | `malloc()` → `free()` 누락 |
| case_022 | `17` for 루프 | RESOURCE_LIFECYCLE | CWE401 | Memory Leak | `func_a` | `strdup()` → `free()` 누락 |
| case_023 | `17` for 루프 | NULL_DEREF | CWE476 | NULL Pointer Dereference | `func_a` | `NULL` 대입 → `printHexCharLine()` |
| case_024 | `17` for 루프 | RESOURCE_LIFECYCLE | CWE775 | Missing Release of File Descriptor | `func_a` | `open()` → `close()` 누락 |
| case_025 | `34` union (함수 내 데이터흐름) | MEMORY_SAFETY | CWE121 | Stack-based Buffer Overflow | `func_a` | `recv()` → `atoi()` → `buffer[]` 쓰기 |
| case_026 | `34` union (함수 내 데이터흐름) | MEMORY_SAFETY | CWE121 | Stack-based Buffer Overflow | `func_a` | `dataBuffer1` 선택 → `strncat()` |
| case_027 | `34` union (함수 내 데이터흐름) | MEMORY_SAFETY | CWE122 | Heap-based Buffer Overflow | `func_a` | `recv()` → `atoi()` → `malloc()` → `buffer[]` 쓰기 |
| case_028 | `34` union (함수 내 데이터흐름) | MEMORY_SAFETY | CWE122 | Heap-based Buffer Overflow | `func_a` | `malloc()` → `strncat()` |
| case_029 | `34` union (함수 내 데이터흐름) | MEMORY_SAFETY | CWE124 | Buffer Underwrite | `func_a` | `recv()` → `atoi()` → `buffer[]` 쓰기 |
| case_030 | `34` union (함수 내 데이터흐름) | FORMAT_STRING | CWE134 | Uncontrolled Format String | `func_a` | `recv()` → `printf()` |
| case_031 | `34` union (함수 내 데이터흐름) | INTEGER_OVERFLOW | CWE190 | Integer Overflow | `func_a` | `recv()` → `atoi()` → `*` 연산 |
| case_032 | `34` union (함수 내 데이터흐름) | INTEGER_OVERFLOW | CWE191 | Integer Underflow | `func_a` | `recv()` → `atoi()` → `-` 연산 |
| case_033 | `34` union (함수 내 데이터흐름) | RESOURCE_LIFECYCLE | CWE401 | Memory Leak | `func_a` | `malloc()` → `free()` 누락 |
| case_034 | `34` union (함수 내 데이터흐름) | RESOURCE_LIFECYCLE | CWE401 | Memory Leak | `func_a` | `strdup()` → `free()` 누락 |
| case_035 | `34` union (함수 내 데이터흐름) | NULL_DEREF | CWE476 | NULL Pointer Dereference | `func_a` | `NULL` 대입 → `printHexCharLine()` |
| case_036 | `34` union (함수 내 데이터흐름) | RESOURCE_LIFECYCLE | CWE775 | Missing Release of File Descriptor | `func_a` | `open()` → `close()` 누락 |
| case_037 | `41` 함수 인자 전달 | MEMORY_SAFETY | CWE121 | Stack-based Buffer Overflow | `func_a` + `func_b` | `recv()` → `atoi()` → `func_b()` → `buffer[]` 쓰기 |
| case_038 | `41` 함수 인자 전달 | MEMORY_SAFETY | CWE121 | Stack-based Buffer Overflow | `func_a` + `func_b` | `dataBuffer1` 선택 → `func_b()` → `strncat()` |
| case_039 | `41` 함수 인자 전달 | MEMORY_SAFETY | CWE122 | Heap-based Buffer Overflow | `func_a` + `func_b` | `recv()` → `atoi()` → `func_b()` → `malloc()` → `buffer[]` 쓰기 |
| case_040 | `41` 함수 인자 전달 | MEMORY_SAFETY | CWE122 | Heap-based Buffer Overflow | `func_a` + `func_b` | `malloc()` → `func_b()` → `strncat()` |
| case_041 | `41` 함수 인자 전달 | MEMORY_SAFETY | CWE124 | Buffer Underwrite | `func_a` + `func_b` | `recv()` → `atoi()` → `func_b()` → `buffer[]` 쓰기 |
| case_042 | `41` 함수 인자 전달 | FORMAT_STRING | CWE134 | Uncontrolled Format String | `func_a` + `func_b` | `recv()` → `func_b()` → `printf()` |
| case_043 | `41` 함수 인자 전달 | INTEGER_OVERFLOW | CWE190 | Integer Overflow | `func_a` + `func_b` | `recv()` → `atoi()` → `func_b()` → `*` 연산 |
| case_044 | `41` 함수 인자 전달 | INTEGER_OVERFLOW | CWE191 | Integer Underflow | `func_a` + `func_b` | `recv()` → `atoi()` → `func_b()` → `-` 연산 |
| case_045 | `41` 함수 인자 전달 | RESOURCE_LIFECYCLE | CWE401 | Memory Leak | `func_a` + `func_b` | `malloc()` → `func_b()` → `free()` 누락 |
| case_046 | `41` 함수 인자 전달 | RESOURCE_LIFECYCLE | CWE401 | Memory Leak | `func_a` + `func_b` | `strdup()` → `func_b()` → `free()` 누락 |
| case_047 | `41` 함수 인자 전달 | NULL_DEREF | CWE476 | NULL Pointer Dereference | `func_a` + `func_b` | `NULL` 대입 → `func_b()` → `printHexCharLine()` |
| case_048 | `41` 함수 인자 전달 | RESOURCE_LIFECYCLE | CWE775 | Missing Release of File Descriptor | `func_a` + `func_b` | `open()` → `func_b()` → `close()` 누락 |


#### 2.3. 데이터셋 원본 변경점

Juliet 원본은 한 파일 안에 `bad()`와 `good*()`이 함께 들어 있고 파일명/함수명/주석에 정답이 그대로 적혀 있다. 모델이 파일을 안 보고도 정답을 맞출 가능성을 배제하기 위해 아래와 같이 수정하였다.

| # | 처리 | 상세 |
| --- | --- | --- |
| 1 | 파일 분할 | `#ifndef OMITBAD` 블록에서 취약 파일, `#ifndef OMITGOOD` 블록에서 정상 파일을 생성, `#ifdef INCLUDEMAIN`의 `main()`은 제거 |
| 2 | 정상본 함수 1개만 선택 | 원본은 `goodB2G1/2`, `goodG2B1/2` 등 최대 4개의 변종을 담는다. `goodB2G` → `goodG2B` → `good1` 순으로 하나만 남겼다. |
| 3 | 파일명 익명화 | `CWE121_Stack_Based_Buffer_Overflow__CWE805_char_declare_ncat_15.c` → `case_031.c`  |
| 4 | 함수명 변경 | `..._15_bad()` / `goodB2G1()` → 양쪽 모두 `func_a`, `func_b` 등으로 수정하여 함수 이름을 통해 정답을 맞출 수 없도록 수정했다. |
| 5 | 변수명 변경 | `dataBadBuffer`→`dataBuffer1`, `dataGoodBuffer`→`dataBuffer2`, `helperBad`→`helper1`, `helperGood`→`helper2` (12개 파일) |
| 6 | 주석 전량 제거 | `@description` 블록(BadSource/GoodSink를 그대로 설명), `/* POTENTIAL FLAW: ... */`, `/* FIX: ... */`가 전부 정답을 의미하기 때문에 모두 제거하였다. |

이 외에 코드 로직, 버퍼 크기, 제어흐름 구조, `#include` 구성은 모두 원본과 동일하다.

#### 2.4. 데이터셋 디렉토리 구조
```bash
~/experiments/preprocessing/samples/
│
├── Juliet/                          ← 원본 C 소스코드
│   ├── clean/{vulnerable,benign}/case_001.c ~ case_048.c
│   ├── perturbed/{vulnerable,benign}/case_001.c ~ case_048.c
│   ├── raw/ support/ manifest.tsv
│
├── expr-juliet/                     ← Joern 출력 (.dot)
│   ├── ast/{clean,perturbed}/{vulnerable,benign}/case_001/*.dot
│   ├── cfg/  (동일 구조)
│   ├── pdg/  (동일 구조)
│   ├── cpg/  (동일 구조)
│   ├── _cpgbin/  _staging/  _logs/
│   └── extract_report.json
│
└── squeezed-juliet/                 ← 압축 결과 (.txt, 모델에게 줄 데이터)
    ├── ast/
    │   ├── clean/
    │   │   ├── vulnerable/case_001.txt ~ case_048.txt
    │   │   └── benign/    case_001.txt ~ case_048.txt
    │   ├── perturbed/
    │   │   ├── vulnerable/case_001.txt ~ case_048.txt
    │   │   └── benign/    case_001.txt ~ case_048.txt
    │   └─── perturbed_v2/
    │       ├── vulnerable/case_001.txt ~ case_048.txt
    │       └── benign/    case_001.txt ~ case_048.txt
    ├── cfg/  (동일 구조)
    ├── pdg/  (동일 구조)
    ├── cpg/  (동일 구조)
    └── squeeze_report.json
```

Juliet 데이터셋은 이미 학습 자료에 포함되어 있을 가능성이 있어, https://arxiv.org/html/2507.16585v1에서 지적한 “변수명 변경, 데드 코드 삽입 등 의미를 보존하는 단순한 변형만으로도 성능이 크게 저하되며, 이는 LLM이 취약점의 논리보다는 표면적인 토큰 패턴을 학습했기 때문일 수 있다”는 점을 확인하기 위해 추가 실험용 데이터셋 perturbed, perturbed_v2를 제작하였다.

- perturbed: 원본 Juliet 코드의 변수명, 함수명 변경 + 데드 코드를 삽입, 100줄 미만인 코드들은 평균 100줄로 맞춘 버전
- perturbed_v2: 원본 Juliet 코드의 변수명, 함수명만 변경

> 변수명: data, buffer, … → v1, v2, …

> 데드 코드 예시:
```bash
    int v30 = 3;
    if (log_level)
    {
        v30 = func_c(v30);
    }
```
=> log_level 값은 0이어서 실행되지 않는 블록이다.

#### 2.5. 데이터 형식 압축

##### 2.5.1. 압축 이유

현재 테스트하려는 모델들의 최대 컨텍스트 상한은 32K tokens(32,768)인데 일부 전처리 형식은 데이터 크기가 커서 그대로 입력할 경우 모델의 컨텍스트 상한을 초과하는 문제가 발생한다.

따라서 각 전처리 형식을 가능한 한 유지하면서도 모델의 컨텍스트 상한 내에 입력할 수 있도록 데이터 형식을 압축하였다.

##### 2.5.2. 압축 방법

> 출력 형식 압축
Joern이 생성하는 출력에서 그래프 구조를 표현하기 위한 형식적인 요소와 중복되는 표현을 제거하거나 축약하였다.

| 대상 | 처리 방식 | 예시 |
| --- | --- | --- |
| 긴 노드 ID | 짧은 순차 번호로 변경 | `107374182414` → `1` |
| HTML Encoding | 원래 문자로 복원 | `&lt;` → `<` |
| 반복 접두어 | 축약 | `<operator>` → `op.` |
| DOT 문법 | 삭제 | `[label = <...>]`, `node [shape="rect"];` |
| 부모 코드 중복 | 삭제 | 부모 노드의 코드가 자식 노드에 반복되는 경우 제거 |

Joern의 출력에서는 일부 노드에 자신의 코드와 부모 노드의 코드가 함께 포함되는 경우가 있는데 부모 노드의 정보는 이미 별도로 존재하기 때문에 이러한 반복 정보는 중복으로 판단하여 제거하였다.

> Joern 내부 항목 제거

Joern은 `=`, `+`, `&` 등의 연산자를 내부적으로 함수와 유사한 형태로 표현하여 이를 별도의 그래프로 생성한다.

```
digraph "<operator>.assignment" { ... }
digraph "<operator>.addition" { ... }
digraph "<clinit>" { ... }
```

이러한 항목은 분석 대상 코드의 실제 함수가 아니라 Joern의 내부 표현을 위한 별도 그래프이므로 제거하였다. (실제 함수 내부에 포함된 연산자 정보는 그대로 유지)

> 엣지 라벨 묶기

PDG에서는 각 엣지에 라벨이 포함되는데 동일한 유형의 엣지가 반복적으로 등장하기 때문에 토큰 수가 크게 증가한다. 따라서 이를 압축하였다.

압축 전:

```
"30064771288" -> "124554051597" [label = "DDG: (size_t)written >= sizeof(record->text)"]
"30064771286" -> "124554051597" [label = "DDG: "]
"30064771287" -> "124554051597" [label = "DDG: written"]
```

압축 후: 

```
D 15>2 11>2 ...
D:written 14>2 17>2 ...
```

동일한 유형의 엣지를 하나의 표현으로 묶었다.

- 라벨이 없는 DDG 엣지는 하나의 `D` 항목으로 묶음
- 변수명 등 추가 정보가 포함된 경우에만 `D:변수명` 형태로 별도 표현

이를 통해 그래프의 연결 관계는 유지하면서 반복되는 DOT 문법과 엣지 표현을 줄였다.

---

### 3. 실험 방법
GPU 서버의 ~/experiments/preprocessing/run.py 스크립트를 사용하여 진행

---

### 4. 평가 방법

단일 모델에게 추론 없이 데이터를 넣다 보니 정확도가 많이 낮다. 따라서 우선 다음과 같은 요소로 평가하였다.

#### 4.1. 평가 요소

1. **취약 함수 적중** — `path`의 `symbol`에 정답 함수가 하나라도 있는가
2. **경로 완성도** — source와 sink를 둘 다 세웠는가, 정답 경로와 얼마나 겹치는가
3. **CWE 일치** — `cwe` 값이 정답과 같은가
4. **verdict 적절성** — 취약 샘플이므로 `vulnerable`이 정답. `need_evidence`는 부분 점수

또 아래처럼 프롬프트 규칙을 어기는 경우가 종종 있는데, 이건 전처리 방식과 무관한 프롬프트/하네스 문제라 평가에 반영하지 않았다.

프롬프트를 어기는 예시

- 존재하지 않는 CWE 번호를 출력하는 경우
- `rca_draft`가 "트리거 가능"인데 `verdict`가 `need_evidence`인 경우 등