import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

reader_path = Path(__file__).resolve().parents[2] / 'contrib/win32/openssh/read-input-log.py'
sys.path.insert(0, str(reader_path.parent))
spec = importlib.util.spec_from_file_location('reader', reader_path)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class ReaderTests(unittest.TestCase):
    def fixture(self, directory, records):
        path = Path(directory) / 'recording.jsonl'
        header = dict(version=1, event='connection', user_hex='75736572', remote_hex='3a3a31', remote_port=22)
        path.write_text('\n'.join(json.dumps(r) for r in [header, *records]) + '\n', encoding='ascii')
        return path

    def event(self, seq, data, channel=1, event='stdin'):
        return dict(event=event, sequence=seq, channel=channel, bytes=len(data), hex=data.hex())

    def test_split_utf8_and_channels(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(d, [self.event(0, b'\xe4'), self.event(1, b'other', 2), self.event(2, b'\xb8\xad\r')])
            result = list(reader.decoded_records(p))
            self.assertEqual(result[0]['user'], 'user')
            self.assertEqual([r['text'] for r in result[1:]], ['', 'other', '\u4e2d\r'])

    def test_binary_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            data = bytes(range(256)) * 64
            p = self.fixture(d, [self.event(0, data)])
            self.assertEqual(list(reader.read_records(p))[1]['data'], data)

    def test_detects_missing_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(d, [self.event(1, b'x')])
            with self.assertRaises(ValueError):
                list(reader.read_records(p))

    def test_detects_wrong_length(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.event(0, b'x')
            r['bytes'] = 2
            p = self.fixture(d, [r])
            with self.assertRaises(ValueError):
                list(reader.read_records(p))

    def test_control_characters_are_not_executed(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(d, [self.event(0, b'\x1b[2J\x08\x00')])
            text = json.dumps(list(reader.decoded_records(p))[1])
            self.assertNotIn('\x1b', text)
            self.assertIn('\\u001b', text)

    def test_truncated_json_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(d, [])
            with p.open('a') as f:
                f.write('{"event":')
            with self.assertRaises(ValueError):
                list(reader.read_records(p))

    def test_partial_final_character_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(d, [self.event(0, b'\xe4'), self.event(1, b'', event='close')])
            self.assertTrue(any(r.get('text') == '\\xe4' for r in reader.decoded_records(p)))

    def test_empty_recording(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'empty.jsonl'
            p.write_text('')
            with self.assertRaises(ValueError):
                list(reader.read_records(p))

    def test_non_object_record(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(d, [])
            with p.open('a') as f:
                f.write('[]\n')
            with self.assertRaises(ValueError):
                list(reader.read_records(p))


if __name__ == '__main__':
    unittest.main()
