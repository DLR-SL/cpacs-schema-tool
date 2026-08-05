from __future__ import annotations

from cpacs_schema_tool.core import (
    ComponentKey,
    apply_type_renames,
    global_components,
    parse_text_as_schema,
    rename_plan,
    unused_type_components,
)
from cpacs_schema_tool.policy import load_policy


def test_rename_plan_and_reference_update() -> None:
    tree = parse_text_as_schema(
        """
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <xsd:element name="cpacs" type="cpacsType"/>
  <xsd:complexType name="cpacsType">
    <xsd:sequence><xsd:element name="value" type="BadName"/></xsd:sequence>
  </xsd:complexType>
  <xsd:simpleType name="BadName"><xsd:restriction base="xsd:string"/></xsd:simpleType>
</xsd:schema>
"""
    )
    plan = rename_plan(tree.getroot(), load_policy())
    assert plan["BadName"] == "badNameType"
    apply_type_renames(tree.getroot(), plan)
    components = global_components(tree.getroot())
    assert ComponentKey("type", "badNameType") in components
    element = tree.xpath("//xsd:element[@name='value']", namespaces={"xsd": "http://www.w3.org/2001/XMLSchema"})[0]
    assert element.get("type") == "badNameType"


def test_unused_type_detection() -> None:
    tree = parse_text_as_schema(
        """
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <xsd:element name="cpacs" type="cpacsType"/>
  <xsd:complexType name="cpacsType"/>
  <xsd:complexType name="unusedType"/>
</xsd:schema>
"""
    )
    unused, unresolved = unused_type_components(tree.getroot(), root_element="cpacs")
    assert not unresolved
    assert unused == [ComponentKey("type", "unusedType")]
