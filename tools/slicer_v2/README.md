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
`git checkout 8175f5ef -- tools/slicer_v2/run_gt.py tools/slicer_v2/subset.py tools/slicer_v2/name_outputs.py`
(브랜치 이름이 아니라 커밋이다 — 브랜치는 머지 뒤 지워진다)
(`attach/`에 기대므로 그대로는 안 돈다).
