import csv, json
from fii_engine import bse_shareholding_scripcode_crosswalk as mod

def test_dated_scripcode_isin_crosswalk_is_exact_and_conservative(tmp_path, monkeypatch):
    candidates=tmp_path/"candidates.csv"
    with candidates.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["reporting_date","symbol","universe_isin","master_isin","master_company_name","master_record_id","master_xbrl_url"])
        w.writeheader()
        w.writerow({"reporting_date":"2026-09-30","symbol":"360ONE","universe_isin":"INE466L01038","master_isin":"INE466L01020","master_company_name":"360 ONE WAM LIMITED","master_record_id":"1","master_xbrl_url":"https://example.test/1"})
        w.writerow({"reporting_date":"2026-09-30","symbol":"AARTIIND","universe_isin":"INE769A01020","master_isin":"INE769A01012","master_company_name":"Aarti Industries Limited","master_record_id":"2","master_xbrl_url":"https://example.test/2"})
    xbrl=tmp_path/"xbrl.json"
    xbrl.write_text(json.dumps({"observations":[
        {"symbol":"360ONE","bse_scrip_codes":["542772"],"sha256":"a"*64},
        {"symbol":"AARTIIND","bse_scrip_codes":["524208"],"sha256":"b"*64}]}))
    bhav=("TradDt,FinInstrmId,ISIN,TckrSymb,SctySrs,FinInstrmNm\n"
          "2026-09-30,542772,INE466L01038,360ONE,A,360 ONE WAM LIMITED\n"
          "2026-09-30,524208,INE769A99999,AARTIIND,B,Aarti Industries Limited\n").encode()
    responses=iter([(b"bootstrap",{"http_status":200}), (bhav,{"http_status":200,"content_type":"text/csv"})])
    monkeypatch.setattr(mod,"get",lambda *a,**k: next(responses))
    report=mod.reconcile(candidates,xbrl,tmp_path/"out","2026-09-30")
    assert report["verified_count"]==1
    assert report["counts"]["verified_exact_dated_bse_scripcode_isin_match"]==1
    assert report["counts"]["bse_isin_conflicts_with_nse_universe"]==1
    rows=list(csv.DictReader((tmp_path/"out/shareholding_bse_scripcode_isin_crosswalk.csv").open()))
    assert rows[0]["bse_isin"]=="INE466L01038"
    assert rows[1]["bse_crosswalk_status"]=="bse_isin_conflicts_with_nse_universe"

def test_xbrl_parser_extracts_only_bse_scripcode_scheme():
    from fii_engine.nse_shareholding_xbrl_entity_probe import parse_xbrl_entity
    x=b'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"><xbrli:context id="c"><xbrli:entity><xbrli:identifier scheme="http://www.bseindia.com/in-bse-shp/ScripCode">542772</xbrli:identifier></xbrli:entity></xbrli:context></xbrli:xbrl>'''
    assert parse_xbrl_entity(x)["bse_scrip_codes"]==["542772"]
    y=x.replace(b"http://www.bseindia.com/in-bse-shp/ScripCode",b"https://www.mca.gov.in")
    assert parse_xbrl_entity(y)["bse_scrip_codes"]==[]
