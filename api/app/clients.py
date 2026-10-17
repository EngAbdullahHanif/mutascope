"""Who the client is: the socket address, or ``X-Forwarded-For`` from a trusted proxy only.

Rate limits are per client, so the client's address must be one it cannot choose. Anyone can
send an ``X-Forwarded-For`` header, so the header is read only when the connection itself comes
from a configured proxy (``trusted_proxies``, the Space's reverse proxy). Each proxy appends the
address it received the request from, so the list is read from the right: trusted proxies are
skipped, and the first address that is not one is the client. Whatever a client wrote into the
header itself stays to the left of that and is never used.

The server must not rewrite the socket address itself (uvicorn's ``--proxy-headers`` does so
for 127.0.0.1 by default); the start command turns that off (``api.app.serve``).
"""

from collections.abc import Iterable, Sequence
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

IPNetwork = IPv4Network | IPv6Network
UNKNOWN = "unknown"
"""The key of a request without a socket address (only in-process test transports)."""


def _parse(value: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(value.strip())
    except ValueError:
        return None


def _trusted(address: IPv4Address | IPv6Address, proxies: Sequence[IPNetwork]) -> bool:
    return any(address.version == net.version and address in net for net in proxies)


def client_address(
    peer: str | None, forwarded_for: Iterable[str], trusted_proxies: Sequence[IPNetwork]
) -> str:
    """The client's address, as a normalised string.

    Args:
        peer: The socket's remote address (ASGI ``scope["client"][0]``).
        forwarded_for: Every ``X-Forwarded-For`` header value, in order.
        trusted_proxies: Networks whose forwarded addresses are believed.

    Returns:
        ``peer`` unless it is a trusted proxy; then the rightmost forwarded address that is not
        one. A malformed entry ends the walk at the last trusted hop, so a proxy that writes
        garbage cannot hand the choice to the client.
    """
    if peer is None:
        return UNKNOWN
    hop = _parse(peer)
    if hop is None:
        return peer
    chain = [part for value in forwarded_for for part in value.split(",") if part.strip()]
    while _trusted(hop, trusted_proxies) and chain:
        previous = _parse(chain.pop())
        if previous is None:
            break
        hop = previous
    return hop.compressed
