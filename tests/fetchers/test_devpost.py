import re

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.devpost import fetch

SAMPLE_PAYLOAD = {
    "hackathons": [
        {
            "id": 31667,
            "title": "Kiroween 2026",
            "open_state": "open",
            "url": "https://kiroween.devpost.com/",
            "time_left_to_submission": "about 1 month left",
            "submission_period_dates": "Oct 30 - Dec 04, 2026",
            "themes": [
                {"id": 1, "name": "Beginner Friendly"},
                {"id": 6, "name": "Machine Learning/AI"},
            ],
            "prize_amount": "$<span data-currency-value>100,000</span>",
            "prizes_counts": {"cash": 11, "other": 2},
            "registrations_count": 5300,
            "organization_name": "Kiro",
            "invite_only": False,
        },
        {
            "id": 99991,
            "title": "Zero Cash Non-Cash Only Hack",
            "open_state": "open",
            "url": "https://zero-cash.devpost.com/",
            "time_left_to_submission": "10 days left",
            "submission_period_dates": "Oct 01 - Nov 15, 2026",
            "themes": [{"id": 6, "name": "Machine Learning/AI"}],
            "prize_amount": "$<span data-currency-value>0</span>",
            "prizes_counts": {"cash": 0, "other": 5},
            "registrations_count": 800,
            "organization_name": "ZeroOrg",
            "invite_only": False,
        },
        {
            "id": 99992,
            "title": "Private Invite Hack",
            "open_state": "open",
            "url": "https://private-invite.devpost.com/",
            "prize_amount": "$<span data-currency-value>50,000</span>",
            "prizes_counts": {"cash": 3, "other": 0},
            "registrations_count": 50,
            "organization_name": "SecretOrg",
            "invite_only": True,
        },
    ]
}


def _cfg(**params) -> SourceConfig:
    return SourceConfig(
        id="devpost_ai",
        type="devpost",
        enabled=True,
        tier=2,
        params=params,
    )


@pytest.mark.asyncio
async def test_fetch_devpost_filters_invite_only_and_zero_cash(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://devpost\.com/api/hackathons.*"),
        json=SAMPLE_PAYLOAD,
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(), client)

    assert len(items) == 1
    it = items[0]
    assert it.title == "Kiroween 2026"
    assert it.source == "devpost"
    assert it.source_handle == "Kiro"
    assert it.url == "https://kiroween.devpost.com/"
    assert "Prize: $100,000 (cash_prizes: 11, other: 2)" in it.excerpt
    assert "https://kiroween.devpost.com/resources" in it.excerpt
    assert it.engagement == {"registrations": 5300, "cash_prizes": 11}


@pytest.mark.asyncio
async def test_fetch_devpost_allows_zero_cash_when_configured(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://devpost\.com/api/hackathons.*"),
        json=SAMPLE_PAYLOAD,
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(min_cash_prizes=0), client)

    assert [it.title for it in items] == ["Kiroween 2026", "Zero Cash Non-Cash Only Hack"]


@pytest.mark.asyncio
async def test_fetch_devpost_allows_non_cash_other_prizes(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"https://devpost\.com/api/hackathons.*"),
        json=SAMPLE_PAYLOAD,
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(min_cash_prizes=1, min_other_prizes=1), client)

    assert [it.title for it in items] == ["Kiroween 2026", "Zero Cash Non-Cash Only Hack"]


@pytest.mark.asyncio
async def test_fetch_mlh_prizes_mode_parses_challenges(httpx_mock):
    sample_html = """
    <html><body>
    <script type="application/json">
    {"props":{"challenges":[
      {
        "id": "01a15a90-5b3e-62db-908a-f44b209c9a5e",
        "sponsorName": "xAI",
        "name": "Build Your Hackathon A-Team with SpaceXSI",
        "prizeDescription": "Mechanical Keyboard",
        "description": "Use Cursor and Grok Bot this weekend.",
        "sponsorCtaUrl": "https://mlh.link/spacexsi",
        "hasDynamicPromoCodes": true,
        "active": true
      },
      {
        "id": "inactive-1",
        "sponsorName": "OldSponsor",
        "name": "Inactive Challenge",
        "active": false
      }
    ]}}
    </script>
    </body></html>
    """
    httpx_mock.add_response(
        url="https://www.mlh.com/events/prizes",
        text=sample_html,
        is_reusable=True,
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(_cfg(mode="mlh_prizes"), client)

    assert len(items) == 1
    it = items[0]
    assert it.title == "[MLH] Build Your Hackathon A-Team with SpaceXSI (xAI)"
    assert it.source == "devpost"
    assert it.source_handle == "MLH"
    assert it.canonical_url == "https://www.mlh.com/events/prizes?challenge=01a15a90-5b3e-62db-908a-f44b209c9a5e"
    assert "promo_code: yes" in it.excerpt
    assert "Resources: https://mlh.link/spacexsi" in it.excerpt
    assert it.engagement == {"promo_code": 1}

