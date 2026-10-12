# 취약점 슬라이서 v2: PoC 입력 문맥

이 디렉터리에는 두 가지 실행 방식이 있다. `slicer/slicer.py`는 저장소 전체에서 취약점 후보를 탐색해 슬라이싱하고, `slicer/slicer_target.py`는 지정한 source·sink에서 슬라이싱한다. 아래 결과는 대상 지정 실행에서 만든다.

| 출력 | 내용 |
| --- | --- |
| `<sample_id>.json` | 슬라이스, 입력 관련 CPG 근거, attach 결과와 선택 근거를 담은 결과 문서 |
| `*.sliced.c` | Joern으로 만든 역방향·정방향 슬라이스 |
| `*.auditor-input.c` | 기존 규칙에 따라 원본 함수와 정의를 붙인 코드 |
| `*.pov-input.c` | 입력값 처리 문맥을 선택적으로 확장한 코드 |

`sliced.c`와 `auditor-input.c`는 기존 `tools`의 생성 규칙을 따른다. `pov-input.c`는 같은 1차 슬라이스를 바탕으로, Joern CPG가 열려 있을 때 수집한 입력 관련 근거를 사용한다. 값의 흐름, 제어 흐름, 함수 호출 관계로 후보를 고른 다음 tree-sitter 인덱스에서 원문을 찾아 붙인다. 입력 경로가 확인되지 않으면 `pov-input.c`가 `auditor-input.c`와 같을 수 있다.

`run_gt.py`는 표본마다 `<sample_id>.json`을 먼저 저장한다. JSON의 각 슬라이스에는 1차 코드(`code`), Auditor 코드(`attached.source`), PoC 코드(`pov_attached.source`)가 들어가며, 수집된 CPG 입력 근거(`analysis.input_evidence`)와 문맥 선택 통계도 함께 남는다. `.c` 파일들은 이 JSON에서 코드를 따로 꺼낸 결과다.

## 준비물

- Python 3.11 이상
- `tree-sitter==0.26.0`, `tree-sitter-c==0.24.2`, `tree-sitter-cpp==0.23.4`
- Joern 서버 또는 실행 파일. CPG 질의는 Joern 4.0.605에서 확인했다.

슬라이서를 실행하는 곳과 Joern 서버가 다른 컴퓨터라면, Joern 서버에서도 전달된 경로의 소스 체크아웃을 읽을 수 있어야 한다. 기존 서버에는 `--joern-endpoint`로 연결한다. Joern이 설치된 서버에서 표본마다 서버를 새로 띄우려면 `--joern-bin`을 사용한다.

## 저장소 전체 후보 탐색

Joern CPG에서 후보를 찾고 후보별 슬라이스를 만들려면 다음과 같이 실행한다.

```bash
python slicer/slicer.py --repo /path/to/repo \
  --out /path/to/candidates.json \
  --joern-endpoint http://127.0.0.1:16240
```

결과 JSON에는 후보 목록(`candidate_manifest`)과 생성된 슬라이스가 담긴다. 이 실행 방식은 아래의 대상 지정 attach 단계를 자동으로 실행하지 않으므로 `auditor-input.c`와 `pov-input.c`를 만들지는 않는다. 코드 파일도 함께 내보내려면 `--export-dir /path/to/export`를 추가한다.


## 표본 하나 직접 실행

```bash
python slicer/slicer_target.py --repo /path/to/repo --spec spec.json \
  --out slices.json --joern-endpoint http://127.0.0.1:16242
python indexer.py --root /path/to/repo --out index.json \
  /path/to/repo/source.c /path/to/repo/header.h
python attach/attach.py --slices slices.json --repo /path/to/repo \
  --index index.json --out attached.json --with-pov
python export_code.py attached.json
```

`spec.json`의 `sink`에는 `file`, `line`, `function`, `symbol`을 넣는다. `source`도 같은 형식이며, 없으면 `null`이다. `attach/attach.py`를 단독으로 실행할 때 `--with-pov`를 생략하면 `auditor-input`만 만든다. PoC 입력 문맥의 함수 수와 크기 상한은 `python attach/attach.py --help`에서 확인할 수 있다.

## input 확장 적용 범위와 한계 (pov 입력용)

현재 테스트 횟수가 적어 잘 일반화되지 않았다. 문법 형태와 표준 라이브러리 함수명을 힌트로 사용하며, CPG 시작점은 직접 읽는 문자·바이트 포인터를 기준으로 고른다. 구조체 필드를 거치거나 함수 포인터 호출을 해소하지 못한 입력 경로는 연결되지 않을 수 있다. 이 경우 파서 함수가 추가되지 않는다.

JSON에는 후보와 선택 근거, 상한에 걸린 사실, 연결되지 않은 입력 경로를 코드 출력과 별도로 기록한다.

기본 Joern 슬라이싱 모듈은 기존 `tools/slicer` 구현을 바탕으로 한다.
