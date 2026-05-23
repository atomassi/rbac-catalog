"""Feed generation service for RSS/Atom feeds."""

import datetime as dt
import html
from xml.etree.ElementTree import Element, SubElement, tostring

from azurerbac.cache import CacheService
from azurerbac.cache.models import CachedChangeEvent


def _build_rich_content(event: CachedChangeEvent, site_url: str) -> str:
    """Build HTML content for a role change event."""
    parts: list[str] = []

    event_label = event.event_type.replace("_", " ").title()
    role_url = f"{site_url}/roles/{event.role_id}"

    # Header
    role_escaped = html.escape(event.role_name)
    parts.append(f"<p><strong>{role_escaped}</strong> was <strong>{event_label}</strong></p>")

    # Role ID
    role_id_escaped = html.escape(event.role_id)
    parts.append(f"<p><strong>Role ID:</strong> <code>{role_id_escaped}</code></p>")

    # Summary of changed fields (only for updates, not creates/deletes)
    if event.summary and event.event_type == "updated":
        escaped_summary = html.escape(event.summary)
        parts.append(f"<p><strong>Changed fields:</strong> {escaped_summary}</p>")

    # Link to full details
    parts.append(f'<p><a href="{role_url}">View full role details →</a></p>')

    return "\n".join(parts)


def build_atom_feed(
    events: list[CachedChangeEvent],
    site_url: str,
    updated: dt.datetime,
) -> bytes:
    """Build Atom 1.0 XML feed from change events."""
    ns = "http://www.w3.org/2005/Atom"

    feed = Element("feed", xmlns=ns)

    # Feed metadata
    title = SubElement(feed, "title")
    title.text = "Azure RBAC Role Changes"

    SubElement(feed, "link", href=f"{site_url}/feeds/changelog.atom", rel="self")
    SubElement(feed, "link", href=site_url, rel="alternate")

    feed_id = SubElement(feed, "id")
    feed_id.text = f"{site_url}/feeds/changelog.atom"

    feed_updated = SubElement(feed, "updated")
    feed_updated.text = updated.isoformat()

    subtitle = SubElement(feed, "subtitle")
    subtitle.text = (
        "Track changes to Azure built-in RBAC roles - new roles, updates, and deprecations"
    )

    author = SubElement(feed, "author")
    author_name = SubElement(author, "name")
    author_name.text = "Azure RBAC Catalog"

    # Feed entries
    for event in events:
        entry = SubElement(feed, "entry")

        entry_title = SubElement(entry, "title")
        event_label = event.event_type.replace("_", " ").title()
        entry_title.text = f"{event.role_name} - {event_label}"

        # Link to role detail page
        role_url = f"{site_url}/roles/{event.role_id}"
        SubElement(entry, "link", href=role_url, rel="alternate")

        entry_id = SubElement(entry, "id")
        entry_id.text = f"{site_url}/roles/{event.role_id}#event-{event.id}"

        # Use azure_updated_on or scan_timestamp
        event_time = event.azure_updated_on or event.scan_timestamp
        if event_time:
            entry_updated = SubElement(entry, "updated")
            entry_updated.text = event_time.isoformat()
            entry_published = SubElement(entry, "published")
            entry_published.text = event_time.isoformat()

        # Rich HTML content with before/after details
        content = SubElement(entry, "content", type="html")
        content.text = _build_rich_content(event, site_url)

        # Plain text summary for preview
        summary = SubElement(entry, "summary")
        summary.text = event.summary or f"Role {event.role_name} was {event.event_type}"

        # Category for event type
        SubElement(entry, "category", term=event.event_type, label=event_label)

    xml_decl = b'<?xml version="1.0" encoding="utf-8"?>\n'
    return xml_decl + tostring(feed, encoding="unicode").encode("utf-8")


def build_rss_feed(
    events: list[CachedChangeEvent],
    site_url: str,
    updated: dt.datetime,
) -> bytes:
    """Build RSS 2.0 XML feed from change events."""
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")

    # Channel metadata
    title = SubElement(channel, "title")
    title.text = "Azure RBAC Role Changes"

    link = SubElement(channel, "link")
    link.text = site_url

    description = SubElement(channel, "description")
    description.text = (
        "Track changes to Azure built-in RBAC roles - new roles, updates, and deprecations"
    )

    last_build = SubElement(channel, "lastBuildDate")
    last_build.text = updated.strftime("%a, %d %b %Y %H:%M:%S +0000")

    # Items
    for event in events:
        item = SubElement(channel, "item")

        item_title = SubElement(item, "title")
        event_label = event.event_type.replace("_", " ").title()
        item_title.text = f"{event.role_name} - {event_label}"

        item_link = SubElement(item, "link")
        item_link.text = f"{site_url}/roles/{event.role_id}"

        guid = SubElement(item, "guid", isPermaLink="false")
        guid.text = f"{event.role_id}-{event.id}"

        # Rich HTML content with before/after details
        item_desc = SubElement(item, "description")
        item_desc.text = _build_rich_content(event, site_url)

        event_time = event.azure_updated_on or event.scan_timestamp
        if event_time:
            pub_date = SubElement(item, "pubDate")
            pub_date.text = event_time.strftime("%a, %d %b %Y %H:%M:%S +0000")

        category = SubElement(item, "category")
        category.text = event_label

    xml_decl = b'<?xml version="1.0" encoding="utf-8"?>\n'
    return xml_decl + tostring(rss, encoding="unicode").encode("utf-8")


def get_recent_events(
    cache: CacheService,
    cutoff: dt.datetime,
    limit: int,
) -> list[CachedChangeEvent]:
    """Get recent change events from cache."""
    events = cache.get_change_events() or []

    # Filter by cutoff date
    filtered = [e for e in events if e.effective_timestamp >= cutoff]

    # Sort by date descending and limit
    filtered.sort(key=lambda e: e.effective_timestamp, reverse=True)
    return filtered[:limit]
