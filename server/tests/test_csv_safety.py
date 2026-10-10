import csv
import io
import unittest
from unittest.mock import Mock, patch
from app import main


class CsvSafetyTests(unittest.TestCase):
    def test_formula_is_exported_as_text_without_modifying_normal_titles(self):
        song = dict(id=1, title='=1+1', artist=' +SUM(1,2)', album='正常专辑',
                    bvid='BVTEST', status='ready')
        with patch.object(main, 'get_conn', return_value=Mock()), patch.object(main, 'list_songs', return_value=[song]):
            response = main.export_csv()
        row = list(csv.reader(io.StringIO(response.body.decode('utf-8-sig'))))[1]
        self.assertEqual(row[1], "'=1+1")
        self.assertEqual(row[2], "' +SUM(1,2)")
        self.assertEqual(row[3], '正常专辑')
