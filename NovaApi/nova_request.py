#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#

import binascii
import logging
import time

import requests
from bs4 import BeautifulSoup

from Auth.aas_token_retrieval import get_aas_token
from Auth.adm_token_retrieval import get_adm_token
from Auth.username_provider import get_username
from NovaApi.query_throttle import query_throttle

logger = logging.getLogger(__name__)

# requests.post() below previously had no timeout at all (could hang
# forever) and no retry, so a single transient TCP/TLS blip - e.g. Google's
# endpoint closing the connection mid-response, surfaced as
# "('Connection aborted.', RemoteDisconnected(...))" - killed the whole
# locate/forward attempt outright.
NOVA_REQUEST_TIMEOUT_S = 30
NOVA_REQUEST_RETRIES = 2
NOVA_REQUEST_RETRY_BACKOFF_S = 2
NOVA_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


def _error_text(html):
    """Google's error pages render as e.g. "Internal Server Error\\n\\n\\n\\n
    Internal Server Error\\nError 500" - collapse to one line, dropping
    repeated lines."""
    lines = []
    for line in BeautifulSoup(html, 'html.parser').get_text().splitlines():
        line = " ".join(line.split())
        if line and line not in lines:
            lines.append(line)
    return " ".join(lines)


def nova_request(api_scope, hex_payload):
    url = "https://android.googleapis.com/nova/" + api_scope

    android_device_manager_oauth_token = get_adm_token(get_username())

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Authorization": "Bearer " + android_device_manager_oauth_token,
        "Accept-Language": "en-US",
        "User-Agent": "fmd/20006320; gzip"
    }

    payload = binascii.unhexlify(hex_payload)

    attempt = 0
    while True:
        query_throttle.wait_turn()
        try:
            response = requests.post(
                url, headers=headers, data=payload, timeout=NOVA_REQUEST_TIMEOUT_S,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as ex:
            attempt += 1
            if attempt > NOVA_REQUEST_RETRIES:
                raise
            logger.info(
                "Nova request to %s hit a transient network error (%s), "
                "retrying (%s/%s)",
                api_scope, ex, attempt, NOVA_REQUEST_RETRIES,
            )
            time.sleep(NOVA_REQUEST_RETRY_BACKOFF_S)
            continue

        # Google's endpoint occasionally returns a one-off 500/502/503/504
        # for a single device while others in the same poll succeed.
        if response.status_code in NOVA_RETRYABLE_STATUS_CODES and attempt < NOVA_REQUEST_RETRIES:
            attempt += 1
            logger.info(
                "Nova request to %s returned %s, retrying (%s/%s)",
                api_scope, response.status_code, attempt, NOVA_REQUEST_RETRIES,
            )
            time.sleep(NOVA_REQUEST_RETRY_BACKOFF_S)
            continue
        break

    if response.status_code == 200:
        return response.content.hex()
    else:
        error_text = _error_text(response.text)
        # DEBUG, not WARNING: every caller already logs the raised error, and
        # logging it here too sent two notifications for one failure.
        logger.debug("Nova request to %s failed with %s: %s", api_scope, response.status_code, error_text)
        # Every caller either discards this return value or waits on some
        # other side effect of the request actually arriving (e.g. an FCM
        # push for a locate action) - silently returning None here used to
        # make a rejected request indistinguishable from "accepted, but
        # nothing ever came back", forcing callers to burn a full timeout
        # before reporting a failure that was already known immediately.
        raise RuntimeError(f"Nova request to {api_scope} failed with {response.status_code}: {error_text}")


if __name__ == '__main__':
    print(get_aas_token())