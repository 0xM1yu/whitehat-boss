# Case B operator 앵커 · WRITERS 실측 — ATOM

PR #243(Case B에 WRITERS 호출·operator 앵커 추가)의 cap 값과 좁힘 기준이 어디서 나왔는지를
남긴다. `candidates_b_query`만 발행해 **후보 생성 단계**를 쟀다 — backward 슬라이스·클러스터링은
돌리지 않았다. release 평가셋의 private ground truth는 ATOM에 올리지 않았고, GT sink의
file:line은 **로컬**에서만 대조했다(후보의 `file`·`line`과 basename+±2줄 매칭).

⚠ **이 수치는 후보 집합의 크기·포착 여부다. 취약점 탐지 recall/오탐률이 아니다.** 슬라이스도
판정도 이 실행엔 없다. 그리고 아래는 **표본 3~5개**의 값이지 40건 전수가 아니다.

## 0. 실측과 그 한계

| 표시 | 뜻 |
| --- | --- |
| **[실측]** | 이 실행에서 ATOM이 낸 값 |
| **[미기록]** | 이 실행에서 안 남긴 것(재현에 필요하나 이번에 캡처 안 함) |

⚠ **run_id: 없음.** 이 실행은 `cli`가 아니라 진단 스크립트(`/query-sync`에 질의 직접 발행)로 돌려
`RunRecord`를 안 냈다. `progress.jsonl`·산출 JSON도 없다. 그래서 **비교표에 넣지 않는다**(CLAUDE.md §0).
아래 값은 그 진단 실행의 stdout에서 온 것이고, 재현하려면 §1 조건으로 같은 질의를 다시 발행해야 한다.

## 1. 입력과 실행 조건

| 항목 | 값 |
| --- | --- |
| 실험일 | 2026-09-22 KST |
| 장비 | ATOM, 계정 `user`, Linux aarch64 |
| 코드 | PR #243 커밋 `f95ce99af02b60db30671c6aca0d231c267d2123`; `slicer_queries.py` SHA-256 `b3f6b95c7a6f314da951accd38b5c5a4996edfc95889311c0d2d18b377b9ddf0` |
| Joern | [미기록] — 서버 버전을 이 실행에서 안 남겼다 |
| 발행 방식 | Joern 서버 `--server` + `/query-sync`에 `candidates_b_query()` 및 진단 질의 직접 발행(하네스 러너 밖) |
| 표본 | `/srv/tenants/user/release-eval-slices-20260918/samples/sample_NN/repo` (WavPack·pcre2·libvnc·libsixel·libtasn1) |
| GT | 로컬 `release.tar.gz`의 `internal/.../sample_NN/private_gt/ground_truth_enriched.json`(ATOM에 올리지 않음) |

## 2. operator 모집단은 폭증한다 — 그래서 좁힌다 (sample_08 WavPack, 1,020 메서드) [실측]

| operator | raw | 좁힘 |
| --- | --- | --- |
| 배열 인덱스 `<operator>.indirectIndexAccess` | 3,042 | 변수 인덱스 1,178 · **쓰기(대입 대상) 550** |
| 포인터 산술 `<operator>.(addition\|subtraction)` | 3,309 | **포인터 피연산자(`*`) 278** |

→ raw를 그대로 앵커하면 후보가 수천이므로, 인덱스는 **쓰기만**(550), 포인터 산술은 **포인터
피연산자만**으로 좁힌 뒤 `CASE_B_OPERATOR_CAP=2000`으로 유계한다.

## 3. 포인터 판정 축 — `*`만으론 typedef 포인터를 놓친다 (sample_09 pcre2, add+sub 1,831) [실측]

sink GT는 `mb->end_subject - eptr`(pcre2_match.c:486). 피연산자 evalType은 `ANY`·`PCRE2_SPTR`라
`*`를 안 담는다. 축별 개수와 그 486 노드 포함 여부:

| 판정 축 | 개수 | 486 노드 포함 |
| --- | --- | --- |
| `evalType.contains("*")` (star) | 198 | **아니오** |
| star ∨ `== "ANY"` | 1,303 | 예 (폭증) |
| 피연산자가 메모리접근(field/deref/index/addr-of) | **293** | **예** |
| 정수 타입 아님 | 1,608 | 예 (폭증) |

→ 채택: **star ∨ 메모리접근**. star가 raw 포인터를, 메모리접근이 typedef 포인터(evalType이
`*` 없는 필드접근 `mb->end_subject`)를 잡는다. `ANY`·정수아님은 폭증이라 버렸다.

## 4. 새 질의의 후보 수와 reason 분포 [실측]

`candidates_b_query()` 발행 결과(total · reason별):

| 샘플 | total | local_array | dangerous_write | repeated_release | index_write | pointer_arithmetic |
| --- | --- | --- | --- | --- | --- | --- |
| sample_09 pcre2 | 815 | 71 | 81 | 0 | 204 | 473 |
| sample_25 libvnc | 1,910 | 333 | 420 | 168 | 496 | 583 |
| sample_08 WavPack | 3,169 | 559 | 787 | 359 | 550 | 1,125 |

각 앵커는 `CASE_B_OPERATOR_CAP`(2000)·`CASE_B_WRITER_CAP`(2000)으로 유계 — 위 표는 전부 그 이하다.

## 5. GT sink 포착 [실측 · 로컬 대조]

| 샘플 | GT sink | 결과 | 판정 |
| --- | --- | --- | --- |
| sample_09 pcre2 | `mb->end_subject - eptr` @pcre2_match.c:486 (포인터차) | **포착** — line 486 `<operator>.subtraction`, reason `pointer_arithmetic` | operator 앵커의 순증 |
| sample_25 libvnc | `thisRow[x*3+c] =` @tight.c:516 (인덱스 쓰기) | **미포착** | `FilterGradientBPP`(BPP 매크로 템플릿)가 cpg.method에는 있으나 span 468-522의 `.ast.isCall`이 0개 — 본문이 CPG에 없다(파싱 한계 ③, #244). 앵커 밖 |
| sample_08 WavPack | `value & bitset[bits-1]` @unpack3.c:1472 (인덱스 **읽기**) | **미포착** | writes-only 유계의 문서화된 한계 |

## 6. baseline ↔ new (동일 CPG, sample_09) [실측]

operator 없는 baseline(7a9546c, WRITERS만) ↔ operator 포함 new를 **같은 CPG**에서 대조:

| | total | GT 486 포착 | reason |
| --- | --- | --- | --- |
| baseline (operator 없음) | 138 | **0** | — |
| new (operator 앵커) | 815 | **1** | `pointer_arithmetic` |

→ sample_09 GT sink는 baseline이 놓치고 operator 앵커가 새로 잡는다. **0 → 1**. (한 샘플의 실측이다.)

## 7. WRITERS [실측 · 로컬 대조]

| 샘플 | GT | 결과 |
| --- | --- | --- |
| sample_04 libsixel | `memset(image->data + …)` @fromsixel.c | **새로 포착** — dest가 필드 포인터라 baseline `local_array_argument` 미포착, `dangerous_write`로 잡힘 |
| sample_05 libtasn1 | `strcpy` (지역배열) | 포착 — baseline도 `local_array_argument`로 잡던 것(WRITERS는 가산) |

## 8. 안 한 것

- backward 슬라이스·클러스터링·전체 파이프라인 — 후보 생성만 쟀다.
- 40건 전수 — 표본 3~5개다.
- `cli` 실행·`RunRecord` — 진단 스크립트라 run_id 없음(§0).
- Joern 버전 캡처 [미기록].
