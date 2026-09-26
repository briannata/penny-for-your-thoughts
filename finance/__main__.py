import argparse
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .core import MONTHS, approve, money, parse_source, digest
from .sheets import GoogleSheetsError
from .reporting import (OWNED_TABS, initial_controls, controls_from_rows, load_state,
                        state_table, tables, ai_payload, summary_state)


@contextmanager
def local_lock():
    """OS lock automatically releases on crash. GitHub adds repository concurrency."""
    with open('.run.lock', 'a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            handle.write(b'0')
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def preview(path, year, completed):
    import openpyxl
    from html import escape
    with open(path, 'rb') as stream:
        workbook = openpyxl.load_workbook(stream, read_only=True, data_only=True)
        source = {s.title: [list(row) for row in s.iter_rows(values_only=True)] for s in workbook
                  if s.title in MONTHS + ['Fixed Expenses']}
        workbook.close()
    months = parse_source(source, year)
    states, eligible = {}, set()
    for key, month in months.items():
        states[key] = {}
        if approve(states[key], month['hash'], key in completed, 1):
            eligible.add(key)
    report = tables(months, states, eligible, now=datetime.now(timezone.utc).isoformat(), note='Offline preview; no API calls')
    directory = Path('outputs')
    directory.mkdir(exist_ok=True)
    html = ['<!doctype html><meta charset="utf-8"><title>Budget report preview</title>',
            '<style>body{font:15px system-ui;margin:32px;color:#203040}table{border-collapse:collapse;margin-bottom:32px}th{background:#203040;color:white}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}h1{font-size:28px}</style>',
            '<h1>Budget report preview</h1><p>Private local preview. Source workbook unchanged. No AI calls.</p>']
    for name, rows in report.items():
        if name == '_State':
            continue
        html += [f'<h2>{escape(name)}</h2><table>']
        for n, row in enumerate(rows):
            tag = 'th' if n == 0 else 'td'
            html.append('<tr>' + ''.join(f'<{tag}>{escape(str(value))}</{tag}>' for value in row) + '</tr>')
        html.append('</table>')
    (directory / 'preview.html').write_text('\n'.join(html), encoding='utf-8')
    print(f'Offline preview created: outputs/preview.html ({len(months)} months). No network requests.')


def sync(year, completed, no_ai=False):
    from .sheets import Reader, Report, spreadsheet_id
    from .ai import reserve, narrative
    source_url = os.environ.get('SOURCE_SHEET_URL', '')
    source_id = spreadsheet_id(source_url)
    report_id = spreadsheet_id(os.environ.get('REPORT_SHEET_URL', ''))
    if source_id == report_id:
        raise ValueError('Source and report must be different spreadsheets')
    source = Reader(source_id)
    names = [s['properties']['title'] for s in source.metadata()]
    months = parse_source(source.read([n for n in names if n in MONTHS + ['Fixed Expenses']]), year)
    report = Report(report_id, source_id)
    existing = {s['properties']['title'] for s in report.metadata()}
    # Never adopt an arbitrary populated report or silently reset a missing state tab.
    if 'Controls' in existing and '_State' not in existing:
        raise ValueError('Missing _State on existing report; restore it before running')
    if 'Controls' not in existing and (set(OWNED_TABS) & existing):
        raise ValueError('Report has reserved tab names but no Controls; use a blank report')
    new = report.ensure(['Controls'] + OWNED_TABS)
    if 'Controls' in new:
        report.write({'Controls': initial_controls(year, completed), '_State': state_table({'_meta': {'version': 1, 'source_id': source_id, 'year': year}})})
        report.style(['Controls'] + OWNED_TABS)
    data = report.read(['Controls', '_State'])
    controls = controls_from_rows(data['Controls'], year)
    state = load_state(data['_State'])
    if state.get('_meta') != {'version': 1, 'source_id': source_id, 'year': year}:
        raise ValueError('Report state/source/year mismatch; use a separate report for another year')
    eligible = set()
    for key, month in months.items():
        s = state.setdefault(key, {})
        if approve(s, month['hash'], *controls[key]):
            eligible.add(key)
    missing = [k for k in state if not k.startswith('_') and k not in months]
    for k in missing:
        state[k]['status'] = 'Source month missing'
    note = 'Source month missing: ' + ', '.join(missing) if missing else 'Refresh completed'
    now = datetime.now(timezone.utc).isoformat()
    # Publish deterministic output and stale status before any paid API call.
    report.write(tables(months, state, eligible, source_url, now, note))
    def save():
        report.write({'_State': state_table(state)})
    api_key = os.getenv('OPENAI_API_KEY', '')
    budget_cents = money(os.getenv('AI_MONTHLY_BUDGET_USD', '2'))
    if not 0 <= budget_cents <= 200:
        raise ValueError('AI_MONTHLY_BUDGET_USD must be between 0 and 2.00')
    limit = int(os.getenv('AI_MAX_SUMMARIES_PER_RUN', '2'))
    if not 0 <= limit <= 12:
        raise ValueError('AI_MAX_SUMMARIES_PER_RUN must be 0 through 12')
    attempted = 0
    for key in sorted(eligible):
        s = state[key]
        payload = ai_payload(key, months, eligible)
        payload_hash = digest(payload)
        condition = summary_state(s, months[key], payload_hash)
        if condition in ('ready', 'reapprove'):
            continue
        if no_ai or not api_key:
            s['ai_status'] = 'AI disabled / no API key'
            continue
        if attempted >= limit:
            s['ai_status'] = 'Queued for next run'
            continue
        if not reserve(state, budget_cents, save):
            s['ai_status'] = 'Monthly AI budget exhausted; calculations still update'
            continue
        attempted += 1
        try:
            summary = narrative(api_key, payload)
        except Exception:
            # Reservation remains charged even if outcome is uncertain; no immediate retry.
            s['ai_status'] = 'AI request failed; retry next run (reservation retained)'
            save()
            continue
        s.update(summary=summary, summary_hash=months[key]['hash'], payload_hash=payload_hash,
                 summary_revision=s['revision'], generated=now, ai_status='Ready')
        save()
    report.write(tables(months, state, eligible, source_url, now, note))
    print(f'Report refreshed: {len(months)} months; {attempted} AI attempts. Source unchanged.')


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # Offline preview and tests work without cloud dependencies.
    parser = argparse.ArgumentParser(description='Read source budget; write a separate report')
    parser.add_argument('--preview-xlsx', metavar='PATH', help='Offline HTML preview, no Google or OpenAI calls')
    parser.add_argument('--no-ai', action='store_true', help='Refresh Google report without paid AI calls')
    parser.add_argument('--check-access', action='store_true', help='Print effective Google identity and test sheet metadata access; no writes')
    args = parser.parse_args()
    if args.check_access:
        from .diagnostics import check_access
        if not check_access():
            sys.exit(1)
        return
    year = int(os.getenv('BUDGET_YEAR', '2026'))
    completed = set(filter(None, os.getenv('INITIAL_COMPLETE_MONTHS', ','.join(f'2026-{m:02}' for m in range(1, 9))).split(',')))
    with local_lock():
        if args.preview_xlsx:
            preview(args.preview_xlsx, year, completed)
        else:
            sync(year, completed, args.no_ai)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never print upstream request objects, credentials, or source cell contents.
        print(f'Run failed ({type(exc).__name__}). Check setup, Controls and source headers. Last successful refresh remains visible.', file=sys.stderr)
        if isinstance(exc, (ValueError, GoogleSheetsError)):
            print(str(exc), file=sys.stderr)
        sys.exit(1)
