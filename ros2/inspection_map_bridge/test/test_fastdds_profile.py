import os
import xml.etree.ElementTree as element_tree

from ament_index_python.packages import get_package_share_directory


def test_udp_only_fastdds_profile_is_installed_and_valid():
    package_share = get_package_share_directory('inspection_map_bridge')
    profile_path = os.path.join(
        package_share,
        'config',
        'fastdds_udp_only.xml',
    )
    root = element_tree.parse(profile_path).getroot()
    namespace = {'fastdds': 'http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles'}

    transport_type = root.find(
        './/fastdds:transport_descriptor/fastdds:type',
        namespace,
    )
    builtin_transports = root.find(
        './/fastdds:participant/fastdds:rtps/fastdds:useBuiltinTransports',
        namespace,
    )

    assert transport_type is not None
    assert transport_type.text == 'UDPv4'
    assert builtin_transports is not None
    assert builtin_transports.text == 'false'
