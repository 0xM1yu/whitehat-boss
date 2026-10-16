# whitehat-boss 기여 안내

[English](CONTRIBUTING.md) · **한국어**

관심 가져 줘서 고맙다. 이 저장소는 **하네스**를 짓는 곳이다 — 하네스의 코드·설정·프롬프트, 하네스가 파일로 받는
입력을 만드는 도구(슬라이서 등), 하네스 결정(ADR)·결함(D·R)이다. 모델 선정, 실험 조건 설계, 벤치마크 구성은
이 저장소의 범위 밖이다([ADR 0012](docs/decisions/0012-harness-canon-v2.md) 결정 5).

- **언어.** 이슈·PR·리뷰 코멘트는 한국어나 영어로 쓴다. `docs/` 아래 문서는 한국어로 쓰여 있고, 새 문서도
  한국어로 쓴다.
- **라이선스.** 기여는 [MIT 라이선스](LICENSE)로 받는다. CLA나 DCO 서명은 요구하지 않는다.
- **보안.** 하네스 자체의 취약점은 공개 이슈로 올리지 않는다 — [SECURITY.ko.md](SECURITY.ko.md).

## 시작하기 전에

1. **열린 이슈와 PR을 먼저 찾는다.** 같은 결함을 누군가 이미 고치고 있을 수 있다.
2. **맞는 템플릿으로 이슈를 연다.**
   - **버그** — 설계 판단이 걸리지 않은 결함.
   - **D-시리즈** — 설계 판단이 필요한 결함·결정. 본문은 `docs/open-questions/D/D<n>.md`에 두고 이슈는 그
     미러다([ADR 0008](docs/decisions/0008-defect-source-of-truth.md)).
3. **관측된 사실**(파일·줄), **어떻게 알았나**(run ID 또는 코드 위치), **선택지**(하나만 적지 않는다)를 넣는다.

## 개발 환경

```bash
pip install -r requirements.txt          # Python 3.11.4 이상
python -m src.cli doctor --profile dev_fake
```

`dev_fake`는 모든 모델을 Fake로 바꿔 GPU 없이 파이프라인 전체를 돌린다. 끝까지 돌리는 법은
[README](README.ko.md#빠른-시작-gpu-없이)를 본다.

## Pull request

- **PR 하나에 주제 하나**, base는 `main`이다. 커밋 메시지는
  [Conventional Commits](https://www.conventionalcommits.org/)(`fix:` · `docs:` · `feat:` …)를 따른다.
- **푸시 전에 두 검사를 돌리고 결과를 눈으로 확인한다.**

  ```bash
  python -m pytest -q
  python -m ruff check .
  ```

  푸시와 한 줄로 잇지 않는다(`pytest | tail -1 && git push`). 파이프가 실패 종료 코드를 삼킨다.
- **이슈를 바르게 건다.** 이슈를 다 해소할 때만 `Closes #N`, 일부만 다루면 `Refs #N`이다. 백틱 없이 평문으로 쓴다.
- **결함 상태는 이슈가 아니라 `D<n>.md`에서 바꾼다.** 이슈는 그 D 파일을 `닫힘`으로 만드는 PR이 닫는다.

## 글쓰기 규칙

[CLAUDE.md](CLAUDE.md)에서 왔다. 일부는 CI가 막고, 나머지는 리뷰어가 본다.

- **실측과 추정을 섞지 않는다.** 안 잰 값은 `0`(또는 `미기재`)으로 두고 사유를 남긴다. 수치를 인용할 때는 출처를
  같은 문장에 적는다.
- **문서가 코드를 서술하면 그 코드를 열어 대조한다.** 기억이나 다른 요약으로 쓰지 않는다.
- **요약이 원문보다 세면 안 된다.**
- **무언가를 지우거나 바꾸면 그것을 가리키던 곳을 전수로 찾아**(`git grep`) 함께 고친다.

## GPU · 실서빙 run 기여

PR의 주장이 실모델로 돈 run(`dev_*` Fake 프로필이 아닌 것)에 기대면, PR에 run 보고서를 같이 낸다.

1. [`docs/experiments/TEMPLATE.md`](docs/experiments/TEMPLATE.md)를
   `docs/experiments/harness/YYYY-MM-DD-<주제>-<장비>.md`로 복사해 채운다.
   - 필수 항목은 `run_id`, `repo_commit`, 프로필, `--device`, 서빙한 모델과 정확한 리비전, 도구 이미지
     digest 등이다.
   - 기록하지 못한 값은 `미기재`로 둔다. 그럴듯한 값으로 채우지 않는다.
2. [`docs/experiments/README.md`](docs/experiments/README.md)의 색인에 그 보고서 행을 더한다.
3. 서빙 모델의 `checksum`이 `config/models.yaml`에서 `0`인 run은 보고는 하되 **비교표에서 뺀다.** 보고서에 그
   사실을 적는다.
4. run 산출물·데이터셋·PoC 바이트는 커밋하지 않는다. `artifacts/`와 `eval/datasets/`는 git이 무시한다.
5. 보고서에 호스트명, IP, 계정명, 개인 경로를 적지 않는다. `<host>` · `<account>` · `/home/user` 같은
   자리표시자를 쓴다.

결정의 근거가 아닌 작은 관측은 보고서 대신 이슈로 올려도 된다.
