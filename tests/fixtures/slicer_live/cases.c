/* Analysis input only. These functions are never executed.
 * The leading comment must not leak through a synthetic global CPG node.
 */
#include <stdlib.h>

extern void consume(const char *data, int count);

void release_second(void *ignored, void *victim)
{
    free(victim);
}

void double_release(void *p)
{
    release_second(0, p);
    free(p);
}

void different_arguments(void *p, void *q)
{
    release_second(p, q);
    free(p);
}

void shadowed_release(void *p)
{
    free(p);
    {
        void *p = malloc(8);
        free(p);
    }
}

void array_entry(int count)
{
    char payload[] = {
        1, 2, 3
    };
    consume(payload, count);
}
