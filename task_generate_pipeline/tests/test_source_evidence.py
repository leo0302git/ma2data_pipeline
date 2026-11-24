import tempfile
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import file_sha
from sources import enrich_profiles


class MeasuredSourceTests(unittest.TestCase):
    def test_measured_file_content_reaches_author_without_preflight_answers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'source.txt';path.write_text('actual source')
            evidence = [{'sha256':file_sha(path),'profile':{'existing':'kept'}}]
            measured = {'files':[{'path':str(path),'sha256':file_sha(path),'text_head':'actual source',
                                 'solve':{'status':'Optimal','objective':123.0,'dual':123.0}}]}
            enrich_profiles(evidence,[path],measured)
            self.assertEqual(evidence[0]['profile']['existing'],'kept')
            self.assertEqual(evidence[0]['profile']['measured']['text_head'],'actual source')
            self.assertEqual(evidence[0]['profile']['measured']['solve'],{'status':'Optimal'})
            self.assertIn('objective',measured['files'][0]['solve'])

    def test_stale_measured_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'source.txt';path.write_text('new data')
            with self.assertRaises(ValueError):
                enrich_profiles([{'sha256':file_sha(path),'profile':{}}],[path],
                                {'files':[{'path':str(path),'sha256':'outdated'}]})
