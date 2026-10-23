"""Black-box contract tests for structured legal XML and open-source bridge adapters."""

from __future__ import annotations

import io
import json
import socket
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from .conftest import make_native_pdf


def _make_rii_zip_bytes(
    doknr: str = "JURE100055033",
    aktenzeichen: str = "VIII ZR 12/25",
    ecli: str = "ECLI:DE:BGH:2026:150426UVIIIZR12.25.0",
) -> bytes:
    buf = io.BytesIO()
    xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <doknr>{doknr}</doknr>
  <ecli>{ecli}</ecli>
  <gertyp>BGH</gertyp>
  <entsch-datum>20260415</entsch-datum>
  <aktenzeichen>{aktenzeichen}</aktenzeichen>
  <titelzeile>Kaufrechtliche Gewährleistung bei digitalen Produkten</titelzeile>
  <tenor><p>Die Revision der Beklagten gegen das Urteil des Oberlandesgerichts wird zurückgewiesen.</p></tenor>
  <entscheidungsgruende><p>Die zulässige Revision hat in der Sache keinen Erfolg. Der Kläger hat Anspruch auf Schadensersatz.</p></entscheidungsgruende>
</dokument>""".encode("utf-8")
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{doknr}.xml", xml_payload)
    return buf.getvalue()


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class _BridgeFixtureHandler(BaseHTTPRequestHandler):
    sample_pdf: bytes = b""
    scotus_pdf_1: bytes = b""
    scotus_pdf_2: bytes = b""
    secondary_netloc: str = ""
    active_source_id: str = ""
    rii_zip: bytes = _make_rii_zip_bytes(
        "JURE100055033", "VIII ZR 12/25", "ECLI:DE:BGH:2026:150426UVIIIZR12.25.0"
    )
    rii_zip_2: bytes = _make_rii_zip_bytes(
        "JURE100055034", "VIII ZR 13/25", "ECLI:DE:BGH:2026:150426UVIIIZR13.25.0"
    )

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/xml/akn-act-2026.xml":
            body = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="publicGeneralAct">
    <meta>
      <identification source="#legislature">
        <FRBRWork>
          <FRBRthis value="/akn/uk/act/2026/15/!main"/>
          <FRBRuri value="/akn/uk/act/2026/15"/>
          <FRBRdate date="2026-03-15" name="enactment"/>
          <FRBRauthor href="#parliament"/>
          <FRBRnumber value="2026 c. 15"/>
        </FRBRWork>
      </identification>
    </meta>
    <preface>
      <longTitle><p><docTitle>Digital Legal Accountability Act 2026</docTitle></p></longTitle>
      <p><docNumber>2026 c. 15</docNumber></p>
    </preface>
    <body>
      <section eId="sec_1">
        <num>1</num>
        <heading>Short title and commencement</heading>
        <content><p>This Act may be cited as the Digital Legal Accountability Act 2026.</p></content>
      </section>
    </body>
  </act>
</akomaNtoso>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/atom.xml"):
            body = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><link rel="alternate" type="application/akn+xml" href="/uksc/2024/1/data.xml"/></entry>
  <entry><link rel="alternate" type="application/akn+xml" href="/uksc/2024/2/data.xml"/></entry>
</feed>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/atom+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/uksc/2024/1/data.xml":
            body = """<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" xmlns:html="http://www.w3.org/1999/xhtml" xmlns:uk="https://caselaw.nationalarchives.gov.uk/akn">
  <judgment name="judgment">
    <meta>
      <identification source="#tna">
        <FRBRWork>
          <FRBRthis value="https://caselaw.nationalarchives.gov.uk/id/uksc/2024/1"/>
          <FRBRuri value="https://caselaw.nationalarchives.gov.uk/id/uksc/2024/1"/>
          <FRBRdate date="2024-01-11" name="judgment"/>
          <FRBRauthor href="#uksc"/>
          <FRBRcountry value="GB-UKM"/>
          <FRBRnumber value="1"/>
          <FRBRname value="Paul and another v Royal Wolverhampton NHS Trust"/>
        </FRBRWork>
      </identification>
      <references source="#tna">
        <TLCOrganization eId="uksc" href="https://www.supremecourt.uk/" showAs="The Supreme Court"/>
      </references>
      <proprietary source="#">
        <uk:court>UKSC</uk:court>
        <uk:year>2024</uk:year>
        <uk:number>1</uk:number>
        <uk:cite>[2024] UKSC 1</uk:cite>
      </proprietary>
      <presentation source="#">
        <html:style>#judgment { font-size: 12pt; }</html:style>
      </presentation>
    </meta>
    <header>
      <p class="JudgmentReferences"><neutralCitation>[2024] UKSC 1</neutralCitation></p>
      <p class="JudgmentTitle">JUDGMENT</p>
    </header>
    <judgmentBody>
      <decision>
        <paragraph eId="para_1"><num>1.</num><content><p>This appeal concerns secondary victim claims in clinical negligence.</p></content></paragraph>
      </decision>
    </judgmentBody>
  </judgment>
</akomaNtoso>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/uksc/2024/2/data.xml":
            body = """<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" xmlns:html="http://www.w3.org/1999/xhtml" xmlns:uk="https://caselaw.nationalarchives.gov.uk/akn">
  <judgment name="judgment">
    <meta>
      <identification source="#tna">
        <FRBRWork>
          <FRBRthis value="https://caselaw.nationalarchives.gov.uk/id/uksc/2024/2"/>
          <FRBRuri value="https://caselaw.nationalarchives.gov.uk/id/uksc/2024/2"/>
          <FRBRdate date="2024-01-11" name="judgment"/>
          <FRBRauthor href="#uksc"/>
          <FRBRcountry value="GB-UKM"/>
          <FRBRnumber value="2"/>
          <FRBRname value="Herculito Maritime Ltd v Gunvor International BV"/>
        </FRBRWork>
      </identification>
      <references source="#tna">
        <TLCOrganization eId="uksc" href="https://www.supremecourt.uk/" showAs="The Supreme Court"/>
      </references>
      <proprietary source="#">
        <uk:court>UKSC</uk:court>
        <uk:year>2024</uk:year>
        <uk:number>2</uk:number>
        <uk:cite>[2024] UKSC 2</uk:cite>
      </proprietary>
      <presentation source="#">
        <html:style>#judgment { font-size: 12pt; }</html:style>
      </presentation>
    </meta>
    <header>
      <p class="JudgmentReferences"><neutralCitation>[2024] UKSC 2</neutralCitation></p>
      <p class="JudgmentTitle">JUDGMENT</p>
    </header>
    <judgmentBody>
      <decision>
        <paragraph eId="para_1"><num>1.</num><content><p>This appeal concerns piracy insurance and general average in maritime law.</p></content></paragraph>
      </decision>
    </judgmentBody>
  </judgment>
</akomaNtoso>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/gii-toc.xml":
            body = """<?xml version="1.0" encoding="UTF-8"?>
<items>
  <item><title>Grundgesetz</title><link>/gg/xml.zip</link></item>
  <item><title>Bürgerliches Gesetzbuch</title><link>/bgb/xml.zip</link></item>
</items>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/gg/xml.zip":
            import io
            import zipfile

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "BJNR000010949.xml",
                    """<?xml version="1.0" encoding="UTF-8" ?><!DOCTYPE dokumente SYSTEM "http://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd">
<dokumente builddate="20260506174650" doknr="BJNR000010949">
  <norm builddate="20260506174650" doknr="BJNR000010949">
    <metadaten>
      <jurabk>GG</jurabk>
      <ausfertigung-datum manuell="ja">1949-05-23</ausfertigung-datum>
      <fundstelle typ="amtlich"><periodikum>BGBl</periodikum><zitstelle>1949, 1</zitstelle></fundstelle>
      <langue>Grundgesetz für die Bundesrepublik Deutschland</langue>
    </metadaten>
  </norm>
  <norm builddate="20260506174650" doknr="BJNR000010949BJNE001600314">
    <metadaten>
      <jurabk>GG</jurabk>
      <enbez>Art 1</enbez>
    </metadaten>
    <textdaten>
      <text format="XML"><Content><P>(1) Die Würde des Menschen ist unantastbar. Sie zu achten und zu schützen ist Verpflichtung aller staatlichen Gewalt.</P></Content></text>
    </textdaten>
  </norm>
</dokumente>""",
                )
            body = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/bgb/xml.zip":
            import io
            import zipfile

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "BJNR001950896.xml",
                    """<?xml version="1.0" encoding="UTF-8" ?><!DOCTYPE dokumente SYSTEM "http://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd">
<dokumente builddate="20260506174650" doknr="BJNR001950896">
  <norm builddate="20260506174650" doknr="BJNR001950896">
    <metadaten>
      <jurabk>BGB</jurabk>
      <ausfertigung-datum manuell="ja">1896-08-18</ausfertigung-datum>
      <fundstelle typ="amtlich"><periodikum>RGBl</periodikum><zitstelle>1896, 195</zitstelle></fundstelle>
      <langue>Bürgerliches Gesetzbuch</langue>
    </metadaten>
  </norm>
  <norm builddate="20260506174650" doknr="BJNR001950896BJNE000102377">
    <metadaten>
      <jurabk>BGB</jurabk>
      <enbez>§ 1</enbez>
    </metadaten>
    <textdaten>
      <text format="XML"><Content><P>Die Rechtsfähigkeit des Menschen beginnt mit der Vollendung der Geburt.</P></Content></text>
    </textdaten>
  </norm>
</dokumente>""",
                )
            body = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/download.html":
            body = b"""<!DOCTYPE html><html><head><title>USC Downloads</title></head><body>
            <a href="/download/releasepoints/us/pl/119/121/xml_usc01@119-121.zip">Title 1</a>
            <a href="/download/releasepoints/us/pl/119/121/xml_usc02@119-121.zip">Title 2</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/download/releasepoints/us/pl/119/121/xml_usc01@119-121.zip":
            import io
            import zipfile

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "usc01.xml",
                    """<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xml:lang="en" identifier="/us/usc/t1" xmlns="http://xml.house.gov/schemas/uslm/1.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">
  <meta>
    <dc:title>Title 1</dc:title>
    <dc:type>USCTitle</dc:type>
    <docNumber>1</docNumber>
    <dc:publisher>OLRC</dc:publisher>
    <dcterms:created>2026-09-09T11:00:02</dcterms:created>
  </meta>
  <main>
    <title identifier="/us/usc/t1">
      <num value="1">Title 1—</num>
      <heading>GENERAL PROVISIONS</heading>
      <chapter identifier="/us/usc/t1/ch1">
        <heading>RULES OF CONSTRUCTION</heading>
        <section identifier="/us/usc/t1/s1">
          <heading>Words denoting number, gender, and so forth</heading>
          <content><p>In determining the meaning of any Act of Congress, words importing the singular include and apply to several persons, parties, or things.</p></content>
        </section>
      </chapter>
    </title>
  </main>
</uscDoc>""",
                )
            body = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/download/releasepoints/us/pl/119/121/xml_usc02@119-121.zip":
            import io
            import zipfile

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "usc02.xml",
                    """<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xml:lang="en" identifier="/us/usc/t2" xmlns="http://xml.house.gov/schemas/uslm/1.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">
  <meta>
    <dc:title>Title 2 - The Congress</dc:title>
    <dc:type>USCTitle</dc:type>
    <docNumber>2</docNumber>
    <dc:publisher>OLRC</dc:publisher>
    <dcterms:created>2026-09-09T11:00:02</dcterms:created>
  </meta>
  <main>
    <title identifier="/us/usc/t2">
      <num value="2">Title 2—</num>
      <heading>THE CONGRESS</heading>
      <chapter identifier="/us/usc/t2/ch1">
        <heading>ELECTION OF SENATORS AND REPRESENTATIVES</heading>
        <section identifier="/us/usc/t2/s1">
          <heading>Time for election of Senators</heading>
          <content><p>At the regular election held in any State next preceding the expiration of the term for which any Senator was elected to represent such State in Congress, a Senator shall be elected by the people thereof.</p></content>
        </section>
      </chapter>
    </title>
  </main>
</uscDoc>""",
                )
            body = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/versioner/v1/structure/2024-01-01/title-1.json":
            payload = {
                "type": "title",
                "identifier": "1",
                "children": [
                    {
                        "type": "chapter",
                        "identifier": "I",
                        "children": [
                            {"type": "part", "identifier": "1", "reserved": False},
                            {"type": "part", "identifier": "2", "reserved": False},
                        ],
                    }
                ],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/versioner/v1/full/2024-01-01/title-1.xml?part=1":
            body = """<?xml version="1.0"?>
<DIV5 N="1" TYPE="PART" VOLUME="1" hierarchy_metadata="{&amp;quot;path&amp;quot;:&amp;quot;/on/2024-01-01/title-1/part-1&amp;quot;,&amp;quot;citation&amp;quot;:&amp;quot;1 CFR Part 1&amp;quot;}">
  <HEAD>PART 1—DEFINITIONS</HEAD>
  <AUTH><HED>Authority:</HED><PSPACE>44 U.S.C. 1506; sec. 6, E.O. 10530.</PSPACE></AUTH>
  <DIV8 N="1.1" TYPE="SECTION" hierarchy_metadata="{&quot;path&quot;:&quot;/on/2024-01-01/title-1/section-1.1&quot;,&quot;citation&quot;:&quot;1 CFR 1.1&quot;}">
    <HEAD>§ 1.1 Definitions.</HEAD>
    <P>As used in this chapter, unless the context requires otherwise, Administrative Committee means the Administrative Committee of the Federal Register.</P>
  </DIV8>
</DIV5>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/versioner/v1/full/2024-01-01/title-1.xml?part=2":
            body = """<?xml version="1.0"?>
<DIV5 N="2" TYPE="PART" VOLUME="1" hierarchy_metadata="{&amp;quot;path&amp;quot;:&amp;quot;/on/2024-01-01/title-1/part-2&amp;quot;,&amp;quot;citation&amp;quot;:&amp;quot;1 CFR Part 2&amp;quot;}">
  <HEAD>PART 2—GENERAL INFORMATION</HEAD>
  <AUTH><HED>Authority:</HED><PSPACE>44 U.S.C. 1506; sec. 6, E.O. 10530.</PSPACE></AUTH>
  <DIV8 N="2.1" TYPE="SECTION" hierarchy_metadata="{&quot;path&quot;:&quot;/on/2024-01-01/title-1/section-2.1&quot;,&quot;citation&quot;:&quot;1 CFR 2.1&quot;}">
    <HEAD>§ 2.1 Scope and purpose.</HEAD>
    <P>This part sets forth general information regarding the Administrative Committee of the Federal Register.</P>
  </DIV8>
</DIV5>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/eng/XML/Legis.xml":
            body = """<?xml version="1.0" encoding="UTF-8"?>
<ActsRegsList>
  <Acts>
    <Act><LinkToXML>/eng/XML/A-1.xml</LinkToXML></Act>
    <Act><LinkToXML>/eng/XML/C-46.xml</LinkToXML></Act>
  </Acts>
</ActsRegsList>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/eng/XML/A-1.xml":
            body = """<?xml version="1.0"?>
<Statute lims:pit-date="2026-06-14" lims:lastAmendedDate="2026-06-14" lims:current-date="2026-06-14" xml:lang="en" xmlns:lims="http://justice.gc.ca/lims">
  <Identification>
    <LongTitle>An Act to extend the present laws of Canada that provide access to information under the control of the Government of Canada</LongTitle>
    <ShortTitle status="official">Access to Information Act</ShortTitle>
    <Chapter><ConsolidatedNumber official="yes">A-1</ConsolidatedNumber></Chapter>
  </Identification>
  <Body>
    <Heading level="1"><TitleText>Short Title</TitleText></Heading>
    <Section><MarginalNote>Short title</MarginalNote><Label>1</Label><Text>This Act may be cited as the Access to Information Act.</Text></Section>
  </Body>
</Statute>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/eng/XML/C-46.xml":
            body = """<?xml version="1.0"?>
<Statute lims:pit-date="2026-06-14" lims:lastAmendedDate="2026-06-14" lims:current-date="2026-06-14" xml:lang="en" xmlns:lims="http://justice.gc.ca/lims">
  <Identification>
    <LongTitle>An Act respecting the Criminal Law</LongTitle>
    <ShortTitle status="official">Criminal Code</ShortTitle>
    <Chapter><ConsolidatedNumber official="yes">C-46</ConsolidatedNumber></Chapter>
  </Identification>
  <Body>
    <Heading level="1"><TitleText>Short Title</TitleText></Heading>
    <Section><MarginalNote>Short title</MarginalNote><Label>1</Label><Text>This Act may be cited as the Criminal Code.</Text></Section>
  </Body>
</Statute>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/wwl/congbao-list.html":
            body = """<html><body>
            <div class="congbao-item">
              <a class="doc-link" href="/wwl/detail/cb-2026-01.html">Nghị định 55/2026/NĐ-CP về thương mại điện tử</a>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/wwl/detail/cb-2026-01.html":
            body = """<html><head><title>Nghị định 55/2026/NĐ-CP</title></head><body>
            <h1>Nghị định 55/2026/NĐ-CP về thương mại điện tử</h1>
            <span class="doc-number">55/2026/NĐ-CP</span>
            <span class="issuer">Chính phủ</span>
            <span class="published-at">2026-04-10</span>
            <div class="content"><p>Điều 1. Phạm vi điều chỉnh của Nghị định này.</p></div>
            <a href="/wwl/files/nd55.pdf">Toàn văn PDF</a>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in (
            "/wwl/files/nd55.pdf",
            "/juriscraper/opinions/25-1001.pdf",
            "/cpp/files/vbpq/2026/04/105-nd.signed.pdf",
            "/cpp/files/vbpq/2026/04/106-nd.signed.pdf",
            "/content/pkg/FR-2026-04-20/pdf/2026-08801.pdf",
            "/Biennium/2025-26/Pdf/Bills/House%20Bills/1091-S.E.pdf",
            "/pdf/2026/07/01/us_v_nemc.pdf",
            "/uploadFiles/host/local/du-thao-01.pdf",
            "/uploadFiles/host/local/du-thao-02.pdf",
            "/scc-csc/scc-csc/en/item/40000/judgment.pdf",
            "/scc-csc/scc-csc/en/item/40001/judgment.pdf",
        ):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_BridgeFixtureHandler.sample_pdf)))
            self.end_headers()
            self.wfile.write(_BridgeFixtureHandler.sample_pdf)
        elif self.path.startswith("/vanban/"):
            cookie_hdr = self.headers.get("Cookie", "")
            if "D1N=" not in cookie_hdr:
                body = b'<html><body><script>document.cookie="D1N=ok123"+"; expires=Thu, 31-Dec-37 23:55:55 GMT; path=/";window.location.reload(true);</script></body></html>'
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(_BridgeFixtureHandler.sample_pdf)))
                self.end_headers()
                self.wfile.write(_BridgeFixtureHandler.sample_pdf)
        elif self.path == "/juriscraper/ca1-opinions.json":
            payload = {
                "court_id": "ca1",
                "court_name": "United States Court of Appeals for the First Circuit",
                "cases": [
                    {
                        "case_name": "United States v. Acme Data Corp.",
                        "docket_number": "25-1001",
                        "date_filed": "2026-06-12",
                        "precedential_status": "Published",
                        "download_url": "/juriscraper/opinions/25-1001.pdf",
                        "detail_url": "/juriscraper/ca1-opinions.json",
                    }
                ],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/oldp/bgh-cases.html":
            body = """<html><body>
            <article class="case-item" data-ecli="ECLI:DE:BGH:2026:150426UVIII_ZR_12_25.0">
              <h2 class="title">BGH Urteil vom 15.04.2026 - VIII ZR 12/25</h2>
              <span class="file-number">VIII ZR 12/25</span>
              <span class="court">Bundesgerichtshof (BGH)</span>
              <span class="date">2026-04-15</span>
              <div class="tenor"><p>Die Revision der Beklagten wird zurückgewiesen.</p></div>
            </article>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/opencaselaw/ch-bger.html":
            body = """<html><body>
            <div class="decision" data-id="BGE-152-III-101">
              <h1 class="title">Urteil 4A_101/2026 vom 20. Mai 2026 (BGE 152 III 101)</h1>
              <span class="docket">4A_101/2026</span>
              <span class="court">Schweizerisches Bundesgericht</span>
              <span class="date">2026-05-20</span>
              <div class="body"><p>Das Bundesgericht erkennt: Die Beschwerde wird abgewiesen.</p></div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/openstates/bills.json":
            payload = {
                "jurisdiction": "United States Congress",
                "bills": [
                    {
                        "identifier": "HR 4200",
                        "title": "Open Federal Legal Materials Act of 2026",
                        "classification": ["bill"],
                        "from_organization": "U.S. House of Representatives",
                        "latest_action_date": "2026-02-20",
                        "status": "Passed House",
                        "url": "/openstates/bills.json",
                        "abstract": "A bill to require machine-readable publication of federal legal materials.",
                    }
                ],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/shenpan/gengduo/77.html":
            body = """<html><head><meta charset="utf-8"><title>指导案例列表</title></head><body>
            <div class="list">
              <a href="/shenpan/xiangqing/202601.html">指导性案例220号：甲公司诉乙公司数据权益纠纷案</a>
              <a href="/shenpan/gengduo/76.html">下一页</a>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/shenpan/gengduo/76.html":
            body = """<html><head><meta charset="utf-8"><title>指导案例列表 第2页</title></head><body>
            <div class="list">
              <a href="/shenpan/xiangqing/202602.html">指导性案例221号：丙公司诉丁公司算法商业秘密纠纷案</a>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/shenpan/gengduo/empty.html":
            body = """<html><head><meta charset="utf-8"><title>改版后的空列表</title></head><body>
            <div class="new-layout-without-detail-links"><p>暂无匹配链接</p></div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/shenpan/xiangqing/202601.html":
            body = """<html><head><meta charset="utf-8"><title>指导性案例220号</title></head><body>
            <div class="detail">
              <div class="title">指导性案例220号：甲公司诉乙公司数据权益纠纷案</div>
              <ul><li>发布时间：2026-03-15</li></ul>
              <div class="txt_txt">
                <p>关键词：民事/数据权益/不正当竞争/裁判要点。人民法院审理涉数据权益民事纠纷案件，应当依法保护企业合法收集与加工的数据集合权益。</p>
              </div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/shenpan/xiangqing/202602.html":
            body = """<html><head><meta charset="utf-8"><title>指导性案例221号</title></head><body>
            <div class="detail">
              <div class="title">指导性案例221号：丙公司诉丁公司算法商业秘密纠纷案</div>
              <ul><li>发布时间：2026-03-16</li></ul>
              <div class="txt_txt">
                <p>关键词：民事/侵害商业秘密/算法参数/裁判要点。人民法院认定算法模型参数构成商业秘密时，应当综合审查其非公知性与保密措施。</p>
              </div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/":
            active = _BridgeFixtureHandler.active_source_id
            if active == "dd-492":
                inner = """<form method="post" action="/">
              <input type="hidden" name="__VIEWSTATE" value="vs1" />
              <table id="ctrl_grvDocument">
                <tr><td><a href="/page/pagenew.aspx?docid=210001">Nghị định 105/2026/NĐ-CP</a></td></tr>
                <tr><td><a href="/page/pagenew.aspx?docid=210002">Nghị định 106/2026/NĐ-CP</a></td></tr>
                <tr><td><span>1</span></td></tr>
              </table>
            </form>"""
            elif active == "dd-493":
                inner = """<div class="congbao-home">
              <a href="/van-ban/quyet-dinh-48-2026-qd-ttg.htm">Quyết định 48/2026/QĐ-TTg của Thủ tướng Chính phủ</a>
              <a href="/van-ban/quyet-dinh-49-2026-qd-ttg.htm">Quyết định 49/2026/QĐ-TTg của Thủ tướng Chính phủ</a>
            </div>"""
            elif active == "dd-513":
                inner = """<div class="congbao-home">
              <a href="/chi-thi-so-37-ct-ttg-tang-cuong-phong-chong-gian-lan-119261021100102474.htm">Chỉ thị số 37/CT-TTg</a>
            </div>"""
            elif active == "dd-495":
                inner = """<div class="congbao-home">
              <a href="/chung-khoan/thong-tu-145-2026-tt-btc-quy-dinh-dang-ky-449607-d1.html">Thông tư 145/2026/TT-BTC</a>
            </div>"""
            elif active in {"dd-512", "dd-575"}:
                inner = """<div class="congbao-home">
              <a href="/dt/luat-sua-doi-bo-sung-mot-so-dieu-cua-09-luat-ve-quan-su-quoc-phong/260805100059615084">Luật sửa đổi, bổ sung một số điều của 09 luật về quân sự, quốc phòng</a>
              <a href="/dt/luat-hoa-giai-o-co-so-sua-doi/260522145512086084">Luật Hòa giải ở cơ sở (sửa đổi)</a>
            </div>"""
            else:
                inner = """<form method="post" action="/">
              <input type="hidden" name="__VIEWSTATE" value="vs1" />
              <table id="ctrl_grvDocument">
                <tr><td><a href="/page/pagenew.aspx?docid=210001">Nghị định 105/2026/NĐ-CP</a></td></tr>
                <tr><td><a href="/page/pagenew.aspx?docid=210002">Nghị định 106/2026/NĐ-CP</a></td></tr>
                <tr><td><span>1</span></td></tr>
              </table>
            </form>
            <div class="congbao-home">
              <a href="/van-ban/quyet-dinh-48-2026-qd-ttg.htm">Quyết định 48/2026/QĐ-TTg của Thủ tướng Chính phủ</a>
              <a href="/van-ban/quyet-dinh-49-2026-qd-ttg.htm">Quyết định 49/2026/QĐ-TTg của Thủ tướng Chính phủ</a>
              <a href="/chi-thi-so-37-ct-ttg-tang-cuong-phong-chong-gian-lan-119261021100102474.htm">Chỉ thị số 37/CT-TTg</a>
              <a href="/chung-khoan/thong-tu-145-2026-tt-btc-quy-dinh-dang-ky-449607-d1.html">Thông tư 145/2026/TT-BTC</a>
              <a href="/dt/luat-sua-doi-bo-sung-mot-so-dieu-cua-09-luat-ve-quan-su-quoc-phong/260805100059615084">Luật sửa đổi, bổ sung một số điều của 09 luật về quân sự, quốc phòng</a>
              <a href="/dt/luat-hoa-giai-o-co-so-sua-doi/260522145512086084">Luật Hòa giải ở cơ sở (sửa đổi)</a>
            </div>"""
            body = f'<html><head><meta charset="utf-8"></head><body>{inner}</body></html>'.encode(
                "utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/chi-thi-so-37-ct-ttg-tang-cuong-phong-chong-gian-lan-119261021100102474.htm":
            body = """<html><head><meta charset="utf-8"><title>Chỉ thị số 37/CT-TTg</title></head><body>
            <h1 data-role="title">Chỉ thị số 37/CT-TTg: Tăng cường phòng, chống gian lận thi cử</h1>
            <div data-role="publishdate">21/10/2026 10:18</div>
            <div data-role="sapo">(Chinhphu.vn) - Toàn văn Chỉ thị số 37/CT-TTg ngày 20/10/2026 của Thủ tướng Chính phủ.</div>
            <div class="detail-content afcbc-body" data-role="content">
              <p>Phó Thủ tướng Chính phủ vừa ký Chỉ thị số 37/CT-TTg về tăng cường chỉ đạo, phối hợp tổ chức kỳ thi.</p>
              <a href="/cpp/files/vbpq/2026/04/105-nd.signed.pdf">37-ct-ttg.pdf</a>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/chung-khoan/thong-tu-145-2026-tt-btc-quy-dinh-dang-ky-449607-d1.html":
            body = """<html><head><meta charset="utf-8"><title>Thông tư 145/2026/TT-BTC</title></head><body>
            <h1>Thông tư 145/2026/TT-BTC quy định đăng ký, lưu ký, bù trừ và thanh toán chứng khoán</h1>
            <table>
              <tr><td>Cơ quan ban hành:</td><td>Bộ Tài chính</td></tr>
              <tr><td>Số hiệu:</td><td>145/2026/TT-BTC</td></tr>
              <tr><td>Ngày ban hành:</td><td>30/09/2026</td></tr>
            </table>
            <div class="the-document-entry">
              <p>Điều 1. Phạm vi điều chỉnh và đối tượng áp dụng đối với hoạt động đăng ký, lưu ký chứng khoán.</p>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/ban-an/":
            body = """<html><head><meta charset="utf-8"><title>Bản án</title></head><body>
            <div class="ban-an-list">
              <a href="/ban-an/ban-an-1463-2026-ds-pt-2-1460069-d11.html">Bản án số 1463/2026/DS-PT</a>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/ban-an/ban-an-1463-2026-ds-pt-2-1460069-d11.html":
            body = """<html><head><meta charset="utf-8"><title>Bản án số 1463/2026/DS-PT</title></head><body>
            <h1>Bản án số 1463/2026/DS-PT ngày 15/10/2026 của TAND TP. Cần Thơ về tranh chấp quyền sử dụng đất</h1>
            <table class="table table-bordered table-thuoc-tinh">
              <tr><th>Tòa án xét xử:</th><td>TAND TP. Cần Thơ</td></tr>
              <tr><th>Số hiệu:</th><td>1463/2026/DS-PT</td></tr>
              <tr><th>Ngày ban hành:</th><td>15/10/2026</td></tr>
            </table>
            <div id="noidung" class="tab-content art-tabs-content active">
              <p>NHÂN DANH NƯỚC CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM. Tòa án nhân dân thành phố Cần Thơ xét xử phúc thẩm vụ án dân sự.</p>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/van-ban/quyet-dinh-48-2026-qd-ttg.htm":
            body = """<html><head><meta charset="utf-8"><title>Quyết định 48/2026/QĐ-TTg</title></head><body>
            <h1>Quyết định 48/2026/QĐ-TTg của Thủ tướng Chính phủ về chuyển đổi số</h1>
            <div class="row"><div class="name">Số, ký hiệu:</div><div class="value">48/2026/QĐ-TTg</div></div>
            <div class="row"><div class="name">Cơ quan ban hành:</div><div class="value">Thủ tướng Chính phủ</div></div>
            <div class="row"><div class="name">Ngày ban hành:</div><div class="value">12-04-2026</div></div>
            <a href="/cpp/files/vbpq/2026/04/105-nd.signed.pdf">48-qd.pdf</a>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/van-ban/quyet-dinh-49-2026-qd-ttg.htm":
            body = """<html><head><meta charset="utf-8"><title>Quyết định 49/2026/QĐ-TTg</title></head><body>
            <h1>Quyết định 49/2026/QĐ-TTg của Thủ tướng Chính phủ về an toàn thông tin</h1>
            <div class="row"><div class="name">Số, ký hiệu:</div><div class="value">49/2026/QĐ-TTg</div></div>
            <div class="row"><div class="name">Cơ quan ban hành:</div><div class="value">Thủ tướng Chính phủ</div></div>
            <div class="row"><div class="name">Ngày ban hành:</div><div class="value">13-04-2026</div></div>
            <a href="/cpp/files/vbpq/2026/04/106-nd.signed.pdf">49-qd.pdf</a>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/page/pagenew.aspx?docid=210001":
            pdf_href_1 = (
                f"http://{self.secondary_netloc}/cpp/files/vbpq/2026/04/105-nd.signed.pdf"
                if self.secondary_netloc
                else "/cpp/files/vbpq/2026/04/105-nd.signed.pdf"
            )
            body = f"""<html><head><meta charset="utf-8"><title>Nghị định 105/2026/NĐ-CP</title></head><body>
            <h4 class="title">Nghị định 105/2026/NĐ-CP quy định về giao dịch điện tử</h4>
            <table>
              <tr><td>Số ký hiệu</td><td>105/2026/NĐ-CP</td></tr>
              <tr><td>Ngày ban hành</td><td>10-04-2026</td></tr>
              <tr><td>Cơ quan ban hành</td><td>Chính phủ</td></tr>
              <tr><td>Loại văn bản</td><td>Nghị định</td></tr>
              <tr><td>Trích yếu</td><td>Nghị định quy định về giao dịch điện tử</td></tr>
            </table>
            <a href="{pdf_href_1}">105-nd.signed.pdf</a>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/page/pagenew.aspx?docid=210002":
            pdf_href_2 = (
                f"http://{self.secondary_netloc}/cpp/files/vbpq/2026/04/106-nd.signed.pdf"
                if self.secondary_netloc
                else "/cpp/files/vbpq/2026/04/106-nd.signed.pdf"
            )
            body = f"""<html><head><meta charset="utf-8"><title>Nghị định 106/2026/NĐ-CP</title></head><body>
            <h4 class="title">Nghị định 106/2026/NĐ-CP quy định về chữ ký số</h4>
            <table>
              <tr><td>Số ký hiệu</td><td>106/2026/NĐ-CP</td></tr>
              <tr><td>Ngày ban hành</td><td>11-04-2026</td></tr>
              <tr><td>Cơ quan ban hành</td><td>Chính phủ</td></tr>
              <tr><td>Loại văn bản</td><td>Nghị định</td></tr>
              <tr><td>Trích yếu</td><td>Nghị định quy định về chữ ký số</td></tr>
            </table>
            <a href="{pdf_href_2}">106-nd.signed.pdf</a>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/v1/documents.json"):
            host = self.headers.get("Host", "127.0.0.1")
            sec_host = self.secondary_netloc or host
            payload = {
                "count": 1,
                "results": [
                    {
                        "document_number": "2026-08801",
                        "title": "Cybersecurity Risk Management Rule for Critical Infrastructure",
                        "type": "Rule",
                        "action": "Final rule.",
                        "agencies": [{"name": "Department of Homeland Security"}],
                        "publication_date": "2026-04-20",
                        "effective_on": "2026-06-01",
                        "citation": "91 FR 24001",
                        "html_url": f"http://{host}/documents/2026/04/20/2026-08801/cybersecurity-rule",
                        "full_text_xml_url": f"http://{host}/documents/full_text/xml/2026/04/20/2026-08801.xml",
                        "pdf_url": f"http://{sec_host}/content/pkg/FR-2026-04-20/pdf/2026-08801.pdf",
                    }
                ],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/documents/full_text/xml/2026/04/20/2026-08801.xml":
            body = """<?xml version="1.0" encoding="UTF-8"?>
<RULE>
  <PREAMB>
    <SUBJECT>Cybersecurity Risk Management Rule for Critical Infrastructure</SUBJECT>
    <HD SOURCE="HD1">Background and Regulatory Authority</HD>
    <P>This final rule establishes mandatory cybersecurity incident reporting and risk management standards for covered entities.</P>
  </PREAMB>
</RULE>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/jportal/docs/bsjrs/bsjrs-toc.xml":
            body = """<?xml version="1.0" encoding="UTF-8"?>
<items>
  <item><link>/jportal/docs/bsjrs/jb-JURE100055033.zip</link></item>
  <item><link>/jportal/docs/bsjrs/jb-JURE100055034.zip</link></item>
</items>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/jportal/docs/bsjrs/jb-JURE100055033.zip":
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(_BridgeFixtureHandler.rii_zip)))
            self.end_headers()
            self.wfile.write(_BridgeFixtureHandler.rii_zip)
        elif self.path == "/jportal/docs/bsjrs/jb-JURE100055034.zip":
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(_BridgeFixtureHandler.rii_zip_2)))
            self.end_headers()
            self.wfile.write(_BridgeFixtureHandler.rii_zip_2)
        elif self.path.startswith("/api/decisions"):
            host = self.headers.get("Host", "127.0.0.1")
            payload = {
                "decisions": [
                    {
                        "decision_id": "bger_2026_4A_101_2026",
                        "docket_number": "4A_101/2026",
                        "title": "Urteil 4A_101/2026 vom 20. Mai 2026",
                        "court_name": "Schweizerisches Bundesgericht",
                        "decision_date": "2026-05-20",
                        "canonical_url": f"http://{host}/entscheid/4A_101_2026",
                        "full_text": "Das Schweizerische Bundesgericht hat in der Zivilsache 4A_101/2026 entschieden: Die Beschwerde wird abgewiesen.",
                    }
                ]
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/bills?jurisdiction=wa"):
            host = self.headers.get("Host", "127.0.0.1")
            payload = {
                "results": [
                    {
                        "id": "ocd-bill/wa-hb-1091",
                        "identifier": "HB 1091",
                        "title": "An Act Relating to Washington State Digital Legal Archives",
                        "from_organization": {"name": "Washington House of Representatives"},
                        "latest_action_date": "2026-10-10",
                        "latest_action_description": "Passed Legislature",
                        "openstates_url": f"http://{host}/bills/wa/hb1091",
                        "abstracts": [
                            {
                                "abstract": "Establishes digital preservation standards for state legislative records."
                            }
                        ],
                        "versions": [
                            {
                                "note": "Engrossed Substitute Bill",
                                "links": [
                                    {
                                        "media_type": "application/pdf",
                                        "url": f"http://{host}/Biennium/2025-26/Pdf/Bills/House%20Bills/1091-S.E.pdf",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/rest/v4/search/"):
            host = self.headers.get("Host", "127.0.0.1")
            payload = {
                "results": [
                    {
                        "cluster_id": 9988776,
                        "caseName": "United States v. New England Maritime Corp.",
                        "docketNumber": "25-1402",
                        "court": "United States Court of Appeals for the First Circuit",
                        "dateFiled": "2026-07-01",
                        "status": "Published",
                        "absolute_url": f"http://{host}/opinion/9988776/united-states-v-new-england-maritime/",
                        "opinions": [
                            {
                                "download_url": f"http://{host}/pdf/2026/07/01/us_v_nemc.pdf",
                            }
                        ],
                    }
                ]
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/gongbao/2026/issue_13046/":
            body = b"""<!DOCTYPE html><html><head><title>Gazette Issue</title></head><body>
            <a href="/gongbao/2026/issue_13046/202604/content_7065845.html">State Council Gazette Document</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/gongbao/2026/issue_13046/202604/content_7065845.html":
            body = """<!DOCTYPE html><html><head>
            <meta name="firstpublishedtime" content="2026-04-10 08:00:00" />
            <title>国务院办公厅关于深入推进政务服务提质增效的指导意见_国务院公报_中国政府网</title>
            </head><body>
            <div id="UCAP-CONTENT" class="pages_content padd-zyygwj">
              <p>国办发〔2026〕12号</p>
              <p>各省、自治区、直辖市人民政府，国务院各部委、各直属机构：现将有关事项通知如下。</p>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/zhengce/index.htm", "/zhengce/xxgk/", "/zhengce/xxgk/gjgzk/index.htm"):
            body = b"""<!DOCTYPE html><html><head><title>Zhengce</title></head><body>
            <a href="/zhengce/content/202610/content_700001.htm">Policy Detail 1</a>
            <a href="/zhengce/content/2026-10/03/content_700002.htm">Policy Detail 2</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in (
            "/zhengce/content/202610/content_7066001.htm",
            "/zhengce/content/202610/content_700001.htm",
        ):
            body = """<!DOCTYPE html><html><head>
            <meta name="firstpublishedtime" content="2026-10-03 09:00:00" />
            <title>生态保护补偿条例_国务院_中国政府网</title>
            </head><body>
            <table class="xxgk-table">
              <tr><td>标　　题：</td><td>生态保护补偿条例</td><td>发文机关：</td><td>国务院</td></tr>
              <tr><td>发文字号：</td><td>国令第779号</td><td>来　　源：</td><td>中国政府网</td></tr>
              <tr><td>主题分类：</td><td>城乡建设、环境保护</td><td>公文种类：</td><td>行政法规</td></tr>
              <tr><td>成文日期：</td><td>2026年10月03日</td><td>发布日期：</td><td>2026年10月05日</td></tr>
            </table>
            <div id="UCAP-CONTENT"><p>第一条 为了健全生态保护补偿制度，制定本条例。</p></div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/zhengce/content/2026-10/03/content_700002.htm":
            body = """<!DOCTYPE html><html><head>
            <meta name="firstpublishedtime" content="2026-10-03 09:00:00" />
            <title>网络数据安全管理条例_国务院_中国政府网</title>
            </head><body>
            <table class="xxgk-table">
              <tr><td>标　　题：</td><td>网络数据安全管理条例</td><td>发文机关：</td><td>国务院</td></tr>
              <tr><td>发文字号：</td><td>国令第790号</td><td>来　　源：</td><td>中国政府网</td></tr>
              <tr><td>主题分类：</td><td>工业、交通\\信息产业</td><td>公文种类：</td><td>行政法规</td></tr>
              <tr><td>成文日期：</td><td>2026年10月03日</td><td>发布日期：</td><td>2026年10月05日</td></tr>
            </table>
            <div id="UCAP-CONTENT"><p>第一条 为了规范网络数据处理活动，保障网络数据安全，制定本条例。</p></div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/spp/gjgb/index.shtml":
            body = b"""<!DOCTYPE html><html><head><title>SPP Gazette</title></head><body>
            <a href="/spp/gjgb/202603/t20260319_722354.shtml">SPP Gazette 2026 No. 1</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/spp/gjgb/202603/t20260319_722354.shtml":
            body = """<!DOCTYPE html><html><head>
            <meta name="firstpublishedtime" content="2026-03-19 10:00:00" />
            <title>2026年第1号（总第309号）_最高人民检察院</title>
            </head><body>
            <div class="detail_tit">2026年第1号（总第309号）</div>
            <div class="detail_extend">时间：2026-03-19　来源：最高人民检察院公报</div>
            <div id="fontzoom" class="detail_con"><p>最高人民检察院公报2026年第1号全文内容。</p></div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/spp/jczdal/index.shtml":
            body = b"""<!DOCTYPE html><html><head><title>SPP Guiding Cases</title></head><body>
            <a href="/spp/jczdal/202509/t20250919_706599.shtml">SPP Guiding Case Batch 60</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/spp/jczdal/202509/t20250919_706599.shtml":
            body = """<!DOCTYPE html><html><head>
            <meta name="firstpublishedtime" content="2025-09-19 14:30:00" />
            <title>最高人民检察院发布第六十批指导性案例_最高人民检察院</title>
            </head><body>
            <div class="detail_tit">最高人民检察院发布第六十批指导性案例</div>
            <div class="detail_extend">时间：2025-09-19　来源：最高人民检察院</div>
            <div id="fontzoom" class="detail_con"><p>检例第240号：某公司金融诈骗立案监督案全文。</p></div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/www/illegal?siteid=beijing":
            body = b"""<!DOCTYPE html><html><head><title>SAFE Penalties</title></head><body>
            <a href="/www/illegal/listQuery?saffno=8a8b8c0123456789abcdef">SAFE Penalty Record</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/www/illegal/listQuery?saffno=8a8b8c0123456789abcdef":
            body = """<!DOCTYPE html><html><head><title>外汇行政处罚信息</title></head><body>
            <div class="cfxx_jg">
              <table>
                <tr><th>违规主体名称</th><td>某跨境贸易有限公司</td></tr>
                <tr><th>行政处罚决定书文号</th><td>京汇罚〔2026〕15号</td></tr>
                <tr><th>违法事实</th><td>违反规定将境内外汇转移境外</td></tr>
                <tr><th>处罚依据</th><td>《中华人民共和国外汇管理条例》第三十九条</td></tr>
                <tr><th>处罚内容</th><td>处以罚款人民币50万元</td></tr>
                <tr><th>处罚决定日期</th><td>2026-09-20</td></tr>
                <tr><th>作出处罚决定的行政机关名称</th><td>国家外汇管理局北京市分局</td></tr>
              </table>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/zhuantilanmu/dfzgl/":
            body = b"""<!DOCTYPE html><html><head><title>Local Gov Debt</title></head><body>
            <a href="/zhuantilanmu/dfzgl/zcfg/202111/t20211110_3764807.htm">MOF Local Gov Debt Policy</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/zhuantilanmu/dfzgl/zcfg/202111/t20211110_3764807.htm":
            body = """<!DOCTYPE html><html><head>
            <meta name="ArticleTitle" content="关于印发《地方政府专项债券用途调整操作指引》的通知" />
            <meta name="ContentSource" content="财政部" />
            <meta name="PubDate" content="2021-11-11 16:38:00" />
            <title>关于印发《地方政府专项债券用途调整操作指引》的通知</title>
            </head><body>
            <div class="my_doccontent">
              <div class="TRS_Editor"><p>财预〔2021〕110号：为规范地方政府专项债券用途调整管理，制定本指引。<o:p></o:p></p></div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/xxgk/tjbz/gjtjbz/201905/t20190521_1758938.html":
            body = """<!DOCTYPE html><html><head><title>国家统计局信息公开</title></head><body>
            <div class="content_top_box">
              <ul>
                <li><div><span>索 引 号</span>410A03-1201-201905-0002</div><div><span>信息类别</span>国家统计标准</div></li>
                <li><div><span>发布机构</span>国家统计局</div><div><span>成文日期</span>2019年05月20日</div></li>
              </ul>
            </div>
            <div class="xxgkNbXq2"><h2 class="xxgkNbXqTitle">关于执行国民经济行业分类第1号修改单的通知</h2></div>
            <div class="TRS_PreAppend">
              <p>国统字〔2019〕66号</p>
              <p>GB/T 4754-2017《国民经济行业分类》国家标准第1号修改单自2019年3月29日起实施。</p>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/xxgk/gsxx/dybalx/":
            body = b"""<!DOCTYPE html><html><head><title>Film Filing Notices</title></head><body>
            <a href="/xxgk/gsxx/dybalx/202610/t20261013_1012172.html">Film Filing Notice 2026-09</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/xxgk/gsxx/dybalx/202610/t20261013_1012172.html":
            body = """<!DOCTYPE html><html><head>
            <meta name="ArticleTitle" content="国家电影局关于2026年9月上全国电影剧本（梗概）备案、立项公示的通知" />
            <meta name="PubDate" content="2026-10-13" />
            <title>国家电影局关于2026年9月上全国电影剧本（梗概）备案、立项公示的通知</title>
            </head><body>
            <div class="m3editor m3editor2">
              <p>本期备案立项公示统计信息如下：全国处理备案故事影片共55部。</p>
              <div class="hmc4Table">
                <table>
                  <tr><th>序号</th><th>备案立项号</th><th>片名</th><th>备案结果</th><th>备案地</th></tr>
                  <tr><td>1</td><td>影剧备字〔2026〕第1230号</td><td>摇滚吧！四郎</td><td>同意拍摄</td><td>北京市</td></tr>
                </table>
              </div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/bsfw/jggs/yxspjg/":
            body = b"""<!DOCTYPE html><html><head><title>Game Approval Results</title></head><body>
            <a href="/bsfw/jggs/yxspjg/jkwlyxspxx/202601/t20260126_947910.html">2026 Imported Game Approvals</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/bsfw/jggs/yxspjg/jkwlyxspxx/202601/t20260126_947910.html":
            body = """<!DOCTYPE html><html><head><title>国家新闻出版署</title></head><body>
            <div class="m3pageCon">
              <h2 class="m3page_t">2026年进口网络游戏审批信息</h2>
              <div class="m3page_funs"><span>2026-09-30</span></div>
              <div class="m3pageEdit">
                <table>
                  <tr><th>序号</th><th>名称</th><th>出版单位</th><th>批复文号</th><th>出版物号</th><th>批准时间</th></tr>
                  <tr><td>1</td><td>3对3街篮</td><td>上海昊嘉信息技术有限公司</td><td>国新出审〔2026〕2424号</td><td>ISBN 978-7-498-17777-3</td><td>2026年09月29日</td></tr>
                </table>
              </div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/law/tsutatsu/kihon/hojin/01.htm":
            body = b"""<!DOCTYPE html><html><head><title>NTA Hojin</title></head><body>
            <a href="/law/tsutatsu/kihon/hojin/01/01_01.htm">Chapter 1 Section 1</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/law/tsutatsu/kihon/hojin/01/01_01.htm":
            body = """<!DOCTYPE html><html><head>
            <meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">
            <title>第1章　納税義務者｜国税庁</title>
            </head><body>
            <div id="bodyArea">
              <h1>第1章　納税義務者</h1>
              <p>（人格のない社団等の意義）1−1−1　法第2条第8号に規定する人格のない社団等とは。</p>
            </div>
            </body></html>""".encode("shift_jis")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/quick-links/e-tax-guides":
            body = b"""<!DOCTYPE html><html><head><title>IRAS e-Tax Guides</title></head><body>
            <a href="/taxes/corporate-income-tax/income-deductions-for-companies/tax-treatment-of-r-d-expenditure">R&amp;D e-Tax Guide</a>
            <a href="/taxes/corporate-income-tax/income-deductions-for-companies/tax-treatment-of-ip-rights">IP Rights e-Tax Guide</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/taxes/corporate-income-tax/income-deductions-for-companies/tax-treatment-of-r-d-expenditure":
            body = """<!DOCTYPE html><html><head><title>Tax Treatment of R&amp;D Expenditure</title></head><body>
            <main id="mainContent">
              <h1>Tax Treatment of Research &amp; Development Expenditure</h1>
              <p>This e-Tax Guide explains the income tax deductibility of research and development expenditure in Singapore.</p>
            </main>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/taxes/corporate-income-tax/income-deductions-for-companies/tax-treatment-of-ip-rights":
            body = """<!DOCTYPE html><html><head><title>Tax Treatment of IP Rights</title></head><body>
            <main id="mainContent">
              <h1>Tax Treatment of Intellectual Property Rights</h1>
              <p>This e-Tax Guide explains the writing-down allowances for intellectual property rights under the Income Tax Act.</p>
            </main>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in (
            "/government/collections/hmrc-manuals",
            "/hmrc-internal-manuals/appeals-reviews-and-tribunals-guidance",
        ):
            body = b"""<!DOCTYPE html><html><head><title>HMRC internal manuals</title></head><body>
            <main id="content">
              <a href="/hmrc-internal-manuals/admin-law-manual">Admin Law Manual</a>
            </main>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/hmrc-internal-manuals/admin-law-manual":
            body = b"""<!DOCTYPE html><html><head><title>Admin Law Manual</title></head><body>
            <main id="content">
              <h1>Admin Law Manual</h1>
              <a href="/hmrc-internal-manuals/admin-law-manual/adml1000">ADML1000 - Incorrect Advice to Customers</a>
              <a href="/hmrc-internal-manuals/admin-law-manual/adml1100">ADML1100 - Incorrect Advice to Customers: Background</a>
            </main>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/hmrc-internal-manuals/admin-law-manual/adml1000":
            body = """<!DOCTYPE html><html><head><title>ADML1000 - Incorrect Advice to Customers - HMRC internal manual</title></head><body>
            <main id="content">
              <dl>
                <dt>From:</dt><dd>HM Revenue &amp; Customs</dd>
                <dt>Published</dt><dd>11 March 2016</dd>
              </dl>
              <h1>Admin Law Manual</h1>
              <h1>ADML1000 - Incorrect Advice to Customers</h1>
              <p>This manual explains what to do when a customer claims that HM Revenue and Customs has given them incorrect advice.</p>
            </main>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/hmrc-internal-manuals/admin-law-manual/adml1100":
            body = """<!DOCTYPE html><html><head><title>ADML1100 - Incorrect Advice to Customers: Background - HMRC internal manual</title></head><body>
            <main id="content">
              <dl>
                <dt>From:</dt><dd>HM Revenue &amp; Customs</dd>
                <dt>Published</dt><dd>11 March 2016</dd>
              </dl>
              <h1>Admin Law Manual</h1>
              <h1>ADML1100 - Incorrect Advice to Customers: Background</h1>
              <p>HMRC aims to provide accurate and timely advice to taxpayers under statutory collection and management powers.</p>
            </main>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/irb":
            body = b"""<!DOCTYPE html><html><head><title>Internal Revenue Bulletins</title></head><body>
            <a href="/irb/2026-16_IRB">Internal Revenue Bulletin: 2026-16</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/irb/2026-16_IRB":
            body = """<!DOCTYPE html><html><head><title>Internal Revenue Bulletin: 2026-16 | Internal Revenue Service</title></head><body>
            <div id="block-pup-irs-barrio-content">
              <h1>Internal Revenue Bulletin: 2026-16</h1>
              <p>Revenue Ruling 2026-09: Applicable Federal Rates for April 2026 under Section 1274(d) of the Internal Revenue Code.</p>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/lrs/common/ju/judgment.jsp":
            body = b"""<!DOCTYPE html><html><head><title>HK Judiciary Judgments</title></head><body>
            <a href="/lrs/common/ju/tjpv.jsp">Newly Added Judgments</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/lrs/common/ju/tjpv.jsp":
            body = b"""<!DOCTYPE html><html><head><title>Judgments List</title></head><body>
            <a href="javascript:matchpop('/lrs/common/ju/ju_frame.jsp?DIS=158000','matchpage')">Case 1</a>
            <a href="javascript:matchpop('/lrs/common/ju/ju_frame.jsp?DIS=158001','matchpage')">Case 2</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/lrs/common/ju/ju_frame.jsp?DIS=158000":
            body = b"""<!DOCTYPE html><html><frameset><frame name="mainFrame" src="/lrs/common/ju/ju_body.jsp?DIS=158000"/></frameset></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/lrs/common/ju/ju_frame.jsp?DIS=158001":
            body = b"""<!DOCTYPE html><html><frameset><frame name="mainFrame" src="/lrs/common/ju/ju_body.jsp?DIS=158001"/></frameset></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/lrs/common/ju/ju_body.jsp?DIS=158000":
            body = """<!DOCTYPE html><html><head>
            <title>HCAL371/2021 RASHID WASEEM v. TORTURE CLAIMS APPEAL BOARD / NON-REFOULEMENT CLAIMS PETITION OFFICE</title>
            </head><body>
            <form name="search_body"><table width="100%"><tr><td>
              <p>HCAL 371/2021</p>
              <p>[2024] HKCFI 247</p>
              <p>1. The applicant filed Form 86 to apply for leave to apply for judicial review of the Board Decision.</p>
              <p>Sent to the Applicant on 6/2/2024</p>
            </td></tr></table></form>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/lrs/common/ju/ju_body.jsp?DIS=158001":
            body = """<!DOCTYPE html><html><head>
            <title>HCAL372/2021 CHAN TAI MAN v. DIRECTOR OF IMMIGRATION</title>
            </head><body>
            <form name="search_body"><table width="100%"><tr><td>
              <p>HCAL 372/2021</p>
              <p>[2024] HKCFI 248</p>
              <p>1. This is an application for leave to apply for judicial review against an immigration detention decision.</p>
              <p>Sent to the Applicant on 7/2/2024</p>
            </td></tr></table></form>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/eng/ppr/dip.htm":
            body = """<!DOCTYPE html><html><head><title>IRD : Departmental Interpretation and Practice Notes</title></head><body>
            <table>
              <tr><td>3.</td><td>Profits tax - Apportionment of expenses</td><td>July 2008</td><td><a href="/eng/pdf/dipn03.pdf">PDF</a></td></tr>
              <tr><td>4.</td><td>Profits tax - Lease inducement payments</td><td>August 2008</td><td><a href="/eng/pdf/dipn04.pdf">PDF</a></td></tr>
            </table>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/eng/pdf/dipn03.pdf", "/eng/pdf/dipn04.pdf"):
            body = self.sample_pdf
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/dt/luat-sua-doi-bo-sung-mot-so-dieu-cua-09-luat-ve-quan-su-quoc-phong/260805100059615084":
            cookie_hdr = self.headers.get("Cookie", "")
            if "D1N=" not in cookie_hdr:
                body = b'<html><body><script>document.cookie="D1N=ok123"+"; expires=Thu, 31-Dec-37 23:55:55 GMT; path=/";window.location.reload(true);</script></body></html>'
            else:
                dt_pdf_1 = (
                    f"http://{self.secondary_netloc}/uploadFiles/host/local/du-thao-01.pdf"
                    if self.secondary_netloc
                    else "/uploadFiles/host/local/du-thao-01.pdf"
                )
                body = f"""<!DOCTYPE html><html><head><title>Luật sửa đổi, bổ sung một số điều của 09 luật về quân sự, quốc phòng</title></head><body>
                <div class="col-lg-8 info-dt-l">
                  <h3>Luật sửa đổi, bổ sung một số điều của 09 luật về quân sự, quốc phòng</h3>
                  Cơ quan chủ trì soạn thảo: Bộ Quốc phòng
                  Cơ quan chủ trì thẩm tra: Ủy ban Quốc phòng, An ninh và Đối ngoại
                  Dự kiến thông qua: Khóa XVI - Kỳ họp không thường lệ thứ Nhất
                </div>
                <div id="nav-home">
                  <p>Lần dự thảo 1 - Ngày đăng: 05/08/2026 - Toàn văn hồ sơ dự án Luật số 45/2026/QH15 sửa đổi bổ sung một số điều về quân sự quốc phòng.</p>
                  <a href="{dt_pdf_1}">Dự thảo PDF</a>
                </div>
                </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/dt/luat-hoa-giai-o-co-so-sua-doi/260522145512086084":
            cookie_hdr = self.headers.get("Cookie", "")
            if "D1N=" not in cookie_hdr:
                body = b'<html><body><script>document.cookie="D1N=ok123"+"; expires=Thu, 31-Dec-37 23:55:55 GMT; path=/";window.location.reload(true);</script></body></html>'
            else:
                dt_pdf_2 = (
                    f"http://{self.secondary_netloc}/uploadFiles/host/local/du-thao-02.pdf"
                    if self.secondary_netloc
                    else "/uploadFiles/host/local/du-thao-02.pdf"
                )
                body = f"""<!DOCTYPE html><html><head><title>Luật Hòa giải ở cơ sở (sửa đổi)</title></head><body>
                <div class="col-lg-8 info-dt-l">
                  <h3>Luật Hòa giải ở cơ sở (sửa đổi)</h3>
                  Cơ quan chủ trì soạn thảo: Bộ Tư pháp
                  Cơ quan chủ trì thẩm tra: Ủy ban Pháp luật và Tư pháp
                  Dự kiến thông qua: Khóa XVI - Kỳ họp thứ Hai
                </div>
                <div id="nav-home">
                  <p>Lần dự thảo 2 - Ngày đăng: 22/05/2026 - Toàn văn hồ sơ dự án Luật Hòa giải ở cơ sở (sửa đổi).</p>
                  <a href="{dt_pdf_2}">Dự thảo PDF</a>
                </div>
                </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/Pages/danh-sach-van-ban.aspx"):
            cookie_hdr = self.headers.get("Cookie", "")
            if "D1N=" not in cookie_hdr:
                body = b'<html><body><script>document.cookie="D1N=ok123"+"; expires=Thu, 31-Dec-37 23:55:55 GMT; path=/";window.location.reload(true);</script></body></html>'
            else:
                body = """<html><head><meta charset="utf-8"><title>Cơ sở dữ liệu Luật Việt Nam - VietLaw</title></head><body>
                <div class="content_box_left">
                  <p class="title_content"><a href="/Pages/vbpq-toan-van.aspx?ItemID=29895">Nghị quyết 46/2026/QH16</a></p>
                </div>
                <div class="content_box_left">
                  <p class="title_content"><a href="/Pages/vbpq-toan-van.aspx?ItemID=29894">Nghị quyết 44/2026/QH16</a></p>
                </div>
                </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/Pages/vbpq-toan-van.aspx?ItemID=29895"):
            cookie_hdr = self.headers.get("Cookie", "")
            if "D1N=" not in cookie_hdr:
                body = b'<html><body><script>document.cookie="D1N=ok123"+"; expires=Thu, 31-Dec-37 23:55:55 GMT; path=/";window.location.reload(true);</script></body></html>'
            else:
                body = """<html><head><meta charset="utf-8"><title>Cơ sở dữ liệu Luật Việt Nam - VietLaw</title></head><body>
                <div id="left_col">
                  <div class="box-container">
                    <div class="box-tab-vb">
                      <div class="box-map">
                        <ul>
                          <li><a href="/">Trang chủ</a></li>
                          <li><a href="javascript:void(0)">Nghị quyết 46/2026/QH16</a></li>
                        </ul>
                      </div>
                      <div class="header">
                        <ul>
                          <li><a href="/vanban/Lists/VBPQConvert/Attachments/29895/NQ_46_QH16.pdf"><span><b>Tải về</b></span></a></li>
                        </ul>
                      </div>
                      <div class="content">
                        <div class="fulltext">
                          <div class="vbInfo">
                            <ul>
                              <li class="green"><span>Ngày ban hành: </span>24/08/2026</li>
                              <li class="red"><span>Hiệu lực: </span>Còn hiệu lực</li>
                            </ul>
                          </div>
                          <div class="toanvan">
                            <p>Toàn văn Nghị quyết số 46/2026/QH16 của Quốc hội về phát triển văn hóa và dân tộc.</p>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/Pages/vbpq-toan-van.aspx?ItemID=29894"):
            cookie_hdr = self.headers.get("Cookie", "")
            if "D1N=" not in cookie_hdr:
                body = b'<html><body><script>document.cookie="D1N=ok123"+"; expires=Thu, 31-Dec-37 23:55:55 GMT; path=/";window.location.reload(true);</script></body></html>'
            else:
                body = """<html><head><meta charset="utf-8"><title>Cơ sở dữ liệu Luật Việt Nam - VietLaw</title></head><body>
                <div id="left_col">
                  <div class="box-container">
                    <div class="box-tab-vb">
                      <div class="box-map">
                        <ul>
                          <li><a href="/">Trang chủ</a></li>
                          <li><a href="javascript:void(0)">Nghị quyết 44/2026/QH16</a></li>
                        </ul>
                      </div>
                      <div class="header">
                        <ul>
                          <li><a href="/vanban/Lists/VBPQConvert/Attachments/29894/NQ_44_QH16.pdf"><span><b>Tải về</b></span></a></li>
                        </ul>
                      </div>
                      <div class="content">
                        <div class="fulltext">
                          <div class="vbInfo">
                            <ul>
                              <li class="green"><span>Ngày ban hành: </span>24/08/2026</li>
                              <li class="red"><span>Hiệu lực: </span>Còn hiệu lực</li>
                            </ul>
                          </div>
                          <div class="toanvan">
                            <p>Toàn văn Nghị quyết số 44/2026/QH16 của Quốc hội khóa 16 về dự án độc lập Ninh Thuận.</p>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/webcenter/portal/anle/anle":
            body = b"""<!DOCTYPE html><html><head><title>Danh sach an le</title></head><body>
            <a href="/webcenter/portal/anle/chitietanle?dDocName=TAND377305">An le 84/2026/AL</a>
            <a href="/webcenter/portal/anle/chitietanle?dDocName=TAND377338">An le 85/2026/AL</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/webcenter/portal/anle/chitietanle?dDocName=TAND377305":
            body = b"""<!DOCTYPE html><html><head><script>
            AdfLoopbackUtils.runLoopback('/webcenter/portal/anle/chitietanle', ['_afrLoop', '987654321']);
            </script></head><body></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/webcenter/portal/anle/chitietanle?dDocName=TAND377305&_afrLoop=987654321&_afrWindowMode=0":
            body = """<!DOCTYPE html><html><head><title>84/2026/AL</title></head><body>
            <div id="tabContent1">
              <div id="thuoctinh">
                Số án lệ 84/2026/AL Tên án lệ Về chấm dứt việc nuôi con nuôi là người chưa thành niên khi người nhận con nuôi là người độc thân chết Ngày thông qua 21/05/2026 Ngày công bố 29/05/2026 Quyết định công bố Quyết định 162/QĐ-CA Ngày áp dụng 01/07/2026
              </div>
              <p>Nội dung án lệ số 84/2026/AL của Hội đồng Thẩm phán Tòa án nhân dân tối cao.</p>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in (
            "/webcenter/portal/anle/chitietanle?dDocName=TAND377338",
            "/webcenter/portal/anle/chitietanle?dDocName=TAND377338&_afrLoop=987654321&_afrWindowMode=0",
        ):
            body = """<!DOCTYPE html><html><head><title>85/2026/AL</title></head><body>
            <div id="tabContent1">
              <div id="thuoctinh">
                Số án lệ 85/2026/AL Tên án lệ Về xác định thẩm quyền giải quyết tranh chấp hợp đồng tín dụng Ngày thông qua 21/05/2026 Ngày công bố 29/05/2026 Quyết định công bố Quyết định 162/QĐ-CA Ngày áp dụng 01/07/2026
              </div>
              <p>Nội dung án lệ số 85/2026/AL của Hội đồng Thẩm phán Tòa án nhân dân tối cao.</p>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in (
            "/hoidapcstc",
            "/hoidapcstc/Area/QuestionByArea/77",
            "/hoidapcstc/Area/QuestionByArea/64",
        ):
            body = b"""<!DOCTYPE html><html><head><title>Hoi dap CSTC</title></head><body>
            <a href="/hoidapcstc/home/cthoidap/166356">Cau hoi 166356</a>
            <a href="/hoidapcstc/home/cthoidap/166357">Cau hoi 166357</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/hoidapcstc/home/cthoidap/166356":
            body = """<!DOCTYPE html><html><head><title>Hỏi đáp CSTC</title></head><body>
            <div id="login_box" style="display:none;" class="modal"><input type="password" name="pwd" /></div>
            <div class="new-content">
              <div>
                <div style="float:right">15/10/2026</div>
                <div>Kính gửi: Bộ Tài chính (Cục Thuế)</div>
                <div style="text-align:justify">Kính gửi Bộ Tài chính, Đề nghị hướng dẫn xác định doanh nghiệp nhỏ và vừa để hưởng miễn thuế TNDN 03 năm đầu theo điểm a khoản 3 Điều 7 Nghị định số 20/2026/NĐ-CP.</div>
              </div>
              <div>Trả lời:</div>
              <div>Căn cứ Nghị định số 20/2026/NĐ-CP của Chính phủ, hướng dẫn áp dụng miễn thuế thu nhập doanh nghiệp đối với doanh nghiệp nhỏ và vừa đăng ký lần đầu.</div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/hoidapcstc/home/cthoidap/166357":
            body = """<!DOCTYPE html><html><head><title>Hỏi đáp CSTC</title></head><body>
            <div id="login_box" style="display:none;" class="modal"><input type="password" name="pwd" /></div>
            <div class="new-content">
              <div>
                <div style="float:right">16/10/2026</div>
                <div>Kính gửi: Bộ Tài chính (Cục Thuế)</div>
                <div style="text-align:justify">Kính gửi Cục Thuế, Đề nghị hướng dẫn kê khai khấu trừ thuế giá trị gia tăng đầu vào theo Thông tư số 80/2021/TT-BTC đối với hàng hóa xuất khẩu.</div>
              </div>
              <div>Trả lời:</div>
              <div>Căn cứ Thông tư số 80/2021/TT-BTC của Bộ Tài chính, hướng dẫn kê khai và hoàn thuế giá trị gia tăng đối với hàng hóa xuất khẩu.</div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/1/lawlists/2":
            body = """<?xml version="1.0" encoding="UTF-8"?>
<DataRoot>
  <Result><Code>0</Code></Result>
  <ApplData>
    <LawNameListInfo><LawId>405AC0000000088</LawId></LawNameListInfo>
    <LawNameListInfo><LawId>321CONSTITUTION</LawId></LawNameListInfo>
  </ApplData>
</DataRoot>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/1/lawdata/405AC0000000088":
            body = """<?xml version="1.0" encoding="UTF-8"?>
            <DataRoot><Result><Code>0</Code></Result><ApplData><LawFullText>
            <Law Era="Heisei" Year="05" Num="088" PromulgateMonth="11" PromulgateDay="12" LawType="Act" Lang="ja">
              <LawNum>平成五年法律第八十八号</LawNum>
              <LawBody>
                <LawTitle>行政手続法</LawTitle>
                <MainProvision>
                  <Chapter Num="1"><ChapterTitle>第一章　総則</ChapterTitle>
                    <Article Num="1"><ArticleCaption>（目的等）</ArticleCaption><ArticleTitle>第一条</ArticleTitle>
                      <Paragraph Num="1"><ParagraphNum/><ParagraphSentence><Sentence>この法律は、処分、行政指導及び届出に関する手続に関し、共通する事項を定めることによって、行政運営における公正の確保と透明性の向上を図り、もって国民の権利利益の保護に資することを目的とする。</Sentence></ParagraphSentence></Paragraph>
                    </Article>
                  </Chapter>
                </MainProvision>
              </LawBody>
            </Law>
            </LawFullText></ApplData></DataRoot>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/1/lawdata/321CONSTITUTION":
            body = """<?xml version="1.0" encoding="UTF-8"?>
            <DataRoot><Result><Code>0</Code></Result><ApplData><LawFullText>
            <Law Era="Showa" Year="21" Num="000" PromulgateMonth="11" PromulgateDay="03" LawType="Constitution" Lang="ja">
              <LawNum>昭和二十一年憲法</LawNum>
              <LawBody>
                <LawTitle>日本国憲法</LawTitle>
                <MainProvision>
                  <Chapter Num="1"><ChapterTitle>第一章　天皇</ChapterTitle>
                    <Article Num="1"><ArticleTitle>第一条</ArticleTitle>
                      <Paragraph Num="1"><ParagraphNum/><ParagraphSentence><Sentence>天皇は、日本国の象徴であり日本国民統合の象徴であつて、この地位は、主権の存する日本国民の総意に基く。</Sentence></ParagraphSentence></Paragraph>
                    </Article>
                  </Chapter>
                </MainProvision>
              </LawBody>
            </Law>
            </LawFullText></ApplData></DataRoot>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in (
            "/opinions/opinions.aspx",
            "/opinions/slipopinion/25",
            "/opinions/relatingtoorders/25",
        ):
            body = b"""<!DOCTYPE html><html><head><title>Opinions of the Court - 2025</title></head><body>
            <a href="/opinions/25pdf/26a388_q86b.pdf">26A388 People Not Politicians v. Onder</a>
            <a href="/opinions/25pdf/24-43_2b35.pdf">24-43 West Virginia v. B. P. J.</a>
            <a href="/opinions/25pdf/609us2r67_g3bi.pdf">Preliminary Print</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/opinions/25pdf/26a388_q86b.pdf":
            body = self.scotus_pdf_1 or self.sample_pdf
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/opinions/25pdf/24-43_2b35.pdf":
            body = self.scotus_pdf_2 or self.sample_pdf
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in (
            "/judgments-jugements/cb/",
            "/scc-csc/scc-csc/en/2026/nav_date.do?iframe=true",
        ):
            body = b"""<!DOCTYPE html><html><head><title>SCC Decisions</title></head><body>
            <main>
              <a href="/scc-csc/scc-csc/en/item/40000/index.do">Christian Chukwuedozie Chijindu v. Law Society of Ontario</a>
              <a href="/scc-csc/scc-csc/en/item/40001/index.do">R. v. Bissonnette</a>
            </main>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in (
            "/judgments-jugements/cb/2026/40000/",
            "/scc-csc/scc-csc/en/item/40000/index.do?iframe=true",
        ):
            body = """<!DOCTYPE html><html><head><title>Christian Chukwuedozie Chijindu v. Law Society of Ontario</title></head><body>
            <div id="decisia-main-content">
              <h3 class="title">Christian Chukwuedozie Chijindu v. Law Society of Ontario</h3>
              <div class="metadata">
                <p>Date 2022-05-12 | Neutral citation 2022 SCC 18 | Case number 40000</p>
              </div>
              <div class="documentcontent">
                <p>Full judgment reasons of the Supreme Court of Canada dismissing the appeal.</p>
                <a href="/scc-csc/scc-csc/en/item/40000/judgment.pdf">PDF</a>
              </div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in (
            "/judgments-jugements/cb/2026/40001/",
            "/scc-csc/scc-csc/en/item/40001/index.do?iframe=true",
        ):
            body = """<!DOCTYPE html><html><head><title>R. v. Bissonnette</title></head><body>
            <div id="decisia-main-content">
              <h3 class="title">R. v. Bissonnette</h3>
              <div class="metadata">
                <p>Date 2022-05-27 | Neutral citation 2022 SCC 23 | Case number 40001</p>
              </div>
              <div class="documentcontent">
                <p>Full judgment reasons of the Supreme Court of Canada on the constitutionality of consecutive parole ineligibility periods.</p>
                <a href="/scc-csc/scc-csc/en/item/40001/judgment.pdf">PDF</a>
              </div>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/cases-dossiers/search-recherche/40000/":
            body = """<!DOCTYPE html><html><head><title>Supreme Court of Canada | 40000</title></head><body>
            <main>
              <h1>Case information</h1>
              <h2 id="case-no">40000</h2>
              <p id="soc">Christian Chukwuedozie Chijindu v. Law Society of Ontario</p>
              <p>2022-05-12 Judgment of the Court on the application for leave to appeal is dismissed.</p>
            </main>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/items/"):
            body = b"""<!DOCTYPE html><html><head><title>NZ Legislation Browse</title></head><body>
            <a href="/act/public/2024/1/en/latest/">New Zealand Productivity Commission Act Repeal Act 2024</a>
            <a href="/act/public/2024/2/en/latest/">Digital Identity Services Trust Framework Act 2024</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/act/public/2024/1/en/latest/":
            body = """<!DOCTYPE html><html><head><title>New Zealand Productivity Commission Act Repeal Act 2024</title></head><body>
            <h1>New Zealand Productivity Commission Act Repeal Act 2024</h1>
            <div id="legislation">
              <p>Public Act 2024 No 1 Date of assent 5 February 2024</p>
              <p>1 Title: This Act is the New Zealand Productivity Commission Act Repeal Act 2024.</p>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/act/public/2024/2/en/latest/":
            body = """<!DOCTYPE html><html><head><title>Digital Identity Services Trust Framework Act 2024</title></head><body>
            <h1>Digital Identity Services Trust Framework Act 2024</h1>
            <div id="legislation">
              <p>Public Act 2024 No 2 Date of assent 10 February 2024</p>
              <p>1 Title: This Act is the Digital Identity Services Trust Framework Act 2024.</p>
            </div>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/legislation/":
            body = b"""<!DOCTYPE html><html><head><title>Kenya Legislation</title></head><body>
            <a href="/akn/ke/act/1963/718/eng@1963-12-10">Constitution of Kenya (Repealed)</a>
            <a href="/akn/ke/act/2016/31/eng@2022-12-31">Access to Information Act</a>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/akn/ke/act/1963/718/eng@1963-12-10":
            body = """<!DOCTYPE html><html><head><title>Constitution of Kenya (Repealed)</title></head><body>
            <main>
              <h1>Constitution of Kenya (Repealed)</h1>
              <h2>Act No. 718 of 1963</h2>
              <dl><dt>Date</dt><dd>10 December 1963</dd></dl>
              <div class="akn-akomaNtoso"><p>Whereas Kenya is an independent Sovereign Republic under the Constitution of Kenya.</p></div>
            </main>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/akn/ke/act/2016/31/eng@2022-12-31":
            body = """<!DOCTYPE html><html><head><title>Access to Information Act</title></head><body>
            <main>
              <h1>Access to Information Act</h1>
              <h2>Act No. 31 of 2016</h2>
              <dl><dt>Date</dt><dd>10 September 2016</dd></dl>
              <div class="akn-akomaNtoso"><p>An Act of Parliament to give effect to Article 35 of the Constitution of Kenya on access to information.</p></div>
            </main>
            </body></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class TestStructuredXmlAndBridgeAdapters:
    @pytest.mark.parametrize(
        ("source_id", "adapter_name", "entry_path", "expected_doc_type", "expected_substr"),
        [
            ("dd-910", "structured_xml", "/xml/akn-act-2026.xml", "法律法规", "2026 c. 15"),
            ("dd-911", "worldwidelaw_bridge", "/wwl/congbao-list.html", "法律法规", "55/2026/NĐ-CP"),
            ("dd-912", "juriscraper_bridge", "/juriscraper/ca1-opinions.json", "案例", "25-1001"),
            ("dd-913", "oldp_bridge", "/oldp/bgh-cases.html", "案例", "VIII ZR 12/25"),
            ("dd-914", "opencaselaw_bridge", "/opencaselaw/ch-bger.html", "案例", "4A_101/2026"),
            ("dd-915", "openstates_bridge", "/openstates/bills.json", "法律法规", "HR 4200"),
        ],
    )
    def test_collect_and_offline_reparse_projects_expected_document_rows(
        self,
        initialized_db: dict[str, str],
        tmp_path: Path,
        source_id: str,
        adapter_name: str,
        entry_path: str,
        expected_doc_type: str,
        expected_substr: str,
    ) -> None:
        """Each structured XML and bridge adapter collects into `wlh.document_row` and replays offline without network."""
        if not _BridgeFixtureHandler.sample_pdf:
            _BridgeFixtureHandler.sample_pdf = make_native_pdf(
                tmp_path / "sample.pdf",
                ["Official Legal Document Full Text Body Content for Verification."],
            ).read_bytes()
        if not _BridgeFixtureHandler.scotus_pdf_1:
            _BridgeFixtureHandler.scotus_pdf_1 = make_native_pdf(
                tmp_path / "scotus1.pdf",
                [
                    "SUPREME COURT OF THE UNITED STATES No. 26A388 PEOPLE NOT POLITICIANS, ET AL. v. ROBERT ONDER, ET AL. ON APPLICATION FOR STAY [September 25, 2026] PER CURIAM. The application for stay presented to the Court is denied."
                ],
            ).read_bytes()
            _BridgeFixtureHandler.scotus_pdf_2 = make_native_pdf(
                tmp_path / "scotus2.pdf",
                [
                    "SUPREME COURT OF THE UNITED STATES Syllabus WEST VIRGINIA ET AL. v. B. P. J., BY HER NEXT FRIEND AND MOTHER, HEATHER JACKSON CERTIORARI TO THE UNITED STATES COURT OF APPEALS FOR THE FOURTH CIRCUIT No. 24–43. Argued January 13, 2026—Decided June 30, 2026 Held: The judgment of the Court of Appeals is reversed."
                ],
            ).read_bytes()

        server = ThreadingHTTPServer(("127.0.0.1", 0), _BridgeFixtureHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stopped = False

        try:
            prefix = "/" + entry_path.strip("/").split("/")[0] + "/"
            source_yaml = tmp_path / f"{source_id}.yaml"
            source_yaml.write_text(
                f"""
id: {source_id}
adapter: {adapter_name}
archive_approved: true
scope: "Adapter contract test for {adapter_name}"
entry_urls:
  - "http://127.0.0.1:{port}{entry_path}"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "{prefix}"
metadata:
  source_name: "Test {adapter_name}"
  document_type: "{expected_doc_type}"
  language: "en"
  jurisdiction: "INTL"
""".strip(),
                encoding="utf-8",
            )

            from wlh.engine.runs import execute_run, prepare_run
            from wlh.projections.canonical import project_canonical

            c_run = prepare_run(source_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"{adapter_name} collect failed: {c_res}"

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                from psycopg.rows import dict_row

                with conn.cursor(row_factory=dict_row) as cur:
                    rows = cur.execute(
                        """
                        SELECT f.source_id, r.id AS resource_id, d.*
                        FROM wlh.document_row d
                        JOIN wlh.version v ON v.id = d.version_id
                        JOIN wlh.resource r ON r.id = v.resource_id
                        JOIN wlh.frontier f ON f.id = r.frontier_id
                        WHERE f.source_id = %s
                        ORDER BY d.row_index
                        """,
                        (source_id,),
                    ).fetchall()
                    assert len(rows) >= 1, f"Expected document_row for {adapter_name}"
                    projected = project_canonical(rows[0])
                    assert projected["doc_type"] == expected_doc_type
                    assert (
                        expected_substr in (projected["doc_number"] or "")
                        or expected_substr in (projected["title"] or "")
                    ), f"Expected {expected_substr} in {projected}"
                    if source_id in {"dd-550", "dd-558"}:
                        assert rows[0]["original_media_type"] == "application/zip"
                        assert (projected["original_file"] or "").endswith(".zip")
                    if source_id == "dd-548":
                        assert projected["issuer"] == "The Supreme Court"
                        assert projected["published_at"] == "2024-01-11"
                    if source_id == "dd-496":
                        assert projected["issuer"] == "TAND TP. Cần Thơ"
                        assert projected["published_at"] == "2026-10-15"
                    if source_id == "dd-502":
                        assert projected["issuer"] == "国务院"
                        assert projected["published_at"] == "2026-10-03"
                    if source_id == "dd-520":
                        assert projected["issuer"] == "国税庁"
                    if source_id == "dd-549":
                        assert projected["issuer"] == "HM Revenue & Customs"
                        assert projected["published_at"] == "2016-03-11"
                    if source_id == "dd-562":
                        assert projected["issuer"] == "Supreme Court of the United States"
                        assert projected["published_at"] in {"2026-09-25", "2026-06-30"}
                        assert " v. " in (projected["title"] or "")

            server.shutdown()
            thread.join(timeout=2.0)
            stopped = True
            rp_run = prepare_run(source_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{adapter_name} reparse failed: {rp_res}"
        finally:
            if not stopped:
                server.shutdown()
                thread.join(timeout=2.0)
            server.server_close()


class TestAdaptedSourceConfigReplayAndZeroTargetGuard:
    @pytest.mark.parametrize(
        ("source_id", "expected_doc_type", "expected_substr"),
        [
            ("dd-017", "案例", "指导性案例220号"),
            ("dd-040", "政策文件", "2026年第1号"),
            ("dd-041", "案例", "第六十批指导性案例"),
            ("dd-102", "通知公告", "京汇罚〔2026〕15号"),
            ("dd-183", "政策文件", "地方政府专项债券用途调整操作指引"),
            ("dd-375", "行业标准", "国统字〔2019〕66号"),
            ("dd-448", "通知公告", "影剧备字〔2026〕第1230号"),
            ("dd-455", "通知公告", "国新出审〔2026〕2424号"),
            ("dd-492", "法律法规", "105/2026/NĐ-CP"),
            ("dd-493", "法律法规", "48/2026/QĐ-TTg"),
            ("dd-501", "政策文件", "国办发〔2026〕12号"),
            ("dd-502", "法律法规", "国令第779号"),
            ("dd-513", "法律法规", "37/CT-TTg"),
            ("dd-495", "法律法规", "145/2026/TT-BTC"),
            ("dd-496", "案例", "1463/2026/DS-PT"),
            ("dd-499", "法律法规", "2026-08801"),
            ("dd-520", "税务资料", "第1章　納税義務者"),
            ("dd-527", "税务资料", "Tax Treatment of Research & Development Expenditure"),
            ("dd-548", "案例", "[2024] UKSC 1"),
            ("dd-549", "税务资料", "ADML1000"),
            ("dd-550", "法律法规", "GG"),
            ("dd-551", "案例", "VIII ZR 12/25"),
            ("dd-556", "案例", "4A_101/2026"),
            ("dd-558", "法律法规", "1"),
            ("dd-559", "法律法规", "1 CFR Part 1"),
            ("dd-560", "法律法规", "HB 1091"),
            ("dd-561", "案例", "25-1402"),
            ("dd-562", "案例", "26A388"),
            ("dd-563", "税务资料", "2026-16"),
            ("dd-564", "法律法规", "A-1"),
            ("dd-565", "案例", "2022 SCC 18"),
            ("dd-570", "法律法规", "2024 No 1"),
            ("dd-572", "法律法规", "Act No. 718 of 1963"),
            ("dd-506", "案例", "HCAL 371/2021"),
            ("dd-507", "税务资料", "dipn03"),
            ("dd-512", "法律法规", "46/2026/QH16"),
            ("dd-514", "案例", "84/2026/AL"),
            ("dd-516", "税务资料", "Đề nghị hướng dẫn"),
            ("dd-517", "法律法规", "平成五年法律第八十八号"),
            ("dd-575", "法律法规", "Luật sửa đổi, bổ sung một số điều của 09 luật về quân sự, quốc phòng"),
        ],
    )
    def test_adapted_production_source_configs_collect_and_reparse_against_local_samples(
        self,
        initialized_db: dict[str, str],
        tmp_path: Path,
        source_id: str,
        expected_doc_type: str,
        expected_substr: str,
    ) -> None:
        """Production `sources/dd-*.yaml` extraction rules collect and offline-reparse cleanly against local fixtures."""
        import re
        from urllib.parse import urlsplit, urlunsplit

        if not _BridgeFixtureHandler.sample_pdf:
            _BridgeFixtureHandler.sample_pdf = make_native_pdf(
                tmp_path / "sample.pdf",
                ["Official Legal Document Full Text Body Content for Verification."],
            ).read_bytes()
        if not _BridgeFixtureHandler.scotus_pdf_1:
            _BridgeFixtureHandler.scotus_pdf_1 = make_native_pdf(
                tmp_path / "scotus1.pdf",
                [
                    "SUPREME COURT OF THE UNITED STATES No. 26A388 PEOPLE NOT POLITICIANS, ET AL. v. ROBERT ONDER, ET AL. ON APPLICATION FOR STAY [September 25, 2026] PER CURIAM. The application for stay presented to the Court is denied."
                ],
            ).read_bytes()
            _BridgeFixtureHandler.scotus_pdf_2 = make_native_pdf(
                tmp_path / "scotus2.pdf",
                [
                    "SUPREME COURT OF THE UNITED STATES Syllabus WEST VIRGINIA ET AL. v. B. P. J., BY HER NEXT FRIEND AND MOTHER, HEATHER JACKSON CERTIORARI TO THE UNITED STATES COURT OF APPEALS FOR THE FOURTH CIRCUIT No. 24–43. Argued January 13, 2026—Decided June 30, 2026 Held: The judgment of the Court of Appeals is reversed."
                ],
            ).read_bytes()

        server = ThreadingHTTPServer(("127.0.0.1", 0), _BridgeFixtureHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        server_v6 = _IPv6ThreadingHTTPServer(("::1", 0), _BridgeFixtureHandler)
        port_v6 = server_v6.server_address[1]
        thread_v6 = threading.Thread(target=server_v6.serve_forever, daemon=True)
        thread_v6.start()
        _BridgeFixtureHandler.secondary_netloc = f"[::1]:{port_v6}"
        _BridgeFixtureHandler.active_source_id = source_id
        stopped = False

        try:
            repo_root = Path(__file__).resolve().parents[1]
            raw_cfg: dict[str, Any] = yaml.safe_load(
                (repo_root / "sources" / f"{source_id}.yaml").read_text(encoding="utf-8")
            )
            orig_hosts = list(raw_cfg.get("allowed_hosts", []))
            primary_host = orig_hosts[0]
            secondary_hosts = orig_hosts[1:]

            rewritten_urls: list[str] = []
            entry_urls: list[str] = raw_cfg["entry_urls"]
            for entry_url in entry_urls:
                parts = urlsplit(entry_url)
                rewritten_urls.append(
                    urlunsplit(("http", f"127.0.0.1:{port}", parts.path, parts.query, ""))
                )
            raw_cfg["entry_urls"] = rewritten_urls
            raw_cfg["allowed_hosts"] = ["127.0.0.1", "::1"] if secondary_hosts else ["127.0.0.1"]
            if "host_path_scopes" in raw_cfg:
                orig_scopes: dict[str, list[str]] = raw_cfg["host_path_scopes"]
                new_scopes: dict[str, list[str]] = {}
                if primary_host in orig_scopes:
                    new_scopes["127.0.0.1"] = list(orig_scopes[primary_host])
                sec_scoped: list[str] = []
                for sec_host in secondary_hosts:
                    for p in orig_scopes.get(sec_host, []):
                        if p not in sec_scoped:
                            sec_scoped.append(p)
                if sec_scoped:
                    new_scopes["::1"] = sec_scoped
                raw_cfg["host_path_scopes"] = new_scopes
                allowed_prefixes: list[str] = list(raw_cfg.get("allowed_path_prefixes", []))
                for p_list in new_scopes.values():
                    for p in p_list:
                        if p not in allowed_prefixes:
                            allowed_prefixes.append(p)
                raw_cfg["allowed_path_prefixes"] = allowed_prefixes
            raw_cfg["delay"] = 0.0
            if "documents" in raw_cfg:

                def _rewrite_pat(pat: str) -> str:
                    out = pat
                    for sec_host in secondary_hosts:
                        out = out.replace(re.escape(sec_host), rf"\[::1\]:{port_v6}")
                        out = out.replace(sec_host.replace(".", r"\."), rf"\[::1\]:{port_v6}")
                    out = out.replace(re.escape(primary_host), rf"127\.0\.0\.1:{port}")
                    out = out.replace(primary_host.replace(".", r"\."), rf"127\.0\.0\.1:{port}")
                    out = re.sub(r"^\^https?://", "^http://", out)
                    return out

                if "detail_url_patterns" in raw_cfg["documents"]:
                    raw_cfg["documents"]["detail_url_patterns"] = [
                        _rewrite_pat(pat) for pat in raw_cfg["documents"]["detail_url_patterns"]
                    ]
                if "attachment_url_pattern" in raw_cfg["documents"]:
                    raw_cfg["documents"]["attachment_url_pattern"] = _rewrite_pat(
                        raw_cfg["documents"]["attachment_url_pattern"]
                    )

            replay_yaml = tmp_path / f"{source_id}-replay.yaml"
            replay_yaml.write_text(
                yaml.safe_dump(raw_cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

            from wlh.engine.runs import execute_run, prepare_run
            from wlh.projections.canonical import project_canonical

            c_run = prepare_run(replay_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "complete", f"{source_id} collect failed: {c_res}"

            multi_resource_sources = {
                "dd-017",
                "dd-492",
                "dd-493",
                "dd-502",
                "dd-506",
                "dd-507",
                "dd-512",
                "dd-514",
                "dd-516",
                "dd-517",
                "dd-527",
                "dd-548",
                "dd-549",
                "dd-550",
                "dd-551",
                "dd-558",
                "dd-559",
                "dd-562",
                "dd-564",
                "dd-565",
                "dd-570",
                "dd-572",
                "dd-575",
            }

            with psycopg.connect(initialized_db["WLH_DATABASE_URL"]) as conn:
                from psycopg.rows import dict_row

                with conn.cursor(row_factory=dict_row) as cur:
                    rows = cur.execute(
                        """
                        SELECT f.source_id, r.id AS resource_id, d.*
                        FROM wlh.document_row d
                        JOIN wlh.version v ON v.id = d.version_id
                        JOIN wlh.resource r ON r.id = v.resource_id
                        JOIN wlh.frontier f ON f.id = r.frontier_id
                        WHERE f.source_id = %s
                        ORDER BY d.row_index, r.id
                        """,
                        (source_id,),
                    ).fetchall()
                    assert len(rows) >= 1, f"Expected document_row for {source_id}"
                    if source_id in multi_resource_sources:
                        assert len({r["resource_id"] for r in rows}) >= 2, (
                            f"Expected at least 2 distinct resources discovered from list/index for {source_id}"
                        )
                    projected_rows = [project_canonical(r) for r in rows]
                    projected = next(
                        (
                            p
                            for p in projected_rows
                            if expected_substr in (p["doc_number"] or "")
                            or expected_substr in (p["title"] or "")
                        ),
                        projected_rows[0],
                    )
                    assert projected["doc_type"] == expected_doc_type
                    assert (
                        expected_substr in (projected["doc_number"] or "")
                        or expected_substr in (projected["title"] or "")
                    ), f"Expected {expected_substr} in {projected}"
                    if source_id in {
                        "dd-492",
                        "dd-493",
                        "dd-499",
                        "dd-507",
                        "dd-512",
                        "dd-513",
                        "dd-560",
                        "dd-561",
                        "dd-562",
                        "dd-565",
                        "dd-575",
                    }:
                        assert {
                            r["resource_id"] for r in rows if r["original_hash"] is not None
                        } == {r["resource_id"] for r in rows}, (
                            f"Expected captured attachment/original for every resource of {source_id}"
                        )
                    if source_id in {"dd-512", "dd-516", "dd-565", "dd-575"}:
                        from wlh.engine.objects import Objects

                        objs = Objects()
                        primary_rows = [r for r in rows if r["role"] == "primary"] or rows
                        for r in primary_rows:
                            md_text = objs.get(r["markdown_hash"]).decode(
                                "utf-8", errors="replace"
                            )
                            if source_id == "dd-575":
                                assert "Toàn văn hồ sơ dự án" in md_text
                            elif source_id == "dd-512":
                                assert "Official Legal Document" in md_text or "Nghị quyết" in md_text
                            elif source_id == "dd-516":
                                assert r["doc_number"] is None
                                assert "Đề nghị hướng dẫn" in md_text
                                assert "Trả lời:" in md_text
                                ans_part = md_text.split("Trả lời:", 1)[1].strip()
                                assert "Căn cứ " in ans_part and len(ans_part) >= 50
                            elif source_id == "dd-565":
                                assert (
                                    md_text.count(
                                        "Full judgment reasons of the Supreme Court of Canada"
                                    )
                                    == 1
                                )
                    if source_id in {"dd-550", "dd-558"}:
                        assert rows[0]["original_media_type"] == "application/zip"
                        assert (projected["original_file"] or "").endswith(".zip")
                    if source_id == "dd-512":
                        assert projected["issuer"] == "Quốc hội"
                        assert projected["published_at"] == "2026-08-24"
                    if source_id == "dd-575":
                        assert "Bộ Quốc phòng" in (projected["issuer"] or "")
                    if source_id == "dd-548":
                        assert projected["issuer"] == "The Supreme Court"
                        assert projected["published_at"] == "2024-01-11"
                    if source_id == "dd-496":
                        assert projected["issuer"] == "TAND TP. Cần Thơ"
                        assert projected["published_at"] == "2026-10-15"
                    if source_id == "dd-502":
                        assert projected["issuer"] == "国务院"
                        assert projected["published_at"] == "2026-10-03"
                    if source_id == "dd-520":
                        assert projected["issuer"] == "国税庁"
                    if source_id == "dd-549":
                        assert projected["issuer"] == "HM Revenue & Customs"
                        assert projected["published_at"] == "2016-03-11"
                    if source_id == "dd-562":
                        assert projected["issuer"] == "Supreme Court of the United States"
                        assert projected["published_at"] in {"2026-09-25", "2026-06-30"}
                        assert " v. " in (projected["title"] or "")

            server.shutdown()
            server_v6.shutdown()
            thread.join(timeout=2.0)
            thread_v6.join(timeout=2.0)
            stopped = True
            rp_run = prepare_run(replay_yaml, "reparse")
            rp_res = execute_run(rp_run)
            assert rp_res["status"] == "complete", f"{source_id} reparse failed: {rp_res}"
            assert rp_res["report"]["external_requests"] == 0
        finally:
            _BridgeFixtureHandler.secondary_netloc = ""
            _BridgeFixtureHandler.active_source_id = ""
            if not stopped:
                server.shutdown()
                server_v6.shutdown()
                thread.join(timeout=2.0)
                thread_v6.join(timeout=2.0)
            server.server_close()
            server_v6.server_close()

    def test_collect_with_zero_discovered_targets_fails_with_explicit_error(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """When an entry page returns HTTP 200 OK but discovers 0 document targets, collect fails with `zero_targets_discovered` without polluting HTTP failure cases."""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _BridgeFixtureHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            empty_yaml = tmp_path / "dd-998.yaml"
            empty_yaml.write_text(
                f"""
id: dd-998
adapter: web
archive_approved: true
scope: "Zero target foolproof guard contract test"
entry_urls:
  - "http://127.0.0.1:{port}/shenpan/gengduo/empty.html"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/shenpan/"
delay: 0.0
metadata:
  source_name: "Empty List Test Source"
  document_type: "案例"
  language: "zh"
  jurisdiction: "CN"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{port}/shenpan/xiangqing/\\\\d+\\\\.html$"
  detail_body: always
  content_xpath: '//div[@class="txt_txt"]'
""".strip(),
                encoding="utf-8",
            )

            from wlh.engine.runs import execute_run, prepare_run

            c_run = prepare_run(empty_yaml, "collect")
            c_res = execute_run(c_run)
            assert c_res["status"] == "failed", f"Expected collect to fail on 0 targets: {c_res}"
            assert "zero_targets_discovered" in c_res["errors"]
            assert "zero_targets_discovered" in c_res["report"]["coverage"]["known_gaps"]
            assert c_res["report"]["failures"].get("enumeration_incomplete", 0) == 1
            assert c_res["report"]["coverage"]["selected_scope_enumeration"] == "incomplete"

            not_found_yaml = tmp_path / "dd-999.yaml"
            not_found_yaml.write_text(
                empty_yaml.read_text(encoding="utf-8")
                .replace("id: dd-998", "id: dd-999")
                .replace("/shenpan/gengduo/empty.html", "/shenpan/gengduo/missing-404.html"),
                encoding="utf-8",
            )
            nf_run = prepare_run(not_found_yaml, "collect")
            nf_res = execute_run(nf_run)
            assert nf_res["status"] == "failed"
            assert "http_404" in nf_res["errors"]
            assert "zero_targets_discovered" not in nf_res["errors"]
            assert nf_res["report"]["failures"] == {"network_failed": 1}
        finally:
            server.shutdown()
            thread.join(timeout=2.0)
            server.server_close()

    def test_browser_detail_subrequest_http_503_propagates_failure_and_blocks_target(
        self, initialized_db: dict[str, str], tmp_path: Path
    ) -> None:
        """When a rendered_dom detail page encounters a same-origin/in-scope subrequest HTTP 503, WebAdapter.parse and collect propagate `http_503` and refuse to register or commit the truncated detail target."""
        from types import SimpleNamespace

        from wlh.adapters.web import Adapter as WebAdapter
        from wlh.config import load_file
        from wlh.core import WlhError

        repo_root = Path(__file__).resolve().parents[1]
        dd575_cfg = load_file(repo_root / "sources" / "dd-575.yaml").model_dump()
        adapter = WebAdapter(dd575_cfg)
        truncated_detail_html = (
            '<!DOCTYPE html><html data-wlh-truncated="http_503"><head><title>Draft</title></head><body>'
            '<div class="info-dt-l"><h3>Luật sửa đổi</h3>Cơ quan chủ trì soạn thảo: Bộ Tư pháp</div>'
            '<div id="nav-home">Ngày đăng: 05/08/2026 Toàn văn hồ sơ dự án</div>'
            "</body></html>"
        ).encode("utf-8")
        detail_url = "https://duthaoonline.quochoi.vn/dt/luat-sua-doi/260805100059615084"
        fake_ctx = SimpleNamespace(
            runtime=SimpleNamespace(mode="collect"),
            read=lambda _acq: truncated_detail_html,
            follow_links=lambda _task: True,
        )
        parsed = adapter.parse(
            fake_ctx,
            {"payload": {"url": detail_url, "role": "page"}, "depth": 1},
            {
                "outcome": "ok",
                "mime_type": "text/html",
                "charset": "utf-8",
                "actual_url": detail_url,
                "request_url": detail_url,
            },
        )
        assert parsed.get("truncated") == "http_503"
        assert parsed["targets"] == []

        for body_mode in ("always", "auto", "never"):
            mode_cfg = dict(dd575_cfg)
            mode_cfg["documents"] = dict(dd575_cfg["documents"], detail_body=body_mode)
            mode_adapter = WebAdapter(mode_cfg)
            with pytest.raises(WlhError) as exc_build:
                mode_adapter.build(
                    fake_ctx,
                    {
                        "identity": {
                            "namespace": "url",
                            "external_key": detail_url,
                            "public_url": detail_url,
                            "recheck_locator": {"url": detail_url, "role": "page"},
                        }
                    },
                    [
                        {
                            "acquisition": {
                                "id": "acq-1",
                                "archive_ref": "0" * 64,
                                "mime_type": "text/html",
                                "charset": "utf-8",
                                "actual_url": detail_url,
                                "request_url": detail_url,
                                "byte_size": len(truncated_detail_html),
                            }
                        },
                        {
                            "acquisition": {
                                "id": "acq-2",
                                "archive_ref": "1" * 64,
                                "mime_type": "application/pdf",
                                "charset": None,
                                "actual_url": "https://gatewayduthaoonline.quochoi.vn/uploadFiles/host/local/draft.pdf",
                                "request_url": "https://gatewayduthaoonline.quochoi.vn/uploadFiles/host/local/draft.pdf",
                                "byte_size": 128,
                            },
                            "text": "Toàn văn dự thảo.pdf",
                        },
                    ],
                )
            assert exc_build.value.code == "http_503"

            no_xpath_cfg = dict(dd575_cfg)
            no_xpath_cfg["documents"] = dict(
                dd575_cfg["documents"], detail_body=body_mode, content_xpath=None
            )
            no_xpath_adapter = WebAdapter(no_xpath_cfg)
            trunc_and_blocked_html = (
                b'<!DOCTYPE html><html data-wlh-truncated="http_503"><body><h1>Draft</h1><div>cf-chl-token</div></body></html>'
            )
            no_xpath_ctx = SimpleNamespace(
                runtime=SimpleNamespace(mode="collect"),
                read=lambda _acq: trunc_and_blocked_html,
                follow_links=lambda _task: True,
            )
            with pytest.raises(WlhError) as exc_prec:
                no_xpath_adapter.build(
                    no_xpath_ctx,
                    {"identity": {"namespace": "url", "external_key": detail_url}},
                    [
                        {
                            "acquisition": {
                                "id": "acq-prec",
                                "archive_ref": "2" * 64,
                                "mime_type": "text/html",
                                "charset": "utf-8",
                                "actual_url": detail_url,
                                "request_url": detail_url,
                                "byte_size": len(trunc_and_blocked_html),
                            }
                        }
                    ],
                )
            assert exc_prec.value.code == "http_503"

        from wlh.adapters.bridges.worldwidelaw_bridge import Adapter as WorldWideLawBridge

        dd493_cfg = load_file(repo_root / "sources" / "dd-493.yaml").model_dump()
        bridge_adapter = WorldWideLawBridge(dd493_cfg)
        links_html = "".join(
            f'<a href="https://congbao.chinhphu.vn/van-ban/sample-{i:02d}-{i:02d}.htm">Doc {i}</a>'
            for i in range(1, 13)
        )
        truncated_list_html = (
            f'<!DOCTYPE html><html data-wlh-truncated="http_503"><body>{links_html}</body></html>'
        ).encode("utf-8")
        bridge_ctx = SimpleNamespace(
            runtime=SimpleNamespace(mode="collect"),
            read=lambda _acq: truncated_list_html,
            follow_links=lambda _task: True,
        )
        bridge_parsed = bridge_adapter.parse(
            bridge_ctx,
            {"payload": {"url": "https://congbao.chinhphu.vn/", "role": "page"}, "depth": 0},
            {
                "outcome": "ok",
                "mime_type": "text/html",
                "charset": "utf-8",
                "actual_url": "https://congbao.chinhphu.vn/",
                "request_url": "https://congbao.chinhphu.vn/",
            },
        )
        assert len(bridge_parsed["requests"]) == dd493_cfg["budget"]["documents"] == 10
        assert bridge_parsed.get("truncated") == "http_503"

        class _DetailSubrequest503Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                if self.path == "/dt":
                    body = b'<!DOCTYPE html><html><body><a href="/dt/law-draft/260805100059615084">Draft 1</a></body></html>'
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/dt/law-draft/260805100059615084":
                    body = (
                        b"<!DOCTYPE html><html><head><title>Draft 1</title>"
                        b"<script>fetch('/dt/api/dossier');</script></head><body>"
                        b'<div class="info-dt-l"><h3>Draft 1</h3>C\xc6\xa1 quan ch\xe1\xbb\xa7 tr\xc3\xac so\xe1\xba\xa1n th\xe1\xba\xa3o: B\xe1\xbb\x99 T\xc6\xb0 ph\xc3\xa1p</div>'
                        b'<div id="nav-home">Ng\xc3\xa0y \xc4\x91\xc4\x83ng: 05/08/2026 To\xc3\xa0n v\xc4\x83n h\xe1\xbb\x93 s\xc6\xa1 d\xe1\xbb\xb1 \xc3\xa1n</div>'
                        b"</body></html>"
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/dt/api/dossier":
                    body = b'{"error":"service_unavailable"}'
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _DetailSubrequest503Handler)
        srv_port = srv.server_address[1]
        srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        srv_thread.start()
        try:
            sub503_yaml = tmp_path / "dd-997.yaml"
            sub503_yaml.write_text(
                f"""
id: dd-997
adapter: web
archive_approved: true
robots: false
scope: "Detail page subrequest 503 propagation E2E test"
entry_urls:
  - "http://127.0.0.1:{srv_port}/dt"
allowed_hosts:
  - "127.0.0.1"
allowed_path_prefixes:
  - "/dt"
delay: 0.0
fetch:
  mode: rendered_dom
  wait_until: load
  max_depth: 1
  retries: 0
  page_timeout_ms: 15000
metadata:
  source_name: "Subrequest 503 Test"
  document_type: "法律法规"
  language: "vi"
  jurisdiction: "VN"
documents:
  detail_url_patterns:
    - "^http://127\\\\.0\\\\.0\\\\.1:{srv_port}/dt/[^/?#]+/\\\\d{{15,}}$"
  detail_body: always
  content_xpath: '//div[contains(@class, "info-dt-l")] | //div[@id="nav-home"]'
""".strip(),
                encoding="utf-8",
            )
            from wlh.engine.runs import execute_run, prepare_run

            run_id = prepare_run(sub503_yaml, "collect")
            res = execute_run(run_id)
            assert res["status"] == "failed", f"Expected detail subrequest 503 run to fail: {res}"
            assert "http_503" in res["errors"]
            assert "http_503" in res["report"]["coverage"]["known_gaps"]
            assert res["report"]["failures"].get("network_failed", 0) >= 1
        finally:
            srv.shutdown()
            srv_thread.join(timeout=2.0)
            srv.server_close()
