/* gif_lib.h fixture -- need_evidence resolver — Scout RAG baseline 명세 hop 1 예의 줄 번호에 맞춘 합성 파일(filler 줄은 자리 맞춤). */

#ifndef GIF_LIB_FIXTURE_H
#define GIF_LIB_FIXTURE_H

#define GIF_STAMP "GIFVER"
/* filler 7 */
/* filler 8 */
/* filler 9 */
/* filler 10 */
/* filler 11 */
/* filler 12 */
/* filler 13 */
/* filler 14 */
/* filler 15 */
/* filler 16 */
#define GIF_STAMP_LEN sizeof(GIF_STAMP) - 1

typedef struct GifFileType {
    int UserData;
} GifFileType;

int DGifGetRecordType(GifFileType *GifFile, int *Type);

#endif
