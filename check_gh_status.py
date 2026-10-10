import urllib.request
import json

url = "https://api.github.com/repos/barshal-horse/Go-qunat-bootcamp/actions/runs?per_page=5"
req = urllib.request.Request(url, headers={"User-Agent": "Quant-Checker"})
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        print(f"Total runs: {data.get('total_count')}")
        for r in data.get("workflow_runs", []):
            msg = r.get("head_commit", {}).get("message", "").split("\n")[0]
            print(f"Run ID: {r['id']} | Status: {r['status']} | Conclusion: {r.get('conclusion')} | Commit: {msg[:60]}")
except Exception as e:
    print(f"Error fetching runs: {e}")
