#!/usr/bin/env python3
"""
GitHub Contents API 推送脚本 — 绕过 git push (github.com:443 被墙)
用法: python api_push.py <file1> <file2> ...
从 ~/.git-credentials 提取 token，用 GitHub REST API 逐个上传文件。

含大响应 IncompleteRead 重连重试（GitHub API 偶发连接提前关闭）。
"""
import base64, http.client, json, os, sys, ssl, time, urllib.request, urllib.error, re


def get_token():
    """从 ~/.git-credentials 提取 GitHub token"""
    cred_file = os.path.expanduser("~/.git-credentials")
    if not os.path.exists(cred_file):
        return None
    with open(cred_file, 'r') as f:
        for line in f:
            if 'github.com' in line:
                m = re.search(r'https://([^:]+):([^@]+)@github\.com', line)
                if m:
                    return m.group(2)
    return None


def _read_full(resp):
    """分块读取完整响应体（避免一次性 read 触发 IncompleteRead）"""
    if resp.status == 204:
        return b''
    expected = None
    cl = resp.getheader('Content-Length')
    if cl:
        try:
            expected = int(cl)
        except ValueError:
            expected = None
    buf = bytearray()
    while True:
        try:
            chunk = resp.read(65536)
        except (http.client.IncompleteRead, ConnectionResetError,
                ConnectionAbortedError, TimeoutError):
            raise  # 让上层重连
        if not chunk:
            break
        buf.extend(chunk)
    data = bytes(buf)
    if expected is not None and len(data) < expected:
        raise http.client.IncompleteRead(data, expected - len(data))
    return data


def _api_request_with_retry(method, url, token, data=None, max_retries=4):
    """带重试的 API 调用 — IncompleteRead/连接错误时整体重发请求"""
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'User-Agent': 'flashwater-bi-deployer/1.0',
        'Connection': 'close',
    }
    body = json.dumps(data).encode() if data else None
    ctx = ssl.create_default_context()

    last_err = 'unknown error'
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            resp = urllib.request.urlopen(req, context=ctx, timeout=60)
            return _read_full(resp), None
        except urllib.error.HTTPError as e:
            try:
                err_body = _read_full(e)
            except Exception:
                err_body = b''
            if e.code == 404 and method == 'GET':
                return None, None
            if e.code in (409, 422):
                msg = (err_body.decode('utf-8', errors='ignore')[:300]
                       if err_body else f'HTTP {e.code}')
                return None, msg
            if e.code in (500, 502, 503, 504, 408):
                last_err = f'HTTP {e.code}: {err_body[:100]!r}'
                time.sleep(2 + attempt * 2)
                continue
            msg = (err_body.decode('utf-8', errors='ignore')[:300]
                   if err_body else f'HTTP {e.code}')
            return None, msg
        except (http.client.IncompleteRead, ConnectionResetError,
                ConnectionAbortedError, TimeoutError, OSError) as e:
            last_err = f'{type(e).__name__}: {e}'
            time.sleep(2 + attempt * 2)
            continue
    return None, last_err


def api_request(method, url, token, data=None):
    """包装 _api_request_with_retry — 返回 JSON dict 或 {'message': ...}"""
    raw, err = _api_request_with_retry(method, url, token, data)
    if raw is None and err is None:
        return {}  # 404
    if raw is None:
        return {'message': err}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        snippet = raw.decode('utf-8', errors='ignore')[:200]
        return {'message': f'JSON decode error: {e}; snippet={snippet!r}'}


def get_file_sha(repo, branch, path, token):
    """获取远程文件当前 SHA"""
    url = f'https://api.github.com/repos/{repo}/contents/{path}?ref={branch}'
    result = api_request('GET', url, token)
    if isinstance(result, dict) and 'sha' in result:
        return result['sha']
    return None


def push_file(repo, branch, local_path, remote_path, token, message):
    """通过 Contents API 推送单个文件"""
    with open(local_path, 'rb') as f:
        content = base64.b64encode(f.read()).decode()

    sha = get_file_sha(repo, branch, remote_path, token)
    data = {
        'message': message,
        'content': content,
        'branch': branch,
    }
    if sha:
        data['sha'] = sha

    url = f'https://api.github.com/repos/{repo}/contents/{remote_path}'
    result = api_request('PUT', url, token, data)

    if isinstance(result, dict) and 'content' in result:
        print(f'  ✓ {remote_path} uploaded')
        return True
    msg = result.get('message', 'unknown error') if isinstance(result, dict) else str(result)
    print(f'  ✗ {remote_path} failed: {msg}')
    return False


def main():
    repo = 'flashwater-BI/flashwater-bi'
    branch = 'master'

    cwd = os.getcwd()
    files = []
    for arg in sys.argv[1:]:
        if os.path.exists(arg):
            abs_path = os.path.abspath(arg)
            # 远程路径使用相对于当前工作目录的路径，避免 Windows 绝对路径含中文
            remote_path = os.path.relpath(abs_path, cwd).replace(os.sep, '/')
            files.append((abs_path, remote_path))

    if not files:
        print('用法: python api_push.py <local_file1> [local_file2] ...')
        sys.exit(1)

    token = get_token()
    if not token:
        print('✗ 未找到 GitHub token (检查 ~/.git-credentials)')
        sys.exit(1)

    commit_msg = (f'自动更新: {__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")}'
                  f' (API push)')

    print(f'通过 GitHub Contents API 推送 {len(files)} 个文件...')
    success = 0
    for local_path, remote_path in files:
        if push_file(repo, branch, local_path, remote_path, token, commit_msg):
            success += 1

    print(f'完成: {success}/{len(files)} 文件推送成功')
    if success < len(files):
        sys.exit(1)


if __name__ == '__main__':
    main()
