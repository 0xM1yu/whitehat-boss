"""조립된 PoC 바이트를 run 산출물에 남긴다 (#355).

`PromptLog`와 같은 모양의 주입 객체다 — run_dir을 아는 것은 러너이고 `RunContext`도 노드도
그것을 몰라야 한다(`context.py`의 `prompts` 필드 주석). `None`이면 아무것도 쓰지 않는다.

**왜 필요한가.** 8단계 PoV 루프는 `assemble_poc`로 바이트를 만들어 반복 판정에 쓰고 **버렸다**.
trace에 남는 것은 길이와 sha256뿐이라, 모델이 낸 PoC를 run이 끝난 뒤 **되살릴 방법이 없었다**.
oracle 제출·재현은 바이트가 있어야 한다.

**기록·회수만 한다 — Evidence로 승격하지 않는다.** 게이트가 `Evidence.ok`만 보므로(#232)
`POC_RUN` kind의 Evidence를 달면 크래시 없이 exit 0으로 끝난 실행에서도 E4가 열린다. 승격은
#232가 풀린 뒤의 일이고, 그때까지 이 모듈은 **우회로이지 해결이 아니다.**

**파일명은 순번뿐이다.** `finding_id`를 쓰지 않는 것은 그것이 모델 출력에서 올 수 있고
`Identifier`가 **길이만** 제약하기 때문이다 — 모델이 고른 값은 파일시스템 경로에 한 조각도
닿지 않는다. finding과의 연결은 trace 이벤트(`subject_id` + `poc_ref`)가 든다.

**바이트로 쓴다.** `write_bytes`는 개행을 번역하지 않는다. 텍스트로 쓰면 Windows에서 파일은
CRLF인데 digest는 LF 본문의 해시가 되어 **digest가 그 파일과 안 맞는다** — `_write`
(`src/adapters/container_tools.py`)가 실제로 그 상태이고 #387이 그것이다. 여기서는 번역이
일어날 길이 없다.

**digest 형식이 trace의 `poc_sha256`과 다르다.** 이쪽은 `sha256_digest`라 `sha256:` 접두가
붙고, 같은 이벤트의 `poc_sha256`은 **접두 없는 맨 hex**다(핀 `tests/test_nodes.py`). 둘을
「통일」하면 그 핀이 깨진다 — 다르게 두는 것이 의도다.

**누출 스캔에 넣지 않는다.** `eval/leakage/__main__.py`의 `RUN_GLOBS`는 대상을
`read_text(..., errors="replace")`로 읽으므로 이진 파일을 넣으면 치환 문자 틈에서 거짓 양성이
난다. 그리고 PoC는 대상 코드에서 흘러든 산문이 아니라 **우리가 조립한 바이트**라 과제 누출의
매개가 아니다. 넣어야 할 근거가 생기면 그때 glob을 더한다(멤버십만 보는 핀이라 추가는 안전하다).
"""

from pathlib import Path

from src.ids import sha256_digest

# run_dir 아래 디렉터리 이름. **`prompts/`가 아니다** — 그 아래 `*.md` 수가 모델 호출 수와
# 같다는 핀(`tests/test_prompt_log.py`)이 있어 다른 것을 끼우면 그 수가 어긋난다.
DIR_NAME = "poc"

# 파일명 순번 폭. `prompt_log.SEQ_WIDTH`와 같은 값이지만 한쪽을 바꿀 이유가 다른 쪽에 번지지
# 않게 따로 둔다(그쪽 주석과 같은 이유).
SEQ_WIDTH = 4


class PocLog:
    """`{run_dir}/poc/`에 PoC 하나당 파일 하나(`0001.bin`).

    **순번은 이 객체가 센다** — `PromptLog`와 같은 이유로 trace의 `seq`도 `IdFactory`도 쓰지
    않는다. v1 전제는 순차 실행이고, 단계를 동시에 돌리게 되면 여기 카운터에도 락이 필요하다.

    크기 상한은 이쪽이 아니라 `schemas/pov.py`의 `_MAX_POC_BYTES`가 조립 전에 건다 — 검증을
    통과한 `PovSpec`만 `assemble_poc`에 들어가므로 여기 오는 바이트는 이미 그 아래다.
    """

    def __init__(self, run_dir: Path) -> None:
        self._dir = Path(run_dir) / DIR_NAME
        self._seq = 0

    @property
    def directory(self) -> Path:
        return self._dir

    def write(self, poc: bytes) -> tuple[str, str]:
        """파일을 쓰고 `(run_dir 상대 경로, 바이트 sha256)`을 돌려준다.

        ref에 `run_id`를 넣지 않는다 — 넣으면 trace의 비교 단위가 run마다 달라진다.
        """
        self._seq += 1
        name = f"{self._seq:0{SEQ_WIDTH}d}.bin"
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / name).write_bytes(poc)
        return f"{DIR_NAME}/{name}", sha256_digest(poc)
