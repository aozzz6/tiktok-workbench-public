"""删除旧模板账号后建表仍使用现行格式；所有写操作限临时夹具。"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PIPE=Path(__file__).resolve().parents[2]/'pipeline'
sys.path.insert(0,str(PIPE/'scripts'))
import build_ledger_15col as builder
import add_account_sheet as add_sheet
import openpyxl
from openpyxl.styles import Font


class DeletedTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ledger=self.root/'ledger.xlsx'
        self.out=self.root/'output'
        self.out.mkdir()
        (self.out/'scrape_new.json').write_text(json.dumps({'items':[{
            'id':'1111111111111111111','desc':'new video','stats':{'playCount':1}}]}),encoding='utf-8')
        wb=openpyxl.Workbook()
        wb.active.title='说明'
        ws=wb.create_sheet('对标记录-current')
        ws.append(builder.HEADER)
        ws.freeze_panes='C2'
        ws.column_dimensions['D'].width=37.25
        ws['A1'].font=Font(name='Arial',bold=True,color='123456')
        wb.save(self.ledger)
        wb.close()
        self.cfg={'xlsx':str(self.ledger),'accounts':[{
            'account':'new','active':True,'out_dir':str(self.out),'lang':'en'}]}

    def assert_format(self,width=37.25):
        wb=openpyxl.load_workbook(self.ledger)
        try:
            self.assertNotIn('对标记录-thesymmetrymarket',wb.sheetnames)
            ws=wb['对标记录-new']
            self.assertEqual(ws.column_dimensions['D'].width,width)
            self.assertEqual(ws.freeze_panes,'C2')
            self.assertTrue(ws['A1'].font.bold)
            self.assertEqual(ws.max_row,2)
        finally:
            wb.close()

    def test_builder_uses_surviving_template(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.main_write(cfg=self.cfg),0)
        self.assert_format()

    def test_add_sheet_default_uses_surviving_template(self):
        with patch.object(add_sheet,'load_cfg',return_value=self.cfg), \
             patch.object(sys,'argv',['add_account_sheet.py','--account','new']), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(add_sheet.main(),0)
        self.assert_format()

    def test_explicit_template_remains_supported(self):
        wb=openpyxl.load_workbook(self.ledger)
        alt=wb.copy_worksheet(wb['对标记录-current'])
        alt.title='自选模板'
        alt.column_dimensions['D'].width=53
        alt.freeze_panes='C2'
        wb.save(self.ledger)
        wb.close()
        with patch.object(add_sheet,'load_cfg',return_value=self.cfg), \
             patch.object(sys,'argv',['add_account_sheet.py','--account','new','--template','自选模板']), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(add_sheet.main(),0)
        self.assert_format(53)

    def test_selector_does_not_choose_nonstandard_sheet(self):
        wb=openpyxl.Workbook()
        bad=wb.create_sheet('对标记录-invalid')
        bad.append(['old header'])
        self.assertIsNone(builder.find_template_sheet(wb))
        wb.close()


if __name__=='__main__':
    unittest.main(verbosity=2)
