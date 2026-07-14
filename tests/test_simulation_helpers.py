import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "ros2" / "inspection_map_bridge"
sys.path.insert(0, str(PACKAGE_ROOT))

from inspection_map_bridge.simulation import interpolate_path  # noqa: E402
from inspection_map_bridge.simulation import make_costmap_data  # noqa: E402
from inspection_map_bridge.simulation import normalize_angle  # noqa: E402
from inspection_map_bridge.simulation import quaternion_to_yaw  # noqa: E402
from inspection_map_bridge.simulation import yaw_to_quaternion  # noqa: E402


class SimulationHelpersTest(unittest.TestCase):
    def test_quaternion_round_trip(self):
        expected = -1.35
        quaternion = yaw_to_quaternion(expected)
        actual = quaternion_to_yaw(*quaternion)
        self.assertTrue(math.isclose(actual, expected, abs_tol=1.0e-9))

    def test_normalize_angle(self):
        self.assertTrue(math.isclose(normalize_angle(3.0 * math.pi), math.pi))
        self.assertTrue(math.isclose(normalize_angle(-3.0 * math.pi), math.pi))

    def test_interpolate_path_includes_endpoints(self):
        points = interpolate_path(1.0, 2.0, 5.0, -2.0, 5)
        self.assertEqual(points[0], (1.0, 2.0))
        self.assertEqual(points[-1], (5.0, -2.0))
        self.assertEqual(len(points), 5)

    def test_costmap_size_and_cost_range(self):
        data = make_costmap_data(30, 20, phase=3)
        self.assertEqual(len(data), 600)
        self.assertGreaterEqual(min(data), 0)
        self.assertEqual(max(data), 100)
        self.assertTrue(any(0 < value < 100 for value in data))
