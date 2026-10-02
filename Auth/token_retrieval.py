#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#

import logging
import time

import gpsoauth
import requests

from Auth.aas_token_retrieval import get_aas_token
from Auth.fcm_receiver import FcmReceiver
from Auth.gpsoauth_response import looks_like_transient_error

logger = logging.getLogger(__name__)

TOKEN_REQUEST_RETRIES = 2
TOKEN_REQUEST_RETRY_BACKOFF_S = 2


def request_token(username, scope, play_services = False):

    aas_token = get_aas_token()
    android_id = FcmReceiver().get_android_id()
    request_app = 'com.google.android.gms' if play_services else 'com.google.android.apps.adm'

    attempt = 0
    while True:
        try:
            auth_response = gpsoauth.perform_oauth(
                username, aas_token, android_id,
                service='oauth2:https://www.googleapis.com/auth/' + scope,
                app=request_app,
                client_sig='38918a453d07199354f8b19af05ec6562ced5788')
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as ex:
            if attempt >= TOKEN_REQUEST_RETRIES:
                raise
            attempt += 1
            logger.info(
                "Google's sign-in endpoint hit a transient network error "
                "(%s) for scope '%s', retrying (%s/%s)",
                ex, scope, attempt, TOKEN_REQUEST_RETRIES,
            )
            time.sleep(TOKEN_REQUEST_RETRY_BACKOFF_S)
            continue

        if 'Auth' in auth_response:
            return auth_response['Auth']

        # Google rejected the exchange outright (e.g. {'Error': 'BadAuthentication'})
        # instead of returning a token - surfaced this as a bare KeyError before,
        # which gave no indication anything had actually gone wrong with the
        # sign-in itself rather than a bug in this code.
        if not looks_like_transient_error(auth_response):
            raise RuntimeError(
                f"Google rejected the sign-in for scope '{scope}': {auth_response}"
            )

        if attempt >= TOKEN_REQUEST_RETRIES:
            raise RuntimeError(
                f"Google's sign-in endpoint returned a transient error for "
                f"scope '{scope}' after {attempt + 1} attempts (likely a "
                f"temporary server issue on Google's end - try again later)"
            )

        attempt += 1
        logger.info(
            "Google's sign-in endpoint returned a transient error for scope "
            "'%s', retrying (%s/%s)",
            scope, attempt, TOKEN_REQUEST_RETRIES,
        )
        time.sleep(TOKEN_REQUEST_RETRY_BACKOFF_S)