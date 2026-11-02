"""归档簿必须退出活动台账发现；临时文件和桩，不写业务表格。"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import config
from fastapi import HTTPException


class ArchiveLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch.object(config, 'LEDGER_DIR', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def file(self, name, mtime):
        path = self.root/name
        path.write_bytes(b'test-only placeholder')
        os.utime(path, (mtime, mtime))
        return path

    def test_newer_archive_cannot_become_main(self):
        main = self.file('tiktok_对标.xlsx', 100)
        self.file('对标账号_归档_20261017.xlsx', 200)
        self.assertEqual(config.list_ledgers(), [main])
        self.assertEqual(config.ledger_path(), main)

    def test_archive_only_means_no_active_ledger(self):
        self.file('对标账号_归档_20261017.xlsx', 200)
        self.assertEqual(config.list_ledgers(), [])
        self.assertIsNone(config.ledger_path())

    def test_multiple_active_books_keep_existing_mtime_order(self):
        old = self.file('first.xlsx', 100)
        new = self.file('second.xlsx', 300)
        self.file('对标账号_归档_20261017.xlsx', 400)
        self.file('~$second.xlsx', 500)
        (self.root/'directory.xlsx').mkdir()
        self.assertEqual(config.list_ledgers(), [new, old])

    def test_archive_markers_are_separate_filename_parts(self):
        for name in ['对标账号_归档_20261017.xlsx', '归档_20261017.xlsx',
                     'accounts-archive-20261017.xlsx', 'ARCHIVED.xlsx']:
            self.assertTrue(config._is_archive_ledger(Path(name)), name)
        for name in ['myarchivework.xlsx', '活动台账.xlsx', 'tiktok_对标.xlsx']:
            self.assertFalse(config._is_archive_ledger(Path(name)), name)

    def test_resolve_cannot_rediscover_deleted_account_in_archive(self):
        self.file('tiktok_对标.xlsx', 100)
        self.file('对标账号_归档_20261017.xlsx', 200)
        # 账号表也自己造：thesymmetrymarket 不存在是这条用例自己制造的前提，
        # 不依赖生产 accounts.json（否则生产把该账号加回来，本用例会假红）。
        accounts = self.root/'accounts.json'
        accounts.write_text('{"accounts": []}', encoding='utf-8')
        wb = Mock()
        wb.sheetnames = ['对标记录-active']
        with patch.object(config, 'ACCOUNTS_JSON', accounts), \
                patch('openpyxl.load_workbook', return_value=wb) as read:
            with self.assertRaises(HTTPException) as cm:
                app.ledger_resolve('thesymmetrymarket')
        # 账号已从账号表删除：404 在打开任何 xlsx 之前就发生 —— 连台账都不该打开。
        # 「归档簿不当活动簿」这条仍然成立（活动簿存在，但没有被读）。
        self.assertEqual(cm.exception.status_code, 404)
        read.assert_not_called()

    def test_pipeline_command_always_uses_active_book(self):
        main = self.file('tiktok_对标.xlsx', 100)
        self.file('对标账号_归档_20261017.xlsx', 200)
        command = app._build_pipeline_cmd({'account':'active', 'lang':'en',
            'date_from':'2026-10-16', 'date_to':'2026-10-17', 'steps':['s2_scrape']})
        self.assertEqual(command[command.index('--xlsx')+1], str(main))


if __name__ == '__main__':
    unittest.main(verbosity=2)
