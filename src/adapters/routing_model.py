"""RoutingModelClient — 역할별로 서로 다른 ModelClient에 분배한다.

model-serving.md §6의 게이트웨이 그림(역할 → vLLM #1/#2/#3)을 코드로 옮긴 것이다.
분배가 별도 클래스인 이유는 **한 프로필 안에서 실서빙과 Fake가 섞이기 때문**이다.
옛 `single_gpu_12g`(2026-09-24 삭제)는 scout만 실제 모델을 쓰고 나머지 전 역할이 Fake였는데,
이 구성은 어댑터를 프로필 단위로 하나 고르는 구조로는 표현되지 않는다.

어느 역할이 어느 구현으로 가는지는 `ModelEntry.endpoint` 스킴이 정하고, 조립은
`factory.py`가 한다. 이 클래스는 이미 만들어진 클라이언트 맵만 받는다 — 구체 어댑터를
import 하지 않으므로 새 백엔드가 생겨도 여기는 그대로다.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from src.interfaces.model_client import ChatTurn, ModelClient, ModelResult, Prompt
from src.schemas.enums import Role


class UnknownRoleError(KeyError):
    """프로필에 없는 역할로 모델을 호출했다."""


class RoutingModelClient:
    """`ModelClient` 구현. 역할 → 하위 클라이언트."""

    def __init__(self, clients: dict[str, ModelClient]) -> None:
        self._clients = dict(clients)

    def client_for(self, role: Role | str) -> ModelClient:
        key = role.value if isinstance(role, Role) else role
        client = self._clients.get(key)
        if client is None:
            raise UnknownRoleError(
                f"역할 {key!r}에 매핑된 모델이 없다. 있는 것: {sorted(self._clients)}"
            )
        return client

    def complete(
        self,
        *,
        role: Role,
        prompt: Prompt,
        schema: type[BaseModel],
        history: Sequence[ChatTurn] = (),
    ) -> ModelResult:
        # 자기 검사를 두지 않는다 — history를 그대로 위임하고 실제 판정(비어 있으면
        # 단일 턴, 있으면 멀티턴으로 답하는 것)은 하위 클라이언트가 한다. 구현체
        # (`fake_model.py`·`openai_compat.py`)와 테스트 더블 전부가 `history=()`를 받으므로,
        # 비어 있을 때만 키워드를 감추던 자리가 더는 필요 없다.
        client = self.client_for(role)
        return client.complete(role=role, prompt=prompt, schema=schema, history=history)

    @property
    def roles(self) -> list[str]:
        return sorted(self._clients)
