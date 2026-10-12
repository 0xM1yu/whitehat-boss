/*
 * Verbatim fragment of file(1)/libmagic src/apprentice.c (BSD-2-Clause, Copyright (c) Ian F.
 * Darwin 1986-1995 and later maintainers; license at the top of the original), lines 2961-2982
 * of apprentice_map(): a `#ifdef QUICK` mmap branch, the `#else` branch with a syscall read, and
 * a close. Used by tests/test_input_context.py (SOURCE_READ must match the read, not the close)
 * and tests/test_slicer_target.py (activating_defines). Not compiled.
 */
	map->len = (size_t)st.st_size;
#ifdef QUICK
	map->type = MAP_TYPE_MMAP;
	if ((map->p = mmap(0, (size_t)st.st_size, PROT_READ|PROT_WRITE,
	    MAP_PRIVATE|MAP_FILE, fd, (off_t)0)) == MAP_FAILED) {
		file_error(ms, errno, "cannot map `%s'", dbname);
		goto error;
	}
#else
	map->type = MAP_TYPE_MALLOC;
	if ((map->p = CAST(void *, malloc(map->len))) == NULL) {
		file_oomem(ms, map->len);
		goto error;
	}
	if (read(fd, map->p, map->len) != (ssize_t)map->len) {
		file_badread(ms);
		goto error;
	}
#define RET	1
#endif
	(void)close(fd);
	fd = -1;
