#!/usr/bin/env python3
"""Decode Windows OpenSSH InputLog1 JSONL. Python 3, standard library only."""
import argparse
import codecs
import json
from pathlib import Path


def read_records(path):
    expected = 0
    line_number = 0
    with open(path, encoding="ascii") as stream:
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
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    if expected == 0 and line_number == 0:
        raise ValueError(f"{path}: empty recording")


def decoded_records(path, channel=None):
    decoders = {}
    for record in read_records(path):
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
