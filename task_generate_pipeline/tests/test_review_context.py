"""A requested independent verifier must reach the model without losing its middle."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import stage_context
from common import canonical


class ReviewContext(unittest.TestCase):
    def test_fresh_full_program_survives_compaction_of_old_logs(self):
        observed = {f'old-{i}.log': 'a'*60000 for i in range(12)}
        program = 'HEAD\n' + 'central-independent-physics\n'*1600 + 'TAIL\n'
        observed['work/make_cases.py'] = program
        history = {p: {'step': 1} for p in observed}
        history['work/make_cases.py'] = {'step': 2}
        payload = stage_context({'fixed': 'x'*550000}, observed, history,
                                {}, [], list(observed), set(), 2, 8)
        self.assertLessEqual(len(canonical(payload)), 700*1024)
        self.assertEqual(payload['read_results']['work/make_cases.py'], program)
        self.assertIn('cache_compacted', ''.join(payload['read_results'].values()))


if __name__ == '__main__':
    unittest.main()
