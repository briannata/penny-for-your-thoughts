import unittest
from unittest.mock import Mock
from finance.sheets import Reader, GoogleSheetsError


class GoogleErrorTests(unittest.TestCase):
    def test_disabled_api_diagnostic_does_not_expose_response(self):
        http = Mock()
        response = http.request.return_value
        response.status_code = 403
        response.json.return_value = {'error': {'message': 'PRIVATE CONTENT',
            'details': [{'reason': 'SERVICE_DISABLED', 'metadata': {'secret': 'PRIVATE KEY'}}]}}
        with self.assertRaises(GoogleSheetsError) as caught:
            Reader('private-id', http).metadata()
        message = str(caught.exception)
        self.assertIn('source read', message)
        self.assertIn('HTTP 403', message)
        self.assertIn('Enable Google Sheets API', message)
        self.assertNotIn('PRIVATE', message)
        self.assertNotIn('private-id', message)

    def test_non_json_error_is_safe(self):
        http = Mock()
        http.request.return_value.status_code = 404
        http.request.return_value.json.side_effect = ValueError('PRIVATE BODY')
        with self.assertRaises(GoogleSheetsError) as caught:
            Reader('private-id', http).metadata()
        self.assertIn('HTTP 404', str(caught.exception))
        self.assertNotIn('PRIVATE', str(caught.exception))
