import json
import os
import socket
import tempfile
import threading
import unittest

from reservation_gateway import GatewayError, ReservationGateway


class GatewayTests(unittest.TestCase):
    def test_protocol_and_invalid_identity(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'socket')
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen()
            seen = []
            def serve():
                for response in ({'ok': True, 'result': {'job_id': 'job', 'origin_seq': 3, 'task_id': 't1'}},
                                 {'ok': True, 'result': {'job_id': 'other', 'origin_seq': 3, 'task_id': 't1'}}):
                    conn, _ = server.accept()
                    with conn:
                        seen.append(json.loads(conn.recv(8192)))
                        conn.sendall(json.dumps(response).encode() + b'\n')
            thread = threading.Thread(target=serve)
            thread.start()
            gateway = ReservationGateway(path)
            self.assertEqual(gateway.reserve('job', 3)['task_id'], 't1')
            with self.assertRaises(GatewayError):
                gateway.activate('job', 3, 't1')
            thread.join(); server.close()
            self.assertEqual(seen[0], {'method': 'reserve', 'job_id': 'job', 'origin_seq': 3})
            self.assertEqual(seen[1]['task_id'], 't1')

    def test_unavailable(self):
        with self.assertRaises(GatewayError):
            ReservationGateway('/nonexistent/scieval.sock').reserve('job', 1)
