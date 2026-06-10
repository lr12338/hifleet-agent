"""
HiFleet 船舶智能服务 Skill
8个独立工具函数，直接调用 Hifleet API
"""
from skills.hifleet_ship_service.tools import (
    ship_search,
    get_ship_position,
    get_ship_archive,
    get_psc_records,
    get_area_traffic,
    get_strait_traffic,
    upload_ship_position,
    update_ship_static_info,
    get_ship_service_tools,
)

__all__ = [
    "ship_search",
    "get_ship_position",
    "get_ship_archive",
    "get_psc_records",
    "get_area_traffic",
    "get_strait_traffic",
    "upload_ship_position",
    "update_ship_static_info",
    "get_ship_service_tools",
]
