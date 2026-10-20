import json
import time
import urllib.error
import urllib.request

PROBE_HOST = '192.168.16.31'
PROBE_PORT = 8791
PROBE_PATH = '/exec'
PROBE_EVERY = 5
PROBE_SECRET = 'SHARED_SECRET'
PROBE_TIMEOUT = 10
PROBE_COMMAND = 'Write-Output probe-ok'
PROBE_HTTP_TIMEOUT = PROBE_TIMEOUT + PROBE_EVERY

def main():
    url = f'http://{PROBE_HOST}:{PROBE_PORT}{PROBE_PATH}'
    body = json.dumps(dict(command=PROBE_COMMAND, timeout_sec=PROBE_TIMEOUT)).encode()
    while True:
        req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json', 'X-Shared-Secret': PROBE_SECRET})
        try:
            with urllib.request.urlopen(req, timeout=PROBE_HTTP_TIMEOUT) as response:
                print(response.status, response.read().decode('utf-8', 'replace'), flush=True)
        except urllib.error.HTTPError as error:
            print(error.code, error.read().decode('utf-8', 'replace'), flush=True)
        except Exception as error:
            print(type(error).__name__, error, flush=True)
        time.sleep(PROBE_EVERY)

if __name__ == '__main__':
    main()
