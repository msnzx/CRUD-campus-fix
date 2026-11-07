"""Run only the synthetic native-pgvector benchmark; never live databases/models."""
import argparse
import json

from experiments.batch2.common import write_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    from pathlib import Path
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        from .local import run_local
        result=run_local()
        write_json(args.out,result)
        print(json.dumps({name:{k:v for k,v in metrics.items() if k!='records'}
                          for name,metrics in result['variants'].items()},indent=2))
        return 0
    except Exception:
        print('Local synthetic benchmark failed; no live service was used.')
        return 2


if __name__=='__main__': raise SystemExit(main())
