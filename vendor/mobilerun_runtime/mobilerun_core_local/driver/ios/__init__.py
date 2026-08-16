"""iOS-side drivers."""

from mobilerun_core_local.driver.ios.http import (
    IOSDriver,
    IOSPortalDriver,
    discover_ios_portal,
    validate_ios_portal_url,
)
from mobilerun_core_local.driver.ios.local import (
    IOS_LOCAL_PORTAL_DEFAULT_PORT,
    IOSPortalHttpDriver,
    create_ios_driver,
    detect_ios_portal_kind,
    discover_ios_device,
)

__all__ = [
    "IOS_LOCAL_PORTAL_DEFAULT_PORT",
    "IOSDriver",
    "IOSPortalDriver",
    "IOSPortalHttpDriver",
    "create_ios_driver",
    "detect_ios_portal_kind",
    "discover_ios_device",
    "discover_ios_portal",
    "validate_ios_portal_url",
]
