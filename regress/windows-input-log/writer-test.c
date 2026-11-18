/* Windows input recorder integration tests. SPDX-License-Identifier: ISC */
#include "../../contrib/win32/win32compat/session-input-log.c"

#define CHECK(x) do { if (!(x)) { \
    fprintf(stderr, "line %d failed (Win32 %lu): %s\n", \
        __LINE__, GetLastError(), #x); return 1; } } while (0)

int main(int argc, char **argv)
{
    struct w32_input_log *log = NULL, *other = NULL;
    unsigned char data[W32_INPUT_LOG_MAX_CHUNK];
    unsigned char editing[] = {'d','i','x',8,'r',13,0,27,'[','A',0xe4,0xb8,0xad};
    size_t i;
    DWORD flags;

    CHECK(argc == 2);
    CHECK(w32_input_log_open(&log, "relative", "u", "::1", 22) == -1);
    CHECK(log == NULL);
    CHECK(w32_input_log_open(&log, argv[1], "test\"user\n", "100.90.80.70", 12345) == 0);
    CHECK(log != NULL);
    CHECK(GetHandleInformation(log->file, &flags));
    CHECK((flags & HANDLE_FLAG_INHERIT) == 0);
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_SHELL, 1, NULL, 0) == 0);
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_EXEC, 2,
        (const unsigned char *)"whoami", 6) == 0);
    for (i = 0; i < sizeof(data); i++)
        data[i] = (unsigned char)i;
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_STDIN, 1, data, sizeof(data)) == 0);
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_STDIN, 1, editing, sizeof(editing)) == 0);
    CHECK(w32_input_log_record(log, 99, 1, NULL, 0) == -1);
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_STDIN, 1, data, sizeof(data) + 1) == -1);
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_STDIN, 1, NULL, 1) == -1);
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_CLOSE, 1, data, 1) == -1);
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_CLOSE, 1, NULL, 0) == 0);
    w32_input_log_close(log);
    log = NULL;

    /* Concurrent connections must get distinct files, including one PID. */
    CHECK(w32_input_log_open(&log, argv[1], "a", "::1", 1) == 0);
    CHECK(w32_input_log_open(&other, argv[1], "b", "::1", 2) == 0);
    CHECK(log->file != other->file);
    /* A real write failure must poison the writer and never acknowledge data. */
    CHECK(CloseHandle(log->file));
    log->file = INVALID_HANDLE_VALUE;
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_STDIN, 1, data, 1) == -1);
    CHECK(log->broken == 1);
    CHECK(w32_input_log_record(log, W32_INPUT_LOG_STDIN, 1, data, 1) == -1);
    w32_input_log_close(log);
    w32_input_log_close(other);
    puts("PASS: native input writer, binary/UTF-8/editing input, limits, unique files, non-inheritance and I/O failure");
    return 0;
}
