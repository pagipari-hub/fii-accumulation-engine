"""Join NSE shareholding XBRL BSE ScripCode to dated BSE UDiFF ISIN evidence."""
from __future__ import annotations
import argparse, csv, hashlib, json, time
from collections import defaultdict
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE="https://www.bseindia.com"
BOOT=f"{BASE}/markets/marketinfo/bhavcopy"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
HTTP=build_opener(HTTPCookieProcessor(CookieJar()))

def get(url, referer, accept="*/*"):
    req=Request(url,headers={"User-Agent":UA,"Referer":referer,"Accept":accept,"Accept-Language":"en-US,en;q=0.9"})
    for attempt in range(5):
        try:
            with HTTP.open(req,timeout=90) as res:
                data=res.read()
                if not data: raise RuntimeError(f"empty BSE response: {url}")
                return data,{"http_status":res.status,"content_type":res.headers.get("Content-Type",""),"response_url":res.geturl()}
        except HTTPError as exc:
            if exc.code in {403,429,500,502,503,504} and attempt<4:
                time.sleep(2**attempt); continue
            raise RuntimeError(f"BSE request failed: {url}: {exc}") from exc
        except (URLError,TimeoutError,OSError) as exc:
            if attempt<4:
                time.sleep(2**attempt); continue
            raise RuntimeError(f"BSE request failed: {url}: {exc}") from exc
    raise RuntimeError("BSE retries exhausted")

def reconcile(candidates_csv:Path,xbrl_json:Path,output_dir:Path,reporting_date:str)->dict:
    with candidates_csv.open(encoding="utf-8-sig",newline="") as f: candidates=list(csv.DictReader(f))
    if not candidates: raise ValueError("empty candidate audit")
    if {r["reporting_date"] for r in candidates}!={reporting_date}: raise ValueError("candidate date mismatch")
    xbrl=json.loads(xbrl_json.read_text(encoding="utf-8"))
    obs={o["symbol"]:o for o in xbrl.get("observations",[])}
    missing=sorted({r["symbol"] for r in candidates}-set(obs))
    if missing: raise ValueError(f"XBRL probe incomplete; missing {len(missing)} symbols: {missing[:10]}")
    _,bootmeta=get(BOOT,BASE+"/","text/html,*/*")
    ymd=reporting_date.replace("-","")
    url=f"{BASE}/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"
    payload,meta=get(url,BOOT,"text/csv,*/*")
    digest=hashlib.sha256(payload).hexdigest()
    output_dir.mkdir(parents=True,exist_ok=True)
    raw=output_dir/f"BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000_{digest[:16]}.csv"
    if raw.exists() and hashlib.sha256(raw.read_bytes()).hexdigest()!=digest: raise RuntimeError("immutable BSE source collision")
    if not raw.exists(): raw.write_bytes(payload)
    reader=csv.DictReader(payload.decode("utf-8-sig").splitlines())
    if not reader.fieldnames: raise ValueError("BSE bhavcopy has no header")
    fm={h.strip():h for h in reader.fieldnames}
    required={"TradDt","FinInstrmId","ISIN"}
    if not required.issubset(fm): raise ValueError(f"BSE bhavcopy missing columns: {sorted(required-set(fm))}")
    data=list(reader)
    dates={r[fm["TradDt"]].strip() for r in data if r.get(fm["TradDt"],"").strip()}
    if dates!={reporting_date}: raise ValueError(f"BSE source date mismatch: {sorted(dates)}")
    bycode=defaultdict(list)
    for r in data:
        code=r.get(fm["FinInstrmId"],"").strip()
        if code: bycode[code].append(r)
    out=[]; counts=defaultdict(int)
    for c in candidates:
        o=obs[c["symbol"]]; codes=o.get("bse_scrip_codes",[])
        row=dict(c); row.update({"xbrl_bse_scrip_code":codes[0] if len(codes)==1 else "",
          "bse_isin":"","bse_series":"","bse_security_name":"","bse_crosswalk_status":"",
          "bse_bhavcopy_sha256":digest,"bse_bhavcopy_url":url,"xbrl_sha256":o.get("sha256","")})
        if len(codes)==0: status="xbrl_has_no_bse_scrip_code"
        elif len(codes)>1: status="xbrl_has_multiple_bse_scrip_codes"
        else:
            matches=bycode.get(codes[0],[])
            isins={r.get(fm["ISIN"],"").strip().upper() for r in matches if r.get(fm["ISIN"],"").strip()}
            if not matches: status="bse_code_absent_from_dated_bhavcopy"
            elif len(isins)!=1: status="ambiguous_bse_code_isin_rows"
            else:
                isin=next(iter(isins)); row["bse_isin"]=isin
                m=next(r for r in matches if r.get(fm["ISIN"],"").strip().upper()==isin)
                row["bse_series"]=m.get(fm.get("SctySrs",""),"").strip() if "SctySrs" in fm else ""
                row["bse_security_name"]=m.get(fm.get("FinInstrmNm",""),"").strip() if "FinInstrmNm" in fm else ""
                status="verified_exact_dated_bse_scripcode_isin_match" if isin==c["universe_isin"].strip().upper() else "bse_isin_conflicts_with_nse_universe"
        row["bse_crosswalk_status"]=status; counts[status]+=1; out.append(row)
    outcsv=output_dir/"shareholding_bse_scripcode_isin_crosswalk.csv"
    with outcsv.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
    verified=sum(v for k,v in counts.items() if k.startswith("verified_"))
    report={"publisher":"Bombay Stock Exchange","retrieved_at_utc":datetime.now(timezone.utc).isoformat(),
      "reporting_date":reporting_date,"candidate_count":len(candidates),"xbrl_observation_count":len(obs),
      "bse_source_url":url,"bse_source_http_status":meta["http_status"],"bse_source_bytes":len(payload),
      "bse_source_sha256":digest,"bse_source_path":str(raw),"bse_source_rows":len(data),
      "counts":dict(sorted(counts.items())),"verified_count":verified,"unresolved_count":len(candidates)-verified,
      "identity_safety":{"exactly_one_bse_scripcode_required":True,"dated_bse_code_to_isin_required":True,
       "exact_isin_match_to_dated_nse_universe_required":True,"no_name_only_match":True,
       "missing_bhavcopy_row_remains_unresolved":True},"output_csv":str(outcsv)}
    (output_dir/"shareholding_bse_scripcode_isin_crosswalk.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidates",type=Path,required=True); p.add_argument("--xbrl-report",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--reporting-date",required=True)
    a=p.parse_args(); print(json.dumps(reconcile(a.candidates,a.xbrl_report,a.output_dir,a.reporting_date),indent=2))
if __name__=="__main__": main()
