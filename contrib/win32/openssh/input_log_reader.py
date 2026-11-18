#!/usr/bin/env python3
"""Decode Windows OpenSSH InputLog1 JSONL recordings."""
import argparse
import base64
import binascii
import codecs
from itertools import groupby
import json
from pathlib import Path
import re


MAX_ENCODED_LINE = 128 * 1024
POWERSHELL_EXE = re.compile(r"""(?<![\w.-])(?:powershell|pwsh)(?:\.exe)?["']?(?=[ \t])""", re.IGNORECASE)
ENCODED_OPTION = re.compile(r'(?<=[ \t])-(?:encodedcommand|enc|ec|e)[ \t]+', re.IGNORECASE)
BASE64_ARGUMENT = re.compile(r"""(?:"([A-Za-z0-9+/=\s]+)"|'([A-Za-z0-9+/=\s]+)'|([A-Za-z0-9+/=]+))(?=$|[\s;&|<>"'])""")
LINE_END = re.compile(r'[\r\n]')


def decode_base64(value, encoding):
    if len(value) > MAX_ENCODED_LINE:
        raise ValueError('Base64 輸入超過 128 KiB。')
    payload = ''.join(value.split())
    if not payload:
        raise ValueError('請輸入 Base64 內容。')
    try:
        data = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('Base64 格式不正確。') from error
    try:
        return data.decode(encoding).removeprefix('\ufeff')
    except UnicodeDecodeError as error:
        raise ValueError(f'解碼結果不是有效的 {encoding} 文字。') from error


def powershell_commands(line):
    if len(line) > MAX_ENCODED_LINE:
        return []
    commands = []
    for segment in re.split(r'[\r\n;&|<>]', line):
        executable = POWERSHELL_EXE.search(segment)
        if executable is None:
            continue
        option = ENCODED_OPTION.search(segment, executable.end())
        if option is None:
            continue
        argument = BASE64_ARGUMENT.match(segment, option.end())
        if argument:
            payload = next(group for group in argument.groups() if group is not None)
            try:
                commands.append(decode_base64(payload, 'utf-16-le'))
            except ValueError:
                continue
    return commands


def decoded_powershell_events(events):
    """Map each detected command to the final event of its input line."""
    results = {}
    pending_stdin = {}

    def scan(index, fragments):
        commands = powershell_commands(''.join(fragments))
        if commands:
            results.setdefault(index, []).extend(commands)

    for (kind, channel), group in groupby(
            enumerate(events), key=lambda item: (item[1]['event'], item[1].get('channel'))):
        if kind in ('exec', 'effective-command'):
            fragments = []
            length = 0
            for index, record in group:
                fragment = record['text']
                length += len(fragment)
                if length <= MAX_ENCODED_LINE:
                    fragments.append(fragment)
            if length <= MAX_ENCODED_LINE:
                scan(index, fragments)
            continue

        for index, record in group:
            if kind == 'close':
                pending = pending_stdin.pop(channel, None)
                if pending and pending[1] <= MAX_ENCODED_LINE:
                    scan(pending[2], pending[0])
            elif kind == 'stdin':
                fragments, length, last_index = pending_stdin.get(channel, ([], 0, index))
                parts = LINE_END.split(record['text'])
                for part_index, part in enumerate(parts):
                    if part:
                        length += len(part)
                        if length <= MAX_ENCODED_LINE:
                            fragments.append(part)
                        last_index = index
                    if part_index + 1 < len(parts):
                        if fragments and length <= MAX_ENCODED_LINE:
                            scan(last_index, fragments)
                        fragments, length = [], 0
                pending_stdin[channel] = fragments, length, last_index

    for fragments, length, index in pending_stdin.values():
        if fragments and length <= MAX_ENCODED_LINE:
            scan(index, fragments)
    return results


def read_records(path):
    with open(path, encoding="ascii") as stream:
        yield from read_record_stream(stream, path)


def read_record_stream(stream, source="<stream>"):
    expected = 0
    line_number = 0
    for line_number, line in enumerate(stream, 1):
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("record must be a JSON object")
            if line_number == 1:
                if record.get("version") != 1 or record.get("event") != "connection":
                    raise ValueError("not an InputLog1 connection header")
                record["user"] = bytes.fromhex(record.pop("user_hex")).decode("utf-8", "replace")
                record["remote"] = bytes.fromhex(record.pop("remote_hex")).decode("utf-8", "replace")
            else:
                if record["sequence"] != expected:
                    raise ValueError(f"expected sequence {expected}")
                expected += 1
                if record["event"] not in {"shell", "exec", "stdin", "close", "effective-command"}:
                    raise ValueError("unknown event")
                data = bytes.fromhex(record.pop("hex"))
                if len(data) != record["bytes"]:
                    raise ValueError("byte count mismatch")
                record["data"] = data
            yield record
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{source}:{line_number}: {exc}") from exc
    if expected == 0 and line_number == 0:
        raise ValueError(f"{source}: empty recording")


def decoded_records(path, channel=None):
    yield from decoded_record_stream(read_records(path), channel)


def decoded_record_stream(records, channel=None):
    decoders = {}
    for original in records:
        record = dict(original)
        data = record.pop("data", None)
        if data is None:
            yield record
            continue
        if channel is not None and record["channel"] != channel:
            continue
        # Separate streams prevent cross-channel or exec/stdin UTF-8 mixing.
        key = (record["channel"], record["event"])
        decoder = decoders.setdefault(key, codecs.getincrementaldecoder("utf-8")("backslashreplace"))
        record["text"] = decoder.decode(data)
        if record["event"] == "close":
            # A partial final character must remain visible in decoded logs.
            for stream_key in list(decoders):
                if stream_key[0] == record["channel"]:
                    trailing = decoders.pop(stream_key).decode(b"", final=True)
                    if trailing:
                        yield {"event": "decoder-tail", "channel": stream_key[0], "text": trailing}
        yield record
    for (channel_id, event), decoder in decoders.items():
        trailing = decoder.decode(b"", final=True)
        if trailing:
            yield {"event": "decoder-tail", "channel": channel_id, "stream": event, "text": trailing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--channel", type=int, help="select the recording channel ID")
    parser.add_argument("--raw-stdin", type=Path, help="write exact stdin bytes to a NEW file; requires --channel")
    args = parser.parse_args()
    try:
        if args.raw_stdin:
            if args.channel is None:
                parser.error("--raw-stdin requires --channel")
            # Validate completely before producing output. Never overwrite files.
            data = b"".join(r["data"] for r in read_records(args.log)
                            if r.get("event") == "stdin" and r["channel"] == args.channel)
            with args.raw_stdin.open("xb") as output:
                output.write(data)
        else:
            # JSON escaping keeps terminal control sequences inert on display.
            for record in decoded_records(args.log, args.channel):
                print(json.dumps(record, ensure_ascii=True))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
