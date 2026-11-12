"""Privileged local accepted-event query and task routing adapter."""
import json
import socket
from reservation_gateway import GatewayError


class DeliveryGateway:
    def __init__(self, socket_path, *, timeout=15):
        self.socket_path, self.timeout = socket_path, timeout

    def _call(self, request):
        payload = json.dumps(request, separators=(',', ':')).encode() + b'\n'
        if len(payload) > 8192:
            raise GatewayError('request too large')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout)
                conn.connect(self.socket_path)
                conn.sendall(payload)
                data = bytearray()
                while b'\n' not in data:
                    part = conn.recv(min(65536, 1048577 - len(data)))
                    if not part:
                        raise GatewayError('incomplete gateway response')
                    data.extend(part)
                    if len(data) > 1048576:
                        raise GatewayError('oversized gateway response')
            response = json.loads(data)
            if response.get('ok') is not True:
                raise GatewayError('gateway rejected request')
            return response['result']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GatewayError('gateway unavailable or invalid response') from exc

    def accepted_scieval(self, report_id):
        result = self._call({'method': 'accepted_scieval', 'report_id': report_id})
        if not isinstance(result, list):
            raise GatewayError('invalid query response')
        return result

    def route_accepted(self, task_id, row, role):
        if role != 'owner':
            raise GatewayError('invalid route role')
        job_id = row['event']['subject']['id']
        result = self._call({'method': 'route_accepted', 'job_id': job_id, 'task_id': task_id, 'seq': row['seq']})
        if result != {'job_id': job_id, 'task_id': task_id, 'seq': row['seq']}:
            raise GatewayError('invalid route response')
