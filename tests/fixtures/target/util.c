/* benign 샘플.
 *
 * 길이 상한을 지키고 널 종료를 보장한다. 정적분석 규칙에 걸리지 않고 위험 호출도
 * 없으므로 Scout 임계값 아래에 머문다 — 이 파일이 걸러지는 것이 픽스처의 핵심이다.
 * 걸러지지 않으면 제거율이 100%가 되어 "파이프라인이 후보를 거른다"는 주장이 깨진다.
 *
 * 컴파일하거나 실행하지 않는다. 분석 대상 텍스트일 뿐이다.
 */
#include <stddef.h>
#include <string.h>

#define NAME_MAX_LEN 63

void copy_name(char *dst, size_t dst_size, const char *src)
{
    size_t limit;

    if (dst == NULL || src == NULL || dst_size == 0) {
        return;
    }
    limit = dst_size - 1;
    strncpy(dst, src, limit);
    dst[limit] = '\0';
}

size_t name_length(const char *name)
{
    return strnlen(name, NAME_MAX_LEN);
}
