#!/usr/bin/env python3
"""Diagnose access to official law sources without modifying the corpus."""
import json
import socket
import ssl
import urllib.request
from datetime import datetime, timezone

HOSTS=("www.pravo.gov.ru","publication.pravo.gov.ru","pravo.minjust.ru")
def probe(host):
    result={"host":host,"dns":"failed","https":"not_attempted"}
    try:
        addresses=sorted({row[4][0] for row in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
        result["dns"]="ok"
        result["resolved_addresses_count"]=len(addresses)
    except OSError as exc:
        result["error"]=type(exc).__name__+": "+str(exc)
        return result
    try:
        req=urllib.request.Request("https://"+host+"/",headers={"User-Agent":"legal-source-connectivity-check/1.0"})
        with urllib.request.urlopen(req,timeout=12,context=ssl.create_default_context()) as response:
            result["https"]="ok"
            result["http_status"]=response.status
    except Exception as exc:
        result["https"]="failed"
        result["error"]=type(exc).__name__+": "+str(exc)
    return result
def main():
    print(json.dumps({"checked_at":datetime.now(timezone.utc).isoformat(),
                      "checks":[probe(host) for host in HOSTS],
                      "note":"Network diagnostic only; does not establish availability of full consolidated texts or rights to download."},
                     ensure_ascii=False,indent=2))
if __name__=="__main__":main()
