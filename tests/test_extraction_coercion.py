"""VLM response coercion: JSON numbers in string-typed fields must not fail the document.

max_retries=0 means there is no re-ask, so a model that emits `50` instead of `"50"`
would otherwise fail the whole document validation.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

# sha256 of json.dumps(_VLMPageExtraction.model_json_schema(), sort_keys=True, indent=1)
# captured BEFORE the before-validators were added. The validators must not change
# the schema instructor sends to the model.
_SCHEMA_SHA256_BEFORE_COERCION = "2e9a09d12dc6c2fb0bcb02af6f8e3d82086060b7c4838e6dbe79c58237782118"


def test_schema_sent_to_model_is_unchanged():
    from app.services.extraction import _VLMPageExtraction

    dumped = json.dumps(_VLMPageExtraction.model_json_schema(), sort_keys=True, indent=1)
    assert hashlib.sha256(dumped.encode()).hexdigest() == _SCHEMA_SHA256_BEFORE_COERCION


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (50, "50"),
        (12.5, "12.5"),
        (1e-05, "0.00001"),
        (50.0, "50"),
        (Decimal("12.45000000"), "12.45000000"),
        (210851, "210851"),
        ("50", "50"),
        (None, None),
    ],
)
def test_quantity_numbers_become_printed_strings(raw, expected):
    from app.services.extraction import _VLMLineItem

    assert _VLMLineItem(quantity=raw).quantity == expected


def test_line_item_no_integer_becomes_string():
    from app.services.extraction import _VLMLineItem

    assert _VLMLineItem(line_item_no=1).line_item_no == "1"


@pytest.mark.parametrize("field", ["document_number", "po_reference"])
def test_identity_fields_are_never_coerced_from_numbers(field):
    # '007' printed on the page cannot survive as the int 7, and 7 would key a
    # different PO: a number in a key field must fail loudly, not become a key.
    from app.services.extraction import _VLMPageExtraction

    with pytest.raises(ValidationError):
        _VLMPageExtraction.model_validate({"document_type": "SI", field: 210851})


def test_dn_no_is_never_coerced_from_a_number():
    from app.services.extraction import _VLMLineItem

    with pytest.raises(ValidationError):
        _VLMLineItem(dn_no=12)


def test_line_item_no_float_is_rejected_not_collapsed():
    # JSON 1.10 would print as '1.1' and collide with the separate line 1.1.
    from app.services.extraction import _VLMLineItem

    with pytest.raises(ValidationError):
        _VLMLineItem(line_item_no=1.10)


@pytest.mark.parametrize("bad", [True, False, float("nan"), float("inf"), float("-inf")])
def test_bool_and_non_finite_are_not_coerced(bad):
    from app.services.extraction import _VLMLineItem

    with pytest.raises(ValidationError):
        _VLMLineItem(quantity=bad)


def test_list_and_dict_are_not_coerced():
    from app.services.extraction import _VLMLineItem

    with pytest.raises(ValidationError):
        _VLMLineItem(quantity=[50])
    with pytest.raises(ValidationError):
        _VLMLineItem(quantity={"value": 50})


def test_whole_page_with_numeric_quantity_validates():
    from app.services.extraction import _VLMPageExtraction

    doc = _VLMPageExtraction.model_validate(
        {
            "document_type": "DN",
            "document_number": "GDN-ARS-26-4619",
            "po_reference": "210851",
            "vendor_name": "IBRAHIM ALI ALSHAB TRADING EST.",
            "line_items": [
                {"line_item_no": 1, "description": "WASHER", "quantity": 50, "dn_no": None},
                {"line_item_no": "2", "description": "NUT", "quantity": "12.5", "unit_price": 350},
            ],
        }
    )
    assert doc.po_reference == "210851"
    assert [li.quantity for li in doc.line_items] == ["50", "12.5"]
    assert doc.line_items[0].line_item_no == "1"
    assert doc.line_items[1].unit_price == "350"
