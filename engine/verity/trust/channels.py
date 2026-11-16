"""Which channel a report came through, read from its source id (W2.1: ``(Source)-[:VIA]->(Channel)``).

A channel is how the evidence reached us (a Google review, an app user, a driver's voice note), not who sent it.
Source ids are written by four places, so the rule is a short prefix table and nothing else:

* ``public:<channel>``  real evidence rows (``evidence.channel_source_id``): the channel is the suffix.
* ``feed:<id>``         an operator's own status feed.
* ``user-``, ``demo-user-``, ``ride-demo-user-``, ``ring-``  individual app users (simulated in the demos; a ring
  member looks like any other user, which is the point of the ring detector, so it is not a channel of its own).
* ``driver:<channel>``  a person reporting live through the API (``driver_voice``).

Anything else is ``"other"``: unknown stays unknown rather than being guessed.
"""

from __future__ import annotations

PUBLIC_PREFIX = "public:"
DRIVER_PREFIX = "driver:"
FEED_PREFIX = "feed:"
APP_USER_PREFIXES = ("user-", "demo-user-", "ride-demo-user-", "ring-")

CHANNEL_APP_USER = "app_user"
CHANNEL_OPERATOR_FEED = "operator_feed"
CHANNEL_OTHER = "other"


def channel_of(source_id: str) -> str:
    """The channel id for a source id (see the module docstring for the table)."""
    for prefix in (PUBLIC_PREFIX, DRIVER_PREFIX):
        if source_id.startswith(prefix) and len(source_id) > len(prefix):
            return source_id[len(prefix):]
    if source_id.startswith(FEED_PREFIX):
        return CHANNEL_OPERATOR_FEED
    if source_id.startswith(APP_USER_PREFIXES):
        return CHANNEL_APP_USER
    return CHANNEL_OTHER
