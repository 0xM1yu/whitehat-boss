# Fixed Vulnerable/Normal Contrast Cases

These are independent, self-authored educational examples for few-shot contrast. They are shared with every v4 profile, are not RAG results, are not FACTS evidence, and do not establish any target answer. Their locations and labels must never be copied into a target Candidate.

The evaluation set was not supplied, so duplicate/similarity checking against the evaluation corpus is **INCOMPLETE**. These examples must be reviewed for independence before any accuracy claim.

## `example_bounds_001`

- `example_id`: `example_bounds_001`
- `origin`: `self-authored educational example; not copied from the evaluation target`
- `weak_code`:

  ```c
  #include <stddef.h>
  #include <string.h>

  void copy_name(const char *input, size_t n) {
      char name[8];
      memcpy(name, input, n);
  }
  ```

- `normal_code`:

  ```c
  #include <stddef.h>
  #include <string.h>

  void copy_name(const char *input, size_t n) {
      char name[8];
      if (n <= sizeof name) {
          memcpy(name, input, n);
      }
  }
  ```

- `differentiating_condition`: Under the explicit premises that `name` has capacity exactly 8 and `input` is valid for the requested count, the weak pair has no visible relation proving `n <= 8`; the normal pair checks that relation before copying.
- `applicable_scope`: Useful only when the destination extent, copy count, terminator requirement, and relevant path are known. `n` may be outside `0..8` in the weak case; that fact is a premise for comparison, not an assumed target fact.
- `do_not_generalize`: `memcpy` or a similar API name alone does not prove a flaw; a hidden caller, allocation contract, or guard must not be invented.

## `example_integer_001`

- `example_id`: `example_integer_001`
- `origin`: `self-authored educational example; not copied from the evaluation target`
- `weak_code`:

  ```c
  #include <stddef.h>
  #include <stdint.h>
  #include <stdlib.h>

  struct item { unsigned char data[16]; };
  void *allocate_items(size_t count) {
      size_t bytes = (size_t)(count * sizeof(struct item));
      void *items = malloc(bytes);
      return items;
  }
  ```

- `normal_code`:

  ```c
  #include <stddef.h>
  #include <stdint.h>
  #include <stdlib.h>

  struct item { unsigned char data[16]; };
  void *allocate_items(size_t count) {
      if (count > SIZE_MAX / sizeof(struct item)) return NULL;
      size_t bytes = count * sizeof(struct item);
      void *items = malloc(bytes);
      if (items == NULL) return NULL;
      return items;
  }
  ```

- `differentiating_condition`: With `size_t` count and the explicit `struct item` size, the weak pair performs multiplication without a preceding representability check; the normal pair checks the range before multiplication and handles allocation failure.
- `applicable_scope`: Requires the actual operand types, widths, range, allocation contract, failure handling, and security-relevant consumer to be visible.
- `do_not_generalize`: A cast to `size_t`, a large constant, or a `malloc` call alone does not establish overflow or an exploitable result.

## `example_injection_001`

- `example_id`: `example_injection_001`
- `origin`: `self-authored educational example; not copied from the evaluation target`
- `weak_code`:

  ```c
  #include <stdio.h>
  #include <stdlib.h>
  #include <string.h>

  void list_file(const char *name) {
      char command[128];
      snprintf(command, sizeof command, "ls %s", name);
      system(command);
  }
  ```

- `normal_code`:

  ```c
  #include <string.h>
  #include <unistd.h>

  void list_file(const char *name) {
      if (strcmp(name, "readme.txt") != 0 && strcmp(name, "notes.txt") != 0) return;
      execl("/bin/ls", "ls", "--", name, (char *)0);
  }
  ```

- `differentiating_condition`: With `name` supplied as `argv[1]`, the weak pair places it into a command string interpreted by `system`; the normal pair accepts only two explicit filenames and passes the selected filename as a separate `execl` argument. These are the pair's command-injection defenses; the example still requires an external-flow and API-contract analysis.
- `applicable_scope`: Assumes POSIX `execl`, a NUL-terminated input string, and the stated allowlist. It does not claim that the normal function is safe for arbitrary filenames or for every program behavior.
- `do_not_generalize`: The presence of `system`, `execl`, or a string operation alone is not a target finding; do not infer attacker control or sanitization behavior.
