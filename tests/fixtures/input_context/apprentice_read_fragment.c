/*
 * Copyright (c) Ian F. Darwin 1986-1995.
 * Software written by Ian F. Darwin and others;
 * maintained 1995-present by Christos Zoulas and others.
 * 
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice immediately at the beginning of the file, without modification,
 *    this list of conditions, and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *  
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR
 * ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 * OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 * OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 */
/*
 * Verbatim fragment of file(1)/libmagic src/apprentice.c (BSD-2-Clause, Copyright (c) Ian F.
 * Darwin 1986-1995 and later maintainers; license above copied verbatim from the top of the original), lines 2961-2982
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
