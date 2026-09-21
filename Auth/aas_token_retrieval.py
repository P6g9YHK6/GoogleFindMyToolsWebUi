#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#

import logging
import time

import gpsoauth
import requests

from Auth.auth_flow import request_oauth_account_token_flow
from Auth.fcm_receiver import FcmReceiver
from Auth.gpsoauth_response import looks_like_transient_error
from Auth.token_cache import get_cached_value_or_set, set_cached_value
from Auth.username_provider import get_username, username_string

logger = logging.getLogger(__name__)

TOKEN_EXCHANGE_RETRIES = 2
TOKEN_EXCHANGE_RETRY_BACKOFF_S = 2


def _generate_aas_token():
    username = get_username()
    android_id = FcmReceiver().get_android_id()
    token = request_oauth_account_token_flow()

    attempt = 0
    while True:
        try:
            aas_token_response = gpsoauth.exchange_token(username, token, android_id)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as ex:
            if attempt >= TOKEN_EXCHANGE_RETRIES:
                raise
            attempt += 1
            logger.info(
                "Google's token exchange endpoint hit a transient network "
                "error (%s), retrying (%s/%s)",
                ex, attempt, TOKEN_EXCHANGE_RETRIES,
            )
            time.sleep(TOKEN_EXCHANGE_RETRY_BACKOFF_S)
            continue

        if 'Token' in aas_token_response:
            break

        # Google rejected the exchange outright, or kept returning a
        # transient error past the retry budget - either way surface a
        # clear error instead of a bare KeyError on
        # aas_token_response['Token']. See Auth/token_retrieval.py's
        # matching comment for why gpsoauth can return a non-token response
        # here at all.
        if not looks_like_transient_error(aas_token_response):
            raise RuntimeError(
                f"Google rejected the token exchange: {aas_token_response}"
            )

        if attempt >= TOKEN_EXCHANGE_RETRIES:
            raise RuntimeError(
                f"Google's token exchange endpoint returned a transient "
                f"error after {attempt + 1} attempts (likely a temporary "
                f"server issue on Google's end - try again later)"
            )

        attempt += 1
        logger.info(
            "Google's token exchange endpoint returned a transient error, "
            "retrying (%s/%s)",
            attempt, TOKEN_EXCHANGE_RETRIES,
        )
        time.sleep(TOKEN_EXCHANGE_RETRY_BACKOFF_S)

    aas_token = aas_token_response['Token']

    if 'Email' in aas_token_response:
        email = aas_token_response['Email']
        set_cached_value(username_string, email)

    return aas_token


def get_aas_token():
    return get_cached_value_or_set('aas_token', _generate_aas_token)


if __name__ == '__main__':
    # This is a live Google auth token - don't pipe/redirect this into a log
    # file, CI output, or anything else that might end up somewhere less
    # trusted than your own terminal.
    print(get_aas_token())