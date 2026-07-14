"""Pure-Python helpers used by the offline ROS navigation simulation."""

import math


def normalize_angle(angle):
    """Normalize an angle to the interval (-pi, pi]."""
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


def yaw_to_quaternion(yaw):
    """Return an x, y, z, w quaternion tuple for a planar yaw."""
    return (0.0, 0.0, math.sin(yaw * 0.5), math.cos(yaw * 0.5))


def quaternion_to_yaw(x, y, z, w):
    """Return planar yaw from a quaternion."""
    return math.atan2(
        2.0 * (w * z + x * y),
        1.0 - 2.0 * (y * y + z * z),
    )


def interpolate_path(start_x, start_y, goal_x, goal_y, count):
    """Create evenly spaced two-dimensional path points."""
    count = max(2, int(count))
    points = []
    for index in range(count):
        ratio = float(index) / float(count - 1)
        points.append(
            (
                start_x + (goal_x - start_x) * ratio,
                start_y + (goal_y - start_y) * ratio,
            )
        )
    return points


def make_costmap_data(width, height, phase=0):
    """Create a deterministic costmap with walls and inflated obstacles."""
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        return []

    data = [0] * (width * height)

    def set_cost(x_value, y_value, cost):
        if 0 <= x_value < width and 0 <= y_value < height:
            offset = y_value * width + x_value
            data[offset] = max(data[offset], int(cost))

    for x_value in range(width):
        set_cost(x_value, 0, 100)
        set_cost(x_value, height - 1, 100)
    for y_value in range(height):
        set_cost(0, y_value, 100)
        set_cost(width - 1, y_value, 100)

    centers = [
        (width // 3, height // 3),
        ((2 * width) // 3, (2 * height) // 3),
        (width // 2 + int(phase) % max(1, width // 8), height // 2),
    ]
    inflation_radius = max(2, min(width, height) // 16)
    for center_x, center_y in centers:
        for delta_y in range(-inflation_radius, inflation_radius + 1):
            for delta_x in range(-inflation_radius, inflation_radius + 1):
                distance = math.hypot(delta_x, delta_y)
                if distance > inflation_radius:
                    continue
                if distance <= 1.0:
                    cost = 100
                else:
                    cost = int(80.0 * (1.0 - distance / inflation_radius)) + 10
                set_cost(center_x + delta_x, center_y + delta_y, cost)
    return data
