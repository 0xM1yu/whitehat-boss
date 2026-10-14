# 슬라이서 v2 — 대상 지정 모드와 입력 문맥

`tools/slicer`(정본)에 **얹힌** 확장이다. 정본 모듈을 이름으로 import하고 사본을 들지 않는다.
계약·JSON 모양·한계는 [docs/slicer-README.md](../../docs/slicer-README.md)의 마지막 절이 정본이다.

| 파일 | 내용 |
| --- | --- |
| `slicer_target.py` | spec(sink·source 위치)에서 역방향(`case: "B"`)·정방향(`case: "F"`)으로 자른다. 정답·CWE 경로를 읽지 않는다 |
| `input_evidence.py` | 슬라이스 옆 입력 처리의 CPG 근거 질의와 모양 검사 |
| `input_context.py` · `input_paths.py` · `input_text.py` | `--index`로 받은 하네스 인덱서 출력과 근거에서 입력 디코더 함수를 고른다 → `analysis.input_context` |

```bash
python tools/slicer_v2/slicer_target.py --repo /path/to/repo --spec spec.json \
  --out slices.json --joern-endpoint http://127.0.0.1:16242 [--index index.json]
```

`pov-input.c`·`auditor-input.c` 렌더는 없다(하네스 `runner._auditor_source`의 사본이었다). 선택 결과는 JSON 키로만 나간다.

`run_gt.py` · `subset.py` · `name_outputs.py`(정답 파일로 표본을 고르고 돌리는 데이터셋 벤치마크 도구)는 PR #638의 첫 판에 있었으나 **지웠다** —
이 저장소는 하네스 개발 전용이고 실험·벤치마크 도구는 실험팀 몫이다(ADR 0012 결정 5). 되살리려면
`git fetch origin 8175f5ef && git checkout 8175f5ef -- tools/slicer_v2/run_gt.py tools/slicer_v2/subset.py tools/slicer_v2/name_outputs.py`
(브랜치 이름이 아니라 커밋이다 — 브랜치는 머지 뒤 지워진다. ⏵ 2026-10-14 — `fetch`가 앞에
있어야 한다: squash로 머지되면 그 sha가 main 이력에 없고 브랜치 삭제 뒤 평소 `fetch`로는
안 끌려온다)
(`attach/`에 기대므로 그대로는 안 돈다).

## 이 PR에서 뗀 C++ 인덱서 보강 — 어디에 있고 무엇이 선행인가

⏵ 2026-10-14 (적대적 리뷰 MEDIUM) — 커밋 `9eed0205`이 C++ 인덱서 4건(`.h`를 C++ 문법으로
파싱 · 빈/한정자 매크로 중립화 · 암시적 `this` 멤버 타입 해결 · 그 선행)을 이 PR에서 뗐다.
**사유가 실측으로 맞다** — `tests/test_indexer.py`가 `tools/harness-tools/indexer.py`의 전체
sha256을 이미지 `harness-tools:v3`에 묶고 `src/preflight.py`가 그것을 대조해 다르면 ERROR로
exit 2다. 그 핀은 `7ddf55ef`(2026-10-13 04:15)로 main에 들어왔고 네 커밋은 **그 15시간 뒤**에
main을 안 머지한 브랜치 위에서 쓰였다(CLAUDE.md §7 첫 줄의 그 형태).

- **작업이 사는 곳: `origin/feat/indexer-cpp-preserved`** — 네 커밋이 현재 main 위로 리베이스돼
  있다. 이 이름이 저장소 안에 적힌 자리는 여기뿐이다(PR 본문은 머지 뒤 이력에만 남고 revert
  커밋 메시지는 squash에서 사라진다).
- **되살리기 선행 둘**: ① 이미지 재빌드 ② `tests/test_indexer.py`의 `_BAKED_INDEXER_SHA256`
  상향. ⚠ 재빌드는 `tool_images` digest를 바꿔 **기존 run과 비교 축을 가른다** — 그 테스트가
  스스로 「의식적으로 한다」고 적는 결정이다.
- ⚠ **그 브랜치의 판에는 조용한 결함 둘이 남아 있다**(revert 메시지가 적는다). 하나는 확인했다 —
  `_neutralize_macros`가 `#`로 시작하지 않는 **모든** 줄에 단어경계 치환을 돌려 문자열·주석
  안의 이름까지 지운다. 되살릴 때 먼저 고칠 자리다.
- 🔴 **추적 이슈·D 항목이 없다**(`gh issue list` 100건·`docs/` 전수 0건). 되살리는 PR이 이슈를
  열고 이 절에 번호를 단다 — CLAUDE.md §3의 #133 사고가 막는 자리다.
