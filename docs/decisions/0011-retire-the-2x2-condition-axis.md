# 0011. 2×2 조건 축을 폐기한다 — Scout 오케스트레이터로 간다

- 상태: **승인** (2026-09-20, @danielcho02)
- 일자: 2026-09-20
- 관련: [evaluation.md](../evaluation.md) §2.1·§2.2, [D1](../open-questions/D/D1.md), [open-questions.md](../open-questions.md), `src/schemas/run.py`의 `CONDITION_PATTERN`, 이슈 [#59](https://github.com/0xM1yu/whitehat-boss/issues/59), [ADR 0010](0010-harness-structure-and-model-roster.md) §3

## 왜 이 기록이 필요한가

**결정은 진작 나 있었다. 저장소에 없었을 뿐이다.**

그 결과가 실제로 두 번 났다.

1. **2026-09-15** — 누군가 이슈 #59를 *"2x2 실험 폐기로 인해 닫음"* 한 줄로 수동 close했다. `Closes`를 건 PR이 없었다([CLAUDE.md](../../CLAUDE.md) §3 규칙 1 위반).
2. **2026-09-18** — 장부 전수 대조(PR #217)가 그것을 찾아 **되열었다.** 사유: *"폐기된 것은 2×2 **실험 트랙**이고, D1이 가리키는 것은 … **§2.1의 라벨 축**입니다. 그 절은 지금도 살아 있고 …"*
   ⚠ **날짜는 KST다.** 되열기 코멘트의 `createdAt`이 `2026-09-17T20:54:06Z`라 UTC로는 09-17이지만 이 저장소의 기준시(`git log --format=%cI`가 전부 `+09:00`)로는 **09-18 05:54**이고, [D1](../open-questions/D/D1.md)과 [인덱스](../open-questions.md)도 09-18로 적는다. **이 ADR의 초판이 UTC를 그대로 써 하루를 틀렸다** — 날짜 순서를 바로잡으려고 쓴 문서에서 난 오류라 여기 남긴다([CLAUDE.md](../../CLAUDE.md) §2.4).

**되열린 쪽이 그때는 옳았다.** 2026-09-01에 폐기된 것은 **실험 트랙**이고(그 기록은 `config/budgets.yaml`의 `output_max` 주석에 남아 있다), D1이 묻던 것은 **라벨 축**이었다 — `B1`/`B1'`/`B3`/`B3'`이 §2.1(2×2)과 §2.2(파이프라인)에서 서로 다른 뜻으로 쓰이는 충돌이다. 트랙이 없어져도 라벨 충돌은 문서에 남아 있었다.

**2026-09-20에 그 라벨 축까지 폐기됐다.** 그런데 그 결정 역시 저장소 밖에 있었다. 기록하지 않으면 **세 번째로 같은 왕복이 난다** — 다음 사람이 "2×2는 폐기됐잖아"로 닫고, 그 다음 사람이 "그 사유는 이 항목을 해소하지 않는다"로 되연다.

[decisions/README.md](README.md)가 **평가 프로토콜 변경(조건 정의, 데이터셋 구성)**을 ADR 대상으로 드는 것이 이 자리다.

## 결정

1. **2×2 조건 축을 폐기한다.** `evaluation.md` §2.1의 2×2 설계는 더 이상 조건 정의가 아니다. **§2.2가 유일한 조건 정의다.**
2. **방향은 Scout 오케스트레이터다** — 단일 모델 대 앙상블을 2×2 표로 가르는 대신, Scout 풀(CWE 계열별 전문가 셋)을 세우고 그 위에서 잰다. 구조는 [ADR 0010](0010-harness-structure-and-model-roster.md) §3이 든다.
3. **`CONDITION_PATTERN`에서 `2x2` 축을 뺀다** — `^(B1|B1prime|B3|B3prime)@pipeline$`. `@` 접미사는 남긴다(축이 다시 늘 때 자리를 미리 지운다).
4. **[D1](../open-questions/D/D1.md)은 `닫힘`이다.** 라벨 충돌은 두 축이 있을 때만 성립하는 질문이고, 축이 하나가 되며 **질문 자체가 소멸했다.** 「어느 쪽으로 통합할까」에 답한 것이 아니다.

## 결과

**얻는 것.** 조건 정의가 한 곳이 된다. 같은 라벨이 문서마다 다른 뜻을 갖던 상태가 끝나고, `RunRecord.condition`을 읽는 쪽이 축을 되묻지 않아도 된다.

**잃는 것 — 그리고 되돌리는 비용.**

- **과거 기록을 새 패턴으로 못 읽는다.** `condition: "B1prime@2x2"`를 든 `RunRecord`는 이제 `ValidationError`를 낸다. ⚠ **지금 저장소에 kind `run`을 되읽는 소비자가 없어 잠복이다** — `store.query(...)` 호출부는 **`src/`·`tools/`에 셋**이다 — `src/cli.py:485`가 `"capsule"`, `tools/probe/critic_output_budget.py:248·249`가 `"finding"`·`"evidence"`다. **테스트에 일곱이 더 있어 합쳐 열이다**(`tests/test_adapters.py` 다섯 · `tests/test_tokenizer_adapter.py` 둘) — ⚠ 이 문단은 한때 「저장소 전체에 셋뿐」이라 적었고 **테스트를 안 셌다**(6라운드 적대 리뷰). **어느 것도 `"run"`이 아니다** — `git grep 'query("run")'`이 저장소 전체 0건이다. ⚠ **`src/` 안에는 `"capsule"` 하나뿐이다** — 나머지 둘은 `tools/probe/`라 프로덕션 경로가 아니다. **소비자가 생기면 그때 옛 `state.db`가 깨진다.** 그 값을 가진 매니페스트가 실험 보고서에 남아 있다(`docs/experiments/harness/2026-08-28-2x2-b1prime-qwen3-8b-atom.md`).
- **되돌리기는 싸다** — 정규식 한 줄과 문서 몇 줄이다. 축을 되살리려면 `CONDITION_PATTERN`에 `(2x2|pipeline)`을 되돌리고 이 ADR을 대체하는 ADR을 쓴다.

**살린 것 하나.** `evaluation.md` §2.1의 **절 번호와 앙상블 각주는 지우지 않았다.** `src/config.py`의 `_check_family_split`이 `ConfigError` 문구로 그 절 번호를 부르고, B3/B3′의 「제작사가 다른 모델」 조건이 그 각주에만 있다. 절을 통째로 지웠으면 런타임 오류 메시지가 없는 자리를 가리켰을 것이다([CLAUDE.md](../../CLAUDE.md) §2.3).

## 대안

- **(A) 라벨을 분리한다** — `B1_2x2` / `B1_pipeline`처럼 이름을 갈라 둘 다 유지. **기각**: 2×2 실험이 이미 안 돌므로 유지할 대상이 없다. 이름만 는다.
- **(B) 아무것도 안 한다** — D1을 열어 둔 채 둔다. **기각**: 그 상태가 2026-09-15·09-17의 왕복을 만들었다. 열린 질문이 「이미 답이 난 질문」인 것이 문제였다.
- **(C) §2.1을 통째로 삭제한다.** **기각**: `src/config.py`가 그 절 번호를 런타임에 부른다(위 「살린 것」).

## 검증

이 결정이 코드에 실제로 서는지를 셋이 잡는다.

- `tests/test_schemas.py` — `B1@2x2`가 `ValidationError`를 내는지 단언한다(패턴을 되돌리면 빨개진다).
- `tests/test_docs_format.py::test_the_dead_2x2_axis_is_never_named_without_saying_it_is_dead` — `PROJECT-CONTEXT.md`·`src/`·`config/`에서 2×2를 **살아 있는 선행**으로 드는 줄을 막는다. 폐기 사실을 적은 줄은 통과한다.
- `tests/test_pipeline_e2e.py::test_the_blocking_messages_do_not_name_an_axis_the_pattern_rejects` — 운영자에게 나가는 차단 문구가 패턴이 안 받는 축을 사유로 들지 못하게 한다.
