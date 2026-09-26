import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from finance.core import parse_source, metrics, approve, money, suggestions, anomalies
from finance.ai import reserve
from finance.reporting import initial_controls, controls_from_rows, summary_state
from finance.sheets import Reader, Report, spreadsheet_id


def source():
    return {'January': [[], [], [], ['Date', 'Amount', 'Description', 'Category', 'Person'],
        ['2026-01-02', 20, 'Dinner', 'Food', 'Brianna'],
        ['2026-01-02', 20, 'Dinner', 'Food', 'Brianna'],
        ['2026-01-03', -5, 'Refund', 'Food', 'Wyatt'],
        ['2026-01-01', 100, 'December Deficit', 'Other', 'Bingus']],
        'Fixed Expenses': [[], ['Amount', 'Description', 'Category', 'Person'],
            [1000, 'Rent', 'House', 'Wyatt'], [12.345, 'Annual insurance / 12', 'Insurance', 'Brianna']]}


class AccountingTests(unittest.TestCase):
    def test_changed_comparison_history_needs_reapproval(self):
        state = {'summary': 'old', 'summary_hash': 'same', 'payload_hash': 'old',
                 'revision': 1, 'summary_revision': 1}
        self.assertEqual(summary_state(state, {'hash': 'same'}, 'old'), 'ready')
        self.assertEqual(summary_state(state, {'hash': 'same'}, 'new'), 'reapprove')
        state['revision'] = 2
        self.assertEqual(summary_state(state, {'hash': 'same'}, 'new'), 'pending')
    def test_month_only_dates_do_not_invent_a_day(self):
        data = source()
        data['January'][4][0] = 'January'
        month = parse_source(data, 2026)['2026-01']
        self.assertEqual(month['transactions'][0]['date'], '2026-01')
        self.assertIn('month-only', month['warnings'][0])

    def test_carryover_duplicates_refund_and_fixed_once(self):
        m = metrics(parse_source(source(), 2026)['2026-01'])
        self.assertEqual(m['variable'], 3500)
        self.assertEqual(m['fixed'], 101235)
        self.assertEqual(m['planning_total'], 104735)
        self.assertEqual(m['carryover'], 10000)
        self.assertEqual(m['payers'], {'Brianna': 4000, 'Wyatt': -500})
        self.assertEqual(m['count'], 3)

    def test_hash_ignores_reorder_and_volatile_cells_but_detects_deletions(self):
        original = source()
        a = parse_source(original, 2026)['2026-01']['hash']
        original['January'][4:] = reversed(original['January'][4:])
        original['January'][0] = ['', '', '', '', '', '', 'Day', 27]
        self.assertEqual(a, parse_source(original, 2026)['2026-01']['hash'])
        original['January'].pop()
        self.assertNotEqual(a, parse_source(original, 2026)['2026-01']['hash'])

    def test_unknown_payer_fails(self):
        data = source()
        data['January'][4][4] = 'Bingus'
        with self.assertRaises(ValueError):
            parse_source(data, 2026)

    def test_missing_amount_fails_and_zero_is_valid(self):
        for bad in (None, '', True, 'NaN', '=A1'):
            with self.assertRaises(ValueError):
                money(bad)
        self.assertEqual(money(0), 0)

    def test_late_edits_require_explicit_revision(self):
        s = {}
        self.assertTrue(approve(s, 'old', True, 1))
        self.assertFalse(approve(s, 'new', True, 1))
        self.assertFalse(approve(s, 'new', True, 1))
        self.assertTrue(approve(s, 'new', True, 2))
        self.assertFalse(approve(s, 'new', False, 2))
        with self.assertRaises(ValueError):
            approve(s, 'new', True, 1)

    def test_controls_start_with_eight_complete_months(self):
        c = controls_from_rows(initial_controls(2026, {f'2026-{m:02}' for m in range(1, 9)}), 2026)
        self.assertEqual(sum(done for done, revision in c.values()), 8)
        self.assertEqual(c['2026-09'], (False, 1))

    def test_open_month_excluded_from_baseline(self):
        m = parse_source(source(), 2026)
        m['2026-02'] = copy.deepcopy(m['2026-01'])
        m['2026-02']['transactions'][0]['amount'] = 10000000
        result = suggestions(m, {'2026-01'})
        self.assertEqual(result[0][1], 1)
        self.assertEqual(result[0][2], 35)

    def test_missing_category_month_counts_as_zero(self):
        m = parse_source(source(), 2026)
        m['2026-02'] = copy.deepcopy(m['2026-01'])
        m['2026-02']['transactions'] = []
        self.assertEqual(suggestions(m, set(m))[0][2], 17.50)

    def test_anomaly_requires_dollar_and_percentage_thresholds(self):
        month = parse_source(source(), 2026)['2026-01']
        m = {f'2026-{n:02}': copy.deepcopy(month) for n in range(1, 5)}
        m['2026-04']['transactions'][0]['amount'] += 4900
        self.assertEqual(anomalies(m, set(m)), [])
        m['2026-04']['transactions'][0]['amount'] += 100
        self.assertEqual(anomalies(m, set(m))[0][4], 50)


class CostAndTransportTests(unittest.TestCase):
    def test_source_client_rejects_writes(self):
        http = Mock()
        with self.assertRaises(ValueError):
            Reader('source', http).request('POST', ':batchUpdate')
        http.request.assert_not_called()

    def test_budget_reserved_before_call_and_month_rollover(self):
        state, saved = {}, []
        save = lambda: saved.append(copy.deepcopy(state))
        now = datetime(2026, 9, 26, tzinfo=timezone.utc)
        self.assertTrue(reserve(state, 2, save, now))
        self.assertFalse(reserve(state, 2, save, now))
        self.assertEqual(saved[0]['_budget:2026-09']['reserved_cents'], 2)
        self.assertTrue(reserve(state, 2, save, datetime(2026, 10, 1, tzinfo=timezone.utc)))

    def test_failed_persistence_blocks_request(self):
        with self.assertRaises(RuntimeError):
            reserve({}, 200, Mock(side_effect=RuntimeError('save failed')))

    def test_same_source_and_report_refused_before_auth(self):
        with self.assertRaises(ValueError):
            Report('same', 'same')

    def test_urls_are_restricted(self):
        self.assertEqual(spreadsheet_id('https://docs.google.com/spreadsheets/d/abc-123/edit#gid=0'), 'abc-123')
        with self.assertRaises(ValueError):
            spreadsheet_id('https://attacker.test/spreadsheets/d/abc/edit')

    def test_writes_only_destination_and_literal_text(self):
        http = Mock()
        http.request.return_value.status_code = 200
        http.request.return_value.json.return_value = {}
        report = Report('destination', 'source', http)
        report.properties = {'Overview': {'sheetId': 1, 'gridProperties': {'rowCount': 100, 'columnCount': 26}}}
        report.write({'Overview': [['Header'], ['=IMPORTXML("example", "x")']]})
        args, kwargs = http.request.call_args
        self.assertEqual(args[0], 'POST')
        self.assertIn('/destination:batchUpdate', args[1])
        self.assertEqual(kwargs['json']['requests'][1]['updateCells']['rows'][1]['values'][0]['userEnteredValue'],
                         {'stringValue': '=IMPORTXML("example", "x")'})


if __name__ == '__main__':
    unittest.main()
