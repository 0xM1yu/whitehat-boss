셋 다 확인했습니다. 결론부터: 1번은 이미 그 형태로 서 있고 위치가 스키마가 아니라 config 경계입니다. 2·3번은 지적이 맞고, 다만 제안하신 assert는 현재 경로에서 죽은 코드가 됩니다.

## 1. 검증 — "새 쓰기만 검증하고 옛 읽기는 허용"이 이미 그 구조입니다

`RunRecord.profile`에 들어가는 값은 `config.profile_name` 하나뿐입니다. 그리고 `HarnessConfig`를 새로 **읽어 만드는** 곳은 `load_config` 하나입니다(`src/config.py:417`). `--seed`·`--tool-runner` 오버라이드는 `transition()`으로 기존 인스턴스를 재검증 복사하는데, `profile_name`은 `model_dump()`에서 그대로 실려 옵니다 — 그 값을 **바꾸는** 대입은 저장소에 없습니다(`grep -rn 'profile_name' src/` → 대입은 `config.py:418` 한 건).

그 함수가 `src/config.py:401-402`에서 이렇게 거릅니다:

```python
if profile not in profiles:
    raise ConfigError(f"프로필 {profile!r}이 없다. 있는 것: {sorted(profiles)}")
```

그래서 `config.profile_name`은 **`config/profiles.yaml`의 키였음이 증명된 문자열**입니다. 오타나 개명된 이름은 run이 시작되기 전에 죽고 `RunRecord`까지 도달할 경로가 없습니다. 이미 `tests/test_config.py:188`의 `test_unknown_profile_fails_loudly`가 그걸 못 박고 있습니다.

옛 읽기가 허용되는 것도 자동으로 따라옵니다 — 검증이 로드 경계에 있으니 이미 쓰인 manifest를 파싱할 때는 걸리지 않습니다.

스키마에 `Literal`이나 enum을 두지 않은 이유는 따로 있습니다. `config/profiles.yaml`이 데이터라서 프로필을 하나 추가할 때마다 스키마 코드를 같이 고쳐야 하고 그 둘은 어긋날 수 있습니다. 지금은 YAML이 단일 원천입니다.

## 2. 인정합니다 — 다만 러너 경로는 타입으로 막힙니다

"현재 코드 흐름의 보장이지 타입 보장이 아니다"는 맞습니다. 정확히 어디까지가 타입인지만 적어 두겠습니다.

`HarnessConfig.profile_name`은 `src/config.py:263`에서 `NonEmptyStr`이고 기본값이 없는 필수 필드입니다. 그래서 `profile=config.profile_name`이 `None`을 낼 수 없는 것은 관례가 아니라 타입입니다. 남는 것은 지적하신 그대로 **`_new_record`를 지나지 않는 새 `RunRecord` 생성 경로**가 생기는 경우고 그건 타입으로 막히지 않습니다.

## 3. assert는 지금 자리에서는 뜨지 않습니다

2번의 이유로 러너 경로에서는 `profile`이 `None`이 될 수 없어서 `_new_record`에 넣는 assert는 실행되자마자 항상 참인 죽은 단언이 됩니다.

`incomplete_fields()`도 같습니다. 호출 지점이 `src/orchestrator/runner.py:1103` 하나뿐이고 거기서 보는 것은 **러너가 방금 만든 record**입니다. 옛 manifest를 읽어 판정하는 경로는 지금 없습니다. 그래서 `profile is None`을 넣으면 영영 안 뜨는 줄이 하나 늘어납니다 — `tool_runner`를 뺀 것과 같은 자리입니다.

말씀하신 사각지대가 실제로 신호를 낼 수 있는 자리는 **옛 manifest를 로드해 판정하는 코드가 생기는 시점**입니다. 그때는 `profile: None`이 "이 run은 비교의 기준축을 안 남겼다"라는 뜻 있는 경고가 됩니다. 그 경로가 생길 때 같이 넣는 게 맞다고 봅니다. 지금 넣자고 하시면 넣겠습니다만, 그러면 뜨지 않는 경고를 하나 만드는 것이라 판단을 남겨 둡니다.
