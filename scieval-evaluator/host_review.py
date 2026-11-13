"""Evaluate a trusted Vika host handoff from stdin and emit JSON on stdout.

This process is an adapter, not an authentication boundary. Only the Vika
host should start it and provide the handoff and evaluator credentials.
"""
import argparse
import json
import sys

from http_evaluator import HttpEvaluator
from review_worker import review_host_handoff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--token-file', required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('handoff too large')
        handoff = json.loads(raw)
        client = HttpEvaluator(args.url, args.token_file, timeout=150)
        result = review_host_handoff(client, handoff)
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(',', ':'))
        sys.stdout.write('\n')
    except Exception as error:
        # Do not expose request contents, credentials, or remote response bodies.
        print(f'host review failed: {type(error).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
