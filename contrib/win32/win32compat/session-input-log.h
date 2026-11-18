/* Windows OpenSSH input-recording extension. SPDX-License-Identifier: ISC */
#ifndef W32_SESSION_INPUT_LOG_H
#define W32_SESSION_INPUT_LOG_H

#include <stddef.h>

#define W32_INPUT_LOG_MAX_CHUNK (16 * 1024)
#define W32_INPUT_LOG_SHELL 1
#define W32_INPUT_LOG_EXEC 2
#define W32_INPUT_LOG_STDIN 3
#define W32_INPUT_LOG_CLOSE 4
#define W32_INPUT_LOG_EFFECTIVE 5

struct w32_input_log;

/* Called only by the privileged, post-authentication monitor. */
int w32_input_log_open(struct w32_input_log **, const char *, const char *,
    const char *, unsigned int);
int w32_input_log_record(struct w32_input_log *, unsigned int, unsigned int,
    const unsigned char *, size_t);
void w32_input_log_close(struct w32_input_log *);

#endif
