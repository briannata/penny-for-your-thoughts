"""Google transport: source object exposes reads only; writes bind to report ID."""
import json
import os
import re


class GoogleSheetsError(RuntimeError):
    """Only locally constructed, safe-to-display diagnostics."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def google_error(response, target, operation):
    error = {}
    hints = {
        400: 'Google rejected the request. Confirm both files are native Google Sheets, not Excel files opened in Drive.',
        401: 'Google authentication failed. Check that the service-account key is active.',
        403: 'Check that Google Sheets API is enabled in the service-account project and the sheet is shared with the service-account email (source: Viewer; report: Editor).',
        404: 'Check the spreadsheet link and sharing with the service-account email.',
        429: 'Google rate limit reached. Wait a few minutes before retrying.',
    }
    hint = hints.get(response.status_code, 'Google service request failed; try again later.')
    try:
        error = response.json().get('error', {})
        reasons = {item.get('reason') for item in error.get('details', []) if isinstance(item, dict)}
        reasons.update(item.get('reason') for item in error.get('errors', []) if isinstance(item, dict))
        if reasons & {'SERVICE_DISABLED', 'accessNotConfigured'}:
            hint = 'Enable Google Sheets API in the project that owns the service account, wait a few minutes, then retry.'
        elif 'ACCESS_TOKEN_SCOPE_INSUFFICIENT' in reasons:
            hint = 'Google rejected the OAuth permission scope. Check the source/report authentication configuration.'
    except (ValueError, TypeError, AttributeError):
        pass
    return GoogleSheetsError(f'Google Sheets {target} {operation} failed (HTTP {response.status_code}). {hint}',
                             error if isinstance(error, dict) else {})


def spreadsheet_id(value):
    match = re.fullmatch(r'https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)(?:/[^\s]*)?', value.strip())
    if not match or match[1].startswith('REPLACE_'):
        raise ValueError('Configure valid, distinct SOURCE_SHEET_URL and REPORT_SHEET_URL')
    return match[1]


def session(readonly):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    raw = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    scope = 'https://www.googleapis.com/auth/spreadsheets' + ('.readonly' if readonly else '')
    if raw:
        credentials = Credentials.from_service_account_info(json.loads(raw), scopes=[scope])
    else:
        path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', '')
        if not path:
            raise ValueError('Configure Google service-account credentials')
        credentials = Credentials.from_service_account_file(path, scopes=[scope])
    return AuthorizedSession(credentials)


class Reader:
    allow_writes = False
    def __init__(self, source_id, http=None, label=None):
        self.id = source_id
        self.label = label or ('report' if self.allow_writes else 'source')
        self.http = http or session(True)
        self.base = f'https://sheets.googleapis.com/v4/spreadsheets/{self.id}'

    def request(self, method, suffix='', **kwargs):
        if method != 'GET' and not self.allow_writes:
            raise ValueError('Source client is read only')
        response = self.http.request(method, self.base + suffix, timeout=60, **kwargs)
        if response.status_code >= 400:
            # API bodies can contain financial data/identifiers. Don't put them in logs.
            raise google_error(response, self.label,
                               'read' if method == 'GET' else 'write')
        return response.json()

    def metadata(self):
        return self.request('GET', params={'fields': 'sheets.properties'})['sheets']

    def read(self, titles):
        if not titles:
            return {}
        ranges = ["'" + title.replace("'", "''") + "'!A:Z" for title in titles]
        result = self.request('GET', '/values:batchGet', params={
            'ranges': ranges, 'valueRenderOption': 'UNFORMATTED_VALUE',
            'dateTimeRenderOption': 'SERIAL_NUMBER'})
        return {title: item.get('values', []) for title, item in zip(titles, result['valueRanges'])}


class Report(Reader):
    allow_writes = True
    def __init__(self, report_id, source_id, http=None):
        if report_id == source_id:
            raise ValueError('Report must be a different spreadsheet from source')
        super().__init__(report_id, http or session(False))

    def ensure(self, names):
        meta = self.metadata()
        existing = {s['properties']['title']: s['properties'] for s in meta}
        missing = [name for name in names if name not in existing]
        if missing:
            self.request('POST', ':batchUpdate', json={'requests': [
                {'addSheet': {'properties': {'title': name, 'gridProperties': {'rowCount': 1000, 'columnCount': 26}}}}
                for name in missing]})
            existing = {s['properties']['title']: s['properties'] for s in self.metadata()}
        self.properties = existing
        return missing

    def write(self, tables):
        """Atomically replace owned tab values; strings remain literal, never formulas."""
        requests = []
        for name, rows in tables.items():
            prop = self.properties[name]
            sid = prop['sheetId']
            width = max((len(row) for row in rows), default=1)
            if len(rows) > prop['gridProperties']['rowCount']:
                requests.append({'updateSheetProperties': {'properties': {'sheetId': sid,
                    'gridProperties': {'rowCount': len(rows) + 100}}, 'fields': 'gridProperties.rowCount'}})
            if width > prop['gridProperties']['columnCount']:
                raise ValueError('Report table exceeds supported width')
            requests.append({'updateCells': {'range': {'sheetId': sid}, 'fields': 'userEnteredValue'}})
            def typed(value):
                if value is None or value == '':
                    return {}
                if isinstance(value, bool):
                    return {'userEnteredValue': {'boolValue': value}}
                if isinstance(value, (int, float)):
                    return {'userEnteredValue': {'numberValue': value}}
                value = str(value)
                if len(value) > 49000:
                    raise ValueError('Report cell too large')
                return {'userEnteredValue': {'stringValue': value}}
            requests.append({'updateCells': {'start': {'sheetId': sid, 'rowIndex': 0, 'columnIndex': 0},
                'rows': [{'values': [typed(v) for v in row]} for row in rows], 'fields': 'userEnteredValue'}})
        if requests:
            self.request('POST', ':batchUpdate', json={'requests': requests})

    def style(self, names):
        requests = []
        for name in names:
            sid = self.properties[name]['sheetId']
            requests += [
                {'updateSheetProperties': {'properties': {'sheetId': sid,
                    'gridProperties': {'frozenRowCount': 1, 'hideGridlines': True}},
                    'fields': 'gridProperties.frozenRowCount,gridProperties.hideGridlines'}},
                {'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1},
                    'cell': {'userEnteredFormat': {'backgroundColor': {'red': .12, 'green': .20, 'blue': .28},
                        'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}}},
                    'fields': 'userEnteredFormat'}},
                {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS',
                    'startIndex': 0, 'endIndex': 16}, 'properties': {'pixelSize': 165}, 'fields': 'pixelSize'}}]
            if name == 'Monthly summaries':
                requests += [
                    {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS',
                        'startIndex': 3, 'endIndex': 4}, 'properties': {'pixelSize': 750}, 'fields': 'pixelSize'}},
                    {'repeatCell': {'range': {'sheetId': sid, 'startColumnIndex': 3, 'endColumnIndex': 4},
                        'cell': {'userEnteredFormat': {'wrapStrategy': 'WRAP'}}, 'fields': 'userEnteredFormat.wrapStrategy'}}]
            if name == 'Controls':
                requests.append({'setDataValidation': {'range': {'sheetId': sid, 'startRowIndex': 1,
                    'endRowIndex': 13, 'startColumnIndex': 1, 'endColumnIndex': 2},
                    'rule': {'condition': {'type': 'BOOLEAN'}, 'strict': True}}})
        if requests:
            self.request('POST', ':batchUpdate', json={'requests': requests})
