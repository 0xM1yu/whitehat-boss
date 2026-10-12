/*
 * Verbatim excerpt of file(1)/libmagic src/apprentice.c (BSD-2-Clause, Copyright (c) Ian F.
 * Darwin 1986-1995 and later maintainers; the full license text is at the top of the original).
 * Source: a CyberGym task checkout, src-vul/file/src/apprentice.c. Taken by line range, nothing
 * retyped: lines 66-67 (LOWCASE), lines 1651-1666 (parse_op_modifier), lines 2801-2813 (hextoint), lines 2879-2900 (eatsize).
 * Used by tests/test_input_context.py as real input-decoding code (CHAR_TEST / SOURCE_READ pins,
 * the indexer, input_context selection). Not compiled.
 */

#define LOWCASE(l) (isupper((unsigned char) (l)) ? \
			tolower((unsigned char) (l)) : (l))

private void
parse_op_modifier(struct magic_set *ms, struct magic *m, const char **lp,
    int op)
{
	const char *l = *lp;
	char *t;
	uint64_t val;

	++l;
	m->mask_op |= op;
	val = (uint64_t)strtoull(l, &t, 0);
	l = t;
	m->num_mask = file_signextend(ms, m, val);
	eatsize(&l);
	*lp = l;
}

private int
hextoint(int c)
{
	if (!isascii((unsigned char) c))
		return -1;
	if (isdigit((unsigned char) c))
		return c - '0';
	if ((c >= 'a') && (c <= 'f'))
		return c + 10 - 'a';
	if (( c>= 'A') && (c <= 'F'))
		return c + 10 - 'A';
	return -1;
}

private void
eatsize(const char **p)
{
	const char *l = *p;

	if (LOWCASE(*l) == 'u') 
		l++;

	switch (LOWCASE(*l)) {
	case 'l':    /* long */
	case 's':    /* short */
	case 'h':    /* short */
	case 'b':    /* char/byte */
	case 'c':    /* char/byte */
		l++;
		/*FALLTHROUGH*/
	default:
		break;
	}

	*p = l;
}
