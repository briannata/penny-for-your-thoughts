import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from finance.diagnostics import check_access, error_explanation, check_token_identity
from finance.sheets import GoogleSheetsError


class DiagnosticsTests(unittest.TestCase):
    def test_token_identity_check_never_prints_token(self):
        credentials = Mock(token='SECRET-TOKEN', service_account_email='test@example.invalid')
        response = io.StringIO('{"email":"test@example.invalid","scope":"https://www.googleapis.com/auth/spreadsheets.readonly"}')
        with patch('finance.diagnostics.urllib.request.urlopen', return_value=response) as send, redirect_stdout(io.StringIO()) as output:
            check_token_identity(credentials)
        self.assertIn('Token email matches configured account: True', output.getvalue())
        self.assertNotIn('SECRET-TOKEN', output.getvalue())
        req = send.call_args.args[0]
        self.assertEqual(req.method, 'POST')
        self.assertNotIn('SECRET-TOKEN', req.full_url)

    def test_google_explanation_redacts_identifiers(self):
        error = GoogleSheetsError('safe', {'message':
            'API disabled in project 123456789012. Visit https://example.test/private. Contact x@example.test. sheet-secret',
            'metadata': {'private_key': 'DO NOT PRINT'}})
        message = error_explanation(error, ['sheet-secret'])
        self.assertIn('API disabled', message)
        for secret in ('123456789012', 'https://', 'x@example.test', 'sheet-secret', 'DO NOT PRINT'):
            self.assertNotIn(secret, message)

    def test_reads_both_targets_without_printing_secrets(self):
        http = Mock()
        http.credentials.service_account_email = 'test@example.invalid'
        http.credentials.project_id = 'test-project'
        http.request.return_value.status_code = 200
        http.request.return_value.json.return_value = {'sheets': []}
        output = io.StringIO()
        with patch('finance.diagnostics.session', return_value=http) as auth, patch.dict(
            'os.environ', {'SOURCE_SHEET_URL': 'https://docs.google.com/spreadsheets/d/source12345678/edit',
                           'REPORT_SHEET_URL': 'https://docs.google.com/spreadsheets/d/report12345678/edit',
                           'GOOGLE_SERVICE_ACCOUNT_JSON': 'SECRET JSON', 'OPENAI_API_KEY': 'SECRET KEY'}), redirect_stdout(output):
            self.assertTrue(check_access())
        auth.assert_called_once_with(True)
        self.assertEqual(http.request.call_count, 2)
        self.assertTrue(all(call.args[0] == 'GET' for call in http.request.call_args_list))
        self.assertIn('test@example.invalid', output.getvalue())
        self.assertNotIn('SECRET', output.getvalue())

    def test_failed_source_still_checks_report(self):
        http = Mock()
        http.credentials.service_account_email = 'test@example.invalid'
        http.credentials.project_id = 'test-project'
        denied, allowed = Mock(), Mock()
        denied.status_code = 403
        denied.json.return_value = {}
        allowed.status_code = 200
        allowed.json.return_value = {'sheets': []}
        http.request.side_effect = [denied, allowed]
        with patch('finance.diagnostics.session', return_value=http), patch.dict('os.environ', {
            'SOURCE_SHEET_URL': 'https://docs.google.com/spreadsheets/d/source/edit',
            'REPORT_SHEET_URL': 'https://docs.google.com/spreadsheets/d/report/edit'}), redirect_stdout(io.StringIO()) as output:
            self.assertFalse(check_access())
        self.assertIn('REPORT: read access OK', output.getvalue())
