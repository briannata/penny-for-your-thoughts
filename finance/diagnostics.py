"""User-run access check: metadata GETs only, no keys or financial data printed."""
import os
import re
import json
import urllib.request
from .sheets import Reader, session, spreadsheet_id, GoogleSheetsError


def check_token_identity(credentials):
    token = credentials.token
    if not isinstance(token, str) or not token:
        print('Google token check: no access token available')
        return
    # Google library uses POST + Bearer here; token never appears in a URL/log.
    request = urllib.request.Request('https://oauth2.googleapis.com/tokeninfo',
        data=b'', method='POST', headers={'Authorization': 'Bearer ' + token,
                                         'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            info = json.load(response)
        email = info.get('email')
        if isinstance(email, str) and re.fullmatch(r'[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+', email):
            print('Google-confirmed token email:', email)
            print('Token email matches configured account:', email == credentials.service_account_email)
        else:
            print('Google did not return a token email; identity comparison unavailable')
        scopes = str(info.get('scope', '')).split()
        print('Google-confirmed Sheets read scope:', 'https://www.googleapis.com/auth/spreadsheets.readonly' in scopes)
    except Exception as exc:
        print(f'Google token check unavailable ({type(exc).__name__}); token and response omitted')


def error_explanation(error, identifiers=()):
    """Only used for metadata requests; never display raw bodies or metadata maps."""
    message = str(error.details.get('message', 'Google supplied no explanation.'))
    for identifier in identifiers:
        if isinstance(identifier, str) and identifier:
            message = message.replace(identifier, '[redacted]')
    message = re.sub(r'-----BEGIN.*?-----END[^-]*-----', '[key redacted]', message, flags=re.S)
    message = re.sub(r'https?://\S+', '[URL redacted]', message)
    message = re.sub(r'[\w.+-]+@[\w.-]+', '[email redacted]', message)
    message = re.sub(r'\b\d{6,}\b|[A-Za-z0-9_./+-]{25,}', '[identifier redacted]', message)
    message = ' '.join(message.split())[:1200]
    return message


def check_access():
    http = session(True)
    credentials = http.credentials
    print('Effective service-account email:', credentials.service_account_email)
    print('Effective Google project:', credentials.project_id)
    print('Requested scope: https://www.googleapis.com/auth/spreadsheets.readonly')
    print('Credential source:', 'GOOGLE_SERVICE_ACCOUNT_JSON environment value'
          if os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') else 'GOOGLE_APPLICATION_CREDENTIALS file')
    print('Compare this email with the entry in each sheet\'s Share dialog.')
    print('Existing shell environment variables take precedence over .env values.')
    def verify_header(response, *args, **kwargs):
        sent = response.request.headers.get('Authorization', '')
        expected = 'Bearer ' + (credentials.token or '')
        print('Outgoing Google authorization matches issued token:', bool(credentials.token) and sent == expected)
        return response
    # Observe only whether the final prepared header matches; never print it.
    if isinstance(http.hooks, dict):
        http.hooks.setdefault('response', []).append(verify_header)
    failed = False
    ids = []
    for label, variable in [('SOURCE', 'SOURCE_SHEET_URL'), ('REPORT', 'REPORT_SHEET_URL')]:
        try:
            sid = spreadsheet_id(os.getenv(variable, ''))
            ids.append(sid)
            print(f'{label}: configured sheet ID ends in ...{sid[-8:]}')
            # Metadata only, using a read-only scope even for the report.
            Reader(sid, http, label=label.lower()).metadata()
            print(f'{label}: read access OK')
        except (GoogleSheetsError, ValueError) as exc:
            failed = True
            print(f'{label}: {exc}')
            if isinstance(exc, GoogleSheetsError):
                print('Google explanation:', error_explanation(exc, (
                    credentials.service_account_email, credentials.project_id, *ids)))
    if len(ids) == 2 and ids[0] == ids[1]:
        failed = True
        print('Source and report URLs point to the same spreadsheet; use a separate report.')
    check_token_identity(credentials)
    print('No spreadsheet changes or OpenAI calls were made. Report write permission was not tested.')
    return not failed
