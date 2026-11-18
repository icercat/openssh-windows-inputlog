"""Black-box checks for the bundled Windows reader; no service changes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reader', type=Path, required=True)
parser.add_argument('--work-directory', type=Path, required=True)
options = parser.parse_args()
scratch = options.work_directory.resolve() / ('reader-exe-' + uuid.uuid4().hex)
package = scratch / 'standalone package with spaces'
package.mkdir(parents=True)
shutil.copy2(options.reader, package / 'read-input-log-cli.exe')
shutil.copy2(options.reader.parent / 'read-input-log.exe', package / 'read-input-log.exe')
shutil.copytree(options.reader.parent / 'read-input-log-runtime', package / 'read-input-log-runtime')
executable = package / 'read-input-log-cli.exe'
env = os.environ.copy()
env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
env['PYTHONHOME'] = env['PYTHONPATH'] = str(scratch / 'python-is-not-installed-here')
env['TEMP'] = env['TMP'] = str(scratch)
source = Path(__file__).resolve().parents[2] / 'contrib/win32/openssh/read-input-log.py'


class ExecutableReaderTests(unittest.TestCase):
    def run_exe(self, *args):
        return subprocess.run([str(executable), *map(str, args)], cwd=scratch,
                              env=env, capture_output=True, timeout=20)

    def event(self, sequence, data, channel=1, event='stdin'):
        return dict(event=event, sequence=sequence, channel=channel, bytes=len(data), hex=data.hex())

    def fixture(self, name, records):
        header = dict(version=1, event='connection', user_hex='測試使用者'.encode().hex(),
                      remote_hex=b'100.90.80.70'.hex(), remote_port=22)
        path = scratch / name
        path.write_text('\n'.join(json.dumps(r) for r in [header, *records]) + '\n', encoding='ascii')
        return path

    def test_help_without_python_on_path(self):
        result = self.run_exe('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'--raw-stdin', result.stdout)

    def test_gui_is_windowed_and_cli_has_a_console(self):
        for name, expected in [('read-input-log.exe', 2), ('read-input-log-cli.exe', 3)]:
            data = (package / name).read_bytes()
            pe = struct.unpack_from('<I', data, 60)[0]
            self.assertEqual(struct.unpack_from('<H', data, pe + 24 + 68)[0], expected)

    def test_gui_runtime_without_external_python(self):
        result = subprocess.run([str(package / 'read-input-log.exe'), '--check-runtime'],
                                env=env, cwd=scratch, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_matches_python_reader_for_unicode_channels_and_controls(self):
        data = '中文'.encode()
        path = self.fixture('unicode.jsonl', [self.event(0, b'echo hello', event='exec'),
            self.event(1, data[:1]), self.event(2, b'other channel', channel=2),
            self.event(3, data[1:] + b'\r\x08\x00\x1b[2J'), self.event(4, b'', event='close')])
        result = self.run_exe(path)
        python_result = subprocess.run([sys.executable, str(source), str(path)], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, python_result.stdout)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(records[0]['user'], '測試使用者')
        self.assertTrue(any('中文' in r.get('text', '') for r in records))
        self.assertNotIn(b'\x1b', result.stdout)

    def test_channel_filter(self):
        path = self.fixture('channels.jsonl', [self.event(0, b'one'), self.event(1, b'two', 2)])
        result = self.run_exe(path, '--channel', 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]['text'], 'two')

    def test_exact_binary_extraction(self):
        data = bytes(range(256)) * 64 + '測試'.encode()
        path = self.fixture('binary.jsonl', [self.event(0, data)])
        output = scratch / 'extracted input.bin'
        result = self.run_exe(path, '--channel', 1, '--raw-stdin', output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), data)

    def test_refuses_overwrite(self):
        path = self.fixture('overwrite.jsonl', [self.event(0, b'input')])
        output = scratch / 'existing.bin'
        output.write_bytes(b'keep me')
        result = self.run_exe(path, '--channel', 1, '--raw-stdin', output)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(output.read_bytes(), b'keep me')

    def test_missing_channel_is_argument_error(self):
        path = self.fixture('argument.jsonl', [])
        output = scratch / 'must-not-exist.bin'
        result = self.run_exe(path, '--raw-stdin', output)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())

    def test_corrupt_records_fail_without_creating_extraction(self):
        wrong_length = self.event(0, b'x')
        wrong_length['bytes'] = 2
        for name, records in [('sequence.jsonl', [self.event(1, b'x')]),
                              ('length.jsonl', [wrong_length]), ('object.jsonl', [[]])]:
            with self.subTest(name=name):
                path = self.fixture(name, records)
                output = scratch / (name + '.bin')
                result = self.run_exe(path, '--channel', 1, '--raw-stdin', output)
                self.assertEqual(result.returncode, 1)
                self.assertIn(name.encode(), result.stderr)
                self.assertFalse(output.exists())

    def test_empty_truncated_and_missing_files(self):
        empty = scratch / 'empty.jsonl'
        empty.write_bytes(b'')
        truncated = self.fixture('truncated.jsonl', [])
        with truncated.open('a') as output:
            output.write('{"event":')
        for path in [empty, truncated, scratch / 'missing.jsonl']:
            with self.subTest(path=path):
                result = self.run_exe(path)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr)

    def test_incomplete_utf8_tail_is_retained(self):
        path = self.fixture('partial.jsonl', [self.event(0, b'\xe4'), self.event(1, b'', event='close')])
        result = self.run_exe(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(any(r.get('text') == '\\xe4' for r in records))


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]])
