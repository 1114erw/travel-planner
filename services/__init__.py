# Services module
from .ctrip_promo_service import (
    CtripPromoService,
    CTRIP_AFFILIATE_ID,
    CTRIP_SID,
    CITY_CTRIP_CODES,
    ATTRACTION_CTRIP_IDS,
    get_ctrip_hotel_url,
    get_ctrip_ticket_url,
    get_ctrip_transport_url
)

__all__ = [
    'CtripPromoService',
    'CTRIP_AFFILIATE_ID',
    'CTRIP_SID',
    'CITY_CTRIP_CODES',
    'ATTRACTION_CTRIP_IDS',
    'get_ctrip_hotel_url',
    'get_ctrip_ticket_url',
    'get_ctrip_transport_url'
]