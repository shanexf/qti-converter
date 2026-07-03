"""
Builds a QTI 2.2 content package (zip of assessmentItem XML files + imsmanifest.xml)
from a list of structured question dicts (see parser.py for the shape of each type).
"""
import io
import zipfile
from xml.sax.saxutils import escape

QTI_NS = "http://www.imsglobal.org/xsd/imsqti_v2p2"
QTI_XSI = "http://www.imsglobal.org/xsd/imsqti_v2p2 http://www.imsglobal.org/xsd/qti/qtiv2p2/imsqti_v2p2.xsd"
RP_MATCH_CORRECT = "http://www.imsglobal.org/question/qti_v2p1/rptemplates/match_correct"


def _esc(s: str) -> str:
    return escape(s or "")


def _item_header(identifier, title):
    return (
        f'<assessmentItem xmlns="{QTI_NS}" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xsi:schemaLocation="{QTI_XSI}" '
        f'identifier="{identifier}" title="{_esc(title)}" '
        f'adaptive="false" timeDependent="false">'
    )


def _mc_item(q, identifier):
    letters = [chr(ord("A") + i) for i in range(len(q["choices"]))]
    correct_letter = letters[q.get("correct_index", 0)]

    choices_xml = "".join(
        f'<simpleChoice identifier="{letters[i]}">{_esc(c["text"])}</simpleChoice>'
        for i, c in enumerate(q["choices"])
    )

    return (
        _item_header(identifier, q["prompt"][:80])
        + f'<responseDeclaration identifier="RESPONSE" cardinality="single" baseType="identifier">'
        f"<correctResponse><value>{correct_letter}</value></correctResponse>"
        f"</responseDeclaration>"
        f'<outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float">'
        f"<defaultValue><value>0</value></defaultValue></outcomeDeclaration>"
        f"<itemBody>"
        f'<p>{_esc(q["prompt"])}</p>'
        f'<choiceInteraction responseIdentifier="RESPONSE" shuffle="false" maxChoices="1">'
        f"{choices_xml}"
        f"</choiceInteraction>"
        f"</itemBody>"
        f'<responseProcessing template="{RP_MATCH_CORRECT}"/>'
        f"</assessmentItem>"
    )


def _matching_item(q, identifier):
    lefts = [(f"L{i+1}", p["left"]) for i, p in enumerate(q["pairs"])]
    rights = [(f"R{i+1}", p["right"]) for i, p in enumerate(q["pairs"])]

    left_choices = "".join(
        f'<simpleAssociableChoice identifier="{lid}" matchMax="1">{_esc(txt)}</simpleAssociableChoice>'
        for lid, txt in lefts
    )
    right_choices = "".join(
        f'<simpleAssociableChoice identifier="rid" matchMax="1">{_esc(txt)}</simpleAssociableChoice>'.replace(
            "rid", rid
        )
        for rid, txt in rights
    )
    correct_pairs = "".join(
        f"<value>{lefts[i][0]} {rights[i][0]}</value>" for i in range(len(q["pairs"]))
    )

    return (
        _item_header(identifier, q["prompt"][:80])
        + f'<responseDeclaration identifier="RESPONSE" cardinality="multiple" baseType="pair">'
        f"<correctResponse>{correct_pairs}</correctResponse>"
        f"</responseDeclaration>"
        f'<outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float">'
        f"<defaultValue><value>0</value></defaultValue></outcomeDeclaration>"
        f"<itemBody>"
        f'<p>{_esc(q["prompt"])}</p>'
        f'<matchInteraction responseIdentifier="RESPONSE" shuffle="false" maxAssociations="{len(q["pairs"])}">'
        f'<simpleMatchSet>{left_choices}</simpleMatchSet>'
        f'<simpleMatchSet>{right_choices}</simpleMatchSet>'
        f"</matchInteraction>"
        f"</itemBody>"
        f'<responseProcessing template="{RP_MATCH_CORRECT}"/>'
        f"</assessmentItem>"
    )


def _fill_blank_item(q, identifier):
    return (
        _item_header(identifier, q["prompt"][:80])
        + f'<responseDeclaration identifier="RESPONSE" cardinality="single" baseType="string">'
        f"<correctResponse><value>{_esc(q.get('answer',''))}</value></correctResponse>"
        f"</responseDeclaration>"
        f'<outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float">'
        f"<defaultValue><value>0</value></defaultValue></outcomeDeclaration>"
        f"<itemBody>"
        f'<p>{_esc(q["prompt"])} '
        f'<textEntryInteraction responseIdentifier="RESPONSE" expectedLength="15"/></p>'
        f"</itemBody>"
        f'<responseProcessing template="{RP_MATCH_CORRECT}"/>'
        f"</assessmentItem>"
    )


def _essay_item(q, identifier):
    return (
        _item_header(identifier, q["prompt"][:80])
        + f'<outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float">'
        f"<defaultValue><value>0</value></defaultValue></outcomeDeclaration>"
        f"<itemBody>"
        f'<p>{_esc(q["prompt"])}</p>'
        f'<extendedTextInteraction responseIdentifier="RESPONSE" expectedLines="10"/>'
        f"</itemBody>"
        f"</assessmentItem>"
    )


BUILDERS = {
    "multiple_choice": _mc_item,
    "truefalse": _mc_item,
    "matching": _matching_item,
    "fill_blank": _fill_blank_item,
    "essay": _essay_item,
}


def _manifest_xml(items):
    resources = "".join(
        f'<resource identifier="{iid}" type="imsqti_item_xmlv2p2" href="{iid}.xml">'
        f'<file href="{iid}.xml"/></resource>'
        for iid, _ in items
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'identifier="MANIFEST-1">'
        "<organizations/>"
        f"<resources>{resources}</resources>"
        "</manifest>"
    )


def build_qti_package(questions) -> bytes:
    """Returns the raw bytes of a zip file containing the QTI 2.2 package."""
    buf = io.BytesIO()
    items = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, q in enumerate(questions):
            builder = BUILDERS.get(q["type"])
            if not builder:
                continue
            item_id = f"item_{i+1}_{q['id']}"
            xml = '<?xml version="1.0" encoding="UTF-8"?>' + builder(q, item_id)
            zf.writestr(f"{item_id}.xml", xml)
            items.append((item_id, q))

        zf.writestr("imsmanifest.xml", _manifest_xml(items))

    buf.seek(0)
    return buf.read()
