# harness-tools:v1 — 파이프라인 2단계·8단계가 쓰는 도구 이미지.
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

ENV DEBIAN_FRONTEND=noninteractive

# clang은 ASAN/UBSAN 런타임(libclang-common-*-dev)을 함께 끌고 온다.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      ca-certificates \
      clang \
      clang-tidy \
      cmake \
      git \
      make \
      python3 \
      python3-pip \
 && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir "semgrep==${SEMGREP_VERSION}"

# 공개 룰셋 중 C만 가져온다. 저장소 전체를 config로 주면 다른 언어의 룰까지 로드되어
# 스캔이 느려지고, 무엇이 신호를 만들었는지도 흐려진다.
RUN git clone --filter=blob:none https://github.com/semgrep/semgrep-rules /tmp/semgrep-rules \
 && git -C /tmp/semgrep-rules checkout "${SEMGREP_RULES_COMMIT}" \
 && mkdir -p /opt/semgrep-rules \
 && cp -r /tmp/semgrep-rules/c /opt/semgrep-rules/c \
 && git -C /tmp/semgrep-rules rev-parse HEAD > /opt/semgrep-rules/COMMIT \
 && rm -rf /tmp/semgrep-rules

# 하네스 전용 룰. 공개 룰셋과 함께 로드된다.
COPY rules/ /opt/semgrep-rules/harness/

# 빌드 시점 연막 검사. ASAN 런타임이 빠진 이미지는 8단계에서야 드러나는데, 그때는
# "재현 실패"와 구분되지 않는다 — 여기서 터지는 편이 훨씬 싸다.
RUN printf 'int main(void){return 0;}\n' > /tmp/smoke.c \
 && clang -fsanitize=address,undefined -o /tmp/smoke /tmp/smoke.c \
 && /tmp/smoke \
 && clang-tidy --version > /dev/null \
 && semgrep scan --config /opt/semgrep-rules/c --config /opt/semgrep-rules/harness \
      --metrics=off --disable-version-check --quiet /tmp/smoke.c \
 && rm -f /tmp/smoke /tmp/smoke.c

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
