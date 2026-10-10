import urllib.request
import json

def main():
    url = "https://api.github.com/repos/barshal-horse/Go-qunat-bootcamp/actions/runs/38029567297/jobs"
    req = urllib.request.Request(url, headers={"User-Agent": "Python"})
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            for job in data.get("jobs", []):
                print(f"Job: {job['id']} | Name: {job['name']} | Status: {job['status']} | Started: {job['started_at']}")
                for step in job.get("steps", []):
                    print(f"  Step: {step['name']} | Status: {step['status']} | Conclusion: {step.get('conclusion')}")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    main()
