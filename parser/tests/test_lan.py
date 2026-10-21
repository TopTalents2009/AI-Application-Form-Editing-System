import unittest

from shared.lan import is_usable_ipv4, lan_urls, parse_ipv4_addresses


class LanAddressTests(unittest.TestCase):
    def test_rejects_loopback_linklocal_and_multicast(self):
        self.assertFalse(is_usable_ipv4("127.0.0.1"))
        self.assertFalse(is_usable_ipv4("169.254.12.3"))
        self.assertFalse(is_usable_ipv4("0.0.0.0"))
        self.assertFalse(is_usable_ipv4("255.255.255.255"))
        self.assertFalse(is_usable_ipv4("224.0.0.1"))

    def test_accepts_private_ipv4(self):
        self.assertTrue(is_usable_ipv4("192.168.2.25"))
        self.assertTrue(is_usable_ipv4("10.0.0.8"))
        self.assertTrue(is_usable_ipv4("172.16.8.1"))

    def test_parses_ipconfig_sample(self):
        sample = """
Windows IP Configuration
   IPv4 Address. . . . . . . . . . . : 192.168.2.25
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.2.1
   IPv4 Address. . . . . . . . . . . : 169.254.80.12
   IPv4 地址 . . . . . . . . . . . . : 10.8.1.36
"""
        self.assertEqual(parse_ipv4_addresses(sample), ["192.168.2.25", "10.8.1.36"])

    def test_lan_urls_include_detected_ips(self):
        import shared.lan as lan

        original = lan.list_lan_ipv4
        lan.list_lan_ipv4 = lambda: ["192.168.2.25"]
        try:
            self.assertEqual(
                lan_urls(5173),
                ["http://127.0.0.1:5173", "http://192.168.2.25:5173"],
            )
            self.assertEqual(
                lan_urls(8767, include_localhost=False),
                ["http://192.168.2.25:8767"],
            )
        finally:
            lan.list_lan_ipv4 = original


if __name__ == "__main__":
    unittest.main()
