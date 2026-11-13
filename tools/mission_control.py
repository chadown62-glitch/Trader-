"""Finite read-only evidence export. Never creates an installation or starts observation."""
import argparse
from pathlib import Path
from sentinelle.services.evidence import export
from tools.validation.archive import encoded, digest, immutable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = encoded(export(args.data))
    immutable(args.output, raw)
    immutable(Path(str(args.output)+'.sha256'), (digest(raw)+'\n').encode())
    print(args.output)


if __name__ == '__main__':
    main()
