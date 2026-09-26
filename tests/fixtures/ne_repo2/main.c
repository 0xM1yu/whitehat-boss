#define MAX_ITEMS 8

struct header {
    int len;
    char *buf;
};

typedef struct {
    int x;
    struct header *h;
} Box;

static char *g_buf = 0;
static Box g_default_box;

int helper_a(int x)
{
    return x + 1;
}

void use_box(Box *b, int n)
{
    int i = 0;
    i = n;
    b->h->len = n;
    helper_a(i);
}

void call_use_box(void)
{
    Box box;
    use_box(&box, 5);
}

void assign_global(void)
{
    g_buf = 0;
}

extern void undefined_sink(int value);

void call_outside_repo(int value)
{
    undefined_sink(value);
}
