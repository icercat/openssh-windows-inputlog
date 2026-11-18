/*
 * Windows OpenSSH input-recording extension. SPDX-License-Identifier: ISC
 *
 * Permission to use, copy, modify, and distribute this software for any
 * purpose with or without fee is hereby granted.
 * THE SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND.
 *
 * This file deliberately uses only Win32 and the C runtime so the writer
 * can be tested independently of authentication and the SSH transport.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
#include <sddl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#include "session-input-log.h"

struct w32_input_log {
    HANDLE file;
    unsigned long long sequence;
    int broken;
};

static char *
hex_string(const unsigned char *data, size_t length)
{
    static const char digits[] = "0123456789abcdef";
    char *hex;
    size_t i;

    if (length > (SIZE_MAX - 1) / 2 || (data == NULL && length != 0)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    if ((hex = malloc(length * 2 + 1)) == NULL) {
        SetLastError(ERROR_OUTOFMEMORY);
        return NULL;
    }
    for (i = 0; i < length; i++) {
        hex[i * 2] = digits[data[i] >> 4];
        hex[i * 2 + 1] = digits[data[i] & 15];
    }
    hex[length * 2] = '\0';
    return hex;
}

static void
utc_timestamp(char stamp[32])
{
    SYSTEMTIME now;
    GetSystemTime(&now);
    snprintf(stamp, 32, "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ",
        now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute,
        now.wSecond, now.wMilliseconds);
}

static int
write_record(struct w32_input_log *log, const char *record, size_t length)
{
    DWORD written;
    size_t offset = 0;

    if (log == NULL || log->broken) {
        SetLastError(ERROR_WRITE_FAULT);
        return -1;
    }
    while (offset < length) {
        if (!WriteFile(log->file, record + offset,
            (DWORD)(length - offset), &written, NULL) || written == 0) {
            log->broken = 1;
            return -1;
        }
        offset += written;
    }
    /* Acknowledge to the unprivileged child only after a durable write. */
    if (!FlushFileBuffers(log->file)) {
        log->broken = 1;
        return -1;
    }
    return 0;
}

int
w32_input_log_open(struct w32_input_log **result, const char *directory,
    const char *user, const char *remote, unsigned int port)
{
    struct w32_input_log *log = NULL;
    PSECURITY_DESCRIPTOR descriptor = NULL;
    SECURITY_ATTRIBUTES attributes = {sizeof(attributes), NULL, FALSE};
    unsigned char random[16];
    char stamp[32], *random_hex = NULL, *user_hex = NULL, *remote_hex = NULL;
    char *header = NULL;
    wchar_t *path = NULL;
    DWORD error = ERROR_INVALID_PARAMETER, file_attributes;
    int count, n;
    size_t capacity;

    if (result == NULL)
        return -1;
    *result = NULL;
    if (directory == NULL || user == NULL || remote == NULL)
        goto fail;
    /* Require an administrator-selected absolute local path, not user CWD. */
    count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
        directory, -1, NULL, 0);
    if (count < 4 || (path = calloc((size_t)count + 100,
        sizeof(*path))) == NULL)
        goto fail;
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
        directory, -1, path, count))
        goto fail;
    if (!((path[0] >= L'A' && path[0] <= L'Z') ||
        (path[0] >= L'a' && path[0] <= L'z')) || path[1] != L':' ||
        (path[2] != L'\\' && path[2] != L'/'))
        goto fail;
    file_attributes = GetFileAttributesW(path);
    if (file_attributes == INVALID_FILE_ATTRIBUTES) {
        error = GetLastError();
        goto fail;
    }
    if (!(file_attributes & FILE_ATTRIBUTE_DIRECTORY) ||
        (file_attributes & FILE_ATTRIBUTE_REPARSE_POINT))
        goto fail;
    if (BCryptGenRandom(NULL, random, sizeof(random),
        BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0)
        goto fail;
    random_hex = hex_string(random, sizeof(random));
    user_hex = hex_string((const unsigned char *)user, strlen(user));
    remote_hex = hex_string((const unsigned char *)remote, strlen(remote));
    if (random_hex == NULL || user_hex == NULL || remote_hex == NULL) {
        error = GetLastError();
        goto fail;
    }
    /* Protected DACL: SYSTEM and Administrators only. No inherited grants. */
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
        L"D:P(A;;FA;;;SY)(A;;FA;;;BA)", SDDL_REVISION_1,
        &descriptor, NULL)) {
        error = GetLastError();
        goto fail;
    }
    attributes.lpSecurityDescriptor = descriptor;
    if ((log = calloc(1, sizeof(*log))) == NULL) {
        error = ERROR_OUTOFMEMORY;
        goto fail;
    }
    log->file = INVALID_HANDLE_VALUE;
    n = (int)wcslen(path);
    _snwprintf_s(path + n, (size_t)count + 100 - n, _TRUNCATE,
        L"\\input-%lu-%hs.jsonl", GetCurrentProcessId(), random_hex);
    log->file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ,
        &attributes, CREATE_NEW, FILE_ATTRIBUTE_NORMAL |
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_WRITE_THROUGH, NULL);
    if (log->file == INVALID_HANDLE_VALUE) {
        error = GetLastError();
        goto fail;
    }
    capacity = strlen(user_hex) + strlen(remote_hex) + 256;
    if ((header = malloc(capacity)) == NULL) {
        error = ERROR_OUTOFMEMORY;
        goto fail;
    }
    utc_timestamp(stamp);
    n = snprintf(header, capacity,
        "{\"version\":1,\"event\":\"connection\",\"time\":\"%s\","
        "\"user_hex\":\"%s\",\"remote_hex\":\"%s\",\"remote_port\":%u}\n",
        stamp, user_hex, remote_hex, port);
    if (n < 0 || (size_t)n >= capacity ||
        write_record(log, header, (size_t)n) != 0) {
        error = GetLastError();
        goto fail;
    }
    free(header);
    free(path);
    free(random_hex);
    free(user_hex);
    free(remote_hex);
    LocalFree(descriptor);
    *result = log;
    return 0;

fail:
    free(header);
    free(path);
    free(random_hex);
    free(user_hex);
    free(remote_hex);
    LocalFree(descriptor);
    w32_input_log_close(log);
    SetLastError(error);
    return -1;
}

int
w32_input_log_record(struct w32_input_log *log, unsigned int event,
    unsigned int channel, const unsigned char *data, size_t length)
{
    static const char *names[] = {NULL, "shell", "exec", "stdin", "close",
        "effective-command"};
    char stamp[32], *hex = NULL, *record = NULL;
    size_t capacity;
    int n, result = -1;
    DWORD error;

    if (log == NULL || event < W32_INPUT_LOG_SHELL ||
        event > W32_INPUT_LOG_EFFECTIVE || length > W32_INPUT_LOG_MAX_CHUNK ||
        (data == NULL && length != 0) ||
        ((event == W32_INPUT_LOG_SHELL || event == W32_INPUT_LOG_CLOSE) &&
        length != 0)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return -1;
    }
    if ((hex = hex_string(data, length)) == NULL)
        return -1;
    capacity = strlen(hex) + 256;
    if ((record = malloc(capacity)) == NULL) {
        free(hex);
        SetLastError(ERROR_OUTOFMEMORY);
        return -1;
    }
    utc_timestamp(stamp);
    n = snprintf(record, capacity,
        "{\"event\":\"%s\",\"time\":\"%s\",\"sequence\":%llu,"
        "\"channel\":%u,\"bytes\":%zu,\"hex\":\"%s\"}\n",
        names[event], stamp, log->sequence, channel, length, hex);
    if (n >= 0 && (size_t)n < capacity) {
        result = write_record(log, record, (size_t)n);
        if (result == 0)
            log->sequence++;
    } else
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
    error = GetLastError();
    free(record);
    free(hex);
    SetLastError(error);
    return result;
}

void
w32_input_log_close(struct w32_input_log *log)
{
    if (log != NULL) {
        if (log->file != INVALID_HANDLE_VALUE)
            CloseHandle(log->file);
        free(log);
    }
}
