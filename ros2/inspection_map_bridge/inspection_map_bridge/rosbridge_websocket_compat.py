"""Run Foxy rosbridge with support for generated ROS action topic messages."""

import importlib
import os
import re
import runpy

from ament_index_python.packages import get_package_prefix
from rosbridge_library.internal import ros_loader


_ORIGINAL_LOAD_CLASS = ros_loader._load_class


def _camel_to_snake(value):
    """Convert a ROS interface class name to its generated module name."""
    return re.sub(r'(?<!^)(?=[A-Z])', '_', value).lower()


def _load_class_with_action_messages(modname, subname, classname):
    """Load standard messages first, then Foxy private action submessages."""
    try:
        return _ORIGINAL_LOAD_CLASS(modname, subname, classname)
    except (ros_loader.InvalidModuleException, ros_loader.InvalidClassException):
        feedback_suffix = '_FeedbackMessage'
        if subname != 'action' or not classname.endswith(feedback_suffix):
            raise

        action_name = classname[:-len(feedback_suffix)]
        module_name = '_{}'.format(_camel_to_snake(action_name))
        module = importlib.import_module(
            '{}.action.{}'.format(modname, module_name)
        )
        return getattr(module, classname)


def main():
    """Install the loader shim before executing the packaged server."""
    ros_loader._load_class = _load_class_with_action_messages
    prefix = get_package_prefix('rosbridge_server')
    executable = os.path.join(
        prefix,
        'lib',
        'rosbridge_server',
        'rosbridge_websocket',
    )
    runpy.run_path(executable, run_name='__main__')


if __name__ == '__main__':
    main()
