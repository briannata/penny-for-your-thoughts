"""Mock cloud end-to-end: real parser, approvals, reporting and budget state."""
import copy
import os
import unittest
from unittest.mock import patch
from finance.__main__ import sync
from finance.reporting import load_state
from test_finance import source


class FakeReader:
    data = None
    def __init__(self, source_id):
        pass
    def metadata(self):
        return [{'properties': {'title': title}} for title in self.data]
    def read(self, names):
        return {n: copy.deepcopy(self.data[n]) for n in names}


class FakeReport:
    data = None
    def __init__(self, report_id, source_id):
        assert report_id != source_id
    def metadata(self):
        return [{'properties': {'title': title}} for title in self.data]
    def ensure(self, names):
        missing = [n for n in names if n not in self.data]
        for n in missing:
            self.data[n] = []
        return missing
    def read(self, names):
        return {n: copy.deepcopy(self.data[n]) for n in names}
    def write(self, tables):
        self.data.update(copy.deepcopy(tables))
    def style(self, names):
        pass


class SyncTests(unittest.TestCase):
    def setUp(self):
        FakeReader.data = source()
        FakeReport.data = {}
        self.environment = patch.dict(os.environ, {
            'SOURCE_SHEET_URL': 'https://docs.google.com/spreadsheets/d/source/edit',
            'REPORT_SHEET_URL': 'https://docs.google.com/spreadsheets/d/report/edit',
            'OPENAI_API_KEY': 'fake-key', 'AI_MONTHLY_BUDGET_USD': '2', 'AI_MAX_SUMMARIES_PER_RUN': '2'})
        self.environment.start()
        self.reader = patch('finance.sheets.Reader', FakeReader)
        self.report = patch('finance.sheets.Report', FakeReport)
        self.reader.start()
        self.report.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.reader.stop)
        self.addCleanup(self.report.stop)

    def test_first_sync_cache_late_edit_and_reapproval(self):
        original = copy.deepcopy(FakeReader.data)
        def reply(key, payload):
            state = load_state(FakeReport.data['_State'])
            self.assertTrue(any(v.get('reserved_cents') == 2 for v in state.values()))
            return 'Sample narrative'
        with patch('finance.ai.narrative', side_effect=reply) as ai:
            sync(2026, {'2026-01'})
            self.assertEqual(ai.call_count, 1)
            sync(2026, {'2026-01'})
            self.assertEqual(ai.call_count, 1)
        self.assertEqual(FakeReader.data, original)
        FakeReader.data['January'][4][1] = 30
        with patch('finance.ai.narrative', return_value='Revised narrative') as ai:
            sync(2026, {'2026-01'})
            ai.assert_not_called()
            self.assertIn('OUTDATED', FakeReport.data['Monthly summaries'][1][1])
            FakeReport.data['Controls'][1][2] = 2
            sync(2026, {'2026-01'})
            self.assertEqual(ai.call_count, 1)
            self.assertEqual(FakeReport.data['Monthly summaries'][1][3], 'Revised narrative')

    def test_ai_failure_keeps_reservation_and_computed_report(self):
        with patch('finance.ai.narrative', side_effect=TimeoutError):
            sync(2026, {'2026-01'})
        state = load_state(FakeReport.data['_State'])
        self.assertEqual(sum(v.get('reserved_cents', 0) for v in state.values()), 2)
        self.assertEqual(FakeReport.data['Overview'][1][2], 35)
        self.assertIn('failed', FakeReport.data['Monthly summaries'][1][1])

    def test_no_ai_and_zero_budget_make_no_requests(self):
        with patch('finance.ai.narrative') as ai:
            sync(2026, {'2026-01'}, no_ai=True)
            os.environ['AI_MONTHLY_BUDGET_USD'] = '0'
            sync(2026, {'2026-01'})
            ai.assert_not_called()
        self.assertIn('exhausted', FakeReport.data['Monthly summaries'][1][1])
