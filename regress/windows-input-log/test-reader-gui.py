"""GUI model/widget regression checks. Does not touch SSH services."""
import argparse
import json
from pathlib import Path
import sys
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--work-directory', type=Path, required=True)
options = parser.parse_args()
scratch = options.work_directory.resolve() / ('reader-gui-tests-' + uuid.uuid4().hex)
scratch.mkdir(parents=True)
source = Path(__file__).resolve().parents[2] / 'contrib/win32/openssh'
sys.path.insert(0, str(source))
from input_log_gui import ALL_CHANNELS, Recording, ReaderWindow, visible_text
from input_log_reader import decoded_record_stream


def event(sequence, data, channel=1, kind='stdin'):
    return dict(event=kind, time='2026-11-18T12:00:00.000Z', sequence=sequence,
                channel=channel, bytes=len(data), hex=data.hex())


def fixture(name, records):
    header = dict(version=1, event='connection', time='2026-11-18T12:00:00.000Z',
                  user_hex='lab-admin'.encode().hex(), remote_hex=b'100.90.80.70'.hex(), remote_port=22)
    path = scratch / name
    path.write_text('\n'.join(json.dumps(r) for r in [header, *records]) + '\n', encoding='ascii')
    return path


class ModelTests(unittest.TestCase):
    def test_unicode_controls_and_stream_decoder_preserves_original_bytes(self):
        path = fixture('unicode.jsonl', [event(0, '中'.encode()[:1]),
            event(1, '中'.encode()[1:] + b'\r\x1b[2J\x00')])
        model = Recording.load(path)
        self.assertIn('中\\r\\x1b[2J\\x00', model.text)
        original = [dict(record) for record in model.raw]
        list(decoded_record_stream(model.raw))
        self.assertEqual(model.raw, original)
        self.assertEqual(visible_text('\u202e'), '\\u202e')

    def test_filters_channel_event_and_unicode_search(self):
        path = fixture('filter.jsonl', [event(0, b'echo HELLO', kind='exec'),
            event(1, '中文輸入'.encode()), event(2, b'other', 2)])
        model = Recording.load(path)
        self.assertEqual(model.filter(event='exec', query='hello'), [0])
        self.assertEqual(model.filter(channel=1, query='中文'), [1])
        self.assertEqual(model.filter(channel=2), [2])

    def test_snapshot_export_is_lossless_and_does_not_follow_later_appends(self):
        data = bytes(range(256)) * 2 + '原始'.encode()
        path = fixture('snapshot.jsonl', [event(0, data), event(1, b'other', 2)])
        model = Recording.load(path)
        with path.open('a') as output:
            output.write(json.dumps(event(2, b'not-in-snapshot')) + '\n')
        target = scratch / 'snapshot.bin'
        model.export_stdin(target, 1)
        self.assertEqual(target.read_bytes(), data)

    def test_text_export_matches_filters_and_preserves_existing_files(self):
        model = Recording.load(fixture('text.jsonl', [event(0, '中文'.encode()), event(1, b'hidden', 2)]))
        target = scratch / 'readable.txt'
        model.export_text(target, model.filter(channel=1))
        self.assertIn('中文', target.read_text('utf-8'))
        self.assertNotIn('hidden', target.read_text('utf-8'))
        before = target.read_bytes()
        with self.assertRaises(FileExistsError):
            model.export_text(target, model.filter())
        with self.assertRaises(FileExistsError):
            model.export_stdin(target, 1)
        self.assertEqual(target.read_bytes(), before)

    def test_corruption_and_invalid_channels_are_rejected(self):
        path = fixture('bad.jsonl', [event(1, b'bad sequence')])
        with self.assertRaises(ValueError):
            Recording.load(path)
        path = fixture('bad-channel.jsonl', [event(0, b'x', channel='one')])
        with self.assertRaises(ValueError):
            Recording.load(path)

    def test_gui_file_limit_is_enforced(self):
        path = fixture('bounded.jsonl', [])
        with patch('input_log_gui.MAX_FILE_BYTES', 4):
            with self.assertRaisesRegex(ValueError, '64 MiB'):
                Recording.load(path)


class WidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = ReaderWindow(self.root)
        self.errors = []
        self.error_patch = patch('input_log_gui.messagebox.showerror', side_effect=lambda *a, **k: self.errors.append(a))
        self.error_patch.start()

    def tearDown(self):
        self.error_patch.stop()
        self.app.close()

    def load(self, path):
        self.app.open_path(path)
        deadline = time.monotonic() + 5
        while self.app.model is None and not self.errors and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertIsNotNone(self.app.model, self.errors)

    def test_load_select_filter_and_clear(self):
        self.load(fixture('widgets.jsonl', [event(0, '中文指令'.encode(), kind='exec'), event(1, b'other', 2)]))
        self.assertEqual(self.app.metadata[0].get(), 'lab-admin')
        self.assertEqual(len(self.app.table.get_children()), 2)
        self.assertIn('中文指令', self.app.detail.get('1.0', 'end'))
        self.app.channel.set('2')
        self.app.apply_filters()
        self.assertEqual(len(self.app.table.get_children()), 1)
        self.assertIn('other', self.app.detail.get('1.0', 'end'))
        self.app.query.set('no match')
        self.app.apply_filters()
        self.assertFalse(self.app.table.get_children())
        self.app.clear_filters()
        self.assertEqual(self.app.channel.get(), ALL_CHANNELS)
        self.assertEqual(len(self.app.table.get_children()), 2)

    def test_save_dialog_actions_export_and_do_not_overwrite(self):
        self.load(fixture('save-widget.jsonl', [event(0, b'first\r'), event(1, b'second', 2)]))
        target = scratch / 'widget-export.txt'
        with patch('input_log_gui.filedialog.asksaveasfilename', return_value=str(target)):
            self.app.save_text()
        self.assertIn('first\\r', target.read_text('utf-8'))
        self.app.channel.set('1')
        self.app.apply_filters()
        raw = scratch / 'widget-export.bin'
        with patch('input_log_gui.filedialog.asksaveasfilename', return_value=str(raw)):
            self.app.save_raw()
            self.app.save_raw()
        self.assertEqual(raw.read_bytes(), b'first\r')
        self.assertTrue(self.errors)

    def test_pagination_and_export_include_all_filtered_pages(self):
        self.load(fixture('pages.jsonl', [event(i, b'line') for i in range(501)]))
        self.assertEqual(len(self.app.table.get_children()), 500)
        self.app.next_button.invoke()
        self.assertEqual(len(self.app.table.get_children()), 1)
        self.assertEqual(self.app.table.item(self.app.table.get_children()[0], 'values')[0], '500')
        target = scratch / 'all-pages.txt'
        self.app.export(target, raw=False)
        self.assertEqual(target.read_text('utf-8').count('stdin'), 501)

    def test_bad_file_clears_stale_data_and_reports_error(self):
        self.load(fixture('first-good.jsonl', [event(0, b'secret old data')]))
        path = fixture('second-bad.jsonl', [event(2, b'bad')])
        self.app.open_path(path)
        deadline = time.monotonic() + 5
        while not self.errors and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(self.errors)
        self.assertIsNone(self.app.model)
        self.assertFalse(self.app.table.get_children())

    def test_export_controls_remain_inside_small_window(self):
        self.root.geometry('960x680')
        self.root.deiconify()
        self.root.update()
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        right = self.root.winfo_rootx() + self.root.winfo_width()
        for control in (self.app.text_button, self.app.raw_button, self.app.next_button):
            self.assertLessEqual(control.winfo_rooty() + control.winfo_height(), bottom)
            self.assertLessEqual(control.winfo_rootx() + control.winfo_width(), right)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]])
