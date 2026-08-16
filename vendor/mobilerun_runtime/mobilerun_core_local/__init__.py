"""Slim async local-device driver core, extracted from droidrun/mobilerun."""

__version__ = "0.6.0"

from mobilerun_core_local.driver.android import (
    AndroidDriver,
    AndroidPortalHttpDriver,
    validate_android_portal_url,
)
from mobilerun_core_local.driver.base import (
    DeviceDisconnectedError,
    DeviceDriver,
    PlatformUnsupportedError,
    unsupported,
)
from mobilerun_core_local.driver.ios import (
    IOSDriver,
    IOSPortalDriver,
    IOSPortalHttpDriver,
    create_ios_driver,
    detect_ios_portal_kind,
    discover_ios_device,
    discover_ios_portal,
    validate_ios_portal_url,
)
from mobilerun_core_local.driver.portal_http import (
    PortalHttpDriver,
    validate_portal_url,
)

__all__ = [
    "AndroidDriver",
    "AndroidPortalHttpDriver",
    "DeviceDriver",
    "DeviceDisconnectedError",
    "IOSDriver",
    "IOSPortalDriver",
    "IOSPortalHttpDriver",
    "PlatformUnsupportedError",
    "PortalHttpDriver",
    "create_ios_driver",
    "detect_ios_portal_kind",
    "discover_ios_device",
    "discover_ios_portal",
    "unsupported",
    "validate_android_portal_url",
    "validate_ios_portal_url",
    "validate_portal_url",
]
