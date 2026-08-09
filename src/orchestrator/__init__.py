"""오케스트레이터 코어.

`src/orchestrator/**`는 `src/adapters/`의 구체 구현을 import 하지 않는다
(engineering-guide.md §3). 의존성은 `src/interfaces/`의 Protocol로만 받는다 —
`tests/test_policy.py::test_orchestrator_source_has_no_adapter_import`가 강제한다.
"""
