# 실서빙 셋 첫 파이프라인 완주 시도 — zlib · ATOM

> **한 줄:** 7단계에서 죽었다([D65](../../open-questions/D/D65.md)). **그런데 완주했어도
> 결과는 비었다** — Finding 150건 중 **148건이 근거 0개**라 게이트가 E0으로 단락했고,
> `path`는 **150건 전부 비어 있다.**

> **이 보고서는 [D65](../../open-questions/D/D65.md)가 자기 첫 후속으로 지목한 것이다.**
> 그 항목이 *"`run_20260904_0001`은 origin/main에 보고서로 없다 — 위 실측의 원천은 보존된
> `trace.jsonl`과 종료 시 콘솔 출력이고 **저장소 밖이다**"*라고 적었다. 여기가 그 자리다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-04 17:41 KST` (16:53 시작 — 48분) |
| 실제 서빙 모델 | scout `qwen3-8b`(8001) · auditor `qwen3-coder-30b-a3b`(8002) · critic `devstral-small-2-24b`(8003). **셋 다 `--max-model-len 32768`** ⚠ **revision 미기재** — 그때 안 적었다 |
| `repo_commit` | **미기재** |
| `run_id` | `run_20260904_0001` |
| run 산출물 경로·보존 여부 | `/srv/tenants/user/artifacts_20260904/run_20260904_0001` — **보존됨**(`trace.jsonl` 5,724줄 · `state.db` 950 KB · 도구 로그 1.5 MB) |
| `tool_runner` | docker (로그의 `# runtime: docker`) |
| `--device` | `atom` |
| `--condition` · `--seed` | `B3prime@pipeline` · `20260904` |
| 프로필 | `local_balanced` |
| 대상 | `/srv/tenants/user/targets/zlib` |
| `tool_images` digest | **미기재** (로그에는 `harness-tools:v1` 태그만 있다) |
| `--gpu-memory-utilization` | **미기재** |
| 동시에 떠 있던 다른 서버 | **미기재** |
| 원문 산출물 보존 위치 | 위 run 디렉터리 |
| 서빙 서버 stdout | **보존하지 않았다** |
| **이 보고서의 집계 수단** | 보존된 산출물을 **읽기만** 했다 — ATOM에서 `venv-probe` 파이썬으로 `trace.jsonl`(5,724줄)과 `state.db`를 셌고 **서버를 띄우지 않았다. 새 run은 없다.** §2·§4의 수치가 전부 그것이다 |

⚠ **이 run은 비교표에 못 들어간다.** 돌던 시점에 critic `devstral-small-2-24b`의
`checksum`이 0이고 `source`가 `null`이었다(CLAUDE.md §0). PR #142가 같은 날 그 둘을
채웠지만 **소급되지 않는다.**

## 1. 무엇이 나왔나

| | |
| --- | --- |
| trace 이벤트 | **5,724건** — 단계별 `{0: 3, 1: 4, 2: 1176, 3: 1224, 4: 1792, 5: 762, 6: 750, 7: 13}`. **8단계는 0** |
| `state.db` 객체 | artifact 300 · candidate 256 · capsule 408 · **evidence 238** · **finding 150** · decision 2 · missing_evidence 1 · run 1 |
| 장부(seq 5721) | `input_tokens 994,317` · `output_tokens 146,660` · `gpu_seconds 2,776` · `tool_ms 135,093` |
| 종료 | 7단계 `ValidationError` — D65 |

## 2. 게이트가 무엇을 판정했나 [실측]

6단계 `gate_eval` **150건** — Finding 수와 1:1이다.

| 등급 | 건수 | 이벤트가 든 키 |
| --- | --- | --- |
| **E0** | **148** | `grade`·**`p0`** 둘뿐 |
| E1 | 2 | `grade` + 술어 11개 전부 |

**`p0`은 단락이다.** `src/orchestrator/gate.py:120` (줄번호는 `origin/main` `e73da9b` 기준이다 — **열린 #167이 같은 파일 docstring에 6줄을 더하므로 그 뒤에는 심볼로 찾는다**: `evaluate()`의 첫 분기):

```python
if not finding.evidence_refs:
    return GateResult(grade=EvidenceGrade.E0, predicates={"p0": True})
```

즉 **148건은 술어가 평가되지도 않았다.** `static_ok`·`path_ok` 같은 값이 거짓이어서가
아니라 **근거 참조가 비어서** 그 앞에서 끊겼다.

⚠ **이 구별을 처음에 놓쳤다.** 없는 키를 `False`로 세면 *"static_ok 1.3%"* 같은 수가
나오는데, 그것은 값이 아니라 **키가 있는가**를 센 것이다. 술어를 실제로 실은 이벤트는
전 단계 통틀어 **4건**(6단계 2 + 7단계 2)이고 그 값은 이렇다:

⚠ **독립 표본 4가 아니다.** 같은 Finding 둘(`find_0035`·`find_0088`)이 6단계와 7단계에서
한 번씩 평가된 것이라 **Finding 단위로는 2건**이고, 그 둘은 D65가 등재 시점에 이미 들고
있던 바로 그 둘이다. 아래 표를 그렇게 읽는다 — 교차표의 표본 크기는 2다.

| 술어 | 4 이벤트(= Finding 2 × 단계 2)의 값 |
| --- | --- |
| `static_ok` | **True 4** |
| `graph_ok` | **True 4** |
| `has_repro_spec` | **True 4** |
| `unblocked` | **True 4** |
| `path_ok` | **False 4** |
| `location_ok` | **False 4** |
| `upheld` | False 3 · True 1 |

**단락을 통과한 건에서는 정적 신호도 재현 명세도 있었다.** 없는 것은 **경로와 위치**다.

## 3. Finding이 무엇을 담았나 [실측 · `state.db`]

| | 150건 중 |
| --- | --- |
| `path`가 빈 리스트 | **150 (100%)** |
| `evidence_refs`가 0개 | **148** (나머지 2건은 3개씩) |
| `repro_spec` 있음 | **107** |
| 최종 `state` | `queued` 149 · `hypothesized` 1 |

표본(`find_0001`):

```json
{"finding_id": "find_0001", "state": "queued", "path": [], "confidence": 0.9,
 "cwe": "CWE-773", "title": "ZALLOC 함수가 NULL을 반환할 경우 메모리 누출이 …",
 "evidence_refs": [], "repro_spec": {"steps": [...], "expected_signal": "Z_MEM_ERROR"},
 "produced_by": {"role": "auditor", "model": "qwen3-coder-30b-a3b", "prompt_version": "v1"}}
```

**Auditor는 `cwe`·`title`·`confidence`·`repro_spec`을 채우고 `path`와 `evidence_refs`를
안 채운다.** ⚠ **「못 채우는 것이 아니라 안 채운다」로는 단정하지 않는다** — 같은 응답의
다른 필드가 다 있다는 것은 **능력**을 보일 뿐 원인을 가르지 않는다. v1 프롬프트가
*"사실 블록의 `evidence`에 없는 근거 ID를 만들어 내지 않는다"*고 적으므로, 사실 블록이
비었다면 모델은 **지시대로** 비운 것이고 그것은 배선 쪽이다. **이 run은 그 갈래를 안
열었다** — §8 후속 1이 그것이다.

⚠ **근거 객체는 실재한다** — `state.db`에 evidence 238건이고 `tool_call` 238건과 같다.
**만들어졌는데 Finding이 참조하지 않는다.**

## 4. 도구는 돌았다 [실측]

| 도구 | ok | 실패 |
| --- | --- | --- |
| `index` | 1 | 0 |
| `semgrep` | 74 | 5 |
| `clang-tidy` | 42 | 37 |
| `build` | 42 | 37 |

실패 74건은 **`.h` 파일에 컴파일러를 돌린 것**이다 — `"컴파일할 C 소스가 없다"`(exit 2,
62건)·`"대상이 컴파일되지 않는다"`(exit 1, 12건). 정상 동작이다.

**⚠ 그런데 `semgrep`은 성공해도 결과가 0건이다.** `adler32.c` 로그:

```
# semgrep: 0 findings
"message": "Syntax error at line /src/adler32.c:53:\n `} while (0)` was unexpected"
"type": ["PartialParsing", ...]
"results": []
```

`exit=0`인데 zlib의 매크로를 파싱 못 해 `results`가 비었다. **이것이 STATIC 근거가
희박한 이유의 후보다** — 다만 §3이 보이듯 Finding이 근거를 **참조조차** 안 하므로,
근거가 적어서인지 참조를 안 해서인지는 이 run만으로 못 가른다.

## 5. 8/26 Fake run과의 대조 [비교 아님 — 참고]

| | 2026-08-26 (auditor·critic **Fake**) | **2026-09-04 (실서빙)** |
| --- | --- | --- |
| 6단계 `gate_eval` | 62건 | 150건 |
| `path_ok` | **78 True** | **0** (`path` 150/150 빔) |
| `static_ok` | 75/78 | 술어 평가가 2건뿐 |
| `location_ok` | 0 | 0 |
| 등급 | E2 59 · E0 3 | **E0 148 · E1 2** |

⚠ **왼쪽 열의 분모가 행마다 다르다.** 8/26 보고서의 `gate_eval`은 **78건**(6단계 62 ·
7단계 8 · 8단계 8)이고 술어 값은 **78건 합산**이다 — 첫 행의 `62`만 6단계다. 오른쪽도
같다: `path_ok` 0은 **Finding 150 기준**이고 「술어 평가가 2건뿐」은 **6단계 `gate_eval`
기준**(전 단계로는 4 이벤트 = Finding 2)이다.

⚠ **두 run은 직접 비교가 아니다** — 모델·커밋·대상이 다르고 이 run은 checksum 0이라
비교표 밖이다(§메타). 그래도 **`path`를 채우던 것이 Fake였다**는 방향은 남는다.
8/26 보고서 자신이 *"E2 59건이 Fake의 산물"*이라는 8/25 해석을 인용한다.

## 6. 이 run이 좁히는 것 — **D65를 고쳐도 결과가 빈다**

[D65](../../open-questions/D/D65.md) 본문이 *"run A·B의 블로커다"*라고 적는다.
**이 실측이 반증하는 것은 그 문장이 아니라 「그것만 고치면 된다」다** — D65는 여전히
**필요**하되 **충분하지 않다.**

⚠ **왜 「블로커가 아니다」로 적으면 안 되나.** [build-plan.md](../../build-plan.md) §8 row 2가
남긴 조건은 *"**그래도 닫히지 않는다 — 남은 것은 완주 하나다.** 파이프라인을 한 번도 안
돌렸다"* 하나인데, 아래가 보이듯 **D65를 고치면 완주한다.** 즉 D65는 row 2의 선행 그대로다.
바뀐 것은 **완주해도 그 행이 재려던 것(「Auditor·Critic 캡슐만 바뀐다」)을 못 본다**는 것이다.

- 이 run의 `path_ok` 통과분은 **0/150**이다. D65의 선택지 **(a)**(`p3`에 `path_ok`)면
  E3가 **0건**이라 `validated`가 안 나오고, **(d)**(E2 미만을 7단계에 안 올림)면
  7단계에 **아무것도 안 올라간다.** ⚠ **(a)는 7단계 진입 조건을 안 건드린다** —
  진입은 등급이 아니라 상태(`hypothesized`)로 걸리므로(D65 기제 ④) E1도 계속 올라오고
  Critic 모델 호출 비용은 그대로다. 어느 쪽이든 크래시는 없어지고 **결과가 빈다.**
- **크래시가 전이를 막은 건은 1건**(`find_0088`)이다. 7단계에 **오른** 것이 2건이고
  `find_0035`는 정상으로 `queued`가 됐다(D65 본문 — *"같은 7단계의 `find_0035`는 죽지
  않았다"*). §3의 최종 state `queued 149 · hypothesized 1`이 그 둘을 가른다.
  나머지 148건은 6단계에서 끝났다.
- **E2 이상이 0건**이다(6단계 한정 — 7단계에서 `find_0088`이 E3를 받았다).
  ⚠ 다만 `architecture.md` §4의 「E2를 Critic으로 승격」은 **E2 건수와 무관하게** 지금
  배선에 없다 — `policy._gate_grade`가 E1도 E2도 `hypothesized`로 보내고 러너는 상태만
  본다(D65 기제 ④). 0건인 것이 그 행을 죽인 것이 아니다.

**결과를 비우는 것은 Auditor 출력이다** — `evidence_refs`(148/150 빔)와 `path`(150/150 빔).
D65를 어떻게 풀든 그 둘이 그대로면 run은 완주하고 **`validated` 0건**을 낸다.
**둘 다 해야 row 2가 닫힌다.**

## 7. 이 보고서가 틀렸다면

- **`path`·`evidence_refs`가 프롬프트 v2에서 달라진다면**: 이 run은 `prompt_version: "v1"`이다
  (`produced_by`). v2가 그 둘을 요구하는지는 이 run이 답하지 않는다
- **semgrep 파싱이 대상 특유라면**: zlib 한 점이다. 다른 대상에서 `results`가 나오면
  §4의 후보가 무너진다
- **`p0` 단락이 의도라면**: 근거 없는 Finding을 E0으로 떨어뜨리는 것 자체는 설계다.
  문제는 **그 상태가 148/150이라는 것**이지 단락의 존재가 아니다
- **§6이 틀렸다면**(제일 센 주장이므로 여기 적는다): 다른 대상에서 Auditor가 `path`·
  `evidence_refs`를 채우면 「결과가 빈다」는 **이 대상 특유**가 된다. zlib 한 점이다.
  그리고 D65를 실제로 고친 뒤 완주시켜 `validated`가 0이 아니면 §6의 전제가 무너진다 —
  **그 run이 아직 없다**

## 8. 후속

1. **Auditor가 `evidence_refs`·`path`를 안 채우는 것** — 이 run의 제일 큰 결함이고
   아직 등재된 D 항목이 없다. 프롬프트인지 배선인지부터 가른다
2. **E0 단락이 술어를 기록에 안 남긴다** — 148건의 판정을 사후에 검증할 수 없다.
   [D23](../../open-questions/D/D23.md)(`Evidence.location`이 안 채워져 E1이 구조적으로
   거짓)의 이웃이다
3. ~~**[D65](../../open-questions/D/D65.md) 본문의 「run A·B의 블로커」를 이 실측으로 고친다**~~
   → **이 PR이 했다**(필요하되 충분하지 않다로 좁혔다)
4. **semgrep이 zlib에서 0건인 것** — 파싱 실패가 원인인지, 규칙이 안 맞는지

## 관련

- [D65](../../open-questions/D/D65.md) — 이 run이 죽은 지점. 이 보고서가 그 첫 후속이다
- [D23](../../open-questions/D/D23.md) — `location_ok`가 이 run에서도 0이다(여섯 번째 확인)
- [2026-08-26 재측정](2026-08-26-pipeline-zlib542-remeasure-atom.md) — §5의 대조 상대
- [experiments/README.md](../README.md) — 보고서 규약
