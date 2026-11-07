"""Legal record schema contracts for the Stage 3a XML and JSON parsers.

Two kinds of black-box contracts live here:

* Characterization: minimal, real-shaped samples of the nine official legal XML dialects and
  the five structured JSON datasets are fed through ``XmlParser.records()`` /
  ``JsonParser.records()``; the observable record fields must stay identical across the
  table-driven schema refactor (Issue #10).
* Public surface: declarative schema tables, the unified parser configuration view and the
  in-process text-to-Markdown gateway that the refactor exposes.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

import lxml.etree as _etree  # type: ignore[import-untyped]
import pytest

etree: Any = _etree

PROJECTED_FIELDS: tuple[str, ...] = (
    "namespace",
    "external_key",
    "title",
    "doc_number",
    "issuer",
    "published_at",
    "effective_at",
    "expired_at",
    "status",
    "doc_type",
    "detail_url",
    "downloads",
)


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _source_config(
    parser_type: str,
    *,
    entry_url: str,
    allowed_hosts: list[str],
    options: Mapping[str, Any] | None = None,
    budget_documents: int | None = None,
    allowed_path_prefixes: list[str] | None = None,
) -> dict[str, Any]:
    from wlh.config import SourceConfig

    scheduler: dict[str, Any] = {
        "scope": "Legal record schema characterization sample",
        "entry_urls": [entry_url],
        "allowed_hosts": allowed_hosts,
    }
    if allowed_path_prefixes is not None:
        scheduler["allowed_path_prefixes"] = allowed_path_prefixes
    if budget_documents is not None:
        scheduler["budget"] = {"documents": budget_documents}
    raw: dict[str, Any] = {
        "id": "dd-990",
        "scheduler": scheduler,
        "processor": {"parser": {"type": parser_type, "options": dict(options or {})}},
        "storage": {"archive_approved": True, "metadata": {"document_type": "法律法规"}},
    }
    return SourceConfig.model_validate(raw).model_dump()


def _acquisition(url: str) -> dict[str, Any]:
    return {
        "actual_url": url,
        "request_url": url,
        "archive_ref": None,
        "charset": "utf-8",
    }


def _xml_records(
    sample: bytes, url: str, *, budget_documents: int | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    from wlh.processor.parsers.xml.parser import XmlParser

    host = url.split("/")[2]
    parser = XmlParser(
        _source_config(
            "xml", entry_url=url, allowed_hosts=[host], budget_documents=budget_documents
        )
    )
    records = parser.records(sample, _acquisition(url))
    return records, parser.truncated


def _json_records(
    sample: Any,
    url: str,
    *,
    options: Mapping[str, Any] | None = None,
    budget_documents: int | None = None,
    extra_hosts: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    from wlh.processor.parsers.json.parser import JsonParser

    host = url.split("/")[2]
    parser = JsonParser(
        _source_config(
            "json",
            entry_url=url,
            allowed_hosts=[host, *(extra_hosts or [])],
            options=options,
            budget_documents=budget_documents,
        )
    )
    records = parser.records(json.dumps(sample).encode("utf-8"), _acquisition(url))
    return records, parser.truncated


def _projection(record: Mapping[str, Any]) -> dict[str, Any]:
    return {name: record.get(name) for name in PROJECTED_FIELDS}


def _expected(**fields: Any) -> dict[str, Any]:
    unknown = set(fields) - set(PROJECTED_FIELDS)
    assert not unknown, unknown
    return {name: fields.get(name) for name in PROJECTED_FIELDS}


def _root(sample: bytes) -> Any:
    return etree.fromstring(
        sample, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    )


# --------------------------------------------------------------------------------------------
# XML samples (one per official legal XML dialect)
# --------------------------------------------------------------------------------------------

DE_DOKUMENT_XML = """<?xml version="1.0" encoding="utf-8"?>
<dokument>
  <metadaten>
    <ecli>ECLI:DE:BGH:2023:120523UIZR12.22.0</ecli>
    <doknr>KORE312342023</doknr>
    <gertyp>BGH</gertyp>
    <spruchkoerper>1. Zivilsenat</spruchkoerper>
    <entsch-datum>20230512</entsch-datum>
    <aktenzeichen>I ZR 12/22</aktenzeichen>
    <doktyp>Urteil</doktyp>
  </metadaten>
  <textdaten>
    <titelzeile><p>Urteil zur Haftung von Plattformbetreibern</p></titelzeile>
    <leitsatz><p>Ein Plattformbetreiber haftet erst ab Kenntnis der Rechtsverletzung.</p></leitsatz>
    <gruende><p>Die Revision des Beklagten hat Erfolg.</p></gruende>
  </textdaten>
</dokument>
""".encode()

AKN_ACT_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="act">
    <meta>
      <identification source="#source">
        <FRBRWork>
          <FRBRthis value="/akn/ke/act/2021/3/main"/>
          <FRBRuri value="/akn/ke/act/2021/3"/>
          <FRBRdate date="2021-03-15" name="Generation"/>
          <FRBRauthor href="#parliament"/>
          <FRBRcountry value="ke"/>
          <FRBRnumber value="3"/>
          <FRBRname value="Data Protection Act"/>
        </FRBRWork>
      </identification>
      <references source="#source">
        <TLCOrganization eId="parliament" href="/ontology/organization/ke/parliament"
                         showAs="Parliament of Kenya"/>
      </references>
    </meta>
    <preface><p><docTitle>Data Protection Act, 2021</docTitle></p></preface>
    <body>
      <section eId="sec_1">
        <num>1.</num>
        <heading>Short title</heading>
        <content><p>This Act may be cited as the Data Protection Act, 2021.</p></content>
      </section>
    </body>
  </act>
</akomaNtoso>
"""

AKN_JUDGMENT_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <judgment name="judgment">
    <meta>
      <identification source="#source">
        <FRBRWork>
          <FRBRthis value="/akn/za/judgment/zacc/2022/14/main"/>
          <FRBRuri value="/akn/za/judgment/zacc/2022/14"/>
          <FRBRdate date="2022-06-09" name="Judgment"/>
          <FRBRauthor href="#zacc"/>
          <FRBRcountry value="za"/>
          <FRBRnumber value="14"/>
          <FRBRname value="S v Mokoena"/>
        </FRBRWork>
      </identification>
      <references source="#source">
        <TLCOrganization eId="zacc" href="/ontology/organization/za/zacc"
                         showAs="Constitutional Court of South Africa"/>
      </references>
    </meta>
    <header>
      <p><neutralCitation>[2022] ZACC 14</neutralCitation></p>
      <p><docTitle>S v Mokoena</docTitle></p>
    </header>
    <judgmentBody>
      <decision><p>The appeal is upheld and the conviction is set aside.</p></decision>
    </judgmentBody>
  </judgment>
</akomaNtoso>
"""

USLM_PLAW_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<pLaw xmlns="http://schemas.gpo.gov/xml/uslm" xmlns:dc="http://purl.org/dc/elements/1.1/"
      identifier="/us/pl/117/58">
  <meta>
    <dc:title>Infrastructure Investment and Jobs Act</dc:title>
    <dc:type>Public Law</dc:type>
    <docNumber>58</docNumber>
    <citableAs>Public Law 117-58</citableAs>
    <approvedDate>2021-11-15</approvedDate>
  </meta>
  <preface>
    <docTitle>An Act</docTitle>
    <officialTitle>To authorize funds for Federal-aid highways, and for other purposes.</officialTitle>
  </preface>
  <main>
    <section identifier="/us/pl/117/58/s1">
      <num value="1">SECTION 1.</num>
      <heading>SHORT TITLE.</heading>
      <content>This Act may be cited as the Infrastructure Investment and Jobs Act.</content>
    </section>
  </main>
</pLaw>
"""

USLM_BILL_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<bill xmlns="http://schemas.gpo.gov/xml/uslm" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <meta>
    <dc:title>118 HR 1234 IH: Clean Water Act Amendments of 2023</dc:title>
    <dc:publisher>U.S. House of Representatives</dc:publisher>
    <dc:date>2023-02-28</dc:date>
    <docNumber>1234</docNumber>
  </meta>
  <preface>
    <officialTitle>To amend the Federal Water Pollution Control Act.</officialTitle>
  </preface>
  <main>
    <section><num>1.</num><content>Section 101 of the Act is amended.</content></section>
  </main>
</bill>
"""

DE_NORM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<dokumente builddate="20230101120000" doknr="BJNR001950896">
  <norm builddate="20230101120000" doknr="BJNR001950896">
    <metadaten>
      <jurabk>BGB</jurabk>
      <ausfertigung-datum manuell="ja">1896-08-18</ausfertigung-datum>
      <fundstelle typ="amtlich"><periodikum>RGBl</periodikum><zitstelle>1896, 195</zitstelle></fundstelle>
      <langue>Bürgerliches Gesetzbuch</langue>
    </metadaten>
  </norm>
  <norm builddate="20230101120000" doknr="BJNR001950896BJNE000102377">
    <metadaten><jurabk>BGB</jurabk><enbez>§ 1</enbez><titel>Beginn der Rechtsfähigkeit</titel></metadaten>
    <textdaten><text format="XML"><Content>
      <P>Die Rechtsfähigkeit des Menschen beginnt mit der Vollendung der Geburt.</P>
    </Content></text></textdaten>
  </norm>
</dokumente>
""".encode()

US_ECFR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DIV5 N="1" NODE="12:1.0.1.1.1" TYPE="PART" hierarchy_metadata="{&quot;title&quot;:&quot;12&quot;,&quot;part&quot;:&quot;1&quot;,&quot;path&quot;:&quot;/on/2024-01-02/title-12/part-1&quot;,&quot;citation&quot;:&quot;12 CFR Part 1&quot;}">
  <HEAD>PART 1—INVESTMENT SECURITIES</HEAD>
  <AUTH><HED>Authority:</HED><PSPACE>12 U.S.C. 1 et seq.</PSPACE></AUTH>
  <DIV8 N="§ 1.1" NODE="12:1.0.1.1.1.0.1.1" TYPE="SECTION">
    <HEAD>§ 1.1 Authority, purpose, and scope.</HEAD>
    <P>(a) Authority. This part is issued by the Comptroller of the Currency.</P>
  </DIV8>
</DIV5>
""".encode()

CA_STATUTE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Statute xmlns:lims="http://justice.gc.ca/lims" lims:lastAmendedDate="2023-06-20"
         lims:pit-date="2023-06-22" lims:current-date="2024-01-10" bill-origin="commons"
         xml:lang="en">
  <Identification>
    <LongTitle>An Act to extend the present laws of Canada that provide access to information</LongTitle>
    <ShortTitle status="official">Access to Information Act</ShortTitle>
    <Chapter><ConsolidatedNumber official="yes">A-1</ConsolidatedNumber></Chapter>
  </Identification>
  <Body>
    <Section><Label>1</Label><Text>This Act may be cited as the Access to Information Act.</Text></Section>
  </Body>
</Statute>
"""

CA_REGULATION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Regulation xmlns:lims="http://justice.gc.ca/lims" lims:lastAmendedDate="2022-11-30"
            regulation-type="SOR" xml:lang="en">
  <Identification>
    <InstrumentNumber>SOR/83-507</InstrumentNumber>
    <LongTitle>Access to Information Regulations</LongTitle>
  </Identification>
  <Body>
    <Section><Label>1</Label><Text>These Regulations may be cited as the Access to Information Regulations.</Text></Section>
  </Body>
</Regulation>
"""

JP_LAW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Law Era="Showa" Year="22" Num="067" LawType="Act" Lang="ja" PromulgateMonth="04" PromulgateDay="17">
  <LawNum>昭和二十二年法律第六十七号</LawNum>
  <LawBody>
    <LawTitle Kana="ちほうじちほう">地方自治法</LawTitle>
    <MainProvision>
      <Article Num="1">
        <ArticleCaption>（目的）</ArticleCaption>
        <ArticleTitle>第一条</ArticleTitle>
        <Paragraph Num="1"><ParagraphNum/>
          <ParagraphSentence><Sentence>この法律は、地方自治の本旨に基いて、地方公共団体の区分並びに地方公共団体の組織及び運営に関する事項の大綱を定める。</Sentence></ParagraphSentence>
        </Paragraph>
      </Article>
    </MainProvision>
  </LawBody>
</Law>
""".encode()

JP_DATAROOT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DataRoot>
  <Result><Code>0</Code><Message/></Result>
  <ApplData>
    <LawFullText>
      <Law Era="Heisei" Year="15" Num="057" LawType="Act" Lang="ja" PromulgateMonth="05" PromulgateDay="30">
        <LawNum>平成十五年法律第五十七号</LawNum>
        <LawBody>
          <LawTitle>個人情報の保護に関する法律</LawTitle>
          <MainProvision><Article Num="1"><ArticleTitle>第一条</ArticleTitle>
            <Paragraph Num="1"><ParagraphNum/><ParagraphSentence><Sentence>この法律は、個人情報の適正な取扱いに関し基本理念を定める。</Sentence></ParagraphSentence></Paragraph>
          </Article></MainProvision>
        </LawBody>
      </Law>
      <Law Era="Reiwa" Year="3" Num="035" LawType="Act" Lang="ja" PromulgateMonth="05" PromulgateDay="19">
        <LawNum>令和三年法律第三十五号</LawNum>
        <LawBody>
          <LawTitle>デジタル庁設置法</LawTitle>
          <MainProvision><Article Num="1"><ArticleTitle>第一条</ArticleTitle>
            <Paragraph Num="1"><ParagraphNum/><ParagraphSentence><Sentence>この法律は、デジタル庁の設置並びに任務及びこれを達成するため必要となる明確な範囲の所掌事務を定める。</Sentence></ParagraphSentence></Paragraph>
          </Article></MainProvision>
        </LawBody>
      </Law>
    </LawFullText>
  </ApplData>
</DataRoot>
""".encode()

TW_MOJ_XML = """<?xml version="1.0" encoding="utf-8"?>
<MojLawSplit>
  <UpdateDate>2024/01/01</UpdateDate>
  <法規>
    <法規性質>法律</法規性質>
    <法規名稱>個人資料保護法</法規名稱>
    <法規網址>https://law.moj.example.tw/LawClass/LawAll.aspx?pcode=I0050021</法規網址>
    <法規類別>行政＞法務部＞法制目</法規類別>
    <最新異動日期>20230531</最新異動日期>
    <生效日期></生效日期>
    <生效內容></生效內容>
    <廢止註記></廢止註記>
    <是否英譯註記>Y</是否英譯註記>
    <英文法規名稱>Personal Data Protection Act</英文法規名稱>
    <附件></附件>
    <沿革內容>1.中華民國八十四年八月十一日總統（84）華總（一）義字第5960號令制定公布全文 45 條；並自公布日施行</沿革內容>
    <前言></前言>
    <法規內容>
      <條文><條號>第 1 條</條號><條文內容>為規範個人資料之蒐集、處理及利用，以避免人格權受侵害，並促進個人資料之合理利用，特制定本法。</條文內容></條文>
    </法規內容>
  </法規>
  <法規>
    <法規性質>命令</法規性質>
    <法規名稱>個人資料保護法施行細則</法規名稱>
    <法規網址>https://law.moj.example.tw/LawClass/LawAll.aspx?pcode=I0050022</法規網址>
    <法規類別>行政＞法務部＞法制目</法規類別>
    <最新異動日期>20160315</最新異動日期>
    <生效日期></生效日期>
    <生效內容></生效內容>
    <廢止註記></廢止註記>
    <沿革內容>1.中華民國八十五年五月一日法務部令訂定發布全文 46 條</沿革內容>
    <法規內容>
      <條文><條號>第 1 條</條號><條文內容>本細則依個人資料保護法第五十五條規定訂定之。</條文內容></條文>
    </法規內容>
  </法規>
</MojLawSplit>
""".encode()

TW_GAZETTE_XML = """<?xml version="1.0" encoding="utf-8"?>
<Gazette>
  <Record>
    <MetaId>145623</MetaId>
    <Doc_Style_LName>法規</Doc_Style_LName>
    <Chapter>財政經濟篇</Chapter>
    <PubGovName>財政部</PubGovName>
    <Date_Created>113/05/20</Date_Created>
    <Date_Published>中華民國113年5月20日</Date_Published>
    <GazetteId>行政院公報 第030卷 第095期</GazetteId>
    <Title>修正「所得稅法施行細則」</Title>
    <ThemeSubject>修正所得稅法施行細則部分條文</ThemeSubject>
    <Explain>依據所得稅法第一百二十一條規定辦理。</Explain>
    <HTMLContent>&lt;p&gt;第一條 本細則依所得稅法第一百二十一條規定訂定之。&lt;/p&gt;</HTMLContent>
  </Record>
  <Record>
    <MetaId>145624</MetaId>
    <Doc_Style_LName>公告</Doc_Style_LName>
    <Chapter>內政篇</Chapter>
    <PubGovName>內政部</PubGovName>
    <Date_Published>中華民國113年5月21日</Date_Published>
    <GazetteId>行政院公報 第030卷 第096期</GazetteId>
    <ThemeSubject>公告113年度土地公告現值</ThemeSubject>
    <Explain>依平均地權條例第四十六條規定公告。</Explain>
    <HTMLContent>&lt;p&gt;公告事項：113年度土地公告現值自即日起生效。&lt;/p&gt;</HTMLContent>
  </Record>
</Gazette>
""".encode()

XML_SCHEMA_SAMPLES: tuple[tuple[str, bytes], ...] = (
    ("de_dokument", DE_DOKUMENT_XML),
    ("akoma_ntoso", AKN_ACT_XML),
    ("akoma_ntoso", AKN_JUDGMENT_XML),
    ("uslm", USLM_PLAW_XML),
    ("uslm", USLM_BILL_XML),
    ("de_norm", DE_NORM_XML),
    ("us_ecfr", US_ECFR_XML),
    ("ca_statute", CA_STATUTE_XML),
    ("ca_statute", CA_REGULATION_XML),
    ("jp_egov_xml", JP_LAW_XML),
    ("jp_egov_xml", JP_DATAROOT_XML),
    ("tw_moj_xml", TW_MOJ_XML),
    ("tw_gazette_xml", TW_GAZETTE_XML),
)


# --------------------------------------------------------------------------------------------
# JSON samples (one per structured dataset schema)
# --------------------------------------------------------------------------------------------

OPENSTATES_BILL: dict[str, Any] = {
    "id": "ocd-bill/5b1c9a1e-7c11-4d5f-9b0a-2a1d7f0f1a01",
    "session": "88R",
    "jurisdiction": {"id": "ocd-jurisdiction/country:us/state:tx/government", "name": "Texas"},
    "from_organization": {
        "id": "ocd-organization/cabf1716-c572-406a-bfdd-1917c11ac629",
        "name": "Senate",
        "classification": "upper",
    },
    "identifier": "SB 101",
    "title": "Relating to consumer data privacy",
    "classification": ["bill"],
    "first_action_date": "2023-01-10",
    "latest_action_date": "2023-05-02",
    "latest_action_description": "Signed by the Governor",
    "openstates_url": "https://openstates.example.org/tx/bills/88R/SB101/",
    "abstracts": [{"abstract": "Establishes consumer rights over personal data.", "note": ""}],
    "versions": [
        {
            "note": "Enrolled",
            "links": [
                {"url": "https://capitol.example.gov/sb101_enr.htm", "media_type": "text/html"},
                {
                    "url": "https://capitol.example.gov/sb101_enr.pdf",
                    "media_type": "application/pdf",
                },
            ],
        }
    ],
    "documents": [
        {
            "note": "Fiscal Note",
            "links": [
                {
                    "url": "https://capitol.example.gov/sb101_fiscal.pdf",
                    "media_type": "application/pdf",
                }
            ],
        }
    ],
    "sources": [{"url": "https://capitol.example.gov/BillLookup/History.aspx?Bill=SB101"}],
}

OPENCASELAW_DECISIONS: list[dict[str, Any]] = [
    {
        "decision_id": "bger_6B_1234_2023",
        "docket_number": "6B_1234/2023",
        "court": "bger",
        "court_name": "Bundesgericht",
        "decision_date": "2024-02-14",
        "title": "Urteil vom 14. Februar 2024,",
        "language": "de",
        "full_text": "Sachverhalt: A. Der Beschwerdeführer wurde wegen Betrugs verurteilt.",
        "canonical_url": "https://opencaselaw.example.ch/decisions/bger_6B_1234_2023",
        "pdf_url": "https://opencaselaw.example.ch/pdf/bger_6B_1234_2023.pdf",
    },
    {
        "decision_id": "bvger_A_567_2022",
        "docket_number": "A-567/2022",
        "court": "bvger",
        "court_name": "Bundesverwaltungsgericht",
        "decision_date": "2023-09-01",
        "language": "fr",
        "source_url": "https://opencaselaw.example.ch/decisions/bvger_A_567_2022",
        "pdf_url": "https://opencaselaw.example.ch/pdf/bvger_A_567_2022.pdf",
    },
    {
        "decision_id": "bge_149_IV_1",
        "docket_number": "149 IV 1",
        "court": "bge",
        "decision_date": "2023-01-05",
        "url": "https://opencaselaw.example.ch/decisions/bge_149_IV_1",
    },
]

JURISCRAPER_CASE_NAMES: dict[str, Any] = {
    "court_name": "Supreme Court of Example",
    "case_names": ["Smith v. Jones", "Doe v. Roe"],
    "case_dates": ["2024-03-01", "2024-03-08"],
    "docket_numbers": ["23-101", "23-102"],
    "download_urls": [
        "https://courts.example.gov/opinions/23-101.pdf",
        "https://courts.example.gov/opinions/23-102.pdf",
    ],
    "precedential_statuses": ["Published", "Unpublished"],
}

JURISCRAPER_CASES: dict[str, Any] = {
    "cases": [
        {
            "case_name": "State v. Miller",
            "docket_number": "S-1-SC-39001",
            "date_filed": "2024-01-18",
            "court": "Example Supreme Court",
            "download_url": "https://courts.example.gov/opinions/39001.pdf",
            "precedential_status": "Published",
            "absolute_url": "https://courts.example.gov/opinion/39001/state-v-miller/",
        },
        {
            "caseName": "In re Estate of Garcia",
            "cluster_id": 987654,
            "docketNumber": "A-22-0417",
            "dateFiled": "2024-01-19",
            "opinions": [{"local_path": "pdf/2024/01/19/in_re_estate_of_garcia.pdf"}],
        },
    ]
}

OLDP_FIXTURE: list[dict[str, Any]] = [
    {
        "model": "cases.case",
        "pk": 1001,
        "fields": {
            "court": "bverfg",
            "court_name": "Bundesverfassungsgericht",
            "file_number": "1 BvR 2017/21",
            "date": "2023-11-15",
            "ecli": "ECLI:DE:BVerfG:2023:rk20231115.1bvr201721",
            "title": "Beschluss zur Versammlungsfreiheit",
            "content": "<p>Die Verfassungsbeschwerde wird nicht zur Entscheidung angenommen.</p>",
            "source_url": "https://oldp.example.de/case/1001/",
        },
    },
    {"model": "courts.court", "pk": 5, "fields": {"name": "Bundesverfassungsgericht"}},
    {
        "model": "cases.case",
        "pk": 1002,
        "fields": {
            "court_name": "Bundesgerichtshof",
            "file_number": "VI ZR 20/23",
            "date": "2023-12-05",
            "content": "<p>Die Revision wird zurückgewiesen.</p>",
            "pdf_url": "https://oldp.example.de/case/1002.pdf",
        },
    },
]

WORLDWIDELAW_ITEMS: dict[str, Any] = {
    "data": {
        "pagination": {"max_page": 1},
        "items": [
            {
                "id": "law-101",
                "title": "Clean Air Act",
                "document_number": "No. 101/2020",
                "issuing_authority": "Ministry of Environment",
                "publication_date": "2020-02-01",
                "effective_date": "2020-07-01",
                "text": "Article 1. Clean air provisions apply nationwide.",
                "files": {"pdf_url": "https://laws.example.org/files/law-101.pdf"},
            },
            {"id": "law-102", "title": "Water Act"},
            {
                "id": "law-103",
                "title": "Forest Act",
                "url": "https://laws.example.org/laws/forest-act",
                "pdf_url": "https://laws.example.org/files/law-103.pdf",
            },
        ],
    }
}

WORLDWIDELAW_OPTIONS: dict[str, Any] = {
    "items_path": "data.items",
    "namespace": "example-gazette/law",
    "detail_url_template": "https://laws.example.org/api/v1/laws/{id}",
    "attachment_rules": [{"url_path": "files.pdf_url", "title_path": "title"}],
}


# --------------------------------------------------------------------------------------------
# Characterization: XML dialects
# --------------------------------------------------------------------------------------------


class TestXmlLegalRecordDialects:
    def test_german_court_dokument_yields_single_case_record(self) -> None:
        records, truncated = _xml_records(
            DE_DOKUMENT_XML, "https://rechtsprechung.example.de/jportal/docs/KORE312342023.xml"
        )
        assert truncated is None
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="oldp/case",
                external_key="ECLI:DE:BGH:2023:120523UIZR12.22.0",
                title="Urteil zur Haftung von Plattformbetreibern",
                doc_number="I ZR 12/22",
                issuer="BGH 1. Zivilsenat",
                published_at="2023-05-12",
                doc_type="案例",
            )
        ]
        assert "Die Revision des Beklagten hat Erfolg." in records[0]["text"]
        assert records[0]["original"] is None

    def test_akoma_ntoso_act_resolves_author_reference_to_legislature(self) -> None:
        url = "https://akn.example.org/akn/ke/act/2021/3/eng@/main.xml"
        records, _ = _xml_records(AKN_ACT_XML, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="/akn/ke/act/2021/3",
                title="Data Protection Act, 2021",
                doc_number="3",
                issuer="Parliament of Kenya",
                published_at="2021-03-15",
                doc_type="法律法规",
                detail_url=url,
            )
        ]
        assert "This Act may be cited as the Data Protection Act" in records[0]["text"]

    def test_akoma_ntoso_judgment_prefers_neutral_citation_and_case_type(self) -> None:
        url = "https://akn.example.org/akn/za/judgment/zacc/2022/14/eng@/main.xml"
        records, _ = _xml_records(AKN_JUDGMENT_XML, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="/akn/za/judgment/zacc/2022/14",
                title="S v Mokoena",
                doc_number="[2022] ZACC 14",
                issuer="Constitutional Court of South Africa",
                published_at="2022-06-09",
                doc_type="案例",
                detail_url=url,
            )
        ]
        assert "The appeal is upheld" in records[0]["text"]

    def test_uslm_public_law_with_generic_doc_title_uses_dublin_core_title(self) -> None:
        url = "https://govinfo.example.gov/link/plaw/117/public/58?link-type=uslm"
        records, _ = _xml_records(USLM_PLAW_XML, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="/us/pl/117/58",
                title="Infrastructure Investment and Jobs Act",
                doc_number="Public Law 117-58",
                issuer="United States Congress",
                published_at="2021-11-15",
                doc_type="法律法规",
                detail_url=url,
            )
        ]
        assert "Infrastructure Investment and Jobs Act" in records[0]["text"]

    def test_uslm_bill_without_identifier_keys_on_doc_number_and_publisher(self) -> None:
        url = "https://govinfo.example.gov/bulkdata/BILLS/118/1/hr/BILLS-118hr1234ih.xml"
        records, _ = _xml_records(USLM_BILL_XML, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="1234",
                title="118 HR 1234 IH: Clean Water Act Amendments of 2023",
                doc_number="1234",
                issuer="U.S. House of Representatives",
                published_at="2023-02-28",
                doc_type="法律法规",
                detail_url=url,
            )
        ]
        assert "Section 101 of the Act is amended." in records[0]["text"]

    def test_uslm_bill_without_doc_title_prefers_long_title_over_dc_title(self) -> None:
        sample = b"""<?xml version="1.0" encoding="UTF-8"?>
<bill xmlns="http://schemas.gpo.gov/xml/uslm" identifier="/us/bill/118/hr/1">
  <meta>
    <dcTitle>118 HR 1 IH</dcTitle>
    <docNumber>1</docNumber>
  </meta>
  <preface>
    <longTitle>Clean Air Act</longTitle>
  </preface>
  <main>
    <section><num>1.</num><content>Body text.</content></section>
  </main>
</bill>
"""
        url = "https://govinfo.example.gov/bulkdata/BILLS/118/1/hr/BILLS-118hr1ih.xml"
        records, _ = _xml_records(sample, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="/us/bill/118/hr/1",
                title="Clean Air Act",
                doc_number="1",
                doc_type="法律法规",
                detail_url=url,
            )
        ]
        assert "# Clean Air Act" in records[0]["text"]

    def test_german_norm_collection_keys_on_root_document_number(self) -> None:
        url = "https://gesetze.example.de/bgb/xml.zip"
        records, _ = _xml_records(DE_NORM_XML, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="BJNR001950896",
                title="Bürgerliches Gesetzbuch",
                doc_number="BGB",
                issuer="RGBl",
                published_at="1896-08-18",
                doc_type="法律法规",
                detail_url=url,
            )
        ]
        assert "Die Rechtsfähigkeit des Menschen beginnt" in records[0]["text"]

    def test_ecfr_part_reads_hierarchy_metadata_and_dates_from_versioned_url(self) -> None:
        url = "https://ecfr.example.gov/api/versioner/v1/full/2024-01-02/title-12.xml?part=1"
        records, _ = _xml_records(US_ECFR_XML, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="/on/2024-01-02/title-12/part-1",
                title="PART 1—INVESTMENT SECURITIES",
                doc_number="12 CFR Part 1",
                issuer="Office of the Federal Register (NARA)",
                published_at="2024-01-02",
                doc_type="法律法规",
                detail_url=url,
            )
        ]
        assert "issued by the Comptroller of the Currency" in records[0]["text"]

    @pytest.mark.parametrize(
        ("sample", "expected_key", "expected_title", "expected_date", "body_snippet"),
        [
            (
                CA_STATUTE_XML,
                "A-1",
                "Access to Information Act",
                "2023-06-20",
                "This Act may be cited as the Access to Information Act.",
            ),
            (
                CA_REGULATION_XML,
                "SOR/83-507",
                "Access to Information Regulations",
                "2022-11-30",
                "These Regulations may be cited",
            ),
        ],
    )
    def test_canadian_consolidation_uses_last_amended_attribute_and_justice_issuer(
        self,
        sample: bytes,
        expected_key: str,
        expected_title: str,
        expected_date: str,
        body_snippet: str,
    ) -> None:
        url = "https://laws-lois.example.ca/eng/XML/consolidation.xml"
        records, _ = _xml_records(sample, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key=expected_key,
                title=expected_title,
                doc_number=expected_key,
                issuer="Department of Justice Canada",
                published_at=expected_date,
                doc_type="法律法规",
                detail_url=url,
            )
        ]
        assert body_snippet in records[0]["text"]

    def test_japanese_law_converts_era_year_to_gregorian_promulgation_date(self) -> None:
        url = "https://elaws.example.jp/api/1/lawdata/322AC0000000067"
        records, _ = _xml_records(JP_LAW_XML, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="昭和二十二年法律第六十七号",
                title="地方自治法",
                doc_number="昭和二十二年法律第六十七号",
                published_at="1947-04-17",
                detail_url=url,
            )
        ]
        assert "地方自治の本旨" in records[0]["text"]

    def test_japanese_data_root_splits_every_embedded_law_into_records(self) -> None:
        url = "https://elaws.example.jp/api/1/lawdata/bulk"
        records, truncated = _xml_records(JP_DATAROOT_XML, url)
        assert truncated is None
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="平成十五年法律第五十七号",
                title="個人情報の保護に関する法律",
                doc_number="平成十五年法律第五十七号",
                published_at="2003-05-30",
                detail_url=url,
            ),
            _expected(
                external_key="令和三年法律第三十五号",
                title="デジタル庁設置法",
                doc_number="令和三年法律第三十五号",
                published_at="2021-05-19",
                detail_url=url,
            ),
        ]
        assert "個人情報の適正な取扱い" in records[0]["text"]
        assert "デジタル庁の設置" in records[1]["text"]

    def test_taiwan_moj_law_split_yields_one_record_per_law_with_moj_metadata(self) -> None:
        url = "https://law.moj.example.tw/api/data/ChLaw.xml"
        records, truncated = _xml_records(TW_MOJ_XML, url)
        assert truncated is None
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="https://law.moj.example.tw/LawClass/LawAll.aspx?pcode=I0050021",
                title="個人資料保護法",
                doc_number="I0050021",
                issuer=records[0].get("issuer"),
                published_at="2023-05-31",
                effective_at=records[0].get("effective_at"),
                status="現行有效",
                detail_url=url,
            ),
            _expected(
                external_key="https://law.moj.example.tw/LawClass/LawAll.aspx?pcode=I0050022",
                title="個人資料保護法施行細則",
                doc_number="I0050022",
                issuer=records[1].get("issuer"),
                published_at="2016-03-15",
                effective_at=records[1].get("effective_at"),
                status="現行有效",
                detail_url=url,
            ),
        ]
        assert "為規範個人資料之蒐集" in records[0]["text"]

    def test_taiwan_moj_law_split_over_document_budget_is_truncated(self) -> None:
        records, truncated = _xml_records(
            TW_MOJ_XML, "https://law.moj.example.tw/api/data/ChLaw.xml", budget_documents=1
        )
        assert truncated == "document_budget_exceeded"
        assert [r["title"] for r in records] == ["個人資料保護法"]

    def test_taiwan_gazette_records_key_on_meta_id_detail_and_convert_roc_dates(self) -> None:
        url = "https://gazette.example.tw/egFront/OpenData/downloadXML.jsp?d=113052"
        records, truncated = _xml_records(TW_GAZETTE_XML, url)
        assert truncated is None
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="https://gazette.example.tw/egFront/detail.do?metaid=145623&log=detailLog",
                title="修正「所得稅法施行細則」",
                doc_number="行政院公報 第030卷 第095期",
                issuer="財政部",
                published_at="2024-05-20",
                doc_type="法律法规",
                detail_url=url,
            ),
            _expected(
                external_key="https://gazette.example.tw/egFront/detail.do?metaid=145624&log=detailLog",
                title="公告113年度土地公告現值",
                doc_number="行政院公報 第030卷 第096期",
                issuer="內政部",
                published_at="2024-05-21",
                detail_url=url,
            ),
        ]
        assert "本細則依所得稅法第一百二十一條規定訂定之" in records[0]["text"]
        assert "土地公告現值" in records[1]["text"]

    def test_taiwan_gazette_over_document_budget_is_truncated(self) -> None:
        records, truncated = _xml_records(
            TW_GAZETTE_XML,
            "https://gazette.example.tw/egFront/OpenData/downloadXML.jsp?d=113052",
            budget_documents=1,
        )
        assert truncated == "document_budget_exceeded"
        assert [r["title"] for r in records] == ["修正「所得稅法施行細則」"]

    def test_feed_and_html_payloads_yield_no_records(self) -> None:
        feed = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
        html_page = b"<!DOCTYPE html><html><body><p>Not legal XML</p></body></html>"
        assert _xml_records(feed, "https://feeds.example.org/laws.rss")[0] == []
        assert _xml_records(html_page, "https://feeds.example.org/index.html")[0] == []

    def test_multi_namespace_akoma_ntoso_and_namespaced_dataroot_extract_records(self) -> None:
        multi_ns_akn = b"""<?xml version="1.0" encoding="UTF-8"?>
<akn:akomaNtoso xmlns:akn="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
                xmlns:ext="urn:national:extension:v1">
  <akn:judgment>
    <akn:meta>
      <akn:identification source="#source">
        <akn:FRBRWork>
          <akn:FRBRuri akn:value="/akn/eu/judgment/sac/2025/42"/>
          <akn:FRBRdate akn:date="2025-11-18"/>
          <akn:FRBRauthor akn:href="#sac"/>
          <akn:FRBRnumber akn:value="2025/SAC/42"/>
        </akn:FRBRWork>
      </akn:identification>
      <akn:references source="#source">
        <ext:TLCOrganization ext:eId="sac" ext:showAs="Supreme Administrative Court"/>
      </akn:references>
    </akn:meta>
    <akn:header>
      <akn:docTitle>Judgment of 18 November 2025 on Value Added Tax</akn:docTitle>
    </akn:header>
    <akn:judgmentBody>
      <akn:decision><akn:p>The appeal is dismissed.</akn:p></akn:decision>
    </akn:judgmentBody>
  </akn:judgment>
</akn:akomaNtoso>"""
        url = "https://courts.example.eu/akn/2025-42.xml"
        records, _ = _xml_records(multi_ns_akn, url)
        assert [_projection(r) for r in records] == [
            _expected(
                external_key="/akn/eu/judgment/sac/2025/42",
                title="Judgment of 18 November 2025 on Value Added Tax",
                doc_number="2025/SAC/42",
                issuer="Supreme Administrative Court",
                published_at="2025-11-18",
                doc_type="案例",
                detail_url=url,
            )
        ]

        ns_dataroot = """<?xml version="1.0" encoding="UTF-8"?>
<DataRoot xmlns="https://elaws.e-gov.go.jp/schema/v1">
  <ApplData>
    <LawFullText>
      <Law Era="Reiwa" Year="6" PromulgateMonth="4" PromulgateDay="1">
        <LawNum>令和六年法律第十号</LawNum>
        <LawBody>
          <LawTitle>行政手続デジタル化推進法</LawTitle>
          <MainProvision>
            <Article Num="1"><Paragraph Num="1"><ParagraphNum/>
              <ParagraphSentence><Sentence>第一条 この法律はデジタル手続を推進する。</Sentence></ParagraphSentence>
            </Paragraph></Article>
          </MainProvision>
        </LawBody>
      </Law>
    </LawFullText>
  </ApplData>
</DataRoot>""".encode("utf-8")
        jp_records, _ = _xml_records(ns_dataroot, "https://elaws.example.jp/api/1/lawdata/r6")
        assert len(jp_records) == 1
        assert jp_records[0]["title"] == "行政手続デジタル化推進法"
        assert jp_records[0]["published_at"] == "2024-04-01"


# --------------------------------------------------------------------------------------------
# Characterization: JSON dataset schemas
# --------------------------------------------------------------------------------------------


class TestJsonLegalRecordSchemas:
    def test_openstates_bills_map_versions_and_documents_to_ordered_downloads(self) -> None:
        url = "https://openstates.example.org/bills?jurisdiction=tx&term=88R"
        payload = {"results": [OPENSTATES_BILL], "pagination": {"page": 1, "max_page": 1}}
        records, truncated = _json_records(payload, url)
        assert truncated is None
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="openstates/bill",
                external_key="ocd-bill/5b1c9a1e-7c11-4d5f-9b0a-2a1d7f0f1a01",
                title="Relating to consumer data privacy",
                doc_number="SB 101",
                issuer="Senate",
                published_at="2023-01-10",
                status="Signed by the Governor",
                doc_type="法律法规",
                detail_url="https://capitol.example.gov/BillLookup/History.aspx?Bill=SB101",
                downloads=[
                    {
                        "url": "https://capitol.example.gov/sb101_enr.pdf",
                        "title": "Relating to consumer data privacy (Enrolled)",
                        "role": "primary",
                    },
                    {
                        "url": "https://capitol.example.gov/sb101_fiscal.pdf",
                        "title": "Relating to consumer data privacy (Fiscal Note)",
                        "role": "independent_attachment",
                    },
                ],
            )
        ]
        assert "Establishes consumer rights over personal data." in records[0]["text"]

    def test_openstates_bills_container_key_is_recognised(self) -> None:
        records, _ = _json_records(
            {"bills": [OPENSTATES_BILL]}, "https://openstates.example.org/bulk/tx.json"
        )
        assert [r["external_key"] for r in records] == [OPENSTATES_BILL["id"]]
        assert records[0]["namespace"] == "openstates/bill"

    def test_opencaselaw_decisions_skip_bodyless_entries_and_strip_title_comma(self) -> None:
        url = "https://opencaselaw.example.ch/api/decisions?court=bger"
        records, truncated = _json_records({"decisions": OPENCASELAW_DECISIONS}, url)
        assert truncated is None
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="opencaselaw/decision",
                external_key="bger_6B_1234_2023",
                title="Urteil vom 14. Februar 2024",
                doc_number="6B_1234/2023",
                issuer="Bundesgericht",
                published_at="2024-02-14",
                doc_type="案例",
                detail_url="https://opencaselaw.example.ch/decisions/bger_6B_1234_2023",
                downloads=[{"url": "https://opencaselaw.example.ch/pdf/bger_6B_1234_2023.pdf"}],
            ),
            _expected(
                namespace="opencaselaw/decision",
                external_key="bvger_A_567_2022",
                title="A-567/2022",
                doc_number="A-567/2022",
                issuer="Bundesverwaltungsgericht",
                published_at="2023-09-01",
                doc_type="案例",
                detail_url="https://opencaselaw.example.ch/decisions/bvger_A_567_2022",
                downloads=[{"url": "https://opencaselaw.example.ch/pdf/bvger_A_567_2022.pdf"}],
            ),
        ]
        assert records[0]["text"].startswith("Sachverhalt")
        assert not records[1].get("text")

    def test_opencaselaw_decisions_over_document_budget_are_truncated(self) -> None:
        records, truncated = _json_records(
            {"decisions": OPENCASELAW_DECISIONS},
            "https://opencaselaw.example.ch/api/decisions?court=bger",
            budget_documents=1,
        )
        assert truncated == "document_budget_exceeded"
        assert [r["external_key"] for r in records] == ["bger_6B_1234_2023"]

    def test_juriscraper_parallel_case_name_lists_zip_into_opinion_records(self) -> None:
        url = "https://courts.example.gov/opinions/recent"
        records, truncated = _json_records(JURISCRAPER_CASE_NAMES, url)
        assert truncated is None
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="juriscraper/opinion",
                external_key="23-101",
                title="Smith v. Jones",
                doc_number="23-101",
                issuer="Supreme Court of Example",
                published_at="2024-03-01",
                status="Published",
                doc_type="案例",
                detail_url=url,
                downloads=[{"url": "https://courts.example.gov/opinions/23-101.pdf"}],
            ),
            _expected(
                namespace="juriscraper/opinion",
                external_key="23-102",
                title="Doe v. Roe",
                doc_number="23-102",
                issuer="Supreme Court of Example",
                published_at="2024-03-08",
                status="Unpublished",
                doc_type="案例",
                detail_url=url,
                downloads=[{"url": "https://courts.example.gov/opinions/23-102.pdf"}],
            ),
        ]

    def test_juriscraper_parallel_lists_over_document_budget_are_truncated(self) -> None:
        records, truncated = _json_records(
            JURISCRAPER_CASE_NAMES, "https://courts.example.gov/opinions/recent", budget_documents=1
        )
        assert truncated == "document_budget_exceeded"
        assert [r["title"] for r in records] == ["Smith v. Jones"]

    def test_juriscraper_case_list_resolves_cluster_ids_and_storage_pdf_paths(self) -> None:
        url = "https://courts.example.gov/api/opinions?page=1"
        records, _ = _json_records(
            JURISCRAPER_CASES, url, extra_hosts=["storage.courts.example.gov"]
        )
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="juriscraper/opinion",
                external_key="S-1-SC-39001",
                title="State v. Miller",
                doc_number="S-1-SC-39001",
                issuer="Example Supreme Court",
                published_at="2024-01-18",
                status="Published",
                doc_type="案例",
                detail_url="https://courts.example.gov/opinion/39001/state-v-miller/",
                downloads=[{"url": "https://courts.example.gov/opinions/39001.pdf"}],
            ),
            _expected(
                namespace="juriscraper/opinion",
                external_key="987654",
                title="In re Estate of Garcia",
                doc_number="A-22-0417",
                published_at="2024-01-19",
                doc_type="案例",
                detail_url=url,
                downloads=[
                    {
                        "url": "https://storage.courts.example.gov/pdf/2024/01/19/"
                        "in_re_estate_of_garcia.pdf"
                    }
                ],
            ),
        ]

    def test_oldp_django_fixture_keeps_only_case_models_with_html_bodies(self) -> None:
        url = "https://oldp.example.de/api/dump/cases.json"
        records, truncated = _json_records(OLDP_FIXTURE, url)
        assert truncated is None
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="oldp/case",
                external_key="ECLI:DE:BVerfG:2023:rk20231115.1bvr201721",
                title="Beschluss zur Versammlungsfreiheit",
                doc_number="1 BvR 2017/21",
                issuer="Bundesverfassungsgericht",
                published_at="2023-11-15",
                doc_type="案例",
                detail_url="https://oldp.example.de/case/1001/",
                downloads=[],
            ),
            _expected(
                namespace="oldp/case",
                external_key="VI ZR 20/23",
                title="VI ZR 20/23",
                doc_number="VI ZR 20/23",
                issuer="Bundesgerichtshof",
                published_at="2023-12-05",
                doc_type="案例",
                downloads=[{"url": "https://oldp.example.de/case/1002.pdf"}],
            ),
        ]
        assert "nicht zur Entscheidung angenommen" in records[0]["html"]
        assert "zurückgewiesen" in records[1]["html"]

    def test_oldp_django_fixture_over_document_budget_is_truncated(self) -> None:
        records, truncated = _json_records(
            OLDP_FIXTURE, "https://oldp.example.de/api/dump/cases.json", budget_documents=1
        )
        assert truncated == "document_budget_exceeded"
        assert [r["doc_number"] for r in records] == ["1 BvR 2017/21"]

    def test_worldwidelaw_items_path_applies_detail_template_and_attachment_rules(
        self,
    ) -> None:
        url = "https://laws.example.org/api/v1/laws?page=1"
        records, truncated = _json_records(WORLDWIDELAW_ITEMS, url, options=WORLDWIDELAW_OPTIONS)
        assert truncated is None
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="example-gazette/law",
                external_key="law-101",
                title="Clean Air Act",
                doc_number="No. 101/2020",
                issuer="Ministry of Environment",
                published_at="2020-02-01",
                effective_at="2020-07-01",
                detail_url="https://laws.example.org/api/v1/laws/law-101",
                downloads=[
                    {"url": "https://laws.example.org/files/law-101.pdf", "title": "Clean Air Act"}
                ],
            ),
            _expected(
                namespace="example-gazette/law",
                external_key="law-103",
                title="Forest Act",
                detail_url="https://laws.example.org/laws/forest-act",
                downloads=[{"url": "https://laws.example.org/files/law-103.pdf"}],
            ),
        ]
        assert records[0]["text"] == "Article 1. Clean air provisions apply nationwide."

    def test_worldwidelaw_generic_records_container_uses_default_namespace(self) -> None:
        url = "https://laws.example.org/export/records.json"
        payload = {
            "records": [
                {
                    "_id": "doc-77",
                    "title": "Consumer Protection Decree",
                    "number": "Decree 77/2019",
                    "authority": "Council of Ministers",
                    "date": "2019-09-09",
                    "url": "https://laws.example.org/decrees/77",
                    "content": "Section 1. Consumers have the right to clear information.",
                }
            ]
        }
        records, _ = _json_records(payload, url)
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="worldwidelaw/document",
                external_key="doc-77",
                title="Consumer Protection Decree",
                doc_number="Decree 77/2019",
                issuer="Council of Ministers",
                published_at="2019-09-09",
                detail_url="https://laws.example.org/decrees/77",
                downloads=[],
            )
        ]
        assert records[0]["text"].startswith("Section 1.")

    def test_opencaselaw_decisions_container_routes_records_without_decision_id_or_docket_number(
        self,
    ) -> None:
        url = "https://example.gov/api/decisions"
        payload = {
            "decisions": [
                {
                    "id": "dec-1",
                    "title": "Judgment One",
                    "case_number": "A-1",
                    "court": "Supreme Court",
                    "date": "2026-01-01",
                    "text": "Full judgment body.",
                    "source_url": "https://example.gov/case/1",
                }
            ]
        }
        records, _ = _json_records(payload, url)
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="opencaselaw/decision",
                external_key="dec-1",
                title="Judgment One",
                doc_number="A-1",
                issuer="Supreme Court",
                published_at="2026-01-01",
                doc_type="案例",
                detail_url="https://example.gov/case/1",
                downloads=[],
            )
        ]
        assert records[0]["text"] == "Full judgment body."

    def test_juriscraper_cases_container_routes_records_with_name_and_nested_opinions(
        self,
    ) -> None:
        url = "https://courts.example.gov/api/cases"
        payload = {
            "cases": [
                {
                    "name": "State v. Miller",
                    "docket_number": "A-1",
                    "opinions": [{"download_url": "https://courts.example.gov/a.pdf"}],
                }
            ]
        }
        records, _ = _json_records(payload, url)
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="juriscraper/opinion",
                external_key="A-1",
                title="State v. Miller",
                doc_number="A-1",
                doc_type="案例",
                detail_url=url,
                downloads=[{"url": "https://courts.example.gov/a.pdf"}],
            )
        ]

    def test_generic_records_with_non_oldp_model_or_scalar_fields_stay_worldwidelaw_documents(
        self,
    ) -> None:
        url = "https://laws.example.org/export/records.json"
        payload = {
            "records": [
                {
                    "id": "law-1",
                    "model": "legislation",
                    "title": "Clean Air Act",
                    "text": "Article 1. Rules apply.",
                    "url": "https://laws.example.org/law/1",
                },
                {
                    "id": "law-2",
                    "fields": ["title", "text"],
                    "title": "Water Act",
                    "text": "Article 1. Water rules apply.",
                    "url": "https://laws.example.org/law/2",
                },
            ]
        }
        records, _ = _json_records(payload, url)
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="worldwidelaw/document",
                external_key="law-1",
                title="Clean Air Act",
                detail_url="https://laws.example.org/law/1",
                downloads=[],
            ),
            _expected(
                namespace="worldwidelaw/document",
                external_key="law-2",
                title="Water Act",
                detail_url="https://laws.example.org/law/2",
                downloads=[],
            ),
        ]

    def test_worldwidelaw_detail_url_template_precedes_number_and_attachment_for_external_key(
        self,
    ) -> None:
        url = "https://laws.example.org/api/v1/laws"
        payload = {
            "items": [
                {
                    "slug": "clean-air",
                    "title": "Clean Air Act",
                    "number": "No. 1/2020",
                    "text": "Article 1. Clean air rules.",
                },
                {
                    "slug": "water-act",
                    "title": "Water Act",
                    "pdf_url": "https://laws.example.org/files/water.pdf",
                },
            ]
        }
        records, _ = _json_records(
            payload,
            url,
            options={"detail_url_template": "https://laws.example.org/laws/{slug}"},
        )
        assert [_projection(r) for r in records] == [
            _expected(
                namespace="worldwidelaw/document",
                external_key="https://laws.example.org/laws/clean-air",
                title="Clean Air Act",
                doc_number="No. 1/2020",
                detail_url="https://laws.example.org/laws/clean-air",
                downloads=[],
            ),
            _expected(
                namespace="worldwidelaw/document",
                external_key="https://laws.example.org/laws/water-act",
                title="Water Act",
                detail_url="https://laws.example.org/laws/water-act",
                downloads=[{"url": "https://laws.example.org/files/water.pdf"}],
            ),
        ]


# --------------------------------------------------------------------------------------------
# Characterization: e-Gov v2 JSON envelopes (shared by XmlParser and JsonParser)
# --------------------------------------------------------------------------------------------

EGOV_HOST = "laws.example.jp"
EGOV_CATALOG_URL = f"https://{EGOV_HOST}/api/2/laws?offset=0"
EGOV_CATALOG: dict[str, Any] = {
    "total_count": 3,
    "count": 2,
    "next_offset": 2,
    "laws": [
        {
            "law_info": {
                "law_id": "415AC0000000057",
                "law_num": "平成十五年法律第五十七号",
                "promulgation_date": "2003-05-30",
            },
            "revision_info": {
                "law_revision_id": "415AC0000000057_20240401_505AC0000000047",
                "law_title": "個人情報の保護に関する法律",
            },
        },
        {
            "law_info": {
                "law_id": "503AC0000000035",
                "law_num": "令和三年法律第三十五号",
                "promulgation_date": "2021-05-19",
            },
            "revision_info": {
                "law_revision_id": "503AC0000000035_20230616_505AC0000000058",
                "law_title": "デジタル庁設置法",
            },
        },
    ],
}

EGOV_REVISION_ID = "503AC0000000035_20230616_505AC0000000058"
EGOV_LAW_DATA_URL = f"https://{EGOV_HOST}/api/2/law_data/{EGOV_REVISION_ID}"
EGOV_LAW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Law Era="Reiwa" Year="3" Num="035" LawType="Act" Lang="ja" PromulgateMonth="05" PromulgateDay="19">
  <LawNum>令和三年法律第三十五号</LawNum>
  <LawBody>
    <LawTitle>デジタル庁設置法</LawTitle>
    <MainProvision>
      <Article Num="1"><ArticleTitle>第一条</ArticleTitle>
        <Paragraph Num="1"><ParagraphNum/><ParagraphSentence><Sentence>この法律は、デジタル庁の設置並びに任務及びこれを達成するため必要となる明確な範囲の所掌事務を定めることを目的とする。</Sentence></ParagraphSentence></Paragraph>
      </Article>
    </MainProvision>
  </LawBody>
</Law>
""".encode()
EGOV_LAW_DATA: dict[str, Any] = {
    "law_info": {
        "law_id": "503AC0000000035",
        "law_num": "令和三年法律第三十五号",
        "promulgation_date": "2021-05-19",
    },
    "revision_info": {
        "law_revision_id": EGOV_REVISION_ID,
        "law_title": "デジタル庁設置法",
        "current_revision_status": "CurrentEnforced",
        "repeal_status": "None",
        "repeal_date": None,
        "amendment_enforcement_date": "2023-06-16",
        "amendment_enforcement_comment": None,
        "amendment_scheduled_enforcement_date": None,
    },
    "law_full_text": base64.b64encode(EGOV_LAW_XML).decode("ascii"),
}


def _egov_parsers() -> tuple[Any, Any]:
    from wlh.processor.parsers.json.parser import JsonParser
    from wlh.processor.parsers.xml.parser import XmlParser

    def cfg(kind: str) -> dict[str, Any]:
        return _source_config(
            kind,
            entry_url=EGOV_CATALOG_URL,
            allowed_hosts=[EGOV_HOST],
            allowed_path_prefixes=["/api/2/"],
        )

    return XmlParser(cfg("xml")), JsonParser(cfg("json"))


class TestEgovEnvelopeRecords:
    def test_catalog_page_requests_each_revision_and_next_offset_identically(self) -> None:
        xml_parser, json_parser = _egov_parsers()
        data = json.dumps(EGOV_CATALOG).encode("utf-8")
        acq = _acquisition(EGOV_CATALOG_URL)
        expected = [
            {
                "url": f"https://{EGOV_HOST}/api/2/law_data/"
                "415AC0000000057_20240401_505AC0000000047?law_full_text_format=xml",
                "role": "page",
            },
            {
                "url": f"https://{EGOV_HOST}/api/2/law_data/"
                "503AC0000000035_20230616_505AC0000000058?law_full_text_format=xml",
                "role": "page",
            },
            {"url": f"https://{EGOV_HOST}/api/2/laws?offset=2", "role": "page"},
        ]
        assert xml_parser.next_requests(data, acq) == expected
        assert json_parser.next_requests(data, acq) == expected
        assert xml_parser.records(data, acq) == []
        assert json_parser.records(data, acq) == []

    def test_law_data_envelope_decodes_embedded_xml_identically_in_both_parsers(self) -> None:
        xml_parser, json_parser = _egov_parsers()
        data = json.dumps(EGOV_LAW_DATA).encode("utf-8")
        acq = _acquisition(EGOV_LAW_DATA_URL)
        xml_records = xml_parser.records(data, acq)
        json_records = json_parser.records(data, acq)
        assert [_projection(r) for r in xml_records] == [
            _expected(
                external_key="503AC0000000035",
                title="デジタル庁設置法",
                doc_number="令和三年法律第三十五号",
                published_at="2021-05-19",
                status="CurrentEnforced",
                doc_type="法律法规",
                detail_url=EGOV_LAW_DATA_URL,
            )
        ]
        assert "デジタル庁の設置" in xml_records[0]["text"]
        assert json_records == xml_records
        assert xml_parser.next_requests(data, acq) == []
        assert json_parser.next_requests(data, acq) == []


# --------------------------------------------------------------------------------------------
# Public surface: declarative schema tables
# --------------------------------------------------------------------------------------------

EXPECTED_XML_SCHEMA_NAMES = {
    "de_dokument",
    "akoma_ntoso",
    "uslm",
    "de_norm",
    "us_ecfr",
    "ca_statute",
    "jp_egov_xml",
    "tw_moj_xml",
    "tw_gazette_xml",
}
EXPECTED_JSON_SCHEMA_NAMES = {"openstates", "opencaselaw", "juriscraper", "oldp", "worldwidelaw"}


class TestXmlSchemaSpecTable:
    def test_schema_table_declares_exactly_the_nine_legal_xml_dialects(self) -> None:
        from wlh.processor.parsers.xml.schemas import XML_SCHEMA_SPECS, XmlSchemaSpec

        names = [spec.name for spec in XML_SCHEMA_SPECS]
        assert len(names) == len(set(names))
        assert set(names) == EXPECTED_XML_SCHEMA_NAMES
        for spec in XML_SCHEMA_SPECS:
            assert isinstance(spec, XmlSchemaSpec)
            assert isinstance(spec.fields, Mapping)
            assert spec.markdown_dialect in {"legal_xml", "dokument", "gazette"}

    def test_schema_specs_are_immutable(self) -> None:
        import dataclasses

        from wlh.processor.parsers.xml.schemas import XML_SCHEMA_SPECS

        spec = XML_SCHEMA_SPECS[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(spec, "name", "mutated")

    @pytest.mark.parametrize(
        ("expected_name", "sample"),
        XML_SCHEMA_SAMPLES,
        ids=[f"{name}-{i}" for i, (name, _) in enumerate(XML_SCHEMA_SAMPLES)],
    )
    def test_parsed_dialect_root_matches_its_schema_spec(
        self, expected_name: str, sample: bytes
    ) -> None:
        from wlh.processor.parsers.xml.schemas import match_xml_schema

        spec = match_xml_schema(_root(sample))
        assert spec is not None
        assert spec.name == expected_name

    def test_unknown_vocabulary_root_matches_no_schema(self) -> None:
        from wlh.processor.parsers.xml.schemas import match_xml_schema

        assert match_xml_schema(_root(b"<inventory><item>widget</item></inventory>")) is None

    def test_table_driven_evaluator_is_published(self) -> None:
        from wlh.processor.parsers.xml.schemas import extract_xml_records

        assert callable(extract_xml_records)

    def test_custom_declarative_xml_schema_spec_evaluates_without_code_branches(
        self,
    ) -> None:
        from wlh.processor.parsers.xml.schemas import (
            XML_SCHEMA_SPECS,
            XmlSchemaSpec,
            extract_xml_records,
            match_xml_schema,
        )

        custom_spec = XmlSchemaSpec(
            name="custom_decree_xml",
            root_tags=("DecreePackage",),
            record_xpath='.//*[local-name()="Decree"]',
            fields={
                "title": ("Heading",),
                "doc_number": ("DecreeNo",),
                "external_key": ("@id", "$doc_number"),
                "published_at": ("IssuedDate",),
                "issuer": ("Ministry",),
            },
        )
        custom_specs = (custom_spec, *XML_SCHEMA_SPECS)

        raw = (
            b"<DecreePackage>"
            b'<Decree id="DEC-2026-01">'
            b"<Heading>Custom Maritime Decree</Heading>"
            b"<DecreeNo>No. 1/2026</DecreeNo>"
            b"<IssuedDate>2026-03-01</IssuedDate>"
            b"<Ministry>Ministry of Transport</Ministry>"
            b"<Body><P>Article 1. Safety rules.</P></Body>"
            b"</Decree>"
            b"</DecreePackage>"
        )
        root = _root(raw)
        matched = match_xml_schema(root, specs=custom_specs)
        assert matched == custom_spec

        records, truncated = extract_xml_records(
            root,
            _acquisition("https://example.gov/decrees.xml"),
            raw_bytes=raw,
            specs=custom_specs,
        )
        assert truncated is None
        assert len(records) == 1
        rec = records[0]
        assert rec["external_key"] == "DEC-2026-01"
        assert rec["title"] == "Custom Maritime Decree"
        assert rec["doc_number"] == "No. 1/2026"
        assert rec["published_at"] == "2026-03-01"
        assert rec["issuer"] == "Ministry of Transport"
        assert "Safety rules." in rec["text"]


class TestJsonSchemaSpecTable:
    def test_schema_table_declares_exactly_the_five_dataset_schemas(self) -> None:
        from wlh.processor.parsers.json.schemas import JSON_SCHEMA_SPECS, JsonSchemaSpec

        names = [spec.name for spec in JSON_SCHEMA_SPECS]
        assert len(names) == len(set(names))
        assert set(names) == EXPECTED_JSON_SCHEMA_NAMES
        for spec in JSON_SCHEMA_SPECS:
            assert isinstance(spec, JsonSchemaSpec)
            assert isinstance(spec.fields, Mapping)
            assert spec.body_format in {"text", "html"}

    @pytest.mark.parametrize(
        ("expected_name", "sample"),
        [
            ("openstates", OPENSTATES_BILL),
            ("opencaselaw", OPENCASELAW_DECISIONS[0]),
            ("juriscraper", JURISCRAPER_CASES["cases"][0]),
            ("juriscraper", JURISCRAPER_CASE_NAMES),
            ("oldp", OLDP_FIXTURE[0]),
            ("worldwidelaw", WORLDWIDELAW_ITEMS["data"]["items"][0]),
            ("worldwidelaw", {"title": "Untyped record", "content": "Body text."}),
        ],
    )
    def test_record_sample_fingerprint_selects_its_schema_spec(
        self, expected_name: str, sample: Mapping[str, Any]
    ) -> None:
        from wlh.processor.parsers.json.schemas import match_json_schema

        assert match_json_schema(sample).name == expected_name

    def test_table_driven_converter_is_published(self) -> None:
        from wlh.processor.parsers.json.schemas import extract_json_records

        assert callable(extract_json_records)

    def test_json_parser_no_longer_carries_per_dataset_record_methods(self) -> None:
        from wlh.processor.parsers.json.parser import JsonParser

        legacy = (
            "_openstates_records",
            "_opencaselaw_records",
            "_juriscraper_json_records",
            "_oldp_json_records",
            "_worldwidelaw_records",
        )
        assert [name for name in legacy if hasattr(JsonParser, name)] == []

    def test_custom_declarative_json_schema_spec_evaluates_without_code_branches(
        self,
    ) -> None:
        from wlh.processor.parsers.json.schemas import (
            JSON_SCHEMA_SPECS,
            JsonSchemaSpec,
            extract_json_records,
            match_json_schema,
        )

        custom_spec = JsonSchemaSpec(
            name="custom_gazette_json",
            namespace="custom/gazette",
            default_doc_type="政策文件",
            fingerprint_keys=("gazette_uid",),
            container_keys=("directives",),
            fields={
                "external_key": ("gazette_uid",),
                "title": ("subject",),
                "doc_number": ("directive_no",),
                "issuer": ("agency.name",),
                "published_at": ("pub_date",),
                "detail_url": ("permalink",),
                "text": ("body_text",),
            },
        )
        custom_specs = (custom_spec, *JSON_SCHEMA_SPECS)

        matched = match_json_schema(
            {"gazette_uid": "GZ-99", "subject": "Energy Directive"},
            specs=custom_specs,
        )
        assert matched == custom_spec

        payload = {
            "directives": [
                {
                    "gazette_uid": "GZ-99",
                    "subject": "Energy Directive",
                    "directive_no": "D-99",
                    "agency": {"name": "Energy Board"},
                    "pub_date": "2026-04-10",
                    "permalink": "https://example.gov/gz/99",
                    "body_text": "Section 1. Grid reliability.",
                }
            ]
        }
        records, truncated = extract_json_records(
            payload,
            _acquisition("https://example.gov/api/directives"),
            specs=custom_specs,
        )
        assert truncated is None
        assert len(records) == 1
        rec = records[0]
        assert rec["namespace"] == "custom/gazette"
        assert rec["external_key"] == "GZ-99"
        assert rec["title"] == "Energy Directive"
        assert rec["doc_number"] == "D-99"
        assert rec["issuer"] == "Energy Board"
        assert rec["published_at"] == "2026-04-10"
        assert rec["doc_type"] == "政策文件"
        assert rec["detail_url"] == "https://example.gov/gz/99"
        assert "Grid reliability." in rec["text"]


class TestJapanEnvelopeHelpers:
    def test_envelope_helpers_live_in_shared_record_base_and_stay_reexported(self) -> None:
        from wlh.processor.parsers import record_base
        from wlh.processor.parsers import xml as xml_package

        assert callable(getattr(record_base, "japan_records", None))
        assert callable(getattr(record_base, "japan_requests", None))
        assert getattr(xml_package, "japan_records") is getattr(record_base, "japan_records")
        assert getattr(xml_package, "japan_requests") is getattr(record_base, "japan_requests")

    def test_shared_envelope_helpers_reproduce_catalog_requests(self) -> None:
        from wlh.processor.parsers import record_base

        japan_requests: Any = getattr(record_base, "japan_requests")
        xml_parser, _ = _egov_parsers()
        data = json.dumps(EGOV_CATALOG).encode("utf-8")
        acq = _acquisition(EGOV_CATALOG_URL)
        assert japan_requests(data, acq, xml_parser.config) == xml_parser.next_requests(data, acq)


# --------------------------------------------------------------------------------------------
# Public surface: unified parser configuration view
# --------------------------------------------------------------------------------------------


def _view_config() -> dict[str, Any]:
    from wlh.config import SourceConfig

    raw: dict[str, Any] = {
        "id": "dd-991",
        "scheduler": {
            "scope": "Parser configuration view contract",
            "entry_urls": ["https://laws.example.org/api/v1/laws?page=1"],
            "allowed_hosts": ["laws.example.org"],
            "allowed_path_prefixes": ["/api/"],
            "max_depth": 4,
            "budget": {"documents": 7, "response_bytes": 204800},
        },
        "processor": {
            "parser": {
                "type": "json",
                "options": {"max_pages": 3, "items_path": "data.items"},
                "transforms": ["verify_zip_attachments"],
                "documents": {"detail_url_patterns": [r"/api/v1/laws/\d+$"]},
            },
            "extractor": {"quirks": ["zip_salvage"]},
        },
        "cleaners": {
            "quality_accept_routes": ["accept", "accept_with_warning"],
            "strip_text_regexes": [r"\[Print\]"],
            "title_strip_regexes": [r"\s*\|\s*Example Laws$"],
        },
        "storage": {
            "archive_approved": True,
            "metadata": {"source_name": "Example Law Portal", "document_type": "法律法规"},
        },
    }
    return SourceConfig.model_validate(raw).model_dump()


def _view_parsers(config: Mapping[str, Any]) -> list[Any]:
    from wlh.processor.parsers.html.parser import HtmlParser
    from wlh.processor.parsers.json.parser import JsonParser
    from wlh.processor.parsers.xml.parser import XmlParser

    return [cls(dict(config)) for cls in (HtmlParser, JsonParser, XmlParser)]


class TestParserConfigView:
    def test_every_engine_exposes_the_same_typed_configuration_view(self) -> None:
        from wlh.processor.parsers import Base

        for parser in _view_parsers(_view_config()):
            assert isinstance(parser, Base)
            assert parser.entry_urls == ["https://laws.example.org/api/v1/laws?page=1"]
            assert parser.max_depth == 4
            assert parser.budget_documents == 7
            assert parser.budget_response_bytes == 204800
            assert parser.quality_accept_routes == ("accept", "accept_with_warning")
            assert parser.strip_text_regexes == (r"\[Print\]",)
            assert parser.title_strip_regexes == (r"\s*\|\s*Example Laws$",)
            assert parser.transforms == {"verify_zip_attachments"}
            assert parser.quirks == {"zip_salvage"}
            assert parser.parser_options["max_pages"] == 3
            assert parser.parser_options["items_path"] == "data.items"
            assert parser.doc_rules["detail_url_patterns"] == [r"/api/v1/laws/\d+$"]
            assert parser.cleaners_spec["strip_text_regexes"] == [r"\[Print\]"]
            assert parser.metadata_spec["source_name"] == "Example Law Portal"
            assert parser.metadata_spec["document_type"] == "法律法规"
            assert parser.parser_spec["type"] == "json"
            assert parser.extractor_spec["quirks"] == ["zip_salvage"]

    def test_metadata_view_is_a_copy_that_cannot_mutate_the_config(self) -> None:
        for parser in _view_parsers(_view_config()):
            view = parser.metadata_spec
            view["source_name"] = "mutated"
            assert parser.metadata_spec["source_name"] == "Example Law Portal"

    def test_budget_without_explicit_limits_falls_back_to_documented_defaults(self) -> None:
        from wlh.processor.parsers import Base

        base = Base(
            {
                "scheduler": {
                    "entry_urls": ["https://laws.example.org/"],
                    "budget": {"requests": 5},
                }
            }
        )
        view: Any = base
        assert view.budget_documents == 25
        assert view.budget_response_bytes == 15_000_000
        assert view.quality_accept_routes == ("accept", "accept_ocr", "accept_with_warning")
        assert view.strip_text_regexes == ()
        assert view.title_strip_regexes == ()

    def test_legacy_flat_config_keys_feed_the_same_view(self) -> None:
        from wlh.processor.parsers import Base

        base = Base(
            {
                "adapter": "json",
                "entry_urls": ["https://laws.example.org/legacy"],
                "allowed_hosts": ["laws.example.org"],
                "adapter_options": {"items_path": "rows"},
                "budget": {"documents": 3, "response_bytes": 4096},
                "metadata": {"document_type": "案例"},
            }
        )
        view: Any = base
        assert view.entry_urls == ["https://laws.example.org/legacy"]
        assert view.parser_options == {"items_path": "rows"}
        assert view.budget_documents == 3
        assert view.budget_response_bytes == 4096
        assert view.metadata_spec["document_type"] == "案例"


# --------------------------------------------------------------------------------------------
# Public surface: in-process text-to-Markdown gateway
# --------------------------------------------------------------------------------------------

INLINE_HTML = (
    b"<html><head><title>Notice</title></head><body><div id='content'>"
    b"<h1>Notice on Tax Filing</h1><p>Taxpayers shall file returns by 31 May.</p>"
    b"<p>[Print]</p></div></body></html>"
)
INLINE_XML = (
    b'<?xml version="1.0"?><RULE><PREAMB><AGENCY>Department of Example</AGENCY>'
    b"<SUBJECT>Clean Water Standards</SUBJECT></PREAMB>"
    b"<SUPLINF><HD SOURCE='HD1'>Background</HD><P>The agency revises water standards.</P>"
    b"<P>[Print]</P></SUPLINF></RULE>"
)
INLINE_JSON = json.dumps(
    {
        "document": {
            "title": "Consumer Protection Decree",
            "body": "Section 1. Consumers have the right to clear information. [Print]",
        }
    }
)
INLINE_TXT = "Article 1. This decree enters into force on publication.\n\n[Print]\n"


class TestInlineMarkdownGateway:
    def test_html_matches_stage3b_html_converter(self) -> None:
        from wlh.processor.extractors.html.html_to_md import html_to_markdown
        from wlh.processor.extractors.registry import inline_markdown

        base_url = "https://laws.example.org/notice.html"
        expected = html_to_markdown(INLINE_HTML, base_url, quirks=())
        assert inline_markdown(INLINE_HTML, "html", base_url=base_url, quirks=()) == expected
        assert "Taxpayers shall file returns by 31 May." in expected

    def test_xml_matches_stage3b_xml_converter(self) -> None:
        from wlh.processor.extractors.registry import inline_markdown
        from wlh.processor.extractors.xml.markdown import xml_to_markdown

        expected = xml_to_markdown(INLINE_XML)
        assert inline_markdown(INLINE_XML, "xml") == expected
        assert "The agency revises water standards." in expected

    def test_json_matches_stage3b_json_converter(self) -> None:
        from wlh.processor.extractors.html.html_to_md import json_to_markdown
        from wlh.processor.extractors.registry import inline_markdown

        expected = json_to_markdown(INLINE_JSON)
        assert inline_markdown(INLINE_JSON, "json") == expected
        assert "Consumer Protection Decree" in expected

    def test_plain_text_matches_stage3b_text_converter(self) -> None:
        from wlh.processor.extractors.html.html_to_md import text_to_markdown
        from wlh.processor.extractors.registry import inline_markdown

        expected = text_to_markdown(INLINE_TXT)
        assert inline_markdown(INLINE_TXT, "txt") == expected
        assert "enters into force on publication" in expected

    @pytest.mark.parametrize(
        ("kind", "source"),
        [
            ("html", INLINE_HTML),
            ("xml", INLINE_XML),
            ("json", INLINE_JSON),
            ("txt", INLINE_TXT),
        ],
    )
    def test_strip_text_patterns_are_applied_like_the_text_cleaner(
        self, kind: str, source: str | bytes
    ) -> None:
        from wlh.cleaners.text_cleaner import apply_text_cleaners
        from wlh.processor.extractors.registry import inline_markdown

        base_url = "https://laws.example.org/notice.html"
        raw = inline_markdown(source, kind, base_url=base_url, quirks=())
        assert "[Print]" in raw
        cleaned = inline_markdown(
            source, kind, base_url=base_url, quirks=(), strip_text_regexes=(r"\[Print\]",)
        )
        assert cleaned == apply_text_cleaners(raw, (r"\[Print\]",))
        assert "[Print]" not in cleaned
