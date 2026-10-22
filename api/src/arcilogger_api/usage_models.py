"""Response and request models of ``/api/v1/usage`` (group 2 contract section 2, MVP-CONTRACTS §12).

Field names follow the contract; fields marked "detail" are the additional ones the owner
decision of 2026-10-20 asks for ("maximum useful detail"). Durations are seconds, volumes bytes;
every one of them is an estimate (``estimated: true`` on every answer).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from arcilogger_api.auth.schemas import StrictModel

Hours = Literal["all", "work", "off"]
SubjectKind = Literal["person", "device", "ip"]


class UsagePeriod(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date
    hours: Hours


class UsageEnvelope(BaseModel):
    """Fields of every answer."""

    period: UsagePeriod
    #: Always true: the numbers are estimated from connection logs.
    estimated: bool
    #: How far the rollups have processed traffic (end of the newest computed day of the period).
    computed_until: datetime | None
    #: Text key of the method note (web locales ``usage.method_note``).
    notice_key: str


class SubjectRef(BaseModel):
    kind: SubjectKind
    #: ``person:<id>`` | ``device:<mac>`` | ``ip:<addr>``; pseudonymous: ``p:…`` / ``d:…``.
    key: str
    label: str
    pseudonymous: bool
    #: Detail: MAC and IP address, host name (null when pseudonymous or unknown).
    mac: str | None = None
    ip: str | None = None
    hostname: str | None = None


class Metrics(BaseModel):
    online_seconds: int
    active_seconds: int
    download_bytes: int
    upload_bytes: int
    sessions: int
    blocked: int
    off_hours_active_seconds: int
    first_activity: datetime | None
    last_activity: datetime | None


class SiteRow(BaseModel):
    domain: str
    #: Detail: ``domain`` (registrable domain), ``service`` (FortiGuard ISDB name) or ``ip``.
    kind: Literal["domain", "service", "ip"]
    category: str | None
    seconds: int
    #: 60 s for every minute with any traffic of the site (not split).
    raw_seconds: int
    #: Share of the active time of the same scope (0..1).
    share: float
    download_bytes: int
    upload_bytes: int
    sessions: int
    users: int
    #: Detail: all of its traffic was background traffic (no active time).
    background: bool


class CategoryRow(BaseModel):
    category: str
    seconds: int
    share: float
    download_bytes: int
    upload_bytes: int
    users: int


class AppRow(BaseModel):
    app: str
    appcat: str | None
    seconds: int
    bytes: int


class HourPoint(BaseModel):
    hour: datetime
    active_users: int
    active_seconds: int
    download_bytes: int
    upload_bytes: int


class BlockedRow(BaseModel):
    time: datetime
    domain: str | None
    category: str | None
    action: str
    source: Literal["web", "dns", "app"]
    #: Detail: full host name, URL (web filter), application (application control).
    hostname: str | None = None
    url: str | None = None
    app: str | None = None


class TimelineSpan(BaseModel):
    start: datetime
    end: datetime
    category: str | None


class HeatCell(BaseModel):
    day: date
    hour: int
    active_minutes: int


class TopSite(BaseModel):
    domain: str
    seconds: int


class TopCategory(BaseModel):
    category: str
    seconds: int


class SubjectRow(BaseModel):
    subject: SubjectRef
    department: str | None
    metrics: Metrics
    top_sites: list[TopSite]
    top_categories: list[TopCategory]


class DepartmentRow(BaseModel):
    department: str
    source: Literal["person", "vlan"]
    active_users: int
    metrics: Metrics


class CompanyUsage(UsageEnvelope):
    active_users: int
    metrics: Metrics
    hourly: list[HourPoint]
    top_sites: list[SiteRow]
    top_categories: list[CategoryRow]
    top_apps: list[AppRow]
    departments: list[DepartmentRow]


class UsagePage(UsageEnvelope):
    total: int
    limit: int
    offset: int


class SubjectPage(UsagePage):
    items: list[SubjectRow]


class DepartmentPage(UsagePage):
    items: list[DepartmentRow]


class HostnameRow(BaseModel):
    hostname: str
    domain: str
    seconds: int
    bytes: int
    sessions: int


class UrlRow(BaseModel):
    url: str
    hostname: str | None
    hits: int
    first_seen: datetime
    last_seen: datetime
    category: str | None
    #: Detail: at least one hit was blocked.
    blocked: bool


class SubjectHour(BaseModel):
    hour: datetime
    active_seconds: int
    online_seconds: int
    download_bytes: int
    upload_bytes: int
    top_domain: str | None


class DeviceUsage(BaseModel):
    device: SubjectRef
    metrics: Metrics
    top_sites: list[TopSite]


class SubjectDetail(UsageEnvelope):
    subject: SubjectRef
    department: str | None
    devices: list[SubjectRef]
    metrics: Metrics
    timeline: list[TimelineSpan]
    sites: list[SiteRow]
    categories: list[CategoryRow]
    apps: list[AppRow]
    hostnames: list[HostnameRow]
    urls: list[UrlRow]
    blocked: list[BlockedRow]
    hourly: list[SubjectHour]
    per_device: list[DeviceUsage]
    heatmap: list[HeatCell]


class SitePage(UsagePage):
    items: list[SiteRow]


class SiteHost(BaseModel):
    hostname: str
    seconds: int
    bytes: int
    #: Detail.
    sessions: int


class SiteDetail(UsageEnvelope):
    domain: str
    category: str | None
    totals: SiteRow
    #: At most 50, most time first.
    hostnames: list[SiteHost]
    #: The users of the site (at most 200, most time first; the exports hold them all). Their
    #: metrics are this site's only: active = time on the site, online = its raw time, no
    #: blocked attempts or first/last activity (not kept per site).
    users: list[SubjectRow]
    #: How many users the site has (``users`` may show fewer).
    users_total: int


class CategoryList(UsageEnvelope):
    items: list[CategoryRow]


class CategoryDetail(UsageEnvelope):
    category: str
    totals: CategoryRow
    #: At most 100, most time first (the exports hold them all).
    sites: list[SiteRow]
    #: How many sites the category has (``sites`` may show fewer).
    sites_total: int
    #: The users of the category (at most 200, most time first; the exports hold them all).
    #: Their metrics are this category's only: active = time in it; online 0 (the minutes with
    #: traffic on any of its sites are not kept), no blocked attempts or first/last activity.
    users: list[SubjectRow]
    #: How many users the category has (``users`` may show fewer).
    users_total: int


class RebuildRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: date = Field(alias="from")
    to: date


class RebuildJob(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: int
    from_: date = Field(alias="from")
    to: date
    status: Literal["queued", "running", "done", "failed"]
    #: Share of the days done (0..1).
    progress: float
    error: str | None
    #: Detail.
    requested_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PseudonymRow(BaseModel):
    pseudonym: str
    kind: SubjectKind
    #: The real subject (names, MAC, address).
    subject: SubjectRef
