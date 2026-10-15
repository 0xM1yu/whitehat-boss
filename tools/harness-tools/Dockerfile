# harness-tools:v3 — 파이프라인 1·2·8단계가 쓰는 도구 이미지.
#
# v3 = v2의 index-v2에 비대화형 gdb를 더한다. v1·v2 이미지는 지우지 않는다 — 지난 run
# 매니페스트의 `tool_images`가 그 digest를 적었다.
# ⏵ 2026-10-04 — #304부터 이 트리로 빌드한 이미지의 인덱서는 **index-v3**를 낸다(태그는 그대로
# v3). 그 전에 빌드한 `harness-tools:v3`(sif 포함)는 index-v2를 내고, 그러면 v3 조회
# (`writes_of`·`fn_pointer_targets` 등)가 `None`이다 — **다시 빌드해야 한다.** 같은 태그에 두
# 내용이 있는 셈이라 run끼리는 `tool_images` digest로 가른다. 태그를 올릴지는 아직 정하지
# 않았다(#464 리뷰). 아래 인덱서 연막 검사가 버전과 v3 표를 보므로 낡은 사본을 구우면 빌드가 죽는다.
# ⏵ 2026-10-04 — 인덱서가 **index-v4**(`includes`·`enum_constants`)가 됐다. 그 전에 빌드한 이미지는
# v3를 내고 정의 조회가 옛 규칙(include 도달성·enum 상수 없음)으로 돈다 — 다시 빌드한다.
#
# **태그를 latest로 두지 않는다.** 이 이미지의 digest가 `RunRecord.tool_images`에 박히므로
# 버전이 고정돼야 두 run의 비교가 성립한다(evaluation.md §6).
#
# **semgrep 룰셋을 빌드 시점에 굽는다.** 실행 시점에는 `--network=none`이라 레지스트리
# (`--config=p/c`)를 볼 수 없고, 우회는 없다 — `network=True`는 disclosure-policy.md 위반이라
# ToolRunner가 거부해야 하는 값이다. 룰셋이 이미지 안에 있으므로 digest가 도구 버전과
# 룰셋 버전을 함께 고정한다.
FROM ubuntu:22.04

# 재현을 위해 고정한다. 이 값을 바꾸면 tool_images digest가 달라지는데, 그것이 정상이다 —
# 도구가 바뀌었으면 다른 이미지다.
ARG SEMGREP_VERSION=1.86.0
# 기본값은 브랜치지만 해석된 커밋을 이미지 안에 남긴다(/opt/semgrep-rules/COMMIT).
# 같은 룰셋으로 다시 만들려면 그 값을 --build-arg로 넘긴다 (tools/README.md).
ARG SEMGREP_RULES_COMMIT=develop
# 1단계 인덱서가 쓰는 파서. 문법 모듈이 올라가면 같은 코드에서 다른 index가 나올 수 있다.
ARG TREE_SITTER_VERSION=0.26.0
ARG TREE_SITTER_C_VERSION=0.24.2
ARG TREE_SITTER_CPP_VERSION=0.23.4

ENV DEBIAN_FRONTEND=noninteractive

# clang은 ASAN/UBSAN 런타임(libclang-common-*-dev)을 함께 끌고 온다.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      ca-certificates \
      clang \
      clang-tidy \
      cmake \
      gdb \
      git \
      make \
      python3 \
      python3-pip \
 && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir "semgrep==${SEMGREP_VERSION}"

# 공개 룰셋 중 C만 가져온다. 저장소 전체를 config로 주면 다른 언어의 룰까지 로드되어
# 스캔이 느려지고, 무엇이 신호를 만들었는지도 흐려진다.
# C 룰을 C++ 파일에도 건다(#689) — 공개 룰셋에는 C++ 디렉터리가 없고 `languages: [c]`면
# `.cc`·`.cpp`·`.hpp`를 아예 안 연다. 형식이 바뀌어 치환이 안 먹으면 빌드를 멈춘다(조용히 C만 남지 않게).
RUN git clone --filter=blob:none https://github.com/semgrep/semgrep-rules /tmp/semgrep-rules \
 && git -C /tmp/semgrep-rules checkout "${SEMGREP_RULES_COMMIT}" \
 && mkdir -p /opt/semgrep-rules \
 && cp -r /tmp/semgrep-rules/c /opt/semgrep-rules/c \
 && find /opt/semgrep-rules/c -name '*.yaml' -exec sed -i 's/languages: \[c\]/languages: [c, cpp]/' {} + \
 && ! grep -rqE 'languages: \[c\]' /opt/semgrep-rules/c \
 && grep -rq 'languages: \[c, cpp\]' /opt/semgrep-rules/c \
 && git -C /tmp/semgrep-rules rev-parse HEAD > /opt/semgrep-rules/COMMIT \
 && rm -rf /tmp/semgrep-rules

# 하네스 전용 룰. 공개 룰셋과 함께 로드된다.
COPY rules/ /opt/semgrep-rules/harness/

# 1단계 인덱서. 버전을 고정하는 이유는 문법 모듈이 올라가면 파싱 결과가 달라질 수 있고,
# 그러면 같은 코드에서 다른 index가 나와 재현성이 깨지기 때문이다. 이미지 digest가
# 이 버전을 함께 고정한다.
RUN pip3 install --no-cache-dir \
      "tree-sitter==${TREE_SITTER_VERSION}" \
      "tree-sitter-c==${TREE_SITTER_C_VERSION}" \
      "tree-sitter-cpp==${TREE_SITTER_CPP_VERSION}"

# ⏵ 2026-10-13 — `indexer.py`는 **주석 한 줄도** 이미지와 바이트가 같아야 한다: `src/preflight.py`가
# 파일 전체 sha256을 이미지 `/opt/indexer.py`와 견주고 다르면 ERROR(exit 2)다(PR #634 HIGH 1).
# 고치면 이미지를 다시 굽고 `tests/test_indexer.py`의 `_BAKED_INDEXER_SHA256` 핀을 올린다.
COPY indexer.py /opt/indexer.py
COPY gdb.batch /opt/gdb.batch

# 빌드 시점 연막 검사. gdb 자체나 ASAN 런타임이 빠진 이미지는 8단계에서야 드러나는데,
# 그때는 "재현 실패"와 구분되지 않는다 — 여기서 터지는 편이 훨씬 싸다.
# 2026-09-28 로컬 Docker에서 ASAN 뒤에 gdb를 둔 순서는 이 RUN이 exit 139로 두 번 끝났고,
# 아래 순서는 전체 빌드를 통과했다. 그 exit 139의 원인은 실측하지 않았다(D92).
RUN printf 'int main(void){return 0;}\n' > /tmp/smoke.c \
 && clang -g -O0 -fno-omit-frame-pointer -o /tmp/gdb-smoke /tmp/smoke.c \
 && gdb --nx -batch --return-child-result -x /opt/gdb.batch -- /tmp/gdb-smoke \
 && clang -fsanitize=address,undefined -o /tmp/smoke /tmp/smoke.c \
 && /tmp/smoke \
 && clang-tidy --version > /dev/null \
 && semgrep scan --config /opt/semgrep-rules/c --config /opt/semgrep-rules/harness \
      --metrics=off --disable-version-check --quiet /tmp/smoke.c \
 && rm -f /tmp/smoke /tmp/gdb-smoke /tmp/smoke.c

# 인덱서 연막 검사. 파서 휠이 이 아키텍처에 없으면 여기서 터진다 — ATOM이 aarch64라
# x86에서만 되는 휠을 물고 있으면 8단계가 아니라 지금 알아야 한다.
# ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340) — 위 「ATOM이 aarch64라」는 그때의 근거다.
# 심볼과 호출이 **실제로 잡히는지**까지 본다. import만 확인하면 문법 모듈이 바뀌어
# 아무것도 못 뽑게 돼도 통과한다. index-v2의 `calls`는 callee → 호출 지점이라 간선 수는
# 값 목록의 길이 합이다(키 수가 아니다). 조회 표도 하나(`macros`) 실제로 차는지 본다.
# 버전과 v3 표(`writes` — 원소 대입 `x[0] = 1`)도 본다. 이미지에 구운 indexer.py가 낡은
# 사본이면 v3 조회가 조용히 `None`이 되기 때문이다(#464 리뷰).
RUN printf '#define N 1\nvoid a(void){}\nvoid b(void){ char x[2]; x[0] = 1; a(); }\n' > /tmp/idx.c \
 && python3 /opt/indexer.py --out /tmp/idx.json --root /tmp /tmp/idx.c \
 && python3 -c "import json,sys; d=json.load(open('/tmp/idx.json')); \
e=sum(map(len, d['calls'].values())); \
sys.exit(0 if len(d['symbols'])==2 and e==1 and 'N' in d['macros'] \
and d['version']=='index-v4' and 'x' in d['writes'] else 1)" \
 && rm -f /tmp/idx.c /tmp/idx.json

# `.inc` 연막 검사. 실측 샘플은 `target.c`가 `#include` 한 줄이고 코드가 전부 `.inc`에
# 있다. 이미지 안 `_GRAMMARS`에서 `.inc`가 빠지면 예외가 아니라 `skipped`에 이름만 남아
# **빈 그래프가 성공으로 기록된다.** 호스트 테스트와 겹치지 않는다 — 이미지에 들어간
# indexer.py 사본과 이 아키텍처의 파서 휠은 호스트가 확인할 수 없다.
RUN printf 'void a(void){}\nvoid b(void){ a(); }\n' > /tmp/idx_core.inc \
 && printf '#include "idx_core.inc"\n' > /tmp/idx_inc.c \
 && python3 /opt/indexer.py --out /tmp/idx_inc.json --root /tmp \
      /tmp/idx_inc.c /tmp/idx_core.inc \
 && python3 -c "import json,sys; d=json.load(open('/tmp/idx_inc.json')); \
e=sum(map(len, d['calls'].values())); \
sys.exit(0 if len(d['symbols'])==2 and e==1 and d['skipped']==[] else 1)" \
 && rm -f /tmp/idx_core.inc /tmp/idx_inc.c /tmp/idx_inc.json

# 대상은 읽기 전용으로 /src에, 쓰기는 /work에만 일어난다(container_tools 모듈 docstring).
#
# 빌드 시점에는 바인드가 없으므로 디렉터리를 만들어 둔다 — 없으면 아래 ENV를 쓰는 도구가
# 빌드 중에 죽는다.
RUN mkdir -p /work

# **쓰기가 필요한 모든 경로를 /work로 모은다.**
#
# TMPDIR: `docker run --read-only`에서는 /tmp도 못 쓰는데 링커와 일부 clang 경로가 임시
# 파일을 만든다. 기본값이면 링크가 "Read-only file system"으로 죽고, 그 실패는 8단계에서
# "재현 실패"와 구분되지 않는다.
#
# HOME: 2026-08-14 첫 dev_docker 실행에서 semgrep이 `$HOME/.semgrep/settings.yml`을 쓰려다
# 같은 이유로 죽었다(처리되지 않은 예외 → exit 1 → "발견함"으로 오집계).
# `SEMGREP_SETTINGS_FILE`로 좁게 막을 수도 있었지만, TMPDIR로 이미 한 번 겪은 **클래스
# 단위 결함**이라 도구별 사후 대응을 반복하지 않는다. 파이썬 계열 도구가 추가돼도 같은
# 방어가 적용된다.
ENV TMPDIR=/work
ENV HOME=/work

WORKDIR /src
