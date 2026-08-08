/* 취약 샘플.
 *
 * 외부 입력의 길이를 검사하지 않고 고정 크기 스택 버퍼에 복사한다.
 * 정적분석 규칙(길이가 상수가 아닌 memcpy)에 걸리고 위험 호출 목록에도 있으므로
 * Scout 임계값을 넘어 5단계로 올라간다.
 *
 * 컴파일하거나 실행하지 않는다. 분석 대상 텍스트일 뿐이다.
 */
#include <stdint.h>
#include <string.h>

struct header {
    uint8_t  type;
    uint16_t length;
};

void handle_header(struct header *hdr);

void parse_header(const char *src, int len)
{
    char buf[64];

    /* len이 sizeof(buf)와 비교되지 않는다. */
    memcpy(buf, src, len);
    handle_header((struct header *)buf);
}

void recv_request(const char *packet, int size)
{
    parse_header(packet + 2, size - 2);
}
