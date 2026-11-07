"""Offline tracked-file credential checks; report locations, never matched values.

This supplements review. It cannot prove that arbitrary sensitive data is absent.
"""
import base64
import json
from pathlib import Path
import re
import subprocess

PATTERNS={
    'private_key':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'supabase_secret':re.compile(rb'sb_secret_[A-Za-z0-9_-]{20,}'),
    'anthropic_secret':re.compile(rb'sk-ant-[A-Za-z0-9_-]{25,}'),
}
JWT=re.compile(rb'eyJ[A-Za-z0-9_-]+\.([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]{10,}')


def findings(data):
    kinds={name for name,pattern in PATTERNS.items() if pattern.search(data)}
    for match in JWT.finditer(data):
        try:
            payload=json.loads(base64.urlsafe_b64decode(match[1]+b'='*(-len(match[1])%4)))
            if isinstance(payload,dict) and payload.get('role')=='service_role':kinds.add('service_role_jwt')
        except (ValueError,UnicodeError):pass
    return sorted(kinds)


def scan(root):
    root=Path(root)
    paths=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
    result=[]
    for relative in paths:
        if not relative:continue
        path=root/relative
        if not path.is_file() or path.is_symlink():continue
        if path.name=='.env':result.append({'file':relative,'kind':'tracked_environment_file'})
        for kind in findings(path.read_bytes()):result.append({'file':relative,'kind':kind})
    return result


if __name__=='__main__':
    result=scan(Path(__file__).resolve().parents[2])
    print(json.dumps({'findings':result},indent=2))
    raise SystemExit(bool(result))
