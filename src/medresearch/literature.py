"""Explicit public PubMed search and local evidence/definition verification."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable


BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def fetch_public(url: str) -> bytes:
    if not url.startswith(BASE):
        raise ValueError("Only public NCBI E-utilities requests are supported")
    request = urllib.request.Request(url, headers={"User-Agent": "MedResearch/0.2 public-literature"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if urllib.parse.urlparse(response.url).hostname != "eutils.ncbi.nlm.nih.gov":
            raise ValueError("Unexpected literature redirect")
        body = response.read(5_000_001)
    if len(body) > 5_000_000:
        raise ValueError("Literature response exceeds size limit")
    return body


def search_pubmed(query: str, limit: int = 5, fetch: Callable = fetch_public) -> dict:
    if not query.strip() or not 1 <= limit <= 10:
        raise ValueError("PubMed requires a public query and limit between 1 and 10")
    url = BASE + "esearch.fcgi?" + urllib.parse.urlencode({"db": "pubmed", "retmode": "json",
        "retmax": limit, "term": query, "tool": "medresearch"})
    search = json.loads(fetch(url))
    ids = search.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return {"query": query, "sources": [], "provider": "PubMed"}
    if any(not str(pmid).isdigit() for pmid in ids):
        raise ValueError("Invalid PubMed identifier")
    xml = fetch(BASE + "efetch.fcgi?" + urllib.parse.urlencode({"db": "pubmed", "retmode": "xml", "id": ",".join(ids)}))
    root = ET.fromstring(xml)
    sources = []
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//MedlineCitation/PMID")
        title = "".join(article.find(".//ArticleTitle").itertext()) if article.find(".//ArticleTitle") is not None else ""
        if pmid not in ids or not title:
            continue
        entry = {"id": "pmid:"+pmid, "pmid": pmid, "title": title,
                 "url": "https://pubmed.ncbi.nlm.nih.gov/"+pmid+"/", "verification": "pubmed"}
        year = article.findtext(".//JournalIssue/PubDate/Year")
        if year and year.isdigit():
            entry["year"] = int(year)
        sources.append(entry)
    return {"query": query, "sources": sources, "provider": "PubMed",
            "limitations": ["Retrieval verifies bibliographic metadata, not relevance, study quality or a clinical definition"]}


def audit_definitions(spec: dict) -> dict:
    sources = {item["id"]: item for item in spec["evidence"]}
    unreviewed, unverified, bad_links = [], [], []
    for definition in spec["definitions"]:
        if not definition["reviewed"]:
            unreviewed.append(definition["column"])
        source = sources[definition["source_id"]]
        if source["verification"] == "unverified":
            unverified.append(source["id"])
        parsed = urllib.parse.urlparse(source["url"])
        if parsed.scheme != "https" or not parsed.hostname:
            bad_links.append(source["id"])
        if source.get("pmid") and source["url"] != f"https://pubmed.ncbi.nlm.nih.gov/{source['pmid']}/":
            bad_links.append(source["id"])
    return {"passed": not (unreviewed or unverified or bad_links),
            "unreviewed_columns": sorted(set(unreviewed)), "unverified_sources": sorted(set(unverified)),
            "invalid_source_links": sorted(set(bad_links)),
            "verification_scope": "Local declarations and metadata consistency, not independent scientific validation"}


def verify_pubmed_sources(sources: list[dict], fetch: Callable = fetch_public) -> dict:
    """Explicit network verification; never implicitly invoked by local studies."""
    pmids = [s["pmid"] for s in sources if s.get("pmid")]
    if not pmids or len(pmids) > 10 or any(not p.isdigit() for p in pmids):
        raise ValueError("Verification requires between one and ten numeric PubMed IDs")
    query = " OR ".join(p+"[uid]" for p in pmids)
    found = search_pubmed(query, limit=len(pmids), fetch=fetch)["sources"]
    actual = {s["pmid"]: s for s in found}
    records = []
    for source in sources:
        pmid = source.get("pmid")
        if not pmid:
            records.append({"id": source["id"], "status": "not_pubmed"})
            continue
        match = actual.get(pmid)
        title_matches = bool(match and " ".join(match["title"].casefold().split()) == " ".join(source["title"].casefold().split()))
        records.append({"id": source["id"], "pmid": pmid, "status": "verified" if title_matches else "metadata_mismatch"})
    return {"sources": records, "verification_scope": "Bibliographic ID/title only, not scientific validity"}
