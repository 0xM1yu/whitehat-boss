# 모델 checksum manifest

`config/models.yaml`의 `checksum` 한 줄이 **무엇으로부터 나왔는지**를 적은 파일들이다.
`tools/model_checksum.py`가 만들고, 파일 하나가 model key 하나에 대응한다
(`<model key>.txt`).

**여기 있는 이유**: checksum 값이 `config/models.yaml`에 있으므로 그 **근거도 `config/`
안에 있어야 한다.** `tools/`나 `artifacts/`에 두면 값과 근거가 다른 수명의 디렉터리로
흩어져 갈라지고, `artifacts/`는 아예 git 밖이다(`.gitignore`).

형식은 `sha256sum` 호환이다.

```
<파일 내용의 sha256>  <스냅샷 루트 기준 상대경로>
```

그래서 모델을 받은 장비의 스냅샷 디렉터리에서 바로 검증할 수 있다.

```bash
cd <스냅샷 경로> && sha256sum -c <이 저장소>/config/checksums/<model key>.txt
```

registry의 `checksum`은 **이 파일 전체의 sha256**이다. 값 하나만 남기면 달라졌을 때 어느
파일이 달라졌는지 알 수 없으므로 manifest를 함께 커밋한다.

절차와 포함·제외 규칙은 [model-serving.md](../../docs/model-serving.md) §5에 있다.

**이 파일을 읽는 코드는 `src/`에 없다 — orphan이 아니라 그것이 설계다.** 쓰는 쪽은
`tools/model_checksum.py` 하나뿐이고, 읽는 쪽은 **스냅샷을 가진 장비의 사람**이다
(위 `sha256sum -c`). 하네스는 registry의 한 줄만 보고 `RunRecord.model_checksums`에
넣는다(`src/config.py`) — 4.9 GB 스냅샷이 없는 CI에서는 어차피 검증할 대상이 없다.
그래서 이 디렉터리는 **런타임 계약이 아니라 provenance 기록**이다.

첫 실례는 `qwen3-4b`였다(2026-09-24 로스터 밖 정리로 키와 manifest를 지웠다). 지금 남은 예:

```
$ sha256sum config/checksums/gpt-oss-20b.txt
b57ed507ba1b9331067add44a7ec7ed6c9c91b8daa0b1a0bf187d0c5be260612
$ grep 'checksum:' -A0 config/models.yaml   # gpt-oss-20b
checksum: "sha256:b57ed507ba1b9331067add44a7ec7ed6c9c91b8daa0b1a0bf187d0c5be260612"
```
