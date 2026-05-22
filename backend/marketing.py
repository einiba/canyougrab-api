"""
Marketing email preference endpoints.
Mounted at /api/account/marketing-preference.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import JWTUser, jwt_auth
from users import get_marketing_preference, set_marketing_preference, upsert_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/account', tags=['Marketing'])


class MarketingPreferenceBody(BaseModel):
    opt_in: bool
    source: Optional[str] = None


@router.get('/marketing-preference')
def read_marketing_preference(user: JWTUser = Depends(jwt_auth)):
    upsert_user(
        auth0_sub=user.sub,
        email=user.email,
        name=user.name,
        email_verified=user.email_verified,
    )
    return get_marketing_preference(user.sub)


@router.post('/marketing-preference')
def write_marketing_preference(
    body: MarketingPreferenceBody,
    user: JWTUser = Depends(jwt_auth),
):
    upsert_user(
        auth0_sub=user.sub,
        email=user.email,
        name=user.name,
        email_verified=user.email_verified,
    )
    try:
        return set_marketing_preference(user.sub, body.opt_in, body.source or '')
    except Exception as e:
        logger.error('marketing-preference write failed for %s: %s', user.sub[:20], e)
        raise HTTPException(status_code=500, detail='Failed to update preference')
