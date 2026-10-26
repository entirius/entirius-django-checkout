# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The Autopay notification parsed provider XML with the standard library.

``autopay_return`` is csrf-exempt and parses the body before any hash check, so a document
that declares an entity was expanded by the worker for anyone who could reach the URL.
The body is parsed by defusedxml: a document with an entity declaration is refused on the
parse error path, and the answers of the view stay as they were.
"""

import base64
from urllib.parse import quote

import defusedxml.ElementTree
import pytest
from defusedxml import EntitiesForbidden
from django.test import RequestFactory

from django_checkout.views.payment_provider import autopay as autopay_view
from django_checkout.views.payment_provider.autopay import autopay_return

factory = RequestFactory()

CHANNEL = "synthetic-channel"
GENERIC_ERROR = b"An error occurred while processing the XML request"
ENTITY_DOCTYPE = '<!DOCTYPE transactionList [<!ENTITY e "EXPANDED">]>'


def _notification(order_id: str = "SYNTHETIC-1", doctype: str = "") -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>{doctype}'
        "<transactionList><serviceID>1</serviceID><transactions><transaction>"
        f"<orderID>{order_id}</orderID><remoteID>R1</remoteID><amount>10.00</amount>"
        "<currency>PLN</currency><paymentStatus>SUCCESS</paymentStatus>"
        "</transaction></transactions><hash>not-the-hash</hash></transactionList>"
    )


def _post(xml: str):
    encoded = quote(base64.b64encode(xml.encode()).decode())
    request = factory.post("/notify/autopay/", data=f"transactions={encoded}", content_type="text/plain")
    return autopay_return(request, channel_idx=CHANNEL)


@pytest.fixture(autouse=True)
def hash_key(monkeypatch):
    monkeypatch.setattr(autopay_view.settings, "AUTOPAY_HASH_KEY", {CHANNEL: "synthetic-hash-key"})


@pytest.fixture
def logged(monkeypatch):
    """Exceptions the view hands to its logger, in order."""
    exceptions = []
    monkeypatch.setattr(autopay_view.logger, "exception", exceptions.append)
    return exceptions


def test_parser_is_defusedxml():
    assert autopay_view.fromstring is defusedxml.ElementTree.fromstring


def test_entity_document_is_refused_as_a_parse_error(logged):
    response = _post(_notification(order_id="&e;", doctype=ENTITY_DOCTYPE))

    assert response.status_code == 500
    assert response.content == GENERIC_ERROR
    assert b"EXPANDED" not in response.content
    assert isinstance(logged[0], EntitiesForbidden)
    assert isinstance(logged[-1], ValueError)
    assert str(logged[-1]) == "Błąd parsowania XML"


def test_malformed_xml_keeps_the_parse_error_answer(logged):
    response = _post("<transactionList><serviceID>1</serviceID>")

    assert response.status_code == 500
    assert response.content == GENERIC_ERROR
    assert str(logged[-1]) == "Błąd parsowania XML"


def test_wrong_hash_keeps_the_not_confirmed_answer():
    response = _post(_notification())

    assert response.status_code == 400
    assert response["Content-Type"] == "text/xml"
    assert b"<orderID>SYNTHETIC-1</orderID><confirmation>NOTCONFIRMED</confirmation>" in response.content
