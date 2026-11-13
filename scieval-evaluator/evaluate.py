"""Evaluate a local UTF-8 HTML file through the evaluator HTTP interface."""
import argparse
import hashlib
import json
import uuid
from pathlib import Path

from framework import validate_report
from http_evaluator import HttpEvaluator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html')
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--job-id', default=None)
    parser.add_argument('--round', type=int, default=1)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    job = args.job_id or str(uuid.uuid4())
    digest = hashlib.sha256(Path(args.html).read_bytes()).hexdigest()
    client = HttpEvaluator(args.url, args.token_file, timeout=150)
    request = {'job_id':job, 'round':args.round, 'uri':args.html, 'sha256':digest}
    key = hashlib.sha256(json.dumps([job, args.round, digest, 'draft-1'], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    evaluation_id = client.submit(request, key)
    report = client.query(evaluation_id)
    validate_report(report, job_id=job, round_number=args.round, input_sha256=digest)
    if report['evaluation_id'] != evaluation_id or report['rubric_id'] != 'scieval-equip-13' or report['rubric_version'] != 'draft-1' or report['policy_version'] != 'draft-1':
        raise ValueError('report evaluation or policy identity mismatch')
    with open(args.output, 'x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f'evaluation_id={evaluation_id} report={args.output}')

if __name__ == '__main__':
    main()
