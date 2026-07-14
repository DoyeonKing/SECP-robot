import xml.etree.ElementTree as element_tree
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE = (
    ROOT
    / "ros2"
    / "inspection_map_bridge"
    / "config"
    / "fastdds_udp_only.xml"
)
NAMESPACE = {"fastdds": "http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles"}


class FastDdsProfileSourceTest(unittest.TestCase):
    def test_source_profile_disables_builtin_transports_and_uses_udp(self):
        root = element_tree.parse(str(PROFILE)).getroot()
        transport_type = root.find(
            ".//fastdds:transport_descriptor/fastdds:type",
            NAMESPACE,
        )
        builtin_transports = root.find(
            ".//fastdds:participant/fastdds:rtps/fastdds:useBuiltinTransports",
            NAMESPACE,
        )
        self.assertIsNotNone(transport_type)
        self.assertEqual(transport_type.text, "UDPv4")
        self.assertIsNotNone(builtin_transports)
        self.assertEqual(builtin_transports.text, "false")
