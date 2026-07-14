import math

from inspection_map_bridge.simulation import interpolate_path
from inspection_map_bridge.simulation import make_costmap_data
from inspection_map_bridge.simulation import normalize_angle
from inspection_map_bridge.simulation import quaternion_to_yaw
from inspection_map_bridge.simulation import yaw_to_quaternion


def test_quaternion_round_trip():
    expected = -1.35
    quaternion = yaw_to_quaternion(expected)
    actual = quaternion_to_yaw(*quaternion)
    assert math.isclose(actual, expected, abs_tol=1.0e-9)


def test_normalize_angle():
    assert math.isclose(normalize_angle(3.0 * math.pi), math.pi)
    assert math.isclose(normalize_angle(-3.0 * math.pi), math.pi)


def test_interpolate_path_includes_endpoints():
    points = interpolate_path(1.0, 2.0, 5.0, -2.0, 5)
    assert points[0] == (1.0, 2.0)
    assert points[-1] == (5.0, -2.0)
    assert len(points) == 5


def test_costmap_size_and_cost_range():
    data = make_costmap_data(30, 20, phase=3)
    assert len(data) == 600
    assert min(data) >= 0
    assert max(data) == 100
    assert any(0 < value < 100 for value in data)
