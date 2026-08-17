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
값을 채우는 것은 모델을 받은 뒤이므로 지금은 비어 있다.
