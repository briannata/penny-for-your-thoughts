"""Report tables and source links, all calculated without an LLM."""
from urllib.parse import quote
from .core import PEOPLE, dollars, metrics, suggestions, anomalies, digest

OWNED_TABS = ['Overview', 'Monthly summaries', 'Categories', 'Budget suggestions',
              'Anomalies', 'Transactions', 'Fixed allocations', 'Run status', '_State']
CONTROL_HEADER = ['Month', 'Complete', 'Approval revision', 'Instructions']


def initial_controls(year, completed):
    return [CONTROL_HEADER] + [[f'{year}-{m:02}', f'{year}-{m:02}' in completed, 1,
        'Check Complete when finished. After late edits, increase revision (1 to 2, etc.).']
        for m in range(1, 13)]


def controls_from_rows(rows, year):
    if not rows or rows[0][:3] != CONTROL_HEADER[:3]:
        raise ValueError('Controls headers changed')
    result = {}
    valid = {f'{year}-{m:02}' for m in range(1, 13)}
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        if len(row) < 3 or row[0] not in valid or row[0] in result:
            raise ValueError('Controls has an invalid or duplicate month')
        if str(row[1]).lower() not in ('true', 'false'):
            raise ValueError('Controls Complete must be a checkbox (TRUE or FALSE)')
        revision = float(row[2])
        if revision < 1 or not revision.is_integer():
            raise ValueError('Controls revision must be a positive integer')
        result[row[0]] = (str(row[1]).lower() == 'true', int(revision))
    if set(result) != valid:
        raise ValueError('Controls must contain all 12 months')
    return result


def state_table(state):
    import json
    return [['Key', 'JSON (managed; do not edit)']] + [[k, json.dumps(v, sort_keys=True)] for k, v in sorted(state.items())]


def load_state(rows):
    import json
    if not rows:
        return {}
    result = {}
    for row in rows[1:]:
        if not row:
            continue
        if len(row) != 2 or row[0] in result:
            raise ValueError('Invalid report state; do not reset the budget ledger')
        result[row[0]] = json.loads(row[1])
    return result


def summary_state(state, month, payload_hash):
    """Prior months can change the comparisons without changing this month's entries."""
    if not state.get('summary'):
        return 'pending'
    if state.get('summary_hash') == month['hash'] and state.get('payload_hash') == payload_hash:
        return 'ready'
    if state.get('revision', 0) <= state.get('summary_revision', 0):
        return 'reapprove'
    return 'pending'


def tables(months, states, eligible, source_url='', now='', note=''):
    overview = [['Month', 'Status', 'Recorded purchases', 'Fixed allocation estimate',
                 'Total incl. allocation', 'Carryover (not spending)', 'Tentative variable allowance',
                 'Allowance minus purchases', 'Brianna purchases', 'Wyatt purchases',
                 'Brianna fixed allocation', 'Wyatt fixed allocation',
                 'Brianna total incl. allocation', 'Wyatt total incl. allocation']]
    categories = [['Month', 'Category', 'Household purchases', 'Brianna purchases', 'Wyatt purchases']]
    transactions = [['Month', 'Date', 'Amount', 'Description', 'Category', 'Payer', 'Type', 'Source range', 'Source link']]
    fixed = [['Month', 'Description', 'Category', 'Payer', 'Monthly allocation estimate', 'Basis']]
    summaries = [['Month', 'Status', 'Generated UTC', 'Narrative (AI-generated; verify against Overview)']]
    for key, month in sorted(months.items()):
        m = metrics(month)
        status = states[key]['status']
        overview.append([key, status, dollars(m['variable']), dollars(m['fixed']),
            dollars(m['planning_total']), dollars(m['carryover']),
            dollars(m['allowance']) if m['allowance'] is not None else '',
            dollars(m['allowance'] - m['variable']) if m['allowance'] is not None else '',
            *[dollars(m['payers'][p]) for p in PEOPLE], *[dollars(m['fixed_payers'][p]) for p in PEOPLE],
            *[dollars(m['payers'][p] + m['fixed_payers'][p]) for p in PEOPLE]])
        for cat, amount in sorted(m['categories'].items()):
            categories.append([key, cat, dollars(amount), *[dollars(sum(
                r['amount'] for r in month['transactions'] if r['category'] == cat and r['person'] == p)) for p in PEOPLE]])
        for typ, records in [('Purchase', month['transactions']), ('Carryover', month['adjustments'])]:
            for r in records:
                transactions.append([key, r['date'], dollars(r['amount']), r['description'],
                    r['category'], r['person'], typ, r['source'],
                    source_url.split('#')[0].split('?')[0] + '?range=' + quote(r['source']) if source_url else ''])
        for r in month['fixed']:
            fixed.append([key, r['description'], r['category'], r['person'], dollars(r['amount']),
                          'Current source schedule applied to this month; historical estimate'])
        s = states[key]
        summary_status = status
        if key in eligible:
            condition = summary_state(s, month, digest(ai_payload(key, months, eligible)))
            summary_status = ('Ready' if condition == 'ready' else
                'OUTDATED comparisons: increase approval revision' if condition == 'reapprove' else
                ('OUTDATED narrative; replacement pending' if s.get('summary') else s.get('ai_status', 'Pending narrative')))
        elif s.get('summary'):
            summary_status = 'OUTDATED / not approved: ' + status
        summaries.append([key, summary_status, s.get('generated', ''), s.get('summary', '')])
    warnings = [[k, warning] for k, m in months.items() for warning in m['warnings']]
    return {'Overview': overview, 'Categories': categories, 'Transactions': transactions,
            'Fixed allocations': fixed, 'Monthly summaries': summaries,
            'Budget suggestions': [['Category', 'Completed months', 'Median monthly purchases',
                '25th percentile', '75th percentile', 'Mean monthly purchases', 'Interpretation']]
                + suggestions(months, eligible),
            'Anomalies': [['Month', 'Category', 'Purchases', 'Prior 3 completed months median',
                'Increase USD', 'Increase fraction', 'Explanation']] + anomalies(months, eligible),
            'Run status': [['Item', 'Value'], ['Last successful refresh UTC', now], ['Run note', note],
                ['Source policy', 'Read only; daily checks include historical months'],
                ['Coverage', 'Recorded entries only; no bank reconciliation'],
                ['Fixed expenses', 'Allocations are estimated, not confirmed payments; annual amounts may be spread monthly'],
                ['Carryovers', 'Prior balances; do not sum across months as new spending or debt'],
                ['Budget proposals', 'Observed spending distributions, not confirmed affordability or enforced limits'],
                ['Historical approval', 'Changed completed months require a higher revision in Controls'],
                ['Notifications', 'Disabled; no Gmail access'],
                ['AI budget', 'UTC calendar month; $0.02 reserved per attempt, including failures'], *warnings],
            '_State': state_table(states)}


def ai_payload(key, months, eligible):
    m = metrics(months[key])
    return {'month': key, 'report_tabs': ['Overview', 'Categories', 'Budget suggestions', 'Anomalies'],
            'recorded_purchases_usd': dollars(m['variable']),
            'fixed_allocation_estimate_usd': dollars(m['fixed']),
            'planning_total_usd': dollars(m['planning_total']),
            'carryover_excluded_usd': dollars(m['carryover']),
            'tentative_variable_allowance_usd': dollars(m['allowance']) if m['allowance'] is not None else None,
            'allowance_minus_purchases_usd': dollars(m['allowance'] - m['variable']) if m['allowance'] is not None else None,
            'purchases_by_payer_usd': {p: dollars(v) for p, v in m['payers'].items()},
            'fixed_allocations_by_payer_usd': {p: dollars(v) for p, v in m['fixed_payers'].items()},
            'categories_usd': {c: dollars(v) for c, v in m['categories'].items()},
            'anomalies_columns': ['month', 'category', 'purchases_usd', 'baseline_usd', 'increase_usd', 'increase_fraction', 'note'],
            'anomalies': [r for r in anomalies(months, eligible) if r[0] == key],
            'budget_range_columns': ['category', 'completed_months', 'median_usd', 'q25_usd', 'q75_usd', 'mean_usd', 'note'],
            'budget_ranges': suggestions({k: v for k, v in months.items() if k <= key}, {k for k in eligible if k <= key})}
