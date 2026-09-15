#include <stdlib.h>
#include <string.h>
extern char *get_input(void);
extern int choose(void);
extern char *relay(char *value);

void release_second(int unused, void *value) { free(value); }

void parameterless(void)
{
    char first[16], second[16];
    char *tainted = get_input();
    char *independent = "constant";
    strcpy(first, relay(tainted)); strcpy(second, independent);
    char *p = malloc(32);
    release_second(0, p);
    release_second(1, p);
}

void control(char *input, int op)
{
    char dst[8];
    switch (op) {
    case 1:
        if (choose()) break;
        strcpy(dst, input);
        break;
    case 2:
    case 3:
        strcpy(dst, input);
        /* Deliberate fall-through. */
    default:
        break;
    }
    for (int i = 0; i < op; ++i) {
        if (i == 1) continue;
        strcpy(dst, input);
        if (i == 3) break;
    }
    if (op == 7) goto cleanup;
    strcpy(dst, input);
cleanup:
    return;
}
