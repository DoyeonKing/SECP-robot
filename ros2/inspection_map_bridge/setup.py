import os
from glob import glob

from setuptools import find_packages
from setuptools import setup


package_name = 'inspection_map_bridge'


setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        (
            os.path.join('share', 'ament_index', 'resource_index', 'packages'),
            [os.path.join('resource', package_name)],
        ),
        (os.path.join('share', package_name), ['package.xml', 'README.md']),
        (
            os.path.join('share', package_name, 'launch'),
            glob(os.path.join('launch', '*.launch.py')),
        ),
        (
            os.path.join('share', package_name, 'maps'),
            glob(os.path.join('maps', '*')),
        ),
        (
            os.path.join('share', package_name, 'config'),
            glob(os.path.join('config', '*')),
        ),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Inspection Map Team',
    maintainer_email='inspection@example.com',
    description=(
        'Standalone map server, rosbridge, and offline navigation data for '
        'the inspection map UI.'
    ),
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'goal_pose_action_bridge = '
            'inspection_map_bridge.goal_pose_action_bridge:main',
            'rosbridge_websocket_compat = '
            'inspection_map_bridge.rosbridge_websocket_compat:main',
            'test_data_publisher = '
            'inspection_map_bridge.test_data_publisher:main',
        ],
    },
)
