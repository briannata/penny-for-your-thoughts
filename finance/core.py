"""Deterministic accounting. No network, credentials, or model calls."""
import calendar
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median


MONTHS = list(calendar.month_name)[1:]
PEOPLE = ('Brianna', 'Wyatt')


def money(value):
    if value is None or isinstance(value, bool) or str(value).strip() == '':
        raise ValueError('Missing amount')
    try:
        number = Decimal(str(value).replace('$', '').replace(',', '').strip())
        if not number.is_finite():
            raise ValueError('Non-finite amount')
        return int((number * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError('Invalid amount or unevaluated formula') from exc


def dollars(cents):
    return round(cents / 100, 2)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def cell(row, col):
    return row[col] if col < len(row) else None


def text(value):
    return '' if value is None else str(value).strip()


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return (datetime(1899, 12, 30) + timedelta(days=value)).date()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            pass
    raise ValueError('Invalid transaction date')


def parse_source(tabs, year):
    """Use month tab as accounting month; warn on date mismatch. Never deduplicate."""
    if 'Fixed Expenses' not in tabs:
        raise ValueError('Missing Fixed Expenses tab')
    fixed_rows = tabs['Fixed Expenses']
    if len(fixed_rows) < 2 or [text(v) for v in fixed_rows[1][:4]] != ['Amount', 'Description', 'Category', 'Person']:
        raise ValueError('Fixed Expenses headers changed; review source mapping')
    fixed = []
    for n, row in enumerate(fixed_rows[2:], 3):
        if not any(text(v) for v in row[:4]):
            continue
        amount, desc, category, person = (cell(row, i) for i in range(4))
        if not text(desc) or person not in PEOPLE:
            raise ValueError(f'Invalid fixed expense at row {n}')
        fixed.append({'amount': money(amount), 'description': text(desc),
                      'category': text(category).casefold() or 'uncategorized', 'person': person})
    result = {}
    for month_num, name in enumerate(MONTHS, 1):
        if name not in tabs:
            continue
        rows = tabs[name]
        if len(rows) < 4 or [text(v) for v in rows[3][:5]] != ['Date', 'Amount', 'Description', 'Category', 'Person']:
            raise ValueError(f'{name}: transaction headers changed; review source mapping')
        key = f'{year}-{month_num:02}'
        transactions, adjustments, warnings = [], [], []
        for n, row in enumerate(rows[4:], 5):
            if not any(text(v) for v in row[:5]):
                continue
            try:
                month_only = text(cell(row, 0)).casefold() == name.casefold()
                when = date(year, month_num, 1) if month_only else parse_date(cell(row, 0))
                amount = money(cell(row, 1))
            except ValueError as exc:
                raise ValueError(f'{name} row {n}: {exc}') from exc
            desc, cat, person = [text(cell(row, i)) for i in (2, 3, 4)]
            if not desc:
                raise ValueError(f'{name} row {n}: missing description')
            record = {'date': key if month_only else when.isoformat(), 'amount': amount, 'description': desc,
                      'category': cat.casefold() or 'uncategorized', 'person': person,
                      'source': f"'{name}'!A{n}:E{n}"}
            if re.search(r'\bdeficit\b', desc, re.I):
                adjustments.append(record)
                continue
            if person not in PEOPLE:
                raise ValueError(f'{name} row {n}: unrecognized payer')
            if month_only:
                warnings.append(f'{name} row {n}: month-only date; no day inferred')
            if when.year != year or when.month != month_num:
                warnings.append(f'{name} row {n}: date outside month; counted in source tab month')
            if cat == '':
                warnings.append(f'{name} row {n}: missing category')
            transactions.append(record)
        allowance = None
        for row in rows[:12]:
            for col, val in enumerate(row):
                if text(val) == 'Monthly Allowance':
                    allowance = money(cell(row, col + 1))
        # Ignore volatile day/rate formulas, formatting, row order and source addresses.
        fingerprint = digest({'transactions': sorted([
            {k: v for k, v in r.items() if k != 'source'} for r in transactions + adjustments
        ], key=lambda r: json.dumps(r, sort_keys=True)), 'fixed': fixed, 'allowance': allowance})
        result[key] = {'transactions': transactions, 'adjustments': adjustments,
                       'fixed': fixed, 'allowance': allowance, 'hash': fingerprint,
                       'warnings': warnings, 'tab': name}
    if not result:
        raise ValueError('No recognized month tabs')
    return result


def metrics(month):
    categories, payers, fixed_payers = defaultdict(int), defaultdict(int), defaultdict(int)
    for row in month['transactions']:
        categories[row['category']] += row['amount']
        payers[row['person']] += row['amount']
    for row in month['fixed']:
        fixed_payers[row['person']] += row['amount']
    variable = sum(categories.values())
    fixed = sum(fixed_payers.values())
    return {'variable': variable, 'fixed': fixed, 'planning_total': variable + fixed,
            'carryover': sum(r['amount'] for r in month['adjustments']),
            'categories': dict(categories), 'payers': {p: payers[p] for p in PEOPLE},
            'fixed_payers': {p: fixed_payers[p] for p in PEOPLE},
            'allowance': month['allowance'], 'count': len(month['transactions'])}


def approve(state, current_hash, complete, revision):
    """Revision is the explicit approval token; edits never silently reapprove."""
    if revision < 1:
        raise ValueError('Approval revision must be a positive integer')
    prior_revision = state.get('revision', 0)
    if revision < prior_revision:
        raise ValueError('Approval revision cannot decrease')
    if not complete:
        state['status'] = 'Open'
        return False
    if not state.get('approved_hash') or revision > prior_revision:
        state['revision'] = revision
        state['approved_hash'] = current_hash
    eligible = state['approved_hash'] == current_hash
    state['status'] = 'Complete' if eligible else 'Changed: increase approval revision'
    return eligible


def suggestions(months, eligible):
    """Observed distributions, not claims about affordable budgets."""
    completed = [metrics(months[k]) for k in sorted(eligible) if k in months]
    cats = sorted({cat for m in completed for cat in m['categories']})
    rows = []
    for cat in cats:
        values = sorted(m['categories'].get(cat, 0) for m in completed)
        def quantile(p):
            pos = (len(values) - 1) * p
            low = int(pos)
            return round(values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (pos - low))
        rows.append([cat, len(values), dollars(round(median(values))),
                     dollars(quantile(.25)), dollars(quantile(.75)),
                     dollars(round(sum(values) / len(values))),
                     'Observed monthly range; proposal only' if len(values) >= 3 else 'Insufficient history for a budget recommendation'])
    return rows


def anomalies(months, eligible):
    rows = []
    for key in sorted(eligible):
        prior = [k for k in sorted(eligible) if k < key][-3:]
        if len(prior) < 3:
            continue
        current = metrics(months[key])
        cats = set(current['categories']) | {c for k in prior for c in metrics(months[k])['categories']}
        for cat in sorted(cats):
            baseline = round(median([metrics(months[k])['categories'].get(cat, 0) for k in prior]))
            value = current['categories'].get(cat, 0)
            delta = value - baseline
            if delta >= 5000 and (baseline <= 0 or delta / baseline >= .25):
                rows.append([key, cat, dollars(value), dollars(baseline), dollars(delta),
                             round(delta / baseline, 4) if baseline > 0 else '',
                             'Above prior 3 completed months; review, not a judgment'])
    return rows
