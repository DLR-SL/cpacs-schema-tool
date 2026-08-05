from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def minimal_schema_text() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<xsd:schema xmlns:xsd=\"http://www.w3.org/2001/XMLSchema\">
    <xsd:complexType name=\"cpacsType\">
        <xsd:sequence>
            <xsd:element name=\"name\" type=\"xsd:string\" minOccurs=\"1\" maxOccurs=\"1\"/>
        </xsd:sequence>
    </xsd:complexType>
    <xsd:element type=\"cpacsType\" name=\"cpacs\"/>
</xsd:schema>
"""


@pytest.fixture
def minimal_schema(tmp_path: Path, minimal_schema_text: str) -> Path:
    path = tmp_path / "schema.xsd"
    path.write_text(minimal_schema_text, encoding="utf-8")
    return path
