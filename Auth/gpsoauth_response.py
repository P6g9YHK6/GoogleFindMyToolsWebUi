#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#


def looks_like_transient_error(response: dict) -> bool:
    """True if a gpsoauth response dict is actually an HTML error page in
    disguise, rather than a real auth rejection.

    gpsoauth's own HTTP layer never checks the response status code - it
    parses whatever body came back by splitting every line on "=". A real
    rejection comes back clean, e.g. {'Error': 'BadAuthentication'}. A
    transient 5xx from Google's infra instead returns an HTML error page,
    and that same naive parse turns it into a dict of HTML fragments -
    always including a literal "<!DOCTYPE html>" key. That's the one
    reliable signal this wasn't a real auth response at all.
    """
    return any(key.startswith("<") for key in response)
