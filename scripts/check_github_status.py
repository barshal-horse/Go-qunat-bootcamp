import os
import httpx
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json"
}

r = httpx.get("https://api.github.com/repos/barshal-horse/Go-qunat-bootcamp/actions/runs?per_page=25", headers=headers)
if r.status_code == 200:
    runs = r.json().get("workflow_runs", [])
    for run in runs:
        print(f"ID: {run['id']} | Status: {run['status']:<12} | Conclusion: {str(run['conclusion']):<10} | Created: {run['created_at']} | Head: {run['head_commit']['message'][:40]}")
else:
    print(f"Failed to fetch runs: {r.status_code} - {r.text[:200]}")
