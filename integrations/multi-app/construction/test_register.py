"""Synthetic route guards; no server, database or credentials are accessed."""
import unittest

from register import active_switch_id


class SwitchRouteGuards(unittest.TestCase):
    def test_old_and_current_switch_listener_ports_cannot_be_reimported(self):
        for host in ("huaqing-switch", "switch", "192.168.2.11", "127.0.0.1"):
            for port in (17621, 23002, 28001):
                with self.subTest(host=host, port=port):
                    with self.assertRaisesRegex(RuntimeError, "already points to a Switch"):
                        active_switch_id("http://" + host + ":" + str(port) + "/v1")

    def test_public_stable_connection_is_still_recognized(self):
        connection = "cae973c0-daf6-42e4-ae91-cf7798a4a8af"
        self.assertEqual(
            active_switch_id("http://192.168.2.11:28001/model-api/hq/services/" + connection + "/v1"),
            connection,
        )

    def test_other_providers_can_use_the_same_port(self):
        self.assertIsNone(active_switch_id("https://api.example.test:23002/v1"))


if __name__ == "__main__":
    unittest.main()
