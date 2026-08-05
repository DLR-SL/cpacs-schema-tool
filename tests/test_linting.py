from __future__ import annotations

from cpacs_schema_tool.core import lint_schema, parse_text_as_schema
from cpacs_schema_tool.policy import load_policy


def test_lint_reports_type_naming_violation() -> None:
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
    diagnostics = lint_schema(tree, load_policy(), include_unused=False)
    codes = {item.code for item in diagnostics}
    assert "CPACS004" in codes
    assert "CPACS008" not in codes
