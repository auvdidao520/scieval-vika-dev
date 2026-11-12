"""Local reservation RPC client; caller must trust and protect the socket directory."""
import json
import socket


class GatewayError(RuntimeError):
    pass


class ReservationGateway:
    def __init__(self, socket_path, *, timeout=15):
        self.socket_path, self.timeout = socket_path, timeout

    def _call(self, method, job_id, origin_seq, task_id=None):
        request = {'method': method, 'job_id': job_id, 'origin_seq': origin_seq}
        if task_id is not None:
            request['task_id'] = task_id
        encoded = json.dumps(request, separators=(',', ':')).encode() + b'\n'
        if len(encoded) > 8192:
            raise ValueError('request too large')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout)
                conn.connect(self.socket_path)
                conn.sendall(encoded)
                data = bytearray()
                while b'\n' not in data:
                    chunk = conn.recv(8193 - len(data))
                    if not chunk:
                        raise GatewayError('incomplete gateway response')
                    data.extend(chunk)
                    if len(data) > 8192:
                        raise GatewayError('oversized gateway response')
            response = json.loads(data)
            result = response['result']
            if response.get('ok') is not True or result.get('job_id') != job_id or type(result.get('origin_seq')) is not int or result['origin_seq'] != origin_seq or not isinstance(result.get('task_id'), str) or not result['task_id'] or (task_id is not None and result['task_id'] != task_id):
                raise GatewayError('invalid gateway response')
            return result
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GatewayError('gateway unavailable or rejected request') from exc

    def reserve(self, job_id, origin_seq):
        return self._call('reserve', job_id, origin_seq)

    def activate(self, job_id, origin_seq, task_id):
        return self._call('activate', job_id, origin_seq, task_id)
