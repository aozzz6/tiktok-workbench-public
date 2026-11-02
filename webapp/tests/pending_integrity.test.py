"""Queue integrity regressions; all ledger and queue files are temporary."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A
import openpyxl

class PendingIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = self.root / 'ledger.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = '对标记录-test'
        ws.append(['视频链接','音频','翻译'])
        ws.append(['https://example.com','hello',None])
        wb.save(self.ledger)
        wb.close()
        self.patches = [patch.object(A, 'PENDING_TRANS_FILE', self.root/'queue.json'),
                        patch.object(A, '_PENDING_SIG', {'mtime':-1.0}),
                        patch.object(A.config, 'ledger_path', return_value=self.ledger)]
        for p in self.patches: p.start()
    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()
    def test_failed_scan_not_green(self):
        with patch('openpyxl.load_workbook', side_effect=PermissionError('locked')):
            self.assertFalse(A._pending_snapshot()['ok'])
    def test_missing_ledger_preserves_queue(self):
        A._refresh_pending_translation()
        before=A.PENDING_TRANS_FILE.read_bytes()
        self.ledger.unlink()
        with self.assertRaises((OSError, ValueError)): A._refresh_pending_translation()
        self.assertEqual(before,A.PENDING_TRANS_FILE.read_bytes())
        self.assertFalse(A._pending_snapshot()['ok'])
    def test_deleted_nonempty_queue_heals(self):
        self.assertEqual(A._pending_snapshot()['total'],1)
        A.PENDING_TRANS_FILE.unlink()
        self.assertEqual(A._pending_snapshot()['total'],1)
    def test_inconsistent_queue_rejected(self):
        A.PENDING_TRANS_FILE.write_text(json.dumps({'ts':1,'total':0,'items':[{'account':'test'}]}))
        self.assertFalse(A._read_pending_translation()['ok'])
    def test_nonfinite_timestamp_rejected(self):
        A.PENDING_TRANS_FILE.write_text(json.dumps({'ts':float('nan'),'total':1,'items':[{'account':'test'}]}))
        self.assertFalse(A._read_pending_translation()['ok'])
    def test_empty_snapshot_is_cached(self):
        wb=openpyxl.load_workbook(self.ledger); wb.active.cell(2,3,'你好'); wb.save(self.ledger); wb.close()
        self.assertEqual(A._pending_snapshot()['total'],0)
        with patch('openpyxl.load_workbook', side_effect=AssertionError('unneeded rescan')):
            self.assertTrue(A._pending_snapshot()['ok'])
    def test_ledger_changes_during_scan_preserve_queue(self):
        A._refresh_pending_translation()
        before=A.PENDING_TRANS_FILE.read_bytes()
        with patch.object(A,'_pending_ledger_signature',side_effect=[('old',1,2),('new',3,4)]):
            with self.assertRaises(OSError): A._refresh_pending_translation()
        self.assertEqual(before,A.PENDING_TRANS_FILE.read_bytes())
    def test_publish_failure_preserves_queue_and_cleans_temp(self):
        A._refresh_pending_translation()
        before=A.PENDING_TRANS_FILE.read_bytes()
        with patch.object(Path,'replace',side_effect=PermissionError('locked queue')):
            with self.assertRaises(PermissionError): A._refresh_pending_translation()
        self.assertEqual(before,A.PENDING_TRANS_FILE.read_bytes())
        self.assertEqual(list(self.root.glob('pending_*.tmp')),[])
    def test_external_reader_never_sees_partial_json(self):
        from concurrent.futures import ThreadPoolExecutor
        A._refresh_pending_translation()
        def writer():
            for _ in range(15): A._refresh_pending_translation()
        def reader():
            for _ in range(150):
                data=json.loads(A.PENDING_TRANS_FILE.read_text(encoding='utf-8'))
                self.assertEqual(data['total'],len(data['items']))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(writer),pool.submit(reader),pool.submit(reader),pool.submit(reader)]
            for f in futures: f.result()

if __name__=='__main__': unittest.main()
