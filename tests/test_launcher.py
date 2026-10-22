import unittest
from unittest.mock import Mock, patch

import start


class LauncherTests(unittest.TestCase):
    def test_existing_server_opens_page_without_second_server(self):
        listener = Mock()
        listener.bind.side_effect = OSError(10048, "address already in use")
        with patch.object(start.socket, "socket", return_value=listener), patch.object(
                start, "existing_project", return_value=True), patch.object(start.webbrowser, "open") as browser:
            self.assertEqual(start.main(), 0)
            browser.assert_called_once_with("http://127.0.0.1:8666")
        listener.close.assert_called_once()

    def test_other_port_owner_is_not_reused(self):
        listener = Mock()
        listener.bind.side_effect = OSError(10048, "address already in use")
        with patch.object(start.socket, "socket", return_value=listener), patch.object(
                start, "existing_project", return_value=False), patch.object(start.webbrowser, "open") as browser:
            self.assertEqual(start.main(), 1)
            browser.assert_not_called()

    def test_project_identity_requires_matching_data_directory(self):
        response = Mock()
        with patch.object(start.requests, "Session") as session:
            session.return_value.__enter__.return_value.get.return_value = response
            response.json.return_value = {"data_status": {"data_dir": start.AppConfig.STOCK_DATA_DIR}}
            self.assertTrue(start.existing_project("http://127.0.0.1:8666"))
            response.json.return_value = {"data_status": {"data_dir": "C:/another-project/stock_data"}}
            self.assertFalse(start.existing_project("http://127.0.0.1:8666"))

    def test_new_server_uses_reserved_socket(self):
        listener = Mock()
        with patch.object(start.socket, "socket", return_value=listener), patch.object(
                start.threading, "Thread"), patch("uvicorn.Server") as server:
            self.assertEqual(start.main(), 0)
        server.return_value.run.assert_called_once_with(sockets=[listener])
        listener.close.assert_called_once()
