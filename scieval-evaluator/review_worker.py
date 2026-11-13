"""Submit a Vika Worker's revised HTML to SciEval for teaching review.

This is an explicit handoff called by a trusted host, not a task scheduler or a
Worker completion listener. The caller supplies the task identity and policy.
"""
import argparse
import hashlib
import json
from pathlib import Path

from framework import validate_report
from http_evaluator import HttpEvaluator


def review_worker_artifact(client, *, task_id, job_id, round_number, html_path,
                           rubric, policy_version, pass_score):
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError('task_id is required')
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError('job_id is required')
    if type(round_number) is not int or round_number < 1:
        raise ValueError('round must be a positive integer')
    if type(pass_score) is not int or not 0 <= pass_score <= 3:
        raise ValueError('pass_score must be an explicitly selected integer from 0 to 3')
    if not isinstance(rubric, dict) or set(rubric) != {'id', 'version'} or any(not isinstance(v, str) or not v for v in rubric.values()):
        raise ValueError('rubric id and version are required')
    if not isinstance(policy_version, str) or not policy_version:
        raise ValueError('policy version is required')
    path = Path(html_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('artifact must be a regular, non-symlink file')
    raw = path.read_bytes()
    raw.decode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    request = {'job_id': job_id, 'round': round_number, 'uri': str(path),
               'sha256': digest, 'rubric': rubric, 'policy_version': policy_version}
    # Bind task and policy to the same evaluation request on retries. A changed
    # artifact or policy produces a different key and a separately linked report.
    key_data = [task_id, job_id, round_number, digest, rubric, policy_version, pass_score]
    key = hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    evaluation_id = client.submit(request, key)
    report = client.query(evaluation_id)
    validate_report(report, job_id=job_id, round_number=round_number, input_sha256=digest)
    if (report['evaluation_id'] != evaluation_id or report['rubric_id'] != rubric['id']
            or report['rubric_version'] != rubric['version'] or report['policy_version'] != policy_version):
        raise ValueError('report evaluation or policy identity mismatch')
    # Require actual, verifiable evidence even when a different evaluator is used.
    html = raw.decode('utf-8')
    results = []
    for item in report['criteria']:
        evidence = item['evidence']
        for entry in evidence:
            if (not isinstance(entry, dict) or not isinstance(entry.get('quote'), str)
                    or not entry['quote'] or entry['quote'] not in html
                    or not isinstance(entry.get('locator'), str) or not entry['locator']):
                raise ValueError('invalid criterion evidence')
        if item['status'] == 'scored' and not evidence:
            raise ValueError('scored criterion requires evidence')
        outcome = ('pass' if item['score'] >= pass_score else 'revise') if item['status'] == 'scored' else 'human_review'
        results.append({'criterion_id': item['criterion_id'], 'result': outcome,
                        'score': item['score'], 'evidence': evidence,
                        'reasoning': item['reasoning'], 'recommendations': item['recommendations']})
    # Thresholds and the draft model are not calibrated; never publish or
    # automatically reassign work based on this provisional recommendation.
    recommendation = 'human_review' if any(x['result'] == 'human_review' for x in results) else ('revise' if any(x['result'] == 'revise' for x in results) else 'pass')
    return {'task_id': task_id, 'job_id': job_id, 'round': round_number,
            'artifact_sha256': digest, 'evaluation_id': evaluation_id,
            'rubric': rubric, 'policy_version': policy_version,
            'pass_score': pass_score, 'recommendation': recommendation,
            'decision': 'human_review', 'criteria': results, 'report': report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html')
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--round', type=int, required=True)
    parser.add_argument('--pass-score', type=int, required=True, help='provisional threshold, selected by caller')
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    client = HttpEvaluator(args.url, args.token_file, timeout=150)
    result = review_worker_artifact(client, task_id=args.task_id, job_id=args.job_id,
                                    round_number=args.round, html_path=args.html,
                                    rubric={'id': 'scieval-equip-13', 'version': 'draft-1'},
                                    policy_version='draft-1', pass_score=args.pass_score)
    with open(args.output, 'x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f"evaluation_id={result['evaluation_id']} report={args.output}")


if __name__ == '__main__':
    main()
