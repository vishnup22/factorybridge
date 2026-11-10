from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.bom import ChangeSetError, apply_change_set, content_hash, diff, validate_bom
from tests.fixtures import EC001, R1_RAW, r1_lines, r2_lines


def test_r1_validates_and_hash_is_stable():
    a, b = r1_lines(), r1_lines()
    assert content_hash(a) == content_hash(b)
    assert len(content_hash(a)) == 64


def test_hash_independent_of_line_order_and_attribute_order():
    reordered = list(reversed(R1_RAW))
    reordered[0] = {**reordered[0], "attributes": dict(reversed(list(reordered[0]["attributes"].items())))}
    lines, issues = validate_bom(reordered)
    assert not issues
    assert content_hash(lines) == content_hash(r1_lines())


def test_quantity_normalization_equivalence():
    a, _ = validate_bom([{**R1_RAW[0], "quantity": "2.000"}])
    b, _ = validate_bom([{**R1_RAW[0], "quantity": 2}])
    assert content_hash(a) == content_hash(b)


def test_ec001_produces_one_updated_line_and_new_hash():
    d = [x for x in diff(r1_lines(), r2_lines()) if x.change_type != "UNCHANGED"]
    assert len(d) == 1 and d[0].line_key == "ACTUATOR_MAIN" and d[0].change_type == "UPDATED"
    assert {"part_number", "part_revision", "attributes.voltage_v", "attributes.mount_pattern"} <= set(
        d[0].changed_fields
    )
    assert "BOM_PART_CHANGED" in d[0].reason_codes and "PART_REVISION_CHANGED" in d[0].reason_codes
    assert content_hash(r1_lines()) != content_hash(r2_lines())


def test_change_application_does_not_mutate_base():
    base = r1_lines()
    before = content_hash(base)
    apply_change_set(base, EC001)
    assert content_hash(base) == before


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"part_number": ""}, "MISSING_PART_NUMBER"),
        ({"quantity": 0}, "NON_POSITIVE_QUANTITY"),
        ({"quantity": -3}, "NON_POSITIVE_QUANTITY"),
        ({"quantity": "abc"}, "INVALID_QUANTITY"),
        ({"unit": "parsecs"}, "UNIT_INVALID"),
        ({"quantity": 1.5}, "UNIT_INCOMPATIBLE"),
        ({"attributes": {"voltage_v": "high"}}, "ATTRIBUTE_TYPE_INVALID"),
        ({"part_revision": ""}, "MISSING_PART_REVISION"),
    ],
)
def test_validation_rejects_bad_lines(patch, code):
    _, issues = validate_bom([{**R1_RAW[0], **patch}])
    assert code in {i.code for i in issues}


def test_duplicate_line_keys_rejected():
    _, issues = validate_bom([R1_RAW[0], R1_RAW[0]])
    assert "DUPLICATE_LINE_KEY" in {i.code for i in issues}


def test_unit_aliases_normalize():
    lines, issues = validate_bom([{**R1_RAW[0], "unit": "pcs"}])
    assert not issues and lines[0].unit == "each"


@pytest.mark.parametrize(
    ("ops", "code"),
    [
        ([{"line_key": "NOPE", "operation": "UPDATE", "new": {"quantity": 3}}], "UNKNOWN_LINE"),
        ([{"line_key": "SENSOR", "operation": "ADD", "new": {}}], "LINE_EXISTS"),
        ([{"line_key": "SENSOR", "operation": "UPDATE", "new": {"quantity": 2}}], "NO_EFFECTIVE_CHANGE"),
        ([{"line_key": "SENSOR", "operation": "FROB"}], "INVALID_OPERATION"),
        ([{"line_key": "SENSOR", "operation": "UPDATE", "new": {"quantity": 0}}], "NON_POSITIVE_QUANTITY"),
    ],
)
def test_invalid_change_sets(ops, code):
    with pytest.raises(ChangeSetError) as e:
        apply_change_set(r1_lines(), ops)
    assert code in {i.code for i in e.value.issues}


def test_add_and_remove():
    new = apply_change_set(
        r1_lines(),
        [
            {"line_key": "SENSOR", "operation": "REMOVE"},
            {
                "line_key": "LIDAR",
                "operation": "ADD",
                "new": {"part_number": "LDR-1", "part_revision": "A", "quantity": 1, "unit": "each"},
            },
        ],
    )
    kinds = {d.line_key: d.change_type for d in diff(r1_lines(), new)}
    assert kinds["SENSOR"] == "REMOVED" and kinds["LIDAR"] == "ADDED"


@given(st.permutations(R1_RAW), st.integers(min_value=1, max_value=500))
def test_property_hash_is_permutation_invariant_and_sensitive(perm, qty):
    lines, _ = validate_bom(list(perm))
    assert content_hash(lines) == content_hash(r1_lines())
    changed, issues = validate_bom([{**R1_RAW[0], "quantity": qty}, *R1_RAW[1:]])
    assert not issues
    assert (content_hash(changed) == content_hash(r1_lines())) == (Decimal(qty) == Decimal(2))
