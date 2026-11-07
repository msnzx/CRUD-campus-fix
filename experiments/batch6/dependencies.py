"""Read-only installed-metadata/lock audit. Never installs or imports model SDKs."""
from importlib.metadata import distributions,version,PackageNotFoundError
from pathlib import Path
import json
import platform

ROOT=Path(__file__).resolve().parents[2]


def audit_environment():
    try:
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
    except ImportError:
        from pip._vendor.packaging.requirements import Requirement
        from pip._vendor.packaging.specifiers import SpecifierSet
    installed=list(distributions());issues=[];pins=0
    for dist in installed:
        name=dist.metadata['Name']
        python=dist.metadata.get('Requires-Python')
        if python and platform.python_version() not in SpecifierSet(python): issues.append(name+': incompatible Python')
        for raw in dist.requires or []:
            req=Requirement(raw)
            if req.marker and not req.marker.evaluate({'extra':''}): continue
            try:
                if req.specifier and version(req.name) not in req.specifier: issues.append(name+': incompatible '+req.name)
            except PackageNotFoundError: issues.append(name+': missing '+req.name)
    for line in (ROOT/'backend/requirements.lock.txt').read_text().splitlines():
        if not line.strip() or line.startswith('#'): continue
        req=Requirement(line)
        if req.marker and not req.marker.evaluate({'extra':''}): continue
        pins+=1
        try:
            if version(req.name) not in req.specifier: issues.append('Lock mismatch: '+req.name)
        except PackageNotFoundError: issues.append('Lock missing: '+req.name)
    optional={}
    for name in ('ragas','datasets','opentelemetry-sdk','arize-phoenix','llama-index-core'):
        try: optional[name]=version(name)
        except PackageNotFoundError: optional[name]=None
    return {'python':platform.python_version(),'installed_distributions':len(installed),'runtime_pins':pins,
            'issues':issues,'optional_dependencies':optional,'network_calls':0}


if __name__=='__main__':
    result=audit_environment();print(json.dumps(result,indent=2))
    raise SystemExit(bool(result['issues']))
