"""Synthetic regressions for recovered history fields and one namespace parser."""
import csv
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import prepare_resset
from normalize_daily_market import resolve_columns, canonical_date


class SourceRecoveryTests(unittest.TestCase):
    def test_industry_execution_standard_and_identity_preserved(self):
        spec = next(x for x in prepare_resset.TABLES if x.key == 'industry_history')
        headers = {0:'公司代码_CompanyCode',1:'上市公司代码_ComCd',2:'股票代码_StkCd',
                   3:'是否执行()_IfPerformed',4:'行业分类标准_IndClsStd',
                   5:'行业分类标准名称_IndClsStdNm',6:'信息来源_InfoSource',
                   7:'是否从2001版更改至2012版_IfChg2001to2012'}
        resolved = prepare_resset.resolve_columns(headers, spec)
        self.assertEqual(resolved['company_code'], 1)
        self.assertEqual(resolved['raw_company_code'], 0)
        for field in ('if_performed','classification_standard','classification_standard_name','info_source','changed_2001_to_2012'):
            self.assertIn(field, resolved)
            self.assertIn(field, spec.output_fields)

    def test_status_identifiers_not_overwritten_by_namespaces(self):
        spec = next(x for x in prepare_resset.TABLES if x.key == 'status_history')
        headers = {0:'公司代码_CompanyCode',1:'上市公司代码_ComCd',2:'ISIN编码_ISIN',
                   3:'证券代码_R_SecuCode',4:'交易时股票代码_StkCdOTrd',5:'证券市场_SecuMkt'}
        resolved = prepare_resset.resolve_columns(headers, spec)
        self.assertEqual(resolved['company_code'], 1)
        self.assertEqual(resolved['raw_company_code'], 0)
        for field in ('isin','security_code','trading_stock_code','security_market'):
            self.assertIn(field, resolved)

    def test_market_alias_priority_and_no_security_fallback(self):
        for headers in (['公司代码_CompanyCode','上市公司代码_ComCd','股票代码_StkCd'],
                        ['股票代码_StkCd','上市公司代码_ComCd','公司代码_CompanyCode']):
            result = resolve_columns(headers)
            self.assertEqual(result['company_code'], '上市公司代码_ComCd')
            self.assertEqual(result['raw_company_code'], '公司代码_CompanyCode')
        result = resolve_columns(['公司代码_CompanyCode','证券代码_R_SecuCode'])
        self.assertNotIn('company_code', result)
        self.assertNotIn('stock_code', result)
        self.assertIn('raw_security_code', result)

    def test_duplicate_compacted_market_header_refused(self):
        with self.assertRaises(ValueError):
            resolve_columns(['上市公司代码_ComCd','上市公司代码_Comcd'])

    def test_invalid_iso_date_refused(self):
        self.assertEqual(canonical_date('20200102'), '2020-01-02')
        with self.assertRaises(ValueError): canonical_date('2020-02-31')

    def test_market_cli_new_private_output_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'raw.csv'
            with source.open('w',newline='',encoding='utf8') as f:
                writer=csv.writer(f)
                writer.writerow(['公司代码_CompanyCode','上市公司代码_ComCd','股票代码_StkCd','日期_Date','日收益率_Dret'])
                writer.writerow(['999','111','000001','2020-01-02','0.01'])
            output=root/'result.csv.gz'
            script=Path(__file__).with_name('normalize_daily_market.py')
            command=[sys.executable,str(script),'--input',str(source),'--output',str(output)]
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            with gzip.open(output,'rt',encoding='utf8') as f: row=next(csv.DictReader(f))
            self.assertEqual(row['stock_code'],'000001')
            self.assertEqual(row['company_code'],'111')
            self.assertEqual(row['raw_company_code'],'999')
            self.assertEqual(row['source_row'],'2')
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            with source.open('a',encoding='utf8') as f: f.write('999,111,000001,2020-01-02,0.02\n')
            bad=root/'duplicate.csv.gz'
            result=subprocess.run(command[:-1]+[str(bad)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(bad.exists())
            self.assertFalse(bad.with_name(bad.name+'.keys.sqlite').exists())


if __name__ == '__main__': unittest.main()
